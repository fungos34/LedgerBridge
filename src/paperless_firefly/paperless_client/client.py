"""
Paperless-ngx API client implementation.
"""

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any, Iterator
from urllib.parse import urljoin
import json
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class PaperlessError(Exception):
    """Base exception for Paperless client errors."""
    pass


class PaperlessAPIError(PaperlessError):
    """API returned an error response."""
    def __init__(self, status_code: int, message: str, response_body: Optional[str] = None):
        self.status_code = status_code
        self.message = message
        self.response_body = response_body
        super().__init__(f"Paperless API error {status_code}: {message}")


class PaperlessConnectionError(PaperlessError):
    """Failed to connect to Paperless."""
    pass


@dataclass
class PaperlessDocument:
    """Paperless document representation."""
    id: int
    title: str
    content: str  # OCR text
    created: Optional[str]  # Document date
    added: str  # When added to Paperless
    modified: str
    
    # Classification
    correspondent: Optional[str] = None
    correspondent_id: Optional[int] = None
    document_type: Optional[str] = None
    document_type_id: Optional[int] = None
    tags: list[str] = field(default_factory=list)
    tag_ids: list[int] = field(default_factory=list)
    
    # File info
    original_file_name: Optional[str] = None
    archive_serial_number: Optional[int] = None
    
    # Custom fields
    custom_fields: dict[str, Any] = field(default_factory=dict)
    
    # URLs
    download_url: Optional[str] = None
    original_download_url: Optional[str] = None
    
    @classmethod
    def from_api_response(cls, data: dict, base_url: str) -> "PaperlessDocument":
        """Create from Paperless API response."""
        doc_id = data["id"]
        
        # Build download URLs
        download_url = f"{base_url.rstrip('/')}/api/documents/{doc_id}/download/"
        original_download_url = f"{base_url.rstrip('/')}/api/documents/{doc_id}/download/?original=true"
        
        # Parse custom fields
        custom_fields = {}
        for cf in data.get("custom_fields", []):
            custom_fields[cf.get("field", cf.get("name", "unknown"))] = cf.get("value")
        
        return cls(
            id=doc_id,
            title=data.get("title", ""),
            content=data.get("content", ""),
            created=data.get("created"),
            added=data.get("added", ""),
            modified=data.get("modified", ""),
            correspondent=data.get("correspondent__name"),
            correspondent_id=data.get("correspondent"),
            document_type=data.get("document_type__name"),
            document_type_id=data.get("document_type"),
            tags=[t for t in data.get("tags__name", []) if t] if isinstance(data.get("tags__name"), list) else [],
            tag_ids=data.get("tags", []) if isinstance(data.get("tags"), list) else [],
            original_file_name=data.get("original_file_name"),
            archive_serial_number=data.get("archive_serial_number"),
            custom_fields=custom_fields,
            download_url=download_url,
            original_download_url=original_download_url,
        )


