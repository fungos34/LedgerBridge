"""
Migration 008: Create linkage table for tracking Paperless-Firefly links.

The linkage table is the SSOT for determining what can be imported to Firefly:
- Only documents with LINKED or ORPHAN status can be imported
- LINKED documents are matched to existing Firefly transactions
- ORPHAN documents have no matching transaction (e.g., cash payments)
"""

import sqlite3

VERSION = 8
NAME = "linkage_table"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create the linkage table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS linkage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            extraction_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            firefly_id INTEGER,  -- NULL for orphans
            link_type TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING, LINKED, ORPHAN, AUTO_LINKED
            confidence REAL,  -- Match confidence score (0.0-1.0)
            match_reasons TEXT,  -- JSON array of match reasons
            linked_at TEXT NOT NULL,
            linked_by TEXT,  -- 'AUTO', 'USER', etc.
            notes TEXT,  -- Optional user notes

            FOREIGN KEY (extraction_id) REFERENCES extractions(id),
            FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id),

            UNIQUE(extraction_id)  -- One link per extraction
        )
        """
    )

    # Create indexes for efficient queries
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkage_document_id ON linkage(document_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkage_firefly_id ON linkage(firefly_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkage_link_type ON linkage(link_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_linkage_extraction_id ON linkage(extraction_id)")


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove the linkage table."""
    conn.execute("DROP INDEX IF EXISTS idx_linkage_extraction_id")
    conn.execute("DROP INDEX IF EXISTS idx_linkage_link_type")
    conn.execute("DROP INDEX IF EXISTS idx_linkage_firefly_id")
    conn.execute("DROP INDEX IF EXISTS idx_linkage_document_id")
    conn.execute("DROP TABLE IF EXISTS linkage")
