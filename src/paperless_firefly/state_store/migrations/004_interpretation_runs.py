"""
Migration 004: Add interpretation_runs table.

This table stores audit trail for every interpretation run,
including deterministic rules and LLM results.
"""

import sqlite3

VERSION = 4
NAME = "interpretation_runs"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create interpretation_runs table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS interpretation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            -- Target object
            document_id INTEGER,
            firefly_id INTEGER,
            external_id TEXT,

            -- Run metadata
            run_timestamp TEXT NOT NULL,
            duration_ms INTEGER,
            pipeline_version TEXT NOT NULL,
            algorithm_version TEXT,

            -- Input summary (JSON)
            inputs_summary TEXT NOT NULL,

            -- Rules applied (JSON)
            rules_applied TEXT,

            -- LLM involvement (JSON, nullable)
            llm_result TEXT,

            -- Final decision
            final_state TEXT NOT NULL,  -- GREEN, YELLOW, RED
            suggested_category TEXT,
            suggested_splits TEXT,  -- JSON array if applicable
            auto_applied BOOLEAN DEFAULT FALSE,

            -- Operational clarity fields
            decision_source TEXT,  -- RULES, LLM, HYBRID, USER
            firefly_write_action TEXT,  -- NONE, CREATE_NEW, UPDATE_EXISTING
            firefly_target_id INTEGER,  -- Firefly transaction ID (if UPDATE_EXISTING)
            linkage_marker_written TEXT,  -- JSON: {"external_id": "...", "notes_appended": true}
            taxonomy_version TEXT,  -- Category taxonomy hash at run time

            -- Foreign keys
            FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
        )
    """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interpretation_runs_document "
        "ON interpretation_runs(document_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interpretation_runs_firefly "
        "ON interpretation_runs(firefly_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_interpretation_runs_timestamp "
        "ON interpretation_runs(run_timestamp)"
    )


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove interpretation_runs table."""
    conn.execute("DROP TABLE IF EXISTS interpretation_runs")
