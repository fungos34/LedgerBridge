"""
Paperless-ngx API Client.

Provides:
- List documents with filters (tags/type/correspondent/custom field)
- Get document detail JSON
- Download original file
- Retry/backoff for transient network failures

Supports LAN-only base URL and token auth.
"""

from .client import PaperlessClient, PaperlessDocument, PaperlessError

__all__ = [
    "PaperlessClient",
    "PaperlessDocument",
    "PaperlessError",
]
