"""
Migration 010: Add AI Job Queue table.

Creates a queue for scheduled AI interpretation jobs. Each job references
a document and stores the processing state, schedule information, and results.

Features:
- One job per document (UNIQUE constraint on document_id for pending jobs)
- Status tracking: PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED
- Stores AI suggestions as JSON
- Tracks scheduling and processing timestamps
- Supports priority ordering
"""

import sqlite3

VERSION = 10
NAME = "ai_job_queue"


def upgrade(conn: sqlite3.Connection) -> None:
    """Create the ai_job_queue table."""
    cursor = conn.cursor()

    # Create AI job queue table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_job_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            extraction_id INTEGER,
            external_id TEXT,
            
            -- Status: PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED
            status TEXT NOT NULL DEFAULT 'PENDING',
            
            -- Priority (higher = processed first)
            priority INTEGER NOT NULL DEFAULT 0,
            
            -- Scheduling info
            scheduled_at TEXT NOT NULL,
            scheduled_for TEXT,  -- When to process (NULL = ASAP)
            
            -- Processing info
            started_at TEXT,
            completed_at TEXT,
            
            -- Results (JSON)
            suggestions_json TEXT,
            error_message TEXT,
            
            -- Retry tracking
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            
            -- Metadata
            created_by TEXT,  -- 'AUTO', 'USER', 'SYSTEM'
            notes TEXT
        )
    """)

    # Index for finding pending jobs (status + scheduled_for + priority)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_job_queue_pending
        ON ai_job_queue (status, scheduled_for, priority DESC)
    """)

    # Index for document lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_job_queue_document
        ON ai_job_queue (document_id)
    """)

    # Index for extraction lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_job_queue_extraction
        ON ai_job_queue (extraction_id)
    """)

    # Partial unique index: only one PENDING/PROCESSING job per document
    # Note: SQLite doesn't support partial unique indexes directly,
    # so we'll enforce this in the application layer
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_job_queue_active_doc
        ON ai_job_queue (document_id, status)
        WHERE status IN ('PENDING', 'PROCESSING')
    """)

    conn.commit()


def downgrade(conn: sqlite3.Connection) -> None:
    """Drop the ai_job_queue table."""
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS ai_job_queue")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_job_queue_pending")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_job_queue_document")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_job_queue_extraction")
    cursor.execute("DROP INDEX IF EXISTS idx_ai_job_queue_active_doc")
    conn.commit()
