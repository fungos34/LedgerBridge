"""
Firefly III API client implementation.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional, Any
from urllib.parse import urljoin, quote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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
        response_body: Optional[str] = None,
        errors: Optional[dict] = None,
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
    def __init__(self, external_id: str, existing_id: Optional[int] = None):
        self.external_id = external_id
        self.existing_id = existing_id
        super().__init__(f"Transaction with external_id '{external_id}' already exists")


@dataclass
class FireflyTransaction:
    """Firefly transaction representation."""
    id: int
    type: str
    date: str
    amount: str
    description: str
    external_id: Optional[str] = None
    source_name: Optional[str] = None
    destination_name: Optional[str] = None


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
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        
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
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
    ) -> requests.Response:
        """Make an API request with error handling."""
        url = f"{self.base_url}{endpoint}"
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )
        except requests.exceptions.ConnectionError as e:
            raise FireflyConnectionError(f"Failed to connect to Firefly at {self.base_url}: {e}")
        except requests.exceptions.Timeout as e:
            raise FireflyConnectionError(f"Request to Firefly timed out: {e}")
        except requests.exceptions.RequestException as e:
            raise FireflyError(f"Request failed: {e}")
        
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
    ) -> Optional[int]:
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
                        logger.info(f"Transaction with external_id '{external_id}' already exists (id={existing.id})")
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
                    logger.warning(f"Duplicate transaction detected by Firefly")
                    return None
                raise
            raise
        
        # Extract transaction ID from response
        data = response.json()
        transaction_id = data.get("data", {}).get("id")
        
        if transaction_id:
            logger.info(f"Created Firefly transaction id={transaction_id}")
        
        return int(transaction_id) if transaction_id else None
    
    def find_by_external_id(self, external_id: str) -> Optional[FireflyTransaction]:
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
    
    def get_transaction(self, transaction_id: int) -> Optional[FireflyTransaction]:
        """Get a transaction by ID."""
        try:
            response = self._request("GET", f"/api/v1/transactions/{transaction_id}")
            data = response.json().get("data", {})
            attrs = data.get("attributes", {})
            transactions = attrs.get("transactions", [])
            
            if transactions:
                tx = transactions[0]
                return FireflyTransaction(
                    id=int(data.get("id", 0)),
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
    
    def list_accounts(self, account_type: str = "asset") -> list[dict]:
        """
        List accounts of a specific type.
        
        Args:
            account_type: asset, expense, revenue, liability, cash
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
                accounts.append({
                    "id": account.get("id"),
                    "name": attrs.get("name"),
                    "type": attrs.get("type"),
                    "currency_code": attrs.get("currency_code"),
                })
            
            # Check for more pages
            meta = data.get("meta", {}).get("pagination", {})
            if page >= meta.get("total_pages", 1):
                break
            page += 1
        
        return accounts
    
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
