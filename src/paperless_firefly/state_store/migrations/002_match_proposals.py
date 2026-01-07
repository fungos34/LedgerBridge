"""
Migration 002: Add match_proposals table.

This table stores proposed matches between receipts and transactions.
"""

import sqlite3

VERSION = 2
NAME = "match_proposals"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create match_proposals table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS match_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firefly_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            match_score REAL NOT NULL,
            match_reasons TEXT,  -- JSON: ["amount_exact", "date_within_3_days", ...]
            status TEXT DEFAULT 'PENDING',  -- PENDING, ACCEPTED, REJECTED
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            FOREIGN KEY (firefly_id) REFERENCES firefly_cache(firefly_id),
            FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
        )
    """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_match_proposals_status ON match_proposals(status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_match_proposals_firefly ON match_proposals(firefly_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_match_proposals_document ON match_proposals(document_id)"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove match_proposals table."""
    conn.execute("DROP TABLE IF EXISTS match_proposals")
