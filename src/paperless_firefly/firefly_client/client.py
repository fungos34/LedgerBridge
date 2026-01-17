"""
Firefly III API client implementation.
"""

import json
import logging
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..schemas.dedupe import generate_external_id_v2
from ..schemas.firefly_payload import FireflyTransactionStore, validate_firefly_payload

logger = logging.getLogger(__name__)


class FireflyError(Exception):
    """Base exception for Firefly client errors."""

    pass


class FireflyAPIError(FireflyError):
    """API returned an error response."""

    def __init__(
        self,
        status_code: int,
        message: str,
        response_body: str | None = None,
        errors: dict | None = None,
    ):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        self.errors = errors or {}

        # Build detailed error message
        error_details = []
        if errors:
            for field, msgs in errors.items():
                if isinstance(msgs, list):
                    error_details.extend([f"{field}: {m}" for m in msgs])
                else:
                    error_details.append(f"{field}: {msgs}")

        detail_str = "; ".join(error_details) if error_details else message
        super().__init__(f"Firefly API error {status_code}: {detail_str}")


class FireflyConnectionError(FireflyError):
    """Failed to connect to Firefly."""

    pass


class FireflyDuplicateError(FireflyError):
    """Transaction already exists (duplicate external_id)."""

    def __init__(self, external_id: str, existing_id: int | None = None):
        self.external_id = external_id
        self.existing_id = existing_id
        super().__init__(f"Transaction with external_id '{external_id}' already exists")


@dataclass
class FireflyTransaction:
    """Firefly transaction representation.

    When a Firefly transaction has multiple splits, we represent it as a single
    FireflyTransaction with:
    - amount: The total amount (sum of all splits)
    - has_splits: True if transaction has multiple splits
    - split_count: Number of splits (1 for single transactions)
    - The category/description are from the first split (primary line)

    This ensures only one link per Firefly transaction is possible, regardless
    of how many splits it contains.

    External ID handling:
    - external_id: The actual external_id stored in Firefly (may be None)
    - computed_external_id: Hash-based ID computed from transaction fields
      This is always computed and can be used for deduplication even when
      external_id is not set in Firefly yet.
    """

    id: int
    type: str
    date: str
    amount: str
    description: str
    external_id: str | None = None
    computed_external_id: str | None = None  # Hash-based ID for deduplication
    source_name: str | None = None
    destination_name: str | None = None
    internal_reference: str | None = None
    notes: str | None = None
    category_name: str | None = None
    tags: list[str] | None = None
    has_splits: bool = False
    split_count: int = 1

    @property
    def effective_external_id(self) -> str | None:
        """Return the external_id to use for deduplication.

        Prefers the stored external_id, falls back to computed_external_id.
        """
        return self.external_id or self.computed_external_id


@dataclass
class FireflyCategory:
    """Firefly category representation."""

    id: int
    name: str
    notes: str | None = None


def _normalize_tags(raw: object) -> list[str] | None:
    """Normalize Firefly tags payload to list[str] or None (SSOT).

    Firefly III API returns tags in varying formats depending on version/context:
    - None → None
    - [] → None (empty treated as absent)
    - ["groceries", "rent"] → ["groceries", "rent"]
    - [{"tag": "groceries"}, {"tag": "rent"}] → ["groceries", "rent"]
    - [{"name": "groceries"}] → ["groceries"] (alternate key)
    - Mixed lists → extract strings safely, skip unknowns

    Args:
        raw: The raw tags value from Firefly API response.

    Returns:
        Normalized list of tag strings, or None if empty/absent.

    Raises:
        FireflyAPIError: If raw is a completely unexpected type (dict, int, etc.)
    """
    if raw is None:
        return None

    if not isinstance(raw, list):
        raise FireflyAPIError(
            500,
            f"Unexpected tags format: expected list or None, got {type(raw).__name__}",
        )

    result: list[str] = []
    _logged_unknown = False

    for item in raw:
        if item is None:
            continue
        elif isinstance(item, str):
            if item:  # Skip empty strings
                result.append(item)
        elif isinstance(item, dict):
            # Try "tag" key first (Firefly standard), then "name" (alternate)
            tag_value = item.get("tag") or item.get("name")
            if tag_value and isinstance(tag_value, str):
                result.append(tag_value)
            elif not _logged_unknown:
                logger.debug(
                    "Unknown tag dict format (no 'tag' or 'name' key): %s",
                    item,
                )
                _logged_unknown = True
        else:
            if not _logged_unknown:
                logger.debug("Skipping unknown tag item type: %s", type(item).__name__)
                _logged_unknown = True

    return result if result else None


