"""
State Store (SQLite-based).

Lightweight persistent DB for tracking:
- Paperless documents processed
- Extractions generated
- Imports to Firefly III
- Bank transaction matches (optional)

Enforces uniqueness on external_id and doc_id.
"""

from .sqlite_store import (
    StateStore,
    DocumentRecord,
    ExtractionRecord,
    ImportRecord,
    ImportStatus,
)

__all__ = [
    "StateStore",
    "DocumentRecord",
    "ExtractionRecord",
    "ImportRecord",
    "ImportStatus",
]
