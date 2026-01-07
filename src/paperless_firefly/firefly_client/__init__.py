"""
Firefly III API Client.

Provides:
- Create transactions (POST /api/v1/transactions)
- Query transactions by external_id
- List transactions and categories
- Get unlinked transactions (for reconciliation)
- Account lookup

Treats Firefly errors as loud failures with actionable messages.
"""

from .client import (
    FireflyAPIError,
    FireflyCategory,
    FireflyClient,
    FireflyError,
    FireflyTransaction,
)

__all__ = [
    "FireflyClient",
    "FireflyError",
    "FireflyAPIError",
    "FireflyTransaction",
    "FireflyCategory",
]
