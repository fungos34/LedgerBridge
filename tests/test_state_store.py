"""Tests for state store."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from paperless_firefly.state_store import (
    DocumentRecord,
    ExtractionRecord,
    ImportRecord,
    ImportStatus,
    StateStore,
)


class TestStateStore:
    """Tests for SQLite state store."""

    @pytest.fixture
    def store(self, temp_db):
        """Create a fresh state store."""
        return StateStore(temp_db)

    def test_init_creates_db(self, temp_db):
        """Initializing creates database file."""
        store = StateStore(temp_db)
        assert temp_db.exists()

    def test_init_creates_tables(self, store):
        """All required tables are created."""
        conn = store._get_connection()
        try:
            # Check tables exist
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            table_names = [t[0] for t in tables]

            assert "paperless_documents" in table_names
            assert "extractions" in table_names
            assert "imports" in table_names
            assert "vendor_mappings" in table_names
        finally:
            conn.close()


class TestDocumentOperations:
    """Tests for document CRUD operations."""

    @pytest.fixture
    def store(self, temp_db):
        return StateStore(temp_db)

    def test_upsert_new_document(self, store):
        """Insert new document."""
        store.upsert_document(
            document_id=123,
            source_hash="abc123",
            title="Test Doc",
            document_type="Receipt",
            correspondent="SPAR",
            tags=["finance", "receipt"],
        )

        doc = store.get_document(123)

        assert doc is not None
        assert doc.document_id == 123
        assert doc.source_hash == "abc123"
        assert doc.title == "Test Doc"
        assert doc.tags == ["finance", "receipt"]

    def test_upsert_update_document(self, store):
        """Update existing document."""
        # Insert
        store.upsert_document(
            document_id=123,
            source_hash="abc123",
            title="Old Title",
        )

        # Update
        store.upsert_document(
            document_id=123,
            source_hash="abc123",
            title="New Title",
        )

        doc = store.get_document(123)
        assert doc.title == "New Title"

    def test_document_exists(self, store):
        """Check document existence."""
        assert not store.document_exists(123)

        store.upsert_document(document_id=123, source_hash="abc")

        assert store.document_exists(123)
        assert not store.document_exists(999)


class TestExtractionOperations:
    """Tests for extraction CRUD operations."""

    @pytest.fixture
    def store(self, temp_db):
        store = StateStore(temp_db)
        # Create a document first
        store.upsert_document(document_id=123, source_hash="abc123")
        return store

    def test_save_extraction(self, store):
        """Save extraction record."""
        extraction_id = store.save_extraction(
            document_id=123,
            external_id="paperless:123:abc:10.00:2024-01-01",
            extraction_json='{"test": "data"}',
            overall_confidence=0.85,
            review_state="AUTO",
        )

        assert extraction_id > 0

    def test_get_extraction_by_document(self, store):
        """Get extraction by document ID."""
        store.save_extraction(
            document_id=123,
            external_id="paperless:123:abc:10.00:2024-01-01",
            extraction_json='{"amount": "10.00"}',
            overall_confidence=0.85,
            review_state="AUTO",
        )

        extraction = store.get_extraction_by_document(123)

        assert extraction is not None
        assert extraction.document_id == 123
        assert extraction.external_id == "paperless:123:abc:10.00:2024-01-01"

    def test_get_extraction_by_external_id(self, store):
        """Get extraction by external_id."""
        store.save_extraction(
            document_id=123,
            external_id="paperless:123:abc:10.00:2024-01-01",
            extraction_json='{"amount": "10.00"}',
            overall_confidence=0.85,
            review_state="AUTO",
        )

        extraction = store.get_extraction_by_external_id("paperless:123:abc:10.00:2024-01-01")

        assert extraction is not None
        assert extraction.document_id == 123

    def test_external_id_unique(self, store):
        """External ID must be unique."""
        store.save_extraction(
            document_id=123,
            external_id="unique-id",
            extraction_json="{}",
            overall_confidence=0.5,
            review_state="REVIEW",
        )

        with pytest.raises(Exception):  # IntegrityError
            store.save_extraction(
                document_id=123,
                external_id="unique-id",
                extraction_json="{}",
                overall_confidence=0.5,
                review_state="REVIEW",
            )

    def test_get_extractions_for_review(self, store):
        """Get pending review extractions."""
        store.save_extraction(
            document_id=123,
            external_id="id-1",
            extraction_json="{}",
            overall_confidence=0.5,
            review_state="REVIEW",
        )

        pending = store.get_extractions_for_review()

        assert len(pending) == 1
        assert pending[0].review_state == "REVIEW"

    def test_update_extraction_review(self, store):
        """Update extraction with review decision."""
        extraction_id = store.save_extraction(
            document_id=123,
            external_id="id-1",
            extraction_json='{"original": true}',
            overall_confidence=0.5,
            review_state="REVIEW",
        )

        store.update_extraction_review(
            extraction_id=extraction_id,
            decision="ACCEPTED",
            updated_json='{"edited": true}',
        )

        extraction = store.get_extraction_by_external_id("id-1")
        assert extraction.review_decision == "ACCEPTED"
        assert "edited" in extraction.extraction_json


class TestImportOperations:
    """Tests for import CRUD operations."""

    @pytest.fixture
    def store(self, temp_db):
        store = StateStore(temp_db)
        store.upsert_document(document_id=123, source_hash="abc123")
        return store

    def test_create_import(self, store):
        """Create import record."""
        import_id = store.create_import(
            external_id="paperless:123:abc:10.00:2024-01-01",
            document_id=123,
            payload_json='{"transactions": []}',
            status=ImportStatus.PENDING,
        )

        assert import_id > 0

    def test_update_import_success(self, store):
        """Mark import as successful."""
        store.create_import(
            external_id="test-id",
            document_id=123,
            payload_json="{}",
        )

        store.update_import_success("test-id", firefly_id=999)

        record = store.get_import_by_external_id("test-id")
        assert record.status == ImportStatus.IMPORTED
        assert record.firefly_id == 999

    def test_update_import_failed(self, store):
        """Mark import as failed."""
        store.create_import(
            external_id="test-id",
            document_id=123,
            payload_json="{}",
        )

        store.update_import_failed("test-id", "Connection error")

        record = store.get_import_by_external_id("test-id")
        assert record.status == ImportStatus.FAILED
        assert record.error_message == "Connection error"

    def test_create_or_update_failed_import_new(self, store):
        """Create failed import when no record exists."""
        # Should create new record
        store.create_or_update_failed_import(
            external_id="new-failed-id",
            document_id=123,
            error_message="Build payload failed",
        )

        record = store.get_import_by_external_id("new-failed-id")
        assert record is not None
        assert record.status == ImportStatus.FAILED
        assert record.error_message == "Build payload failed"

    def test_create_or_update_failed_import_existing(self, store):
        """Update existing import to failed."""
        # First create a pending import
        store.create_import(
            external_id="existing-id",
            document_id=123,
            payload_json="{}",
            status=ImportStatus.PENDING,
        )

        # Now update it to failed
        store.create_or_update_failed_import(
            external_id="existing-id",
            document_id=123,
            error_message="API returned 422",
        )

        record = store.get_import_by_external_id("existing-id")
        assert record.status == ImportStatus.FAILED
        assert record.error_message == "API returned 422"

    def test_is_imported(self, store):
        """Check if external_id was successfully imported."""
        store.create_import(
            external_id="test-id",
            document_id=123,
            payload_json="{}",
        )

        assert not store.is_imported("test-id")

        store.update_import_success("test-id", 999)

        assert store.is_imported("test-id")

    def test_import_exists(self, store):
        """Check if import record exists."""
        assert not store.import_exists("nonexistent")

        store.create_import(
            external_id="test-id",
            document_id=123,
            payload_json="{}",
        )

        assert store.import_exists("test-id")


class TestVendorMappings:
    """Tests for vendor mapping operations."""

    @pytest.fixture
    def store(self, temp_db):
        return StateStore(temp_db)

    def test_save_vendor_mapping(self, store):
        """Save vendor mapping."""
        store.save_vendor_mapping(
            vendor_pattern="SPAR",
            destination_account="Groceries",
            category="Food",
            tags=["supermarket"],
        )

        mapping = store.get_vendor_mapping("SPAR")

        assert mapping is not None
        assert mapping["destination_account"] == "Groceries"
        assert mapping["category"] == "Food"
        assert mapping["tags"] == ["supermarket"]

    def test_update_vendor_mapping(self, store):
        """Update increments use count."""
        store.save_vendor_mapping(vendor_pattern="SPAR", category="Food")
        store.save_vendor_mapping(vendor_pattern="SPAR", category="Groceries")

        mapping = store.get_vendor_mapping("SPAR")

        assert mapping["category"] == "Groceries"
        assert mapping["use_count"] == 2


class TestStatistics:
    """Tests for statistics."""

    @pytest.fixture
    def store(self, temp_db):
        return StateStore(temp_db)

    def test_get_stats_empty(self, store):
        """Stats on empty database."""
        stats = store.get_stats()

        assert stats["documents_processed"] == 0
        assert stats["extractions_total"] == 0
        assert stats["imports_total"] == 0

    def test_get_stats_with_data(self, store):
        """Stats with data."""
        store.upsert_document(document_id=1, source_hash="a")
        store.upsert_document(document_id=2, source_hash="b")
        store.save_extraction(
            document_id=1,
            external_id="id-1",
            extraction_json="{}",
            overall_confidence=0.9,
            review_state="AUTO",
        )

        stats = store.get_stats()

        assert stats["documents_processed"] == 2
        assert stats["extractions_total"] == 1
