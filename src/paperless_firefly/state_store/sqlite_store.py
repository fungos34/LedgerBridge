"""
SQLite-based state store implementation.

Tables:
- paperless_documents: Track processed documents
- extractions: Store extraction JSON and confidence
- imports: Track Firefly imports with external_id
- bank_matches: Optional bank transaction matching
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class ImportStatus(str, Enum):
    """Status of a Firefly import."""

    PENDING = "PENDING"
    IMPORTED = "IMPORTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # e.g., rejected during review
    DUPLICATE = "DUPLICATE"


@dataclass
class DocumentRecord:
    """Record of a processed Paperless document."""

    document_id: int
    source_hash: str
    title: str | None
    document_type: str | None
    correspondent: str | None
    tags: list[str]
    first_seen: str  # ISO timestamp
    last_seen: str  # ISO timestamp

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DocumentRecord":
        """Create from database row."""
        return cls(
            document_id=row["document_id"],
            source_hash=row["source_hash"],
            title=row["title"],
            document_type=row["document_type"],
            correspondent=row["correspondent"],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
        )


@dataclass
class ExtractionRecord:
    """Record of a finance extraction."""

    id: int
    document_id: int
    external_id: str
    extraction_json: str
    overall_confidence: float
    review_state: str
    created_at: str
    reviewed_at: str | None
    review_decision: str | None  # ACCEPTED, REJECTED, EDITED
    llm_opt_out: bool = False  # Per-document LLM opt-out (Spark v1.0)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ExtractionRecord":
        """Create from database row."""
        return cls(
            id=row["id"],
            document_id=row["document_id"],
            external_id=row["external_id"],
            extraction_json=row["extraction_json"],
            overall_confidence=row["overall_confidence"],
            review_state=row["review_state"],
            created_at=row["created_at"],
            reviewed_at=row["reviewed_at"],
            review_decision=row["review_decision"],
            llm_opt_out=bool(row["llm_opt_out"]) if "llm_opt_out" in row.keys() else False,
        )


@dataclass
class ImportRecord:
    """Record of a Firefly III import."""

    id: int
    external_id: str
    document_id: int
    firefly_id: int | None
    status: ImportStatus
    error_message: str | None
    payload_json: str
    created_at: str
    imported_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ImportRecord":
        """Create from database row."""
        return cls(
            id=row["id"],
            external_id=row["external_id"],
            document_id=row["document_id"],
            firefly_id=row["firefly_id"],
            status=ImportStatus(row["status"]),
            error_message=row["error_message"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
            imported_at=row["imported_at"],
        )


class StateStore:
    """
    SQLite-based state store for the pipeline.

    Provides persistent tracking of:
    - Processed documents
    - Generated extractions
    - Firefly imports
    - Firefly cache (for reconciliation)
    - Match proposals
    - Interpretation runs (audit trail)

    Thread-safe for single-writer scenarios.
    """

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path | str, run_migrations: bool = True):
        """
        Initialize state store.

        Args:
            db_path: Path to SQLite database file
            run_migrations: Whether to run pending migrations (default True)
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        if run_migrations:
            self._run_migrations()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._transaction() as conn:
            # Schema version tracking
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """
            )

            # Paperless documents table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paperless_documents (
                    document_id INTEGER PRIMARY KEY,
                    source_hash TEXT NOT NULL,
                    title TEXT,
                    document_type TEXT,
                    correspondent TEXT,
                    tags TEXT,  -- JSON array
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
            """
            )

            # Extractions table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS extractions (
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

            # Imports table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_id TEXT NOT NULL UNIQUE,
                    document_id INTEGER NOT NULL,
                    firefly_id INTEGER,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    imported_at TEXT,
                    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
                )
            """
            )

            # Bank matches table (optional, for future use)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bank_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    bank_reference TEXT NOT NULL,
                    bank_date TEXT NOT NULL,
                    bank_amount TEXT NOT NULL,
                    matched_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
                )
            """
            )

            # Vendor mappings (learning from user edits)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vendor_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vendor_pattern TEXT NOT NULL UNIQUE,
                    destination_account TEXT,
                    category TEXT,
                    tags TEXT,  -- JSON array
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    use_count INTEGER DEFAULT 1
                )
            """
            )

            # Create indexes
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_extractions_document_id ON extractions(document_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_imports_document_id ON imports(document_id)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_status ON imports(status)")

            # Set schema version
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,)
            )

    def _run_migrations(self) -> None:
        """Run pending database migrations."""
        from .migrations import MigrationRunner

        conn = self._get_connection()
        try:
            runner = MigrationRunner(conn)
            runner.run_pending()
        finally:
            conn.close()

    # Document methods

    def upsert_document(
        self,
        document_id: int,
        source_hash: str,
        title: str | None = None,
        document_type: str | None = None,
        correspondent: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Insert or update a document record."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tags_json = json.dumps(tags or [])

        with self._transaction() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT first_seen FROM paperless_documents WHERE document_id = ?", (document_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE paperless_documents
                    SET source_hash = ?, title = ?, document_type = ?, correspondent = ?,
                        tags = ?, last_seen = ?
                    WHERE document_id = ?
                """,
                    (source_hash, title, document_type, correspondent, tags_json, now, document_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO paperless_documents
                    (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        document_id,
                        source_hash,
                        title,
                        document_type,
                        correspondent,
                        tags_json,
                        now,
                        now,
                    ),
                )

    def get_document(self, document_id: int) -> DocumentRecord | None:
        """Get a document record by ID."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM paperless_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            return DocumentRecord.from_row(row) if row else None

    def document_exists(self, document_id: int) -> bool:
        """Check if a document has been processed."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM paperless_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
            return row is not None

    # Extraction methods

    def save_extraction(
        self,
        document_id: int,
        external_id: str,
        extraction_json: str,
        overall_confidence: float,
        review_state: str,
    ) -> int:
        """Save an extraction record. Returns the extraction ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO extractions
                (document_id, external_id, extraction_json, overall_confidence, review_state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (document_id, external_id, extraction_json, overall_confidence, review_state, now),
            )
            return cursor.lastrowid or 0

    def get_extraction_by_document(self, document_id: int) -> ExtractionRecord | None:
        """Get the latest extraction for a document."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM extractions WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            return ExtractionRecord.from_row(row) if row else None

    def get_extraction_by_external_id(self, external_id: str) -> ExtractionRecord | None:
        """Get extraction by external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM extractions WHERE external_id = ?", (external_id,)
            ).fetchone()
            return ExtractionRecord.from_row(row) if row else None

    def update_extraction_review(
        self,
        extraction_id: int,
        decision: str,
        updated_json: str | None = None,
    ) -> None:
        """Update extraction with review decision."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            if updated_json:
                conn.execute(
                    """
                    UPDATE extractions
                    SET reviewed_at = ?, review_decision = ?, extraction_json = ?
                    WHERE id = ?
                """,
                    (now, decision, updated_json, extraction_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE extractions
                    SET reviewed_at = ?, review_decision = ?
                    WHERE id = ?
                """,
                    (now, decision, extraction_id),
                )

    def update_extraction_status(
        self,
        extraction_id: int,
        review_decision: str | None = None,
        review_state: str | None = None,
    ) -> bool:
        """Update extraction status and/or decision.

        Args:
            extraction_id: The extraction ID to update.
            review_decision: New review decision (e.g., LINKED, ORPHAN_CONFIRMED).
            review_state: New review state (e.g., ORPHAN_CONFIRMED).

        Returns:
            True if updated, False if extraction not found.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            updates = ["reviewed_at = ?"]
            params = [now]

            if review_decision is not None:
                updates.append("review_decision = ?")
                params.append(review_decision)

            if review_state is not None:
                updates.append("review_state = ?")
                params.append(review_state)

            params.append(extraction_id)

            cursor = conn.execute(
                f"UPDATE extractions SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    def update_extraction_llm_opt_out(
        self,
        extraction_id: int,
        opt_out: bool,
    ) -> bool:
        """Update LLM opt-out setting for an extraction.

        Per SPARK_EVALUATION_REPORT.md 6.7.2: Per-document opt-out support.

        Args:
            extraction_id: The extraction ID to update.
            opt_out: True to disable LLM for this document, False to enable.

        Returns:
            True if updated, False if extraction not found.
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE extractions
                SET llm_opt_out = ?
                WHERE id = ?
                """,
                (opt_out, extraction_id),
            )
            return cursor.rowcount > 0

    def reset_extraction_for_review(self, extraction_id: int) -> bool:
        """
        Reset an extraction so it can be reviewed again.

        This clears the review_decision, allowing:
        - REJECTED extractions to be reviewed and accepted
        - ACCEPTED extractions to be re-reviewed

        Returns True if reset, False if extraction not found.
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE extractions
                SET reviewed_at = NULL, review_decision = NULL
                WHERE id = ?
            """,
                (extraction_id,),
            )
            return cursor.rowcount > 0

    def reset_extraction_by_document(self, document_id: int) -> bool:
        """
        Reset an extraction by document ID for re-review.

        Returns True if reset, False if no extraction found.
        """
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE extractions
                SET reviewed_at = NULL, review_decision = NULL
                WHERE document_id = ?
            """,
                (document_id,),
            )
            return cursor.rowcount > 0

    def get_extractions_for_review(self) -> list[ExtractionRecord]:
        """Get all extractions pending review."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM extractions
                WHERE review_state IN ('REVIEW', 'MANUAL')
                AND review_decision IS NULL
                ORDER BY created_at ASC
            """
            ).fetchall()
            return [ExtractionRecord.from_row(row) for row in rows]

    # Import methods

    def create_import(
        self,
        external_id: str,
        document_id: int,
        payload_json: str,
        status: ImportStatus = ImportStatus.PENDING,
    ) -> int:
        """Create an import record. Returns the import ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO imports
                (external_id, document_id, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (external_id, document_id, payload_json, status.value, now),
            )
            return cursor.lastrowid or 0

    def update_import_success(self, external_id: str, firefly_id: int) -> None:
        """Mark import as successful with Firefly transaction ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE imports
                SET status = ?, firefly_id = ?, imported_at = ?
                WHERE external_id = ?
            """,
                (ImportStatus.IMPORTED.value, firefly_id, now, external_id),
            )

    def update_import_failed(self, external_id: str, error_message: str) -> None:
        """Mark import as failed with error message."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE imports
                SET status = ?, error_message = ?, imported_at = ?
                WHERE external_id = ?
            """,
                (ImportStatus.FAILED.value, error_message, now, external_id),
            )

    def create_or_update_failed_import(
        self,
        external_id: str,
        document_id: int,
        error_message: str,
        payload_json: str = "{}",
    ) -> None:
        """Create a failed import record or update existing one with error."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            # Try to update first
            cursor = conn.execute(
                """
                UPDATE imports
                SET status = ?, error_message = ?, imported_at = ?
                WHERE external_id = ?
            """,
                (ImportStatus.FAILED.value, error_message, now, external_id),
            )

            if cursor.rowcount == 0:
                # No existing record, create one
                conn.execute(
                    """
                    INSERT INTO imports
                    (external_id, document_id, payload_json, status, error_message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        external_id,
                        document_id,
                        payload_json,
                        ImportStatus.FAILED.value,
                        error_message,
                        now,
                    ),
                )

    def get_import_by_external_id(self, external_id: str) -> ImportRecord | None:
        """Get import record by external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM imports WHERE external_id = ?", (external_id,)
            ).fetchone()
            return ImportRecord.from_row(row) if row else None

    def import_exists(self, external_id: str) -> bool:
        """Check if an import exists for this external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM imports WHERE external_id = ?", (external_id,)
            ).fetchone()
            return row is not None

    def is_imported(self, external_id: str) -> bool:
        """Check if external_id was successfully imported."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM imports WHERE external_id = ? AND status = ?",
                (external_id, ImportStatus.IMPORTED.value),
            ).fetchone()
            return row is not None

    def delete_import(self, external_id: str) -> bool:
        """Delete an import record. Returns True if deleted."""
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM imports WHERE external_id = ?", (external_id,))
            return cursor.rowcount > 0

    def reset_import_for_retry(self, external_id: str) -> int | None:
        """
        Reset an import to PENDING for reimport.

        Keeps the firefly_id so the same Firefly transaction can be updated.
        Returns the firefly_id if exists, None otherwise.
        """
        with self._transaction() as conn:
            # Get the current firefly_id before resetting
            row = conn.execute(
                "SELECT firefly_id FROM imports WHERE external_id = ?",
                (external_id,),
            ).fetchone()

            if not row:
                return None

            firefly_id = row["firefly_id"]

            # Reset status to PENDING (keep firefly_id for update)
            conn.execute(
                """
                UPDATE imports
                SET status = ?, error_message = NULL, imported_at = NULL
                WHERE external_id = ?
            """,
                (ImportStatus.PENDING.value, external_id),
            )

            return firefly_id

    def get_import_by_document(self, document_id: int) -> ImportRecord | None:
        """Get import record by document_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM imports WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            return ImportRecord.from_row(row) if row else None

    def get_pending_imports(self) -> list[ImportRecord]:
        """Get all pending imports."""
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM imports WHERE status = ? ORDER BY created_at ASC",
                (ImportStatus.PENDING.value,),
            ).fetchall()
            return [ImportRecord.from_row(row) for row in rows]

    # Vendor mapping methods

    def save_vendor_mapping(
        self,
        vendor_pattern: str,
        destination_account: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Save or update a vendor mapping."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tags_json = json.dumps(tags or [])

        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT use_count FROM vendor_mappings WHERE vendor_pattern = ?", (vendor_pattern,)
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE vendor_mappings
                    SET destination_account = ?, category = ?, tags = ?,
                        updated_at = ?, use_count = use_count + 1
                    WHERE vendor_pattern = ?
                """,
                    (destination_account, category, tags_json, now, vendor_pattern),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO vendor_mappings
                    (vendor_pattern, destination_account, category, tags, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (vendor_pattern, destination_account, category, tags_json, now, now),
                )

    def get_vendor_mapping(self, vendor_pattern: str) -> dict[str, Any] | None:
        """Get vendor mapping by pattern."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM vendor_mappings WHERE vendor_pattern = ?", (vendor_pattern,)
            ).fetchone()
            if row:
                return {
                    "vendor_pattern": row["vendor_pattern"],
                    "destination_account": row["destination_account"],
                    "category": row["category"],
                    "tags": json.loads(row["tags"]) if row["tags"] else [],
                    "use_count": row["use_count"],
                }
            return None

    def get_processed_extractions(self) -> list[dict[str, Any]]:
        """
        Get all extractions that have been processed (imported, rejected, or pending import).

        Used to show archive/history of processed documents that can be reset.
        """
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT e.*, i.status as import_status, i.firefly_id, i.error_message as import_error
                FROM extractions e
                LEFT JOIN imports i ON e.external_id = i.external_id
                WHERE e.review_decision IS NOT NULL
                   OR i.status IS NOT NULL
                ORDER BY COALESCE(e.reviewed_at, e.created_at) DESC
            """
            ).fetchall()

            results = []
            for row in rows:
                results.append(
                    {
                        "id": row["id"],
                        "document_id": row["document_id"],
                        "external_id": row["external_id"],
                        "extraction_json": row["extraction_json"],
                        "overall_confidence": row["overall_confidence"],
                        "review_state": row["review_state"],
                        "review_decision": row["review_decision"],
                        "reviewed_at": row["reviewed_at"],
                        "created_at": row["created_at"],
                        "import_status": row["import_status"],
                        "firefly_id": row["firefly_id"],
                        "import_error": row["import_error"],
                    }
                )
            return results

    # Statistics

    def get_stats(self) -> dict[str, Any]:
        """Get pipeline statistics."""
        with self._transaction() as conn:
            docs = conn.execute("SELECT COUNT(*) as count FROM paperless_documents").fetchone()
            extractions = conn.execute("SELECT COUNT(*) as count FROM extractions").fetchone()
            pending_review = conn.execute(
                "SELECT COUNT(*) as count FROM extractions WHERE review_state IN ('REVIEW', 'MANUAL') AND review_decision IS NULL"
            ).fetchone()
            imports = conn.execute("SELECT COUNT(*) as count FROM imports").fetchone()
            imported = conn.execute(
                "SELECT COUNT(*) as count FROM imports WHERE status = ?",
                (ImportStatus.IMPORTED.value,),
            ).fetchone()
            failed = conn.execute(
                "SELECT COUNT(*) as count FROM imports WHERE status = ?",
                (ImportStatus.FAILED.value,),
            ).fetchone()

            return {
                "documents_processed": docs["count"] if docs else 0,
                "extractions_total": extractions["count"] if extractions else 0,
                "pending_review": pending_review["count"] if pending_review else 0,
                "imports_total": imports["count"] if imports else 0,
                "imports_success": imported["count"] if imported else 0,
                "imports_failed": failed["count"] if failed else 0,
            }

    # === Firefly Cache Methods ===

    def upsert_firefly_cache(
        self,
        firefly_id: int,
        type_: str,
        date: str,
        amount: str,
        description: str | None = None,
        external_id: str | None = None,
        internal_reference: str | None = None,
        source_account: str | None = None,
        destination_account: str | None = None,
        notes: str | None = None,
        category_name: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """Upsert a Firefly transaction into the cache."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tags_json = json.dumps(tags or [])

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO firefly_cache
                (firefly_id, external_id, internal_reference, type, date, amount, description,
                 source_account, destination_account, notes, category_name, tags, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(firefly_id) DO UPDATE SET
                    external_id = excluded.external_id,
                    internal_reference = excluded.internal_reference,
                    type = excluded.type,
                    date = excluded.date,
                    amount = excluded.amount,
                    description = excluded.description,
                    source_account = excluded.source_account,
                    destination_account = excluded.destination_account,
                    notes = excluded.notes,
                    category_name = excluded.category_name,
                    tags = excluded.tags,
                    synced_at = excluded.synced_at
            """,
                (
                    firefly_id,
                    external_id,
                    internal_reference,
                    type_,
                    date,
                    amount,
                    description,
                    source_account,
                    destination_account,
                    notes,
                    category_name,
                    tags_json,
                    now,
                ),
            )

    def get_unmatched_firefly_transactions(self) -> list[dict[str, Any]]:
        """Get cached Firefly transactions that are not yet matched (excludes soft-deleted)."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM firefly_cache
                WHERE match_status = 'UNMATCHED' AND deleted_at IS NULL
                ORDER BY date DESC
            """
            ).fetchall()
            return [dict(row) for row in rows]

    def update_firefly_match_status(
        self,
        firefly_id: int,
        status: str,
        document_id: int | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update match status for a cached Firefly transaction."""
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE firefly_cache
                SET match_status = ?, matched_document_id = ?, match_confidence = ?
                WHERE firefly_id = ?
            """,
                (status, document_id, confidence, firefly_id),
            )

    def get_firefly_cache_entry(self, firefly_id: int) -> dict[str, Any] | None:
        """Get a single cached Firefly transaction."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM firefly_cache WHERE firefly_id = ?", (firefly_id,)
            ).fetchone()
            return dict(row) if row else None

    def soft_delete_firefly_cache(self, firefly_id: int) -> bool:
        """
        Soft delete a Firefly cache entry by setting deleted_at timestamp.
        
        This preserves audit trail while marking the record as removed from Firefly.
        Returns True if a record was updated, False if not found.
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE firefly_cache
                SET deleted_at = ?
                WHERE firefly_id = ? AND deleted_at IS NULL
            """,
                (now, firefly_id),
            )
            return cursor.rowcount > 0

    def soft_delete_missing_firefly_transactions(self, current_firefly_ids: set[int]) -> int:
        """
        Soft delete cached entries that are no longer in Firefly.
        
        Args:
            current_firefly_ids: Set of firefly_ids that currently exist in Firefly
            
        Returns:
            Count of soft-deleted records
        """
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            # Get all non-deleted cached IDs
            rows = conn.execute(
                "SELECT firefly_id FROM firefly_cache WHERE deleted_at IS NULL"
            ).fetchall()
            cached_ids = {row["firefly_id"] for row in rows}
            
            # Find IDs that were in cache but not in current Firefly
            deleted_ids = cached_ids - current_firefly_ids
            
            if not deleted_ids:
                return 0
            
            # Soft delete them
            placeholders = ",".join("?" * len(deleted_ids))
            cursor = conn.execute(
                f"""
                UPDATE firefly_cache
                SET deleted_at = ?
                WHERE firefly_id IN ({placeholders}) AND deleted_at IS NULL
            """,
                [now, *deleted_ids],
            )
            return cursor.rowcount

    def get_active_firefly_cache_count(self) -> int:
        """Get count of non-deleted cached Firefly transactions."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as count FROM firefly_cache WHERE deleted_at IS NULL"
            ).fetchone()
            return row["count"] if row else 0

    def clear_firefly_cache(self) -> int:
        """Clear all cached Firefly transactions. Returns count of deleted rows."""
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM firefly_cache")
            return cursor.rowcount

    # === Match Proposals Methods ===

    def create_match_proposal(
        self,
        firefly_id: int,
        document_id: int,
        match_score: float,
        match_reasons: list[str] | None = None,
    ) -> int:
        """Create a match proposal. Returns the proposal ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        reasons_json = json.dumps(match_reasons or [])

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO match_proposals
                (firefly_id, document_id, match_score, match_reasons, created_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (firefly_id, document_id, match_score, reasons_json, now),
            )
            return cursor.lastrowid or 0

    def get_pending_proposals(self) -> list[dict[str, Any]]:
        """Get all pending match proposals."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT mp.*, fc.date as tx_date, fc.amount as tx_amount,
                       fc.description as tx_description, fc.source_account, fc.destination_account,
                       pd.title as doc_title, e.overall_confidence
                FROM match_proposals mp
                JOIN firefly_cache fc ON mp.firefly_id = fc.firefly_id
                JOIN paperless_documents pd ON mp.document_id = pd.document_id
                LEFT JOIN extractions e ON mp.document_id = e.document_id
                WHERE mp.status = 'PENDING'
                ORDER BY mp.match_score DESC
            """
            ).fetchall()
            return [dict(row) for row in rows]

    def update_proposal_status(self, proposal_id: int, status: str) -> None:
        """Update match proposal status (ACCEPTED, REJECTED)."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE match_proposals
                SET status = ?, reviewed_at = ?
                WHERE id = ?
            """,
                (status, now, proposal_id),
            )

    def get_proposal_by_id(self, proposal_id: int) -> dict[str, Any] | None:
        """Get a match proposal by ID."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM match_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
            return dict(row) if row else None

    # === Interpretation Runs Methods ===

    def create_interpretation_run(
        self,
        document_id: int | None,
        firefly_id: int | None,
        external_id: str | None,
        pipeline_version: str,
        inputs_summary: dict,
        final_state: str,
        duration_ms: int | None = None,
        algorithm_version: str | None = None,
        rules_applied: list[dict] | None = None,
        llm_result: dict | None = None,
        suggested_category: str | None = None,
        suggested_splits: list[dict] | None = None,
        auto_applied: bool = False,
        decision_source: str | None = None,
        firefly_write_action: str | None = None,
        firefly_target_id: int | None = None,
        linkage_marker_written: dict | None = None,
        taxonomy_version: str | None = None,
    ) -> int:
        """Create an interpretation run record. Returns the run ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO interpretation_runs
                (document_id, firefly_id, external_id, run_timestamp, duration_ms,
                 pipeline_version, algorithm_version, inputs_summary, rules_applied,
                 llm_result, final_state, suggested_category, suggested_splits,
                 auto_applied, decision_source, firefly_write_action, firefly_target_id,
                 linkage_marker_written, taxonomy_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    document_id,
                    firefly_id,
                    external_id,
                    now,
                    duration_ms,
                    pipeline_version,
                    algorithm_version,
                    json.dumps(inputs_summary),
                    json.dumps(rules_applied) if rules_applied else None,
                    json.dumps(llm_result) if llm_result else None,
                    final_state,
                    suggested_category,
                    json.dumps(suggested_splits) if suggested_splits else None,
                    auto_applied,
                    decision_source,
                    firefly_write_action,
                    firefly_target_id,
                    json.dumps(linkage_marker_written) if linkage_marker_written else None,
                    taxonomy_version,
                ),
            )
            return cursor.lastrowid or 0

    def get_interpretation_runs(self, document_id: int) -> list[dict[str, Any]]:
        """Get all interpretation runs for a document."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM interpretation_runs
                WHERE document_id = ?
                ORDER BY run_timestamp DESC
            """,
                (document_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_latest_interpretation_run(self, document_id: int) -> dict[str, Any] | None:
        """Get the most recent interpretation run for a document."""
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM interpretation_runs
                WHERE document_id = ?
                ORDER BY run_timestamp DESC
                LIMIT 1
            """,
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    # === LLM Feedback Methods ===

    def record_llm_feedback(
        self,
        run_id: int,
        suggested_category: str,
        actual_category: str,
        feedback_type: str,
        notes: str | None = None,
    ) -> int:
        """Record feedback on an LLM suggestion. Returns feedback ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO llm_feedback
                (run_id, suggested_category, actual_category, feedback_type, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (run_id, suggested_category, actual_category, feedback_type, notes, now),
            )
            return cursor.lastrowid or 0

    def get_llm_feedback_stats(self) -> dict[str, int]:
        """Get statistics on LLM feedback."""
        with self._transaction() as conn:
            total = conn.execute("SELECT COUNT(*) FROM llm_feedback").fetchone()[0]
            wrong = conn.execute(
                "SELECT COUNT(*) FROM llm_feedback WHERE feedback_type = 'WRONG'"
            ).fetchone()[0]
            correct = conn.execute(
                "SELECT COUNT(*) FROM llm_feedback WHERE feedback_type = 'CORRECT'"
            ).fetchone()[0]

            return {
                "total": total,
                "wrong": wrong,
                "correct": correct,
                "accuracy": (correct / total) if total > 0 else 0.0,
            }

    # === LLM Cache Methods ===

    def get_llm_cache(self, cache_key: str) -> dict[str, Any] | None:
        """Get cached LLM response by key."""
        with self._transaction() as conn:
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            row = conn.execute(
                """
                SELECT * FROM llm_cache
                WHERE cache_key = ? AND expires_at > ?
            """,
                (cache_key, now),
            ).fetchone()

            if row:
                # Update hit count
                conn.execute(
                    "UPDATE llm_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                    (cache_key,),
                )
                return dict(row)
            return None

    def set_llm_cache(
        self,
        cache_key: str,
        model: str,
        prompt_version: str,
        taxonomy_version: str,
        response_json: str,
        ttl_days: int = 30,
    ) -> None:
        """Store LLM response in cache."""
        now = datetime.now(timezone.utc)
        expires = now + __import__("datetime").timedelta(days=ttl_days)

        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO llm_cache
                (cache_key, model, prompt_version, taxonomy_version, response_json, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    hit_count = 1
            """,
                (
                    cache_key,
                    model,
                    prompt_version,
                    taxonomy_version,
                    response_json,
                    now.isoformat().replace("+00:00", "Z"),
                    expires.isoformat().replace("+00:00", "Z"),
                ),
            )

    def clear_expired_llm_cache(self) -> int:
        """Clear expired LLM cache entries. Returns count of deleted rows."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._transaction() as conn:
            cursor = conn.execute("DELETE FROM llm_cache WHERE expires_at < ?", (now,))
            return cursor.rowcount

    def get_llm_suggestion_count(self) -> int:
        """Get total number of LLM suggestions made (for calibration)."""
        with self._transaction() as conn:
            result = conn.execute(
                """
                SELECT COUNT(*) FROM interpretation_runs
                WHERE llm_result IS NOT NULL
            """
            ).fetchone()
            return result[0] if result else 0
