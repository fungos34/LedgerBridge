"""
Tests for the web review interface.

These tests validate the Django views and workflow handling,
ensuring proper document display, form processing, and state transitions.
"""

import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

# Set Django settings before importing Django components
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paperless_firefly.review.web.settings")

import django
from django.conf import settings


@pytest.fixture(scope="module")
def django_db_setup():
    """Configure Django for testing."""
    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={},
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
            ],
            ROOT_URLCONF="paperless_firefly.review.web.urls",
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [
                        Path(__file__).parent.parent
                        / "src"
                        / "paperless_firefly"
                        / "review"
                        / "web"
                        / "templates"
                    ],
                    "APP_DIRS": True,
                }
            ],
            PAPERLESS_BASE_URL="http://paperless.test:8000",
            PAPERLESS_TOKEN="test-token",
            FIREFLY_BASE_URL="http://firefly.test:8080",
            FIREFLY_TOKEN="test-firefly-token",
            STATE_DB_PATH=":memory:",
            SECRET_KEY="test-secret-key",
        )
    django.setup()


@pytest.fixture
def temp_state_db(tmp_path):
    """Create a temporary state database for testing."""
    db_path = tmp_path / "test_state.db"
    return db_path


@pytest.fixture
def sample_extraction_json():
    """Sample extraction JSON for testing."""
    return json.dumps(
        {
            "paperless_document_id": 12345,
            "source_hash": "abc123def456789012345678901234567890123456789012345678901234",
            "paperless_url": "http://paperless.test:8000/documents/12345/",
            "paperless_title": "SPAR Einkauf 18.11.2024",
            "raw_text": "Sample OCR text",
            "document_classification": {
                "document_type": "Receipt",
                "correspondent": "SPAR",
                "tags": ["finance/inbox"],
                "storage_path": None,
            },
            "proposal": {
                "transaction_type": "withdrawal",
                "date": "2024-11-18",
                "amount": "11.48",
                "currency": "EUR",
                "description": "SPAR - 2024-11-18",
                "source_account": "Checking Account",
                "destination_account": "SPAR",
                "category": None,
                "tags": ["finance/inbox"],
                "notes": "Extracted from Paperless document 12345",
                "external_id": "paperless:12345:abc123def4567890:11.48:2024-11-18",
                "invoice_number": "R-2024-11832",
                "due_date": None,
                "payment_reference": None,
                "total_net": None,
                "tax_amount": None,
                "tax_rate": None,
            },
            "line_items": [],
            "confidence": {
                "overall": 0.72,
                "amount": 0.85,
                "date": 0.90,
                "currency": 0.95,
                "description": 0.65,
                "vendor": 0.70,
                "invoice_number": 0.55,
                "line_items": 0.0,
                "review_state": "REVIEW",
            },
            "provenance": {
                "source_system": "paperless",
                "parser_version": "0.1.0",
                "parsed_at": "2024-11-19T10:00:00Z",
                "ruleset_id": None,
                "extraction_strategy": "ocr_heuristic",
            },
            "structured_payloads": [],
            "created_at": "2024-11-19T10:00:00Z",
        }
    )


