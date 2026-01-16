"""
Migration 009: Add updated_at column to extractions table.

This column tracks when an extraction's data was last modified (edited by user),
separate from when it was created or reviewed.
"""

import sqlite3

VERSION = 9
NAME = "extractions_updated_at"


def upgrade(conn: sqlite3.Connection) -> None:
    """Add updated_at column to extractions table."""
    # Check if column already exists
    cursor = conn.execute("PRAGMA table_info(extractions)")
    columns = [row[1] for row in cursor.fetchall()]

    if "updated_at" not in columns:
        # Add the column with NULL default (existing rows will have NULL)
        conn.execute("ALTER TABLE extractions ADD COLUMN updated_at TEXT")

        # Optionally set updated_at to created_at for existing rows
        conn.execute("UPDATE extractions SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove updated_at column from extractions table.

    Note: SQLite doesn't support DROP COLUMN directly in older versions,
    so we'd need to recreate the table. For simplicity, we leave the column.
    """
    pass
