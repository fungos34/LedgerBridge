"""
Migration 006: Add llm_cache table.

This table caches LLM responses to avoid redundant API calls.
Cache is keyed by a hash of normalized inputs.
"""

import sqlite3

VERSION = 6
NAME = "llm_cache"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create llm_cache table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_cache (
            cache_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            taxonomy_version TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            hit_count INTEGER DEFAULT 1
        )
    """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_cache_expires ON llm_cache(expires_at)")


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove llm_cache table."""
    conn.execute("DROP TABLE IF EXISTS llm_cache")
