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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, Iterator, Any


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
    title: Optional[str]
    document_type: Optional[str]
    correspondent: Optional[str]
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
    reviewed_at: Optional[str]
    review_decision: Optional[str]  # ACCEPTED, REJECTED, EDITED
    
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
        )


@dataclass
class ImportRecord:
    """Record of a Firefly III import."""
    id: int
    external_id: str
    document_id: int
    firefly_id: Optional[int]
    status: ImportStatus
    error_message: Optional[str]
    payload_json: str
    created_at: str
    imported_at: Optional[str]
    
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
    
    Thread-safe for single-writer scenarios.
    """
    
    SCHEMA_VERSION = 1
    
    def __init__(self, db_path: Path | str):
        """
        Initialize state store.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)
            
            # Paperless documents table
            conn.execute("""
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
            """)
            
            # Extractions table
            conn.execute("""
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
            """)
            
            # Imports table
            conn.execute("""
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
            """)
            
            # Bank matches table (optional, for future use)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bank_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER,
                    bank_reference TEXT NOT NULL,
                    bank_date TEXT NOT NULL,
                    bank_amount TEXT NOT NULL,
                    matched_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
                )
            """)
            
            # Vendor mappings (learning from user edits)
            conn.execute("""
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
            """)
            
            # Create indexes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_extractions_document_id ON extractions(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_document_id ON imports(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_status ON imports(status)")
            
            # Set schema version
            conn.execute("INSERT OR REPLACE INTO schema_version (version) VALUES (?)", (self.SCHEMA_VERSION,))
    
    # Document methods
    
    def upsert_document(
        self,
        document_id: int,
        source_hash: str,
        title: Optional[str] = None,
        document_type: Optional[str] = None,
        correspondent: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        """Insert or update a document record."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tags_json = json.dumps(tags or [])
        
        with self._transaction() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT first_seen FROM paperless_documents WHERE document_id = ?",
                (document_id,)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE paperless_documents
                    SET source_hash = ?, title = ?, document_type = ?, correspondent = ?,
                        tags = ?, last_seen = ?
                    WHERE document_id = ?
                """, (source_hash, title, document_type, correspondent, tags_json, now, document_id))
            else:
                conn.execute("""
                    INSERT INTO paperless_documents
                    (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (document_id, source_hash, title, document_type, correspondent, tags_json, now, now))
    
    def get_document(self, document_id: int) -> Optional[DocumentRecord]:
        """Get a document record by ID."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM paperless_documents WHERE document_id = ?",
                (document_id,)
            ).fetchone()
            return DocumentRecord.from_row(row) if row else None
    
    def document_exists(self, document_id: int) -> bool:
        """Check if a document has been processed."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM paperless_documents WHERE document_id = ?",
                (document_id,)
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
            cursor = conn.execute("""
                INSERT INTO extractions
                (document_id, external_id, extraction_json, overall_confidence, review_state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (document_id, external_id, extraction_json, overall_confidence, review_state, now))
            return cursor.lastrowid or 0
    
    def get_extraction_by_document(self, document_id: int) -> Optional[ExtractionRecord]:
        """Get the latest extraction for a document."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM extractions WHERE document_id = ? ORDER BY created_at DESC LIMIT 1",
                (document_id,)
            ).fetchone()
            return ExtractionRecord.from_row(row) if row else None
    
    def get_extraction_by_external_id(self, external_id: str) -> Optional[ExtractionRecord]:
        """Get extraction by external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM extractions WHERE external_id = ?",
                (external_id,)
            ).fetchone()
            return ExtractionRecord.from_row(row) if row else None
    
    def update_extraction_review(
        self,
        extraction_id: int,
        decision: str,
        updated_json: Optional[str] = None,
    ) -> None:
        """Update extraction with review decision."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        with self._transaction() as conn:
            if updated_json:
                conn.execute("""
                    UPDATE extractions
                    SET reviewed_at = ?, review_decision = ?, extraction_json = ?
                    WHERE id = ?
                """, (now, decision, updated_json, extraction_id))
            else:
                conn.execute("""
                    UPDATE extractions
                    SET reviewed_at = ?, review_decision = ?
                    WHERE id = ?
                """, (now, decision, extraction_id))
    
    def get_extractions_for_review(self) -> list[ExtractionRecord]:
        """Get all extractions pending review."""
        with self._transaction() as conn:
            rows = conn.execute("""
                SELECT * FROM extractions
                WHERE review_state IN ('REVIEW', 'MANUAL')
                AND review_decision IS NULL
                ORDER BY created_at ASC
            """).fetchall()
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
            cursor = conn.execute("""
                INSERT INTO imports
                (external_id, document_id, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (external_id, document_id, payload_json, status.value, now))
            return cursor.lastrowid or 0
    
    def update_import_success(self, external_id: str, firefly_id: int) -> None:
        """Mark import as successful with Firefly transaction ID."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        with self._transaction() as conn:
            conn.execute("""
                UPDATE imports
                SET status = ?, firefly_id = ?, imported_at = ?
                WHERE external_id = ?
            """, (ImportStatus.IMPORTED.value, firefly_id, now, external_id))
    
    def update_import_failed(self, external_id: str, error_message: str) -> None:
        """Mark import as failed with error message."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        with self._transaction() as conn:
            conn.execute("""
                UPDATE imports
                SET status = ?, error_message = ?, imported_at = ?
                WHERE external_id = ?
            """, (ImportStatus.FAILED.value, error_message, now, external_id))
    
    def get_import_by_external_id(self, external_id: str) -> Optional[ImportRecord]:
        """Get import record by external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM imports WHERE external_id = ?",
                (external_id,)
            ).fetchone()
            return ImportRecord.from_row(row) if row else None
    
    def import_exists(self, external_id: str) -> bool:
        """Check if an import exists for this external_id."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM imports WHERE external_id = ?",
                (external_id,)
            ).fetchone()
            return row is not None
    
    def is_imported(self, external_id: str) -> bool:
        """Check if external_id was successfully imported."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM imports WHERE external_id = ? AND status = ?",
                (external_id, ImportStatus.IMPORTED.value)
            ).fetchone()
            return row is not None
    
    def delete_import(self, external_id: str) -> bool:
        """Delete an import record. Returns True if deleted."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM imports WHERE external_id = ?",
                (external_id,)
            )
            return cursor.rowcount > 0
    
    def get_pending_imports(self) -> list[ImportRecord]:
        """Get all pending imports."""
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM imports WHERE status = ? ORDER BY created_at ASC",
                (ImportStatus.PENDING.value,)
            ).fetchall()
            return [ImportRecord.from_row(row) for row in rows]
    
    # Vendor mapping methods
    
    def save_vendor_mapping(
        self,
        vendor_pattern: str,
        destination_account: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> None:
        """Save or update a vendor mapping."""
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        tags_json = json.dumps(tags or [])
        
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT use_count FROM vendor_mappings WHERE vendor_pattern = ?",
                (vendor_pattern,)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE vendor_mappings
                    SET destination_account = ?, category = ?, tags = ?,
                        updated_at = ?, use_count = use_count + 1
                    WHERE vendor_pattern = ?
                """, (destination_account, category, tags_json, now, vendor_pattern))
            else:
                conn.execute("""
                    INSERT INTO vendor_mappings
                    (vendor_pattern, destination_account, category, tags, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (vendor_pattern, destination_account, category, tags_json, now, now))
    
    def get_vendor_mapping(self, vendor_pattern: str) -> Optional[dict[str, Any]]:
        """Get vendor mapping by pattern."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM vendor_mappings WHERE vendor_pattern = ?",
                (vendor_pattern,)
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
                (ImportStatus.IMPORTED.value,)
            ).fetchone()
            failed = conn.execute(
                "SELECT COUNT(*) as count FROM imports WHERE status = ?",
                (ImportStatus.FAILED.value,)
            ).fetchone()
            
            return {
                "documents_processed": docs["count"] if docs else 0,
                "extractions_total": extractions["count"] if extractions else 0,
                "pending_review": pending_review["count"] if pending_review else 0,
                "imports_total": imports["count"] if imports else 0,
                "imports_success": imported["count"] if imported else 0,
                "imports_failed": failed["count"] if failed else 0,
            }
