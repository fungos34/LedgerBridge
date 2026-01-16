"""
Migration 011: Add user ownership to documents and extractions.

Adds user_id column to paperless_documents and extractions tables to support
multi-user isolation. Each user can only see their own documents and extractions,
unless they are a superuser who can see all.

Features:
- user_id column on paperless_documents (nullable for backwards compatibility)
- user_id column on extractions (nullable for backwards compatibility)
- Index on user_id for efficient filtering
- Existing records remain visible to all (NULL user_id = legacy/shared)
"""

import sqlite3

VERSION = 11
NAME = "user_ownership"


def upgrade(conn: sqlite3.Connection) -> None:
    """Add user_id columns to documents and extractions tables."""
    cursor = conn.cursor()

    # Check if column already exists in paperless_documents
    cursor.execute("PRAGMA table_info(paperless_documents)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE paperless_documents
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        # Create index for efficient user filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_paperless_documents_user_id
            ON paperless_documents(user_id)
        """)

    # Check if column already exists in extractions
    cursor.execute("PRAGMA table_info(extractions)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE extractions
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        # Create index for efficient user filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_extractions_user_id
            ON extractions(user_id)
        """)

    # Also add to imports table for consistency
    cursor.execute("PRAGMA table_info(imports)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE imports
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        # Create index for efficient user filtering
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_imports_user_id
            ON imports(user_id)
        """)

    # Add to firefly_cache as well
    cursor.execute("PRAGMA table_info(firefly_cache)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE firefly_cache
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_firefly_cache_user_id
            ON firefly_cache(user_id)
        """)

    # Add to match_proposals
    cursor.execute("PRAGMA table_info(match_proposals)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE match_proposals
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_match_proposals_user_id
            ON match_proposals(user_id)
        """)

    # Add to ai_job_queue
    cursor.execute("PRAGMA table_info(ai_job_queue)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE ai_job_queue
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_job_queue_user_id
            ON ai_job_queue(user_id)
        """)

    # Add to interpretation_runs for AI audit trail privacy
    # This is strictly private - even superusers shouldn't see other users' AI data
    cursor.execute("PRAGMA table_info(interpretation_runs)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if "user_id" not in columns:
        cursor.execute("""
            ALTER TABLE interpretation_runs
            ADD COLUMN user_id INTEGER DEFAULT NULL
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_interpretation_runs_user_id
            ON interpretation_runs(user_id)
        """)

    conn.commit()


def downgrade(conn: sqlite3.Connection) -> None:
    """Remove user_id columns (SQLite doesn't support DROP COLUMN easily)."""
    # SQLite doesn't support DROP COLUMN directly before 3.35.0
    # For now, we'll leave the columns in place as they're nullable
    # and won't affect functionality if not used
    raise NotImplementedError(
        "Downgrade not supported for this migration. "
        "The user_id columns will remain but can be ignored."
    )
