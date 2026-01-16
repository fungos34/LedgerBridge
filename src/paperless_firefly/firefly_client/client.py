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

    def list_accounts(self, account_type: str = "asset", max_pages: int = 10) -> list[dict]:
        """
        List accounts of a specific type.

        Args:
            account_type: asset, expense, revenue, liability, cash
            max_pages: Maximum number of pages to fetch (prevents hanging on large datasets)

        Returns:
            List of account dictionaries with id, name, type, and currency_code
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
                accounts.append(
                    {
                        "id": account.get("id"),
                        "name": attrs.get("name"),
                        "type": attrs.get("type"),
                        "currency_code": attrs.get("currency_code"),
                    }
                )

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