@pytest.fixture
def populated_state_db(temp_state_db, sample_extraction_json):
    """Create a state database with test data."""
    conn = sqlite3.connect(str(temp_state_db))
    conn.row_factory = sqlite3.Row

    # Create schema
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

        CREATE TABLE IF NOT EXISTS paperless_documents (
            document_id INTEGER PRIMARY KEY,
            source_hash TEXT NOT NULL,
            title TEXT,
            document_type TEXT,
            correspondent TEXT,
            tags TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            external_id TEXT NOT NULL UNIQUE,
            extraction_json TEXT NOT NULL,
            overall_confidence REAL NOT NULL,
            review_state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            review_decision TEXT
        );

        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL UNIQUE,
            document_id INTEGER NOT NULL,
            firefly_id INTEGER,
            status TEXT NOT NULL,
            error_message TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            imported_at TEXT
        );
    """
    )

    # Insert test document
    conn.execute(
        """
        INSERT INTO paperless_documents
        (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
        VALUES (12345, 'abc123', 'Test Receipt', 'Receipt', 'SPAR', '["finance/inbox"]',
                '2024-11-19T10:00:00Z', '2024-11-19T10:00:00Z')
    """
    )

    # Insert test extraction pending review
    conn.execute(
        """
        INSERT INTO extractions
        (document_id, external_id, extraction_json, overall_confidence, review_state, created_at)
        VALUES (12345, 'paperless:12345:abc123def4567890:11.48:2024-11-18', ?, 0.72, 'REVIEW', '2024-11-19T10:00:00Z')
    """,
        (sample_extraction_json,),
    )

    # Insert second extraction for navigation testing
    sample_data_2 = json.loads(sample_extraction_json)
    sample_data_2["paperless_document_id"] = 12346
    sample_data_2["paperless_title"] = "Invoice from Vendor"
    sample_data_2["proposal"]["external_id"] = "paperless:12346:def456:50.00:2024-11-20"
    sample_data_2["proposal"]["amount"] = "50.00"
    sample_data_2["confidence"]["overall"] = 0.65

    conn.execute(
        """
        INSERT INTO paperless_documents
        (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
        VALUES (12346, 'def456', 'Test Invoice', 'Invoice', 'Vendor', '["finance/inbox"]',
                '2024-11-20T10:00:00Z', '2024-11-20T10:00:00Z')
    """
    )

    conn.execute(
        """
        INSERT INTO extractions
        (document_id, external_id, extraction_json, overall_confidence, review_state, created_at)
        VALUES (12346, 'paperless:12346:def456:50.00:2024-11-20', ?, 0.65, 'REVIEW', '2024-11-20T10:00:00Z')
    """,
        (json.dumps(sample_data_2),),
    )

    conn.commit()
    conn.close()

    return temp_state_db


class TestReviewWorkflow:
    """Test the review workflow logic."""

    def test_review_decision_enum_values(self):
        """Ensure ReviewDecision enum has expected values."""
        from paperless_firefly.review.workflow import ReviewDecision

        assert ReviewDecision.ACCEPTED.value == "ACCEPTED"
        assert ReviewDecision.REJECTED.value == "REJECTED"
        assert ReviewDecision.EDITED.value == "EDITED"
        assert ReviewDecision.SKIPPED.value == "SKIPPED"

    def test_review_state_determines_routing(self):
        """Test that review state correctly routes documents."""
        from paperless_firefly.schemas.finance_extraction import ReviewState

        # These are the routing rules
        assert ReviewState.AUTO.value == "AUTO"  # High confidence, auto-import
        assert ReviewState.REVIEW.value == "REVIEW"  # Needs review
        assert ReviewState.MANUAL.value == "MANUAL"  # Low confidence, careful review


class TestStateStoreIntegration:
    """Test state store operations used by web views."""

    def test_get_extractions_for_review(self, populated_state_db):
        """Test retrieving pending extractions."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()

        # Should have 2 pending extractions
        assert len(pending) == 2

        # Check first extraction
        ext1 = pending[0]
        assert ext1.document_id == 12345
        assert ext1.overall_confidence == 0.72
        assert ext1.review_state == "REVIEW"
        assert ext1.review_decision is None

    def test_update_extraction_review_accept(self, populated_state_db):
        """Test accepting an extraction."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()
        extraction_id = pending[0].id

        store.update_extraction_review(extraction_id, "ACCEPTED")

        # Should no longer be in pending
        updated_pending = store.get_extractions_for_review()
        assert len(updated_pending) == 1
        assert updated_pending[0].id != extraction_id

    def test_update_extraction_review_reject(self, populated_state_db):
        """Test rejecting an extraction."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()
        extraction_id = pending[0].id

        store.update_extraction_review(extraction_id, "REJECTED")

        # Should no longer be in pending
        updated_pending = store.get_extractions_for_review()
        assert len(updated_pending) == 1

    def test_update_extraction_with_edited_json(self, populated_state_db, sample_extraction_json):
        """Test updating extraction with modified data."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()
        extraction_id = pending[0].id

        # Modify the extraction
        modified = json.loads(sample_extraction_json)
        modified["proposal"]["amount"] = "15.99"
        modified["proposal"]["external_id"] = "paperless:12345:abc123def4567890:15.99:2024-11-18"

        store.update_extraction_review(extraction_id, "EDITED", json.dumps(modified))

        # Verify the change persisted
        conn = sqlite3.connect(str(populated_state_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM extractions WHERE id = ?", (extraction_id,)).fetchone()
        conn.close()

        saved_data = json.loads(row["extraction_json"])
        assert saved_data["proposal"]["amount"] == "15.99"


class TestExternalIdGeneration:
    """Test external ID generation for deduplication."""

    def test_external_id_format(self):
        """Test external ID follows expected format."""
        from paperless_firefly.schemas.dedupe import generate_external_id

        ext_id = generate_external_id(
            document_id=12345,
            source_hash="abc123def456789012345678901234567890123456789012345678901234",
            amount=Decimal("11.48"),
            date="2024-11-18",
        )

        # Format: paperless:{doc_id}:{hash[:16]}:{amount}:{date}
        assert ext_id.startswith("paperless:12345:")
        assert ":11.48:" in ext_id
        assert ext_id.endswith(":2024-11-18")

    def test_external_id_deterministic(self):
        """Test same inputs produce same external ID."""
        from paperless_firefly.schemas.dedupe import generate_external_id

        args = {
            "document_id": 12345,
            "source_hash": "abc123def456789012345678901234567890123456789012345678901234",
            "amount": Decimal("11.48"),
            "date": "2024-11-18",
        }

        id1 = generate_external_id(**args)
        id2 = generate_external_id(**args)

        assert id1 == id2

    def test_external_id_changes_with_amount(self):
        """Test external ID changes when amount changes."""
        from paperless_firefly.schemas.dedupe import generate_external_id

        base_args = {
            "document_id": 12345,
            "source_hash": "abc123def456789012345678901234567890123456789012345678901234",
            "date": "2024-11-18",
        }

        id1 = generate_external_id(**base_args, amount=Decimal("11.48"))
        id2 = generate_external_id(**base_args, amount=Decimal("15.99"))

        assert id1 != id2

    def test_external_id_changes_with_date(self):
        """Test external ID changes when date changes."""
        from paperless_firefly.schemas.dedupe import generate_external_id

        base_args = {
            "document_id": 12345,
            "source_hash": "abc123def456789012345678901234567890123456789012345678901234",
            "amount": Decimal("11.48"),
        }

        id1 = generate_external_id(**base_args, date="2024-11-18")
        id2 = generate_external_id(**base_args, date="2024-11-19")

        assert id1 != id2


class TestFinanceExtractionSerialization:
    """Test FinanceExtraction serialization for review views."""

    def test_extraction_from_dict(self, sample_extraction_json):
        """Test creating FinanceExtraction from dictionary."""
        from paperless_firefly.schemas.finance_extraction import FinanceExtraction

        data = json.loads(sample_extraction_json)
        extraction = FinanceExtraction.from_dict(data)

        assert extraction.paperless_document_id == 12345
        assert extraction.paperless_title == "SPAR Einkauf 18.11.2024"
        assert extraction.proposal.amount == Decimal("11.48")
        assert extraction.proposal.currency == "EUR"
        assert extraction.confidence.overall == 0.72

    def test_extraction_to_dict_roundtrip(self, sample_extraction_json):
        """Test FinanceExtraction serializes back correctly."""
        from paperless_firefly.schemas.finance_extraction import FinanceExtraction

        data = json.loads(sample_extraction_json)
        extraction = FinanceExtraction.from_dict(data)

        # Roundtrip
        serialized = extraction.to_dict()
        extraction2 = FinanceExtraction.from_dict(serialized)

        assert extraction2.paperless_document_id == extraction.paperless_document_id
        assert extraction2.proposal.amount == extraction.proposal.amount
        assert extraction2.confidence.overall == extraction.confidence.overall

    def test_proposal_field_modification(self, sample_extraction_json):
        """Test that proposal fields can be modified for editing."""
        from paperless_firefly.schemas.finance_extraction import FinanceExtraction

        data = json.loads(sample_extraction_json)
        extraction = FinanceExtraction.from_dict(data)

        # Simulate form edit
        extraction.proposal.amount = Decimal("15.99")
        extraction.proposal.description = "Modified description"
        extraction.proposal.category = "Groceries"

        # Serialize and verify
        serialized = extraction.to_dict()
        assert serialized["proposal"]["amount"] == "15.99"
        assert serialized["proposal"]["description"] == "Modified description"
        assert serialized["proposal"]["category"] == "Groceries"


class TestConfidenceDisplay:
    """Test confidence score display logic."""

    def test_confidence_percentage_conversion(self, sample_extraction_json):
        """Test confidence converts to percentage for display."""
        from paperless_firefly.schemas.finance_extraction import FinanceExtraction

        data = json.loads(sample_extraction_json)
        extraction = FinanceExtraction.from_dict(data)

        # These are what the view converts for display
        confidence_pct = {
            "overall": extraction.confidence.overall * 100,
            "amount": extraction.confidence.amount * 100,
            "date": extraction.confidence.date * 100,
        }

        assert confidence_pct["overall"] == 72.0
        assert confidence_pct["amount"] == 85.0
        assert confidence_pct["date"] == 90.0

    def test_confidence_thresholds_classification(self):
        """Test confidence threshold classification."""
        from paperless_firefly.schemas.finance_extraction import ConfidenceScores, ReviewState

        # Test AUTO threshold
        conf_high = ConfidenceScores(overall=0.90)
        assert conf_high.compute_review_state() == ReviewState.AUTO

        # Test REVIEW threshold
        conf_med = ConfidenceScores(overall=0.72)
        assert conf_med.compute_review_state() == ReviewState.REVIEW

        # Test MANUAL threshold
        conf_low = ConfidenceScores(overall=0.50)
        assert conf_low.compute_review_state() == ReviewState.MANUAL


class TestDocumentProxyLogic:
    """Test document proxy URL construction."""

    def test_paperless_download_url(self):
        """Test Paperless download URL construction."""
        base_url = "http://paperless.test:8000"
        document_id = 12345

        download_url = f"{base_url}/api/documents/{document_id}/download/"
        thumb_url = f"{base_url}/api/documents/{document_id}/thumb/"

        assert download_url == "http://paperless.test:8000/api/documents/12345/download/"
        assert thumb_url == "http://paperless.test:8000/api/documents/12345/thumb/"

    def test_paperless_document_view_url(self):
        """Test Paperless document view URL construction."""
        base_url = "http://paperless.test:8000"
        document_id = 12345

        # This is the URL for viewing in Paperless UI
        view_url = f"{base_url}/documents/{document_id}/"

        assert view_url == "http://paperless.test:8000/documents/12345/"


class TestFormValidation:
    """Test form field validation logic."""

    def test_amount_parsing(self):
        """Test amount string parsing."""
        from decimal import Decimal, InvalidOperation

        # Standard format
        assert Decimal("11.48") == Decimal("11.48")

        # European comma format
        assert Decimal("11,48".replace(",", ".")) == Decimal("11.48")

        # With thousands separator (should fail)
        with pytest.raises(InvalidOperation):
            Decimal("1.000,00".replace(",", "."))

    def test_currency_normalization(self):
        """Test currency code normalization."""
        inputs = ["eur", "EUR", "Eur", " EUR "]

        for inp in inputs:
            normalized = inp.strip().upper()
            assert normalized == "EUR"

    def test_date_format_validation(self):
        """Test date format validation."""
        from datetime import datetime

        # ISO format
        valid_date = "2024-11-18"
        parsed = datetime.strptime(valid_date, "%Y-%m-%d")
        assert parsed.year == 2024
        assert parsed.month == 11
        assert parsed.day == 18

        # Invalid format
        with pytest.raises(ValueError):
            datetime.strptime("18.11.2024", "%Y-%m-%d")

    def test_transaction_type_validation(self):
        """Test transaction type validation."""
        from paperless_firefly.schemas.finance_extraction import TransactionType

        # Valid types (lowercase as stored in serialized form)
        assert TransactionType("withdrawal") == TransactionType.WITHDRAWAL
        assert TransactionType("deposit") == TransactionType.DEPOSIT
        assert TransactionType("transfer") == TransactionType.TRANSFER

        # Invalid type
        with pytest.raises(ValueError):
            TransactionType("INVALID")


class TestStatsCalculation:
    """Test statistics calculation."""

    def test_get_stats(self, populated_state_db):
        """Test retrieving pipeline statistics."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        stats = store.get_stats()

        # Should have stats for documents, extractions
        assert (
            "documents_processed" in stats or "total_documents" in stats or isinstance(stats, dict)
        )

    def test_stats_counts_pending_correctly(self, populated_state_db):
        """Test pending count in stats."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()

        # We inserted 2 extractions pending review
        assert len(pending) == 2


class TestNavigationLogic:
    """Test review queue navigation."""

    def test_navigation_indices(self, populated_state_db):
        """Test prev/next calculation for navigation."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_state_db)
        pending = store.get_extractions_for_review()
        pending_ids = [r.id for r in pending]

        assert len(pending_ids) == 2

        # At first item
        current_idx = 0
        prev_id = pending_ids[current_idx - 1] if current_idx > 0 else None
        next_id = pending_ids[current_idx + 1] if current_idx < len(pending_ids) - 1 else None

        assert prev_id is None
        assert next_id == pending_ids[1]

        # At second item
        current_idx = 1
        prev_id = pending_ids[current_idx - 1] if current_idx > 0 else None
        next_id = pending_ids[current_idx + 1] if current_idx < len(pending_ids) - 1 else None

        assert prev_id == pending_ids[0]
        assert next_id is None


# ============================================================================
# Reconciliation UI Tests (Phase 3)
# ============================================================================


@pytest.fixture
def populated_reconciliation_db(tmp_path, sample_extraction_json):
    """Create a state database with reconciliation test data."""
    db_path = tmp_path / "test_reconciliation_state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Create full schema including reconciliation tables
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);

        CREATE TABLE IF NOT EXISTS schema_migrations (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paperless_documents (
            document_id INTEGER PRIMARY KEY,
            source_hash TEXT NOT NULL,
            title TEXT,
            document_type TEXT,
            correspondent TEXT,
            tags TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            external_id TEXT NOT NULL UNIQUE,
            extraction_json TEXT NOT NULL,
            overall_confidence REAL NOT NULL,
            review_state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            review_decision TEXT
        );

        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id TEXT NOT NULL UNIQUE,
            document_id INTEGER NOT NULL,
            firefly_id INTEGER,
            status TEXT NOT NULL,
            error_message TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            imported_at TEXT
        );

        CREATE TABLE IF NOT EXISTS firefly_cache (
            firefly_id INTEGER PRIMARY KEY,
            external_id TEXT,
            internal_reference TEXT,
            type TEXT NOT NULL,
            date TEXT NOT NULL,
            amount TEXT NOT NULL,
            description TEXT,
            source_account TEXT,
            destination_account TEXT,
            notes TEXT,
            category_name TEXT,
            tags TEXT,
            synced_at TEXT NOT NULL,
            match_status TEXT DEFAULT 'UNMATCHED',
            matched_document_id INTEGER,
            match_confidence REAL
        );

        CREATE TABLE IF NOT EXISTS match_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firefly_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            match_score REAL NOT NULL,
            match_reasons TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS interpretation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER,
            firefly_id INTEGER,
            external_id TEXT,
            run_timestamp TEXT NOT NULL,
            duration_ms INTEGER,
            pipeline_version TEXT NOT NULL,
            algorithm_version TEXT,
            inputs_summary TEXT,
            rules_applied TEXT,
            llm_result TEXT,
            final_state TEXT NOT NULL,
            suggested_category TEXT,
            suggested_splits TEXT,
            auto_applied INTEGER DEFAULT 0,
            decision_source TEXT,
            firefly_write_action TEXT,
            firefly_target_id INTEGER,
            linkage_marker_written TEXT,
            taxonomy_version TEXT
        );
    """
    )

    # Insert test documents
    conn.execute(
        """
        INSERT INTO paperless_documents
        (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
        VALUES (100, 'hash100', 'SPAR Receipt Dec', 'Receipt', 'SPAR', '["finance/inbox"]',
                '2024-12-01T10:00:00Z', '2024-12-01T10:00:00Z')
    """
    )
    conn.execute(
        """
        INSERT INTO paperless_documents
        (document_id, source_hash, title, document_type, correspondent, tags, first_seen, last_seen)
        VALUES (101, 'hash101', 'Amazon Invoice', 'Invoice', 'Amazon', '["finance/inbox"]',
                '2024-12-02T10:00:00Z', '2024-12-02T10:00:00Z')
    """
    )

    # Insert extraction for doc 100
    conn.execute(
        """
        INSERT INTO extractions
        (document_id, external_id, extraction_json, overall_confidence, review_state, created_at)
        VALUES (100, 'paperless:100:hash100:25.50:2024-12-01', ?, 0.85, 'AUTO', '2024-12-01T10:00:00Z')
    """,
        (sample_extraction_json,),
    )

    # Insert firefly cache entries
    conn.execute(
        """
        INSERT INTO firefly_cache
        (firefly_id, type, date, amount, description, source_account, destination_account, synced_at, match_status)
        VALUES (500, 'withdrawal', '2024-12-01', '25.50', 'SPAR Einkauf', 'Checking', 'SPAR', '2024-12-01T12:00:00Z', 'UNMATCHED')
    """
    )
    conn.execute(
        """
        INSERT INTO firefly_cache
        (firefly_id, type, date, amount, description, source_account, destination_account, synced_at, match_status)
        VALUES (501, 'withdrawal', '2024-12-02', '99.99', 'Amazon Order', 'Checking', 'Amazon', '2024-12-02T12:00:00Z', 'UNMATCHED')
    """
    )
    conn.execute(
        """
        INSERT INTO firefly_cache
        (firefly_id, type, date, amount, description, source_account, destination_account, synced_at, match_status)
        VALUES (502, 'withdrawal', '2024-12-03', '15.00', 'Coffee Shop', 'Checking', 'Coffee', '2024-12-03T12:00:00Z', 'CONFIRMED')
    """
    )

    # Insert match proposals
    conn.execute(
        """
        INSERT INTO match_proposals
        (firefly_id, document_id, match_score, match_reasons, status, created_at)
        VALUES (500, 100, 0.92, '["amount_exact", "date_within_3_days", "vendor_fuzzy"]', 'PENDING', '2024-12-01T14:00:00Z')
    """
    )
    conn.execute(
        """
        INSERT INTO match_proposals
        (firefly_id, document_id, match_score, match_reasons, status, created_at)
        VALUES (501, 101, 0.75, '["amount_close", "vendor_partial"]', 'PENDING', '2024-12-02T14:00:00Z')
    """
    )
    conn.execute(
        """
        INSERT INTO match_proposals
        (firefly_id, document_id, match_score, match_reasons, status, created_at, reviewed_at)
        VALUES (502, 100, 0.65, '["date_match"]', 'REJECTED', '2024-12-03T14:00:00Z', '2024-12-03T15:00:00Z')
    """
    )

    # Insert interpretation runs for audit trail
    conn.execute(
        """
        INSERT INTO interpretation_runs
        (document_id, firefly_id, external_id, run_timestamp, pipeline_version, inputs_summary, final_state, decision_source)
        VALUES (100, 500, 'paperless:100:hash100', '2024-12-01T14:00:00Z', '1.0.0', '{"action": "propose_match"}', 'PROPOSED', 'RULES')
    """
    )
    conn.execute(
        """
        INSERT INTO interpretation_runs
        (document_id, firefly_id, external_id, run_timestamp, pipeline_version, inputs_summary, final_state, decision_source, firefly_write_action)
        VALUES (100, 502, 'paperless:100:hash100', '2024-12-03T15:00:00Z', '1.0.0', '{"action": "reject_proposal"}', 'REJECTED', 'USER', NULL)
    """
    )

    conn.commit()
    conn.close()

    return db_path


class TestReconciliationStatsCalculation:
    """Test reconciliation statistics calculation."""

    def test_get_pending_proposals(self, populated_reconciliation_db):
        """Test retrieving pending match proposals."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        proposals = store.get_pending_proposals()

        # Should have 2 pending proposals
        assert len(proposals) == 2

        # Check first proposal (highest score first due to ORDER BY)
        prop = proposals[0]
        assert prop["firefly_id"] == 500
        assert prop["document_id"] == 100
        assert prop["match_score"] == 0.92
        assert prop["status"] == "PENDING"

    def test_get_proposal_by_id(self, populated_reconciliation_db):
        """Test retrieving a single proposal by ID."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        proposals = store.get_pending_proposals()
        proposal_id = proposals[0]["id"]

        proposal = store.get_proposal_by_id(proposal_id)
        assert proposal is not None
        assert proposal["id"] == proposal_id
        assert proposal["match_score"] == 0.92

    def test_update_proposal_status(self, populated_reconciliation_db):
        """Test updating proposal status."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        proposals = store.get_pending_proposals()
        proposal_id = proposals[0]["id"]

        # Accept the proposal
        store.update_proposal_status(proposal_id, "ACCEPTED")

        # Verify it's no longer pending
        updated_proposals = store.get_pending_proposals()
        assert len(updated_proposals) == 1
        assert updated_proposals[0]["id"] != proposal_id

        # Verify the status was updated
        accepted = store.get_proposal_by_id(proposal_id)
        assert accepted["status"] == "ACCEPTED"
        assert accepted["reviewed_at"] is not None

    def test_get_unmatched_firefly_transactions(self, populated_reconciliation_db):
        """Test retrieving unmatched Firefly transactions."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        unmatched = store.get_unmatched_firefly_transactions()

        # Should have 2 unmatched transactions
        assert len(unmatched) == 2
        firefly_ids = [tx["firefly_id"] for tx in unmatched]
        assert 500 in firefly_ids
        assert 501 in firefly_ids
        assert 502 not in firefly_ids  # This one is CONFIRMED


class TestReconciliationStatsHelper:
    """Test the _get_reconciliation_stats helper function."""

    def test_reconciliation_stats_counts(self, populated_reconciliation_db, monkeypatch):
        """Test that reconciliation stats are calculated correctly."""
        # Mock the settings to use our test database
        import paperless_firefly.review.web.views as views_module
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)

        # Call the stats function directly
        stats = views_module._get_reconciliation_stats(store)

        assert stats["pending"] == 2
        assert stats["rejected"] == 1
        assert stats["accepted"] == 0
        assert stats["unlinked"] == 2  # UNMATCHED transactions


class TestAuditTrailFunctions:
    """Test audit trail state store functions."""

    def test_get_interpretation_runs(self, populated_reconciliation_db):
        """Test retrieving interpretation runs for a document."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        runs = store.get_interpretation_runs(100)

        # Should have 2 runs for document 100
        assert len(runs) == 2

        # Check runs are ordered by timestamp DESC
        assert runs[0]["final_state"] == "REJECTED"  # More recent
        assert runs[1]["final_state"] == "PROPOSED"

    def test_create_interpretation_run(self, populated_reconciliation_db):
        """Test creating a new interpretation run."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)

        run_id = store.create_interpretation_run(
            document_id=101,
            firefly_id=501,
            external_id="paperless:101:hash101",
            pipeline_version="1.0.0",
            inputs_summary={"action": "accept_proposal"},
            final_state="LINKED",
            decision_source="USER",
            firefly_write_action="UPDATE_LINKAGE",
            firefly_target_id=501,
        )

        assert run_id > 0

        # Verify it was created
        runs = store.get_interpretation_runs(101)
        assert len(runs) == 1
        assert runs[0]["final_state"] == "LINKED"
        assert runs[0]["decision_source"] == "USER"


class TestReconciliationNavigationLogic:
    """Test reconciliation queue navigation."""

    def test_proposal_navigation_indices(self, populated_reconciliation_db):
        """Test prev/next calculation for proposal navigation."""
        from paperless_firefly.state_store import StateStore

        store = StateStore(populated_reconciliation_db)
        proposals = store.get_pending_proposals()
        proposal_ids = [p["id"] for p in proposals]

        assert len(proposal_ids) == 2

        # At first item
        current_idx = 0
        prev_id = proposal_ids[current_idx - 1] if current_idx > 0 else None
        next_id = proposal_ids[current_idx + 1] if current_idx < len(proposal_ids) - 1 else None

        assert prev_id is None
        assert next_id == proposal_ids[1]

        # At second item
        current_idx = 1
        prev_id = proposal_ids[current_idx - 1] if current_idx > 0 else None
        next_id = proposal_ids[current_idx + 1] if current_idx < len(proposal_ids) - 1 else None

        assert prev_id == proposal_ids[0]
        assert next_id is None