class FireflyClient:
    """
    Client for Firefly III API.

    Features:
    - Create transactions
    - Query by external_id
    - Account management
    - Automatic retry with backoff
    """

    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        """
        Initialize Firefly client.

        Args:
            base_url: Firefly III URL (e.g., "http://192.168.1.138:8081")
            token: Personal access token
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            backoff_factor: Backoff factor for retries
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

        # Configure session with retry
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict | None = None,
        json_data: dict | None = None,
    ) -> requests.Response:
        """Make an API request with error handling."""
        url = f"{self.base_url}{endpoint}"

        logger.debug(f"API Request: {method} {url}")
        if json_data:
            logger.debug(f"Request body: {json.dumps(json_data, indent=2)}")

        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {url}: {e}")
            raise FireflyConnectionError(
                f"Failed to connect to Firefly at {self.base_url}: {e}"
            ) from e
        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout for {url}: {e}")
            raise FireflyConnectionError(f"Request to Firefly timed out: {e}") from e
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error for {url}: {e}")
            raise FireflyError(f"Request failed: {e}") from e

        logger.debug(f"Response status: {response.status_code}")

        if not response.ok:
            error_body = None
            errors = {}

            try:
                error_body = response.text
                error_json = response.json()
                errors = error_json.get("errors", {})
                message = error_json.get("message", response.reason)
            except Exception:
                message = response.reason

            logger.error(f"API Error {response.status_code}: {message}")
            logger.error(f"Error details: {errors}")
            logger.debug(f"Full response body: {error_body}")

            raise FireflyAPIError(
                status_code=response.status_code,
                message=message,
                response_body=error_body,
                errors=errors,
            )

        return response

    def test_connection(self) -> bool:
        """Test connection to Firefly API."""
        try:
            self._request("GET", "/api/v1/about")
            return True
        except FireflyError:
            return False

    def get_about(self) -> dict:
        """Get Firefly III instance information."""
        response = self._request("GET", "/api/v1/about")
        return response.json().get("data", {})

    def create_transaction(
        self,
        payload: FireflyTransactionStore,
        skip_duplicates: bool = True,
    ) -> int | None:
        """
        Create a transaction in Firefly III.

        Args:
            payload: Transaction store payload
            skip_duplicates: If True, don't raise error for duplicates

        Returns:
            Firefly transaction ID, or None if duplicate skipped

        Raises:
            FireflyAPIError: If API returns an error
            FireflyDuplicateError: If duplicate and skip_duplicates=False
            ValueError: If payload is invalid
        """
        # Validate payload before sending
        validation_errors = validate_firefly_payload(payload)
        if validation_errors:
            raise ValueError(f"Invalid payload: {'; '.join(validation_errors)}")

        # Check for existing transaction with same external_id
        if payload.transactions:
            external_id = payload.transactions[0].external_id
            if external_id:
                existing = self.find_by_external_id(external_id)
                if existing:
                    if skip_duplicates:
                        logger.info(
                            f"Transaction with external_id '{external_id}' already exists (id={existing.id})"
                        )
                        return existing.id
                    else:
                        raise FireflyDuplicateError(external_id, existing.id)

        # Create transaction
        try:
            response = self._request(
                "POST",
                "/api/v1/transactions",
                json_data=payload.to_dict(),
            )
        except FireflyAPIError as e:
            # Check if it's a duplicate hash error
            if e.status_code == 422 and "duplicate" in str(e.errors).lower():
                if skip_duplicates:
                    logger.warning("Duplicate transaction detected by Firefly")
                    return None
                raise
            raise

        # Extract transaction ID from response
        data = response.json()
        transaction_id = data.get("data", {}).get("id")

        if transaction_id:
            logger.info(f"Created Firefly transaction id={transaction_id}")

        return int(transaction_id) if transaction_id else None

    def update_transaction(
        self,
        transaction_id: int,
        payload: FireflyTransactionStore,
    ) -> bool:
        """
        Update an existing transaction in Firefly III.

        Args:
            transaction_id: The Firefly transaction ID to update
            payload: Transaction store payload with new data

        Returns:
            True if updated successfully

        Raises:
            FireflyAPIError: If API returns an error
            ValueError: If payload is invalid
        """
        # Validate payload before sending
        validation_errors = validate_firefly_payload(payload)
        if validation_errors:
            raise ValueError(f"Invalid payload: {'; '.join(validation_errors)}")

        self._request(
            "PUT",
            f"/api/v1/transactions/{transaction_id}",
            json_data=payload.to_dict(),
        )

        logger.info(f"Updated Firefly transaction id={transaction_id}")
        return True

    def find_by_external_id(self, external_id: str) -> FireflyTransaction | None:
        """
        Find a transaction by external_id.

        Note: Firefly III doesn't have direct external_id search,
        so we search by the external_id value in the query.
        """
        try:
            # Search transactions with the external_id
            response = self._request(
                "GET",
                "/api/v1/search/transactions",
                params={"query": f"external_id:{external_id}"},
            )

            results = response.json().get("data", [])

            for result in results:
                attrs = result.get("attributes", {})
                transactions = attrs.get("transactions", [])

                for tx in transactions:
                    if tx.get("external_id") == external_id:
                        return FireflyTransaction(
                            id=int(result.get("id", 0)),
                            type=tx.get("type", ""),
                            date=tx.get("date", ""),
                            amount=tx.get("amount", ""),
                            description=tx.get("description", ""),
                            external_id=tx.get("external_id"),
                            source_name=tx.get("source_name"),
                            destination_name=tx.get("destination_name"),
                        )
        except FireflyAPIError as e:
            if e.status_code == 404:
                return None
            raise

        return None

    def get_transaction(self, transaction_id: int) -> FireflyTransaction | None:
        """Get a transaction by ID."""
        try:
            response = self._request("GET", f"/api/v1/transactions/{transaction_id}")
            data = response.json().get("data", {})
            attrs = data.get("attributes", {})
            transactions = attrs.get("transactions", [])

            if transactions:
                tx = transactions[0]
                tx_date = tx.get("date", "")[:10]
                source_name = tx.get("source_name")
                destination_name = tx.get("destination_name")
                description = tx.get("description", "")
                amount = tx.get("amount", "")

                # Compute hash-based external_id for deduplication
                computed_external_id = None
                try:
                    computed_external_id = generate_external_id_v2(
                        amount=amount,
                        date=tx_date,
                        source=source_name,
                        destination=destination_name,
                        description=description,
                    )
                except (ValueError, TypeError):
                    pass

                return FireflyTransaction(
                    id=int(data.get("id", 0)),
                    type=tx.get("type", ""),
                    date=tx.get("date", ""),
                    amount=amount,
                    description=description,
                    external_id=tx.get("external_id"),
                    computed_external_id=computed_external_id,
                    source_name=source_name,
                    destination_name=destination_name,
                )
        except FireflyAPIError as e:
            if e.status_code == 404:
                return None
            raise

        return None

    def list_accounts(
        self,
        account_type: str = "asset",
        max_pages: int = 10,
        include_identifiers: bool = False,
    ) -> list[dict]:
        """
        List accounts of a specific type.

        Args:
            account_type: asset, expense, revenue, liability, cash
            max_pages: Maximum number of pages to fetch (prevents hanging on large datasets)
            include_identifiers: If True, include IBAN, account_number, bic fields

        Returns:
            List of account dictionaries with id, name, type, currency_code,
            and optionally iban, account_number, bic if include_identifiers=True
        """
        accounts = []
        page = 1

        while True:
            response = self._request(
                "GET",
                "/api/v1/accounts",
                params={"type": account_type, "page": page},
            )

            data = response.json()
            for account in data.get("data", []):
                attrs = account.get("attributes", {})
                account_dict = {
                    "id": account.get("id"),
                    "name": attrs.get("name"),
                    "type": attrs.get("type"),
                    "currency_code": attrs.get("currency_code"),
                }
                # Include bank identifiers for AI source account matching
                if include_identifiers:
                    account_dict["iban"] = attrs.get("iban")
                    account_dict["account_number"] = attrs.get("account_number")
                    account_dict["bic"] = attrs.get("bic")
                accounts.append(account_dict)

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            total_pages = meta.get("total_pages", 1)

            if page >= total_pages or page >= max_pages:
                if page >= max_pages and total_pages > max_pages:
                    logger.warning(
                        "Reached max_pages limit (%d) while fetching %s accounts. "
                        "Total pages: %d. Some accounts may not be listed.",
                        max_pages,
                        account_type,
                        total_pages,
                    )
                break
            page += 1

        return accounts

    def get_account(self, account_id: int) -> dict | None:
        """
        Get a single account by ID.

        Args:
            account_id: The Firefly III account ID.

        Returns:
            Account dictionary with id, name, type, and currency_code, or None if not found.
        """
        try:
            response = self._request("GET", f"/api/v1/accounts/{account_id}")
            data = response.json()
            account_data = data.get("data", {})
            attrs = account_data.get("attributes", {})
            return {
                "id": account_data.get("id"),
                "name": attrs.get("name"),
                "type": attrs.get("type"),
                "currency_code": attrs.get("currency_code"),
            }
        except Exception as e:
            logger.warning(f"Failed to get account {account_id}: {e}")
            return None

    def find_or_create_account(
        self,
        name: str,
        account_type: str = "expense",
        currency_code: str = "EUR",
    ) -> int:
        """
        Find an account by name or create it.

        Args:
            name: Account name
            account_type: asset, expense, revenue, etc.
            currency_code: Currency code

        Returns:
            Account ID
        """
        # Search existing accounts
        accounts = self.list_accounts(account_type)
        for account in accounts:
            if account["name"].lower() == name.lower():
                return int(account["id"])

        # Create new account
        response = self._request(
            "POST",
            "/api/v1/accounts",
            json_data={
                "name": name,
                "type": account_type,
                "currency_code": currency_code,
            },
        )

        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def list_currencies(self, enabled_only: bool = True) -> list[dict]:
        """
        List available currencies from Firefly III.

        Args:
            enabled_only: If True, only return enabled currencies

        Returns:
            List of currency dictionaries with code, name, symbol, decimal_places, enabled, default
        """
        currencies = []
        page = 1

        while True:
            response = self._request(
                "GET",
                "/api/v1/currencies",
                params={"page": page},
            )

            data = response.json()
            for currency in data.get("data", []):
                attrs = currency.get("attributes", {})
                is_enabled = attrs.get("enabled", True)
                # Skip disabled currencies if enabled_only is True
                if enabled_only and not is_enabled:
                    continue
                currencies.append(
                    {
                        "id": currency.get("id"),
                        "code": attrs.get("code"),
                        "name": attrs.get("name"),
                        "symbol": attrs.get("symbol"),
                        "decimal_places": attrs.get("decimal_places", 2),
                        "enabled": is_enabled,
                        "default": attrs.get("default", False),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            total_pages = meta.get("total_pages", 1)

            if page >= total_pages:
                break
            page += 1

        return currencies

    def list_transactions(
        self,
        start_date: str,
        end_date: str,
        type_filter: str | None = None,
        limit: int | None = None,
    ) -> list[FireflyTransaction]:
        """
        List transactions in a date range.

        IMPORTANT: Split transactions in Firefly are returned as a single
        FireflyTransaction with the total amount. This ensures only one link
        per Firefly transaction is possible. The first split's metadata
        (description, category, etc.) is used for display purposes.

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            type_filter: Optional filter: withdrawal, deposit, transfer
            limit: Optional max results (None = all)

        Returns:
            List of FireflyTransaction objects (one per Firefly transaction, not per split)
        """
        transactions = []
        page = 1

        while True:
            params = {
                "start": start_date,
                "end": end_date,
                "page": page,
            }
            if type_filter:
                params["type"] = type_filter

            response = self._request("GET", "/api/v1/transactions", params=params)
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                tx_list = attrs.get("transactions", [])

                if not tx_list:
                    continue

                # Aggregate all splits into a single transaction
                # Use first split for primary metadata, sum amounts
                first_split = tx_list[0]
                split_count = len(tx_list)
                has_splits = split_count > 1

                # Sum amounts from all splits
                total_amount = sum(float(tx.get("amount", 0)) for tx in tx_list)

                # Collect all unique tags across splits
                all_tags: list[str] = []
                for tx in tx_list:
                    tx_tags = _normalize_tags(tx.get("tags"))
                    if tx_tags:
                        for tag in tx_tags:
                            if tag not in all_tags:
                                all_tags.append(tag)

                # Build description that includes split info if relevant
                description = first_split.get("description", "")
                if has_splits:
                    # Append indicator that this has splits
                    split_summaries = [
                        f"{tx.get('description', 'Split')} ({tx.get('amount', '?')})"
                        for tx in tx_list[1:4]  # Show up to 3 additional splits
                    ]
                    if split_count > 4:
                        split_summaries.append(f"... +{split_count - 4} more")
                    # Keep original description primary, add split count note
                    notes_suffix = f" [Split: {split_count} parts]"
                else:
                    notes_suffix = ""

                # Get notes - combine with split indicator
                existing_notes = first_split.get("notes") or ""
                combined_notes = (
                    (existing_notes + notes_suffix).strip()
                    if notes_suffix
                    else existing_notes or None
                )

                # Extract date in YYYY-MM-DD format for hash computation
                tx_date = first_split.get("date", "")[:10]
                source_name = first_split.get("source_name")
                destination_name = first_split.get("destination_name")

                # Compute hash-based external_id for deduplication
                # This is computed even if external_id exists, for consistent dedup
                computed_external_id = None
                try:
                    computed_external_id = generate_external_id_v2(
                        amount=str(total_amount),
                        date=tx_date,
                        source=source_name,
                        destination=destination_name,
                        description=description,
                    )
                except (ValueError, TypeError):
                    # If hash computation fails, continue without it
                    pass

                transactions.append(
                    FireflyTransaction(
                        id=int(item.get("id", 0)),
                        type=first_split.get("type", ""),
                        date=first_split.get("date", ""),
                        amount=str(total_amount),
                        description=description,
                        external_id=first_split.get("external_id"),
                        computed_external_id=computed_external_id,
                        source_name=source_name,
                        destination_name=destination_name,
                        internal_reference=first_split.get("internal_reference"),
                        notes=combined_notes,
                        category_name=first_split.get("category_name"),
                        tags=all_tags if all_tags else None,
                        has_splits=has_splits,
                        split_count=split_count,
                    )
                )

            # Check limit
            if limit and len(transactions) >= limit:
                return transactions[:limit]

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return transactions

    def list_categories(self) -> list[FireflyCategory]:
        """
        List all categories from Firefly.

        Returns:
            List of FireflyCategory objects
        """
        categories = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/categories", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                categories.append(
                    FireflyCategory(
                        id=int(item.get("id", 0)),
                        name=attrs.get("name", ""),
                        notes=attrs.get("notes"),
                    )
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return categories

    def list_tags(self) -> list[dict]:
        """
        List all tags from Firefly.

        Returns:
            List of tag dictionaries with id, tag (name), and description
        """
        tags = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/tags", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                tags.append(
                    {
                        "id": int(item.get("id", 0)),
                        "tag": attrs.get("tag", ""),
                        "description": attrs.get("description"),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return tags

    def create_tag(self, tag: str, description: str | None = None) -> int:
        """
        Create a new tag in Firefly.

        Args:
            tag: Tag name
            description: Optional description

        Returns:
            Tag ID
        """
        payload = {"tag": tag}
        if description:
            payload["description"] = description

        response = self._request("POST", "/api/v1/tags", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_tag_by_name(self, name: str) -> dict | None:
        """
        Find a tag by exact name match.

        Args:
            name: Tag name to search for

        Returns:
            Tag dict or None if not found
        """
        tags = self.list_tags()
        name_lower = name.lower().strip()
        for tag in tags:
            if tag["tag"].lower().strip() == name_lower:
                return tag
        return None

    def list_piggy_banks(self) -> list[dict]:
        """
        List all piggy banks from Firefly.

        Returns:
            List of piggy bank dictionaries
        """
        piggy_banks = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/piggy-banks", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                piggy_banks.append(
                    {
                        "id": int(item.get("id", 0)),
                        "name": attrs.get("name", ""),
                        "target_amount": attrs.get("target_amount"),
                        "current_amount": attrs.get("current_amount"),
                        "account_id": attrs.get("account_id"),
                        "notes": attrs.get("notes"),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return piggy_banks

    def create_piggy_bank(
        self,
        name: str,
        account_id: int,
        target_amount: str | None = None,
        notes: str | None = None,
    ) -> int:
        """
        Create a new piggy bank in Firefly.

        Args:
            name: Piggy bank name
            account_id: Linked asset account ID
            target_amount: Target savings amount (optional)
            notes: Optional notes

        Returns:
            Piggy bank ID
        """
        payload = {"name": name, "account_id": account_id}
        if target_amount:
            payload["target_amount"] = target_amount
        if notes:
            payload["notes"] = notes

        response = self._request("POST", "/api/v1/piggy-banks", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_piggy_bank_by_name(self, name: str) -> dict | None:
        """
        Find a piggy bank by exact name match.

        Args:
            name: Piggy bank name to search for

        Returns:
            Piggy bank dict or None if not found
        """
        piggy_banks = self.list_piggy_banks()
        name_lower = name.lower().strip()
        for pb in piggy_banks:
            if pb["name"].lower().strip() == name_lower:
                return pb
        return None

    def create_category(self, name: str, notes: str | None = None) -> int:
        """
        Create a new category in Firefly.

        Args:
            name: Category name
            notes: Optional notes

        Returns:
            Category ID
        """
        payload = {"name": name}
        if notes:
            payload["notes"] = notes

        response = self._request("POST", "/api/v1/categories", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_category_by_name(self, name: str) -> FireflyCategory | None:
        """
        Find a category by exact name match.

        Args:
            name: Category name to search for

        Returns:
            FireflyCategory or None if not found
        """
        categories = self.list_categories()
        name_lower = name.lower().strip()
        for cat in categories:
            if cat.name.lower().strip() == name_lower:
                return cat
        return None

    def get_unlinked_transactions(
        self,
        start_date: str,
        end_date: str,
        type_filter: str | None = None,
    ) -> list[FireflyTransaction]:
        """
        Get transactions not linked to Spark/LedgerBridge.

        A transaction is "unlinked" if:
        - external_id does NOT start with "paperless:"
        - internal_reference does NOT contain "PAPERLESS:"
        - notes do NOT contain "Paperless doc_id="

        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            type_filter: Optional filter: withdrawal, deposit, transfer

        Returns:
            List of unlinked FireflyTransaction objects
        """
        from ..schemas.linkage import is_linked_to_spark

        all_transactions = self.list_transactions(start_date, end_date, type_filter)

        return [
            tx
            for tx in all_transactions
            if not is_linked_to_spark(tx.external_id, tx.internal_reference, tx.notes)
        ]

    def update_transaction_linkage(
        self,
        transaction_id: int,
        external_id: str,
        internal_reference: str,
        notes_to_append: str,
    ) -> bool:
        """
        Update an existing transaction with linkage markers.

        This adds Spark linkage to an existing transaction (e.g., bank import).

        Args:
            transaction_id: Firefly transaction ID
            external_id: External ID to set
            internal_reference: Internal reference to set
            notes_to_append: Text to append to existing notes

        Returns:
            True if updated successfully
        """
        # Get existing transaction to preserve data
        existing = self.get_transaction(transaction_id)
        if not existing:
            raise FireflyAPIError(404, f"Transaction {transaction_id} not found")

        # Build notes (append to existing)
        new_notes = existing.notes or ""
        if new_notes:
            new_notes += "\n\n"
        new_notes += notes_to_append

        # Update via PUT - need to get full transaction data first
        response = self._request("GET", f"/api/v1/transactions/{transaction_id}")
        data = response.json().get("data", {})
        attrs = data.get("attributes", {})
        tx_list = attrs.get("transactions", [])

        if not tx_list:
            raise FireflyAPIError(500, f"Transaction {transaction_id} has no splits")

        # Update the first split with linkage
        tx = tx_list[0]
        tx["external_id"] = external_id
        tx["internal_reference"] = internal_reference
        tx["notes"] = new_notes

        # Send update
        self._request(
            "PUT",
            f"/api/v1/transactions/{transaction_id}",
            json_data={"transactions": tx_list},
        )

        logger.info(f"Updated transaction {transaction_id} with linkage markers")
        return True

    def set_external_id(
        self,
        transaction_id: int,
        external_id: str,
    ) -> bool:
        """
        Set the external_id for a transaction (without other changes).

        This is used to assign a computed hash-based external_id to Firefly
        transactions that don't have one, ensuring deduplication across syncs.

        Note: Only sets external_id if the transaction doesn't already have one,
        to avoid overwriting existing IDs that may have other meanings.

        Args:
            transaction_id: Firefly transaction ID
            external_id: External ID to set (typically computed hash)

        Returns:
            True if updated successfully, False if transaction already has external_id
        """
        # Get full transaction data
        response = self._request("GET", f"/api/v1/transactions/{transaction_id}")
        data = response.json().get("data", {})
        attrs = data.get("attributes", {})
        tx_list = attrs.get("transactions", [])

        if not tx_list:
            raise FireflyAPIError(500, f"Transaction {transaction_id} has no splits")

        # Check if external_id is already set
        first_split = tx_list[0]
        if first_split.get("external_id"):
            logger.debug(f"Transaction {transaction_id} already has external_id, skipping")
            return False

        # Set external_id on first split
        first_split["external_id"] = external_id

        # Send update
        self._request(
            "PUT",
            f"/api/v1/transactions/{transaction_id}",
            json_data={"transactions": tx_list},
        )

        logger.info(f"Set external_id for transaction {transaction_id}: {external_id}")
        return True

    # =========================================================================
    # Budget Methods (Sync Assistant - Everything)
    # =========================================================================

    def list_budgets(self) -> list[dict]:
        """
        List all budgets from Firefly.

        Returns:
            List of budget dictionaries with id, name, auto_budget_type, etc.
        """
        budgets = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/budgets", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                budgets.append(
                    {
                        "id": int(item.get("id", 0)),
                        "name": attrs.get("name", ""),
                        "auto_budget_type": attrs.get("auto_budget_type"),
                        "auto_budget_amount": attrs.get("auto_budget_amount"),
                        "auto_budget_period": attrs.get("auto_budget_period"),
                        "notes": attrs.get("notes"),
                        "active": attrs.get("active", True),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return budgets

    def create_budget(
        self,
        name: str,
        auto_budget_type: str | None = None,
        auto_budget_amount: str | None = None,
        auto_budget_period: str | None = None,
        notes: str | None = None,
    ) -> int:
        """
        Create a new budget in Firefly.

        Args:
            name: Budget name
            auto_budget_type: Optional (reset, rollover, none)
            auto_budget_amount: Optional budget amount
            auto_budget_period: Optional period (daily, weekly, monthly, quarterly, yearly)
            notes: Optional notes

        Returns:
            Budget ID
        """
        payload: dict = {"name": name}
        if auto_budget_type:
            payload["auto_budget_type"] = auto_budget_type
        if auto_budget_amount:
            payload["auto_budget_amount"] = auto_budget_amount
        if auto_budget_period:
            payload["auto_budget_period"] = auto_budget_period
        if notes:
            payload["notes"] = notes

        response = self._request("POST", "/api/v1/budgets", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_budget_by_name(self, name: str) -> dict | None:
        """
        Find a budget by exact name match.

        Args:
            name: Budget name to search for

        Returns:
            Budget dict or None if not found
        """
        budgets = self.list_budgets()
        name_lower = name.lower().strip()
        for budget in budgets:
            if budget["name"].lower().strip() == name_lower:
                return budget
        return None

    # =========================================================================
    # Bill Methods (Sync Assistant - Everything)
    # =========================================================================

    def list_bills(self) -> list[dict]:
        """
        List all bills from Firefly.

        Returns:
            List of bill dictionaries
        """
        bills = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/bills", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                bills.append(
                    {
                        "id": int(item.get("id", 0)),
                        "name": attrs.get("name", ""),
                        "amount_min": attrs.get("amount_min"),
                        "amount_max": attrs.get("amount_max"),
                        "date": attrs.get("date"),
                        "repeat_freq": attrs.get("repeat_freq"),
                        "skip": attrs.get("skip", 0),
                        "active": attrs.get("active", True),
                        "notes": attrs.get("notes"),
                        "currency_code": attrs.get("currency_code"),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return bills

    def create_bill(
        self,
        name: str,
        amount_min: str,
        amount_max: str,
        date: str,
        repeat_freq: str,
        skip: int = 0,
        active: bool = True,
        notes: str | None = None,
        currency_code: str = "EUR",
    ) -> int:
        """
        Create a new bill in Firefly.

        Args:
            name: Bill name
            amount_min: Minimum amount
            amount_max: Maximum amount
            date: Next expected date (YYYY-MM-DD)
            repeat_freq: Repeat frequency (weekly, monthly, quarterly, yearly, etc.)
            skip: Number of periods to skip
            active: Whether the bill is active
            notes: Optional notes
            currency_code: Currency code (default EUR)

        Returns:
            Bill ID
        """
        payload = {
            "name": name,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "date": date,
            "repeat_freq": repeat_freq,
            "skip": skip,
            "active": active,
            "currency_code": currency_code,
        }
        if notes:
            payload["notes"] = notes

        response = self._request("POST", "/api/v1/bills", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_bill_by_name(self, name: str) -> dict | None:
        """
        Find a bill by exact name match.

        Args:
            name: Bill name to search for

        Returns:
            Bill dict or None if not found
        """
        bills = self.list_bills()
        name_lower = name.lower().strip()
        for bill in bills:
            if bill["name"].lower().strip() == name_lower:
                return bill
        return None

    # =========================================================================
    # Rule Group Methods (Sync Assistant - Everything)
    # =========================================================================

    def list_rule_groups(self) -> list[dict]:
        """
        List all rule groups from Firefly.

        Returns:
            List of rule group dictionaries
        """
        rule_groups = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/rule-groups", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                rule_groups.append(
                    {
                        "id": int(item.get("id", 0)),
                        "title": attrs.get("title", ""),
                        "order": attrs.get("order"),
                        "active": attrs.get("active", True),
                        "description": attrs.get("description"),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return rule_groups

    def create_rule_group(
        self,
        title: str,
        order: int | None = None,
        active: bool = True,
        description: str | None = None,
    ) -> int:
        """
        Create a new rule group in Firefly.

        Args:
            title: Rule group title
            order: Optional order position
            active: Whether active
            description: Optional description

        Returns:
            Rule group ID
        """
        payload: dict = {"title": title, "active": active}
        if order is not None:
            payload["order"] = order
        if description:
            payload["description"] = description

        response = self._request("POST", "/api/v1/rule-groups", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_rule_group_by_title(self, title: str) -> dict | None:
        """
        Find a rule group by exact title match.

        Args:
            title: Rule group title to search for

        Returns:
            Rule group dict or None if not found
        """
        rule_groups = self.list_rule_groups()
        title_lower = title.lower().strip()
        for rg in rule_groups:
            if rg["title"].lower().strip() == title_lower:
                return rg
        return None

    # =========================================================================
    # Rule Methods (Sync Assistant - Everything)
    # =========================================================================

    def list_rules(self) -> list[dict]:
        """
        List all rules from Firefly.

        Returns:
            List of rule dictionaries with triggers and actions
        """
        rules = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/rules", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                rules.append(
                    {
                        "id": int(item.get("id", 0)),
                        "title": attrs.get("title", ""),
                        "rule_group_id": attrs.get("rule_group_id"),
                        "rule_group_title": attrs.get("rule_group_title"),
                        "order": attrs.get("order"),
                        "active": attrs.get("active", True),
                        "strict": attrs.get("strict", False),
                        "triggers": attrs.get("triggers", []),
                        "actions": attrs.get("actions", []),
                        "description": attrs.get("description"),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return rules

    def create_rule(
        self,
        title: str,
        rule_group_id: int,
        triggers: list[dict],
        actions: list[dict],
        order: int | None = None,
        active: bool = True,
        strict: bool = False,
        description: str | None = None,
    ) -> int:
        """
        Create a new rule in Firefly.

        Args:
            title: Rule title
            rule_group_id: Parent rule group ID
            triggers: List of trigger definitions
            actions: List of action definitions
            order: Optional order position
            active: Whether active
            strict: Whether all triggers must match (AND) vs any (OR)
            description: Optional description

        Returns:
            Rule ID
        """
        payload: dict = {
            "title": title,
            "rule_group_id": rule_group_id,
            "triggers": triggers,
            "actions": actions,
            "active": active,
            "strict": strict,
        }
        if order is not None:
            payload["order"] = order
        if description:
            payload["description"] = description

        response = self._request("POST", "/api/v1/rules", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_rule_by_title(self, title: str) -> dict | None:
        """
        Find a rule by exact title match.

        Args:
            title: Rule title to search for

        Returns:
            Rule dict or None if not found
        """
        rules = self.list_rules()
        title_lower = title.lower().strip()
        for rule in rules:
            if rule["title"].lower().strip() == title_lower:
                return rule
        return None

    # =========================================================================
    # Recurrence Methods (Sync Assistant - Everything)
    # =========================================================================

    def list_recurrences(self) -> list[dict]:
        """
        List all recurring transactions from Firefly.

        Returns:
            List of recurrence dictionaries
        """
        recurrences = []
        page = 1

        while True:
            response = self._request("GET", "/api/v1/recurrences", params={"page": page})
            data = response.json()

            for item in data.get("data", []):
                attrs = item.get("attributes", {})
                recurrences.append(
                    {
                        "id": int(item.get("id", 0)),
                        "title": attrs.get("title", ""),
                        "first_date": attrs.get("first_date"),
                        "latest_date": attrs.get("latest_date"),
                        "repeat_freq": attrs.get("repeat_until"),
                        "repetitions": attrs.get("repetitions", []),
                        "transactions": attrs.get("transactions", []),
                        "notes": attrs.get("notes"),
                        "active": attrs.get("active", True),
                    }
                )

            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1

        return recurrences

    def create_recurrence(
        self,
        title: str,
        first_date: str,
        repeat_freq: str,
        transactions: list[dict],
        repeat_until: str | None = None,
        nr_of_repetitions: int | None = None,
        apply_rules: bool = True,
        active: bool = True,
        notes: str | None = None,
    ) -> int:
        """
        Create a new recurring transaction in Firefly.

        Args:
            title: Recurrence title
            first_date: First occurrence date (YYYY-MM-DD)
            repeat_freq: Frequency (daily, weekly, monthly, etc.)
            transactions: List of transaction definitions
            repeat_until: Optional end date
            nr_of_repetitions: Optional number of repetitions
            apply_rules: Whether to apply rules to generated transactions
            active: Whether active
            notes: Optional notes

        Returns:
            Recurrence ID
        """
        # Build repetitions array
        repetitions = [
            {
                "type": repeat_freq,
                "moment": "",
            }
        ]

        payload: dict = {
            "title": title,
            "first_date": first_date,
            "repetitions": repetitions,
            "transactions": transactions,
            "apply_rules": apply_rules,
            "active": active,
        }
        if repeat_until:
            payload["repeat_until"] = repeat_until
        if nr_of_repetitions:
            payload["nr_of_repetitions"] = nr_of_repetitions
        if notes:
            payload["notes"] = notes

        response = self._request("POST", "/api/v1/recurrences", json_data=payload)
        data = response.json()
        return int(data.get("data", {}).get("id", 0))

    def find_recurrence_by_title(self, title: str) -> dict | None:
        """
        Find a recurrence by exact title match.

        Args:
            title: Recurrence title to search for

        Returns:
            Recurrence dict or None if not found
        """
        recurrences = self.list_recurrences()
        title_lower = title.lower().strip()
        for rec in recurrences:
            if rec["title"].lower().strip() == title_lower:
                return rec
        return None

    # =========================================================================
    # Currency Methods (Sync Assistant - Everything)
    # =========================================================================

    # Note: list_currencies is defined earlier in the file with enabled_only parameter.
    # This section previously had a duplicate which has been removed.

    def enable_currency(self, code: str) -> bool:
        """
        Enable a currency in Firefly.

        Args:
            code: Currency code (e.g., 'USD', 'EUR')

        Returns:
            True if enabled successfully
        """
        code = code.upper().strip()
        response = self._request("POST", f"/api/v1/currencies/{code}/enable")
        return response.status_code == 204 or response.status_code == 200

    def find_currency_by_code(self, code: str) -> dict | None:
        """
        Find a currency by code.

        Args:
            code: Currency code to search for

        Returns:
            Currency dict or None if not found
        """
        currencies = self.list_currencies()
        code_upper = code.upper().strip()
        for curr in currencies:
            if curr["code"].upper().strip() == code_upper:
                return curr
        return None
