"""
Migration 005: Add llm_feedback table.

This table tracks user feedback on LLM suggestions,
particularly for "wrong green" detection.
"""

import sqlite3

VERSION = 5
NAME = "llm_feedback"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create llm_feedback table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            suggested_category TEXT NOT NULL,
            actual_category TEXT NOT NULL,
            feedback_type TEXT NOT NULL,  -- WRONG, CORRECT, PARTIAL
            notes TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES interpretation_runs(id)
        )
    """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_feedback_run ON llm_feedback(run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_feedback_type ON llm_feedback(feedback_type)")


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove llm_feedback table."""
    conn.execute("DROP TABLE IF EXISTS llm_feedback")
