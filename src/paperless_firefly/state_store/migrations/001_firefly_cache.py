"""
Migration 001: Add firefly_cache table.

This table caches Firefly transactions for reconciliation matching.
"""

import sqlite3

VERSION = 1
NAME = "firefly_cache"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create firefly_cache table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS firefly_cache (
            firefly_id INTEGER PRIMARY KEY,
            external_id TEXT,
            internal_reference TEXT,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            amount TEXT NOT NULL,
            description TEXT,
            source_account TEXT,
            destination_account TEXT,
            notes TEXT,
            category_name TEXT,
            tags TEXT,  -- JSON array
            synced_at TEXT NOT NULL,
            -- Match state
            match_status TEXT DEFAULT 'UNMATCHED',  -- UNMATCHED, PROPOSED, CONFIRMED
            matched_document_id INTEGER,
            match_confidence REAL,
            FOREIGN KEY (matched_document_id) REFERENCES paperless_documents(document_id)
        )
    """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_firefly_cache_match ON firefly_cache(match_status)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_firefly_cache_date ON firefly_cache(date)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_firefly_cache_external ON firefly_cache(external_id)"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove firefly_cache table."""
    conn.execute("DROP TABLE IF EXISTS firefly_cache")
