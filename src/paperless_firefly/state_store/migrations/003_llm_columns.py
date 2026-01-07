"""
Migration 003: Add LLM columns to extractions table.

Adds llm_opt_out column for per-document LLM opt-out support.
"""

import sqlite3

VERSION = 3
NAME = "llm_columns"


def upgrade(conn: sqlite3.Connection) -> None:
    """Add llm_opt_out column to extractions."""
    # Check if column already exists
    cursor = conn.execute("PRAGMA table_info(extractions)")
    columns = [row[1] for row in cursor.fetchall()]

    if "llm_opt_out" not in columns:
        conn.execute("ALTER TABLE extractions ADD COLUMN llm_opt_out BOOLEAN DEFAULT FALSE")


def downgrade(conn: sqlite3.Connection) -> None:
    """
    Remove llm_opt_out column.

    Note: SQLite doesn't support DROP COLUMN before 3.35.0.
    This creates a new table without the column.
    """
    # Check SQLite version
    version = sqlite3.sqlite_version_info
    if version >= (3, 35, 0):
        conn.execute("ALTER TABLE extractions DROP COLUMN llm_opt_out")
    else:
        # Manual column removal for older SQLite
        conn.execute(
            """
            CREATE TABLE extractions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                external_id TEXT NOT NULL UNIQUE,
                extraction_json TEXT NOT NULL,
                overall_confidence REAL NOT NULL,
                review_state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                review_decision TEXT,
                FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
            )
        """
        )
        conn.execute(
            """
            INSERT INTO extractions_new
            SELECT id, document_id, external_id, extraction_json, overall_confidence,
                   review_state, created_at, reviewed_at, review_decision
            FROM extractions
        """
        )
        conn.execute("DROP TABLE extractions")
        conn.execute("ALTER TABLE extractions_new RENAME TO extractions")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_extractions_document_id ON extractions(document_id)"
        )