class PaperlessClient:
    """
    Client for Paperless-ngx API.
    
    Features:
    - List documents with filters
    - Get document details
    - Download original files
    - Automatic retry with backoff
    """
    
    DEFAULT_TIMEOUT = 30
    DEFAULT_PAGE_SIZE = 25
    
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
    ):
        """
        Initialize Paperless client.
        
        Args:
            base_url: Paperless instance URL (e.g., "http://192.168.1.138:8000")
            token: API token for authentication
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts for transient failures
            backoff_factor: Backoff factor for retries
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        
        # Configure session with retry
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
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
        stream: bool = False,
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
                stream=stream,
            )
        except requests.exceptions.ConnectionError as e:
            raise PaperlessConnectionError(f"Failed to connect to Paperless at {self.base_url}: {e}")
        except requests.exceptions.Timeout as e:
            raise PaperlessConnectionError(f"Request to Paperless timed out: {e}")
        except requests.exceptions.RequestException as e:
            raise PaperlessError(f"Request failed: {e}")
        
        if not response.ok:
            try:
                error_body = response.text
            except Exception:
                error_body = None
            raise PaperlessAPIError(
                status_code=response.status_code,
                message=response.reason,
                response_body=error_body,
            )
        
        return response
    
    def test_connection(self) -> bool:
        """Test connection to Paperless API."""
        try:
            self._request("GET", "/api/")
            return True
        except (PaperlessError, Exception):
            return False
    
    def list_documents(
        self,
        tags: Optional[list[str]] = None,
        tag_ids: Optional[list[int]] = None,
        document_type: Optional[str] = None,
        document_type_id: Optional[int] = None,
        correspondent: Optional[str] = None,
        correspondent_id: Optional[int] = None,
        query: Optional[str] = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        ordering: str = "-added",
    ) -> Iterator[PaperlessDocument]:
        """
        List documents with optional filters.
        
        Args:
            tags: Filter by tag names (all must match)
            tag_ids: Filter by tag IDs (all must match)
            document_type: Filter by document type name
            document_type_id: Filter by document type ID
            correspondent: Filter by correspondent name
            correspondent_id: Filter by correspondent ID
            query: Full-text search query
            page_size: Results per page
            ordering: Sort order (prefix with - for descending)
        
        Yields:
            PaperlessDocument objects
        """
        params: dict[str, Any] = {
            "page_size": page_size,
            "ordering": ordering,
        }
        
        # Add filters
        if tag_ids:
            params["tags__id__all"] = ",".join(str(t) for t in tag_ids)
        if document_type_id:
            params["document_type__id"] = document_type_id
        if correspondent_id:
            params["correspondent__id"] = correspondent_id
        if query:
            params["query"] = query
        
        # Fetch tag/type/correspondent IDs if names provided
        if tags:
            resolved_tag_ids = self._resolve_tag_ids(tags)
            if resolved_tag_ids:
                existing = params.get("tags__id__all", "")
                new_ids = ",".join(str(t) for t in resolved_tag_ids)
                params["tags__id__all"] = f"{existing},{new_ids}".strip(",")
        
        if document_type and not document_type_id:
            type_id = self._resolve_document_type_id(document_type)
            if type_id:
                params["document_type__id"] = type_id
        
        if correspondent and not correspondent_id:
            corr_id = self._resolve_correspondent_id(correspondent)
            if corr_id:
                params["correspondent__id"] = corr_id
        
        # Paginate through results
        page = 1
        while True:
            params["page"] = page
            response = self._request("GET", "/api/documents/", params=params)
            data = response.json()
            
            for doc_data in data.get("results", []):
                yield PaperlessDocument.from_api_response(doc_data, self.base_url)
            
            if not data.get("next"):
                break
            page += 1
    
    def get_document(self, document_id: int) -> PaperlessDocument:
        """
        Get full document details by ID.
        
        Args:
            document_id: Paperless document ID
        
        Returns:
            PaperlessDocument with full details
        """
        response = self._request("GET", f"/api/documents/{document_id}/")
        data = response.json()
        
        # Fetch tag names
        tag_names = []
        for tag_id in data.get("tags", []):
            try:
                tag_response = self._request("GET", f"/api/tags/{tag_id}/")
                tag_data = tag_response.json()
                tag_names.append(tag_data.get("name", ""))
            except PaperlessError:
                pass
        
        # Fetch correspondent name
        correspondent_name = None
        if data.get("correspondent"):
            try:
                corr_response = self._request("GET", f"/api/correspondents/{data['correspondent']}/")
                corr_data = corr_response.json()
                correspondent_name = corr_data.get("name")
            except PaperlessError:
                pass
        
        # Fetch document type name
        doc_type_name = None
        if data.get("document_type"):
            try:
                type_response = self._request("GET", f"/api/document_types/{data['document_type']}/")
                type_data = type_response.json()
                doc_type_name = type_data.get("name")
            except PaperlessError:
                pass
        
        doc = PaperlessDocument.from_api_response(data, self.base_url)
        doc.tags = tag_names
        doc.correspondent = correspondent_name
        doc.document_type = doc_type_name
        
        return doc
    
    def download_original(self, document_id: int) -> tuple[bytes, str]:
        """
        Download original document file.
        
        Args:
            document_id: Paperless document ID
        
        Returns:
            Tuple of (file_bytes, filename)
        """
        response = self._request(
            "GET",
            f"/api/documents/{document_id}/download/",
            params={"original": "true"},
            stream=True,
        )
        
        # Get filename from Content-Disposition header
        filename = f"document_{document_id}"
        content_disp = response.headers.get("Content-Disposition", "")
        if "filename=" in content_disp:
            # Parse filename from header
            parts = content_disp.split("filename=")
            if len(parts) > 1:
                filename = parts[1].strip('"\'')
        
        file_bytes = response.content
        return file_bytes, filename
    
    def download_archived(self, document_id: int) -> tuple[bytes, str]:
        """
        Download archived (processed) document file.
        
        Args:
            document_id: Paperless document ID
        
        Returns:
            Tuple of (file_bytes, filename)
        """
        response = self._request(
            "GET",
            f"/api/documents/{document_id}/download/",
            stream=True,
        )
        
        filename = f"document_{document_id}.pdf"
        content_disp = response.headers.get("Content-Disposition", "")
        if "filename=" in content_disp:
            parts = content_disp.split("filename=")
            if len(parts) > 1:
                filename = parts[1].strip('"\'')
        
        file_bytes = response.content
        return file_bytes, filename
    
    def get_document_ids_by_tag(self, tag_name: str) -> list[int]:
        """Get all document IDs with a specific tag."""
        doc_ids = []
        for doc in self.list_documents(tags=[tag_name]):
            doc_ids.append(doc.id)
        return doc_ids
    
    def _resolve_tag_ids(self, tag_names: list[str]) -> list[int]:
        """Resolve tag names to IDs."""
        tag_ids = []
        try:
            response = self._request("GET", "/api/tags/", params={"page_size": 1000})
            tags_data = response.json().get("results", [])
            name_to_id = {t["name"].lower(): t["id"] for t in tags_data}
            
            for name in tag_names:
                if name.lower() in name_to_id:
                    tag_ids.append(name_to_id[name.lower()])
                else:
                    logger.warning(f"Tag not found: {name}")
        except PaperlessError as e:
            logger.warning(f"Failed to resolve tag IDs: {e}")
        
        return tag_ids
    
    def _resolve_document_type_id(self, type_name: str) -> Optional[int]:
        """Resolve document type name to ID."""
        try:
            response = self._request("GET", "/api/document_types/", params={"page_size": 1000})
            types_data = response.json().get("results", [])
            for t in types_data:
                if t["name"].lower() == type_name.lower():
                    return t["id"]
        except PaperlessError as e:
            logger.warning(f"Failed to resolve document type ID: {e}")
        return None
    
    def _resolve_correspondent_id(self, corr_name: str) -> Optional[int]:
        """Resolve correspondent name to ID."""
        try:
            response = self._request("GET", "/api/correspondents/", params={"page_size": 1000})
            corr_data = response.json().get("results", [])
            for c in corr_data:
                if c["name"].lower() == corr_name.lower():
                    return c["id"]
        except PaperlessError as e:
            logger.warning(f"Failed to resolve correspondent ID: {e}")
        return None


def compute_document_hash(file_bytes: bytes) -> str:
    """Compute SHA256 hash of document bytes."""
    return hashlib.sha256(file_bytes).hexdigest()
