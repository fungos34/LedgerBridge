"""
Firefly III API Client.

Provides:
- Create transactions (POST /api/v1/transactions)
- Query transactions by external_id
- Account lookup

Treats Firefly errors as loud failures with actionable messages.
"""

from .client import FireflyAPIError, FireflyClient, FireflyError

__all__ = [
    "FireflyClient",
    "FireflyError",
    "FireflyAPIError",
]
