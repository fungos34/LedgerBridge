"""
Migration 007: Add soft delete support to firefly_cache table.

Adds deleted_at column to preserve audit trail when Firefly transactions
are deleted from Firefly but we want to retain the record.
"""

import sqlite3

VERSION = 7
NAME = "firefly_cache_soft_delete"


def upgrade(conn: sqlite3.Connection) -> None:
    """Add soft delete column to firefly_cache."""
    conn.execute(
        """
        ALTER TABLE firefly_cache ADD COLUMN deleted_at TEXT DEFAULT NULL
    """
    )
    # Index for efficient filtering of active (non-deleted) records
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_firefly_cache_deleted ON firefly_cache(deleted_at)"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove soft delete column (SQLite doesn't support DROP COLUMN easily)."""
    # For SQLite, we'd need to recreate the table - leave as-is for now
    raise NotImplementedError("Downgrade not supported for this migration")
