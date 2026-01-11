"""Tests for state store."""

import sqlite3

import pytest

from paperless_firefly.state_store import (
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
        StateStore(temp_db)
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

        with pytest.raises(sqlite3.IntegrityError):
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


class TestExtractionReset:
    """Tests for extraction reset functionality (unlist/relist scenario)."""

    @pytest.fixture
    def store(self, temp_db):
        store = StateStore(temp_db)
        store.upsert_document(document_id=123, source_hash="abc123")
        return store

    def test_reset_extraction_for_review(self, store):
        """Reset a rejected extraction so it can be reviewed again."""
        # Create extraction and reject it
        extraction_id = store.save_extraction(
            document_id=123,
            external_id="test-ext-1",
            extraction_json='{"test": true}',
            overall_confidence=0.7,
            review_state="REVIEW",
        )
        store.update_extraction_review(extraction_id, "REJECTED")

        # Verify it was rejected
        ext = store.get_extraction_by_external_id("test-ext-1")
        assert ext.review_decision == "REJECTED"

        # Reset it
        result = store.reset_extraction_for_review(extraction_id)
        assert result is True

        # Verify it's reset
        ext = store.get_extraction_by_external_id("test-ext-1")
        assert ext.review_decision is None
        assert ext.reviewed_at is None

    def test_reset_extraction_by_document(self, store):
        """Reset extraction by document ID."""
        extraction_id = store.save_extraction(
            document_id=123,
            external_id="test-ext-2",
            extraction_json='{"test": true}',
            overall_confidence=0.7,
            review_state="REVIEW",
        )
        store.update_extraction_review(extraction_id, "ACCEPTED")

        # Reset by document ID
        result = store.reset_extraction_by_document(123)
        assert result is True

        # Verify it's reset
        ext = store.get_extraction_by_document(123)
        assert ext.review_decision is None

    def test_reset_nonexistent_extraction(self, store):
        """Reset returns False for nonexistent extraction."""
        result = store.reset_extraction_for_review(999)
        assert result is False

    def test_rejected_extraction_not_in_review_queue(self, store):
        """Rejected extractions are not in review queue."""
        extraction_id = store.save_extraction(
            document_id=123,
            external_id="test-ext-3",
            extraction_json='{"test": true}',
            overall_confidence=0.7,
            review_state="REVIEW",
        )

        # Before rejection, should be in queue
        pending = store.get_extractions_for_review()
        assert len(pending) == 1

        # After rejection, should not be in queue
        store.update_extraction_review(extraction_id, "REJECTED")
        pending = store.get_extractions_for_review()
        assert len(pending) == 0

        # After reset, should be back in queue
        store.reset_extraction_for_review(extraction_id)
        pending = store.get_extractions_for_review()
        assert len(pending) == 1


class TestImportReset:
    """Tests for import reset functionality (reimport scenario)."""

    @pytest.fixture
    def store(self, temp_db):
        store = StateStore(temp_db)
        store.upsert_document(document_id=123, source_hash="abc123")
        return store

    def test_reset_import_for_retry(self, store):
        """Reset imported record for reimport."""
        store.create_import(
            external_id="test-import-1",
            document_id=123,
            payload_json='{"transactions": []}',
        )
        store.update_import_success("test-import-1", firefly_id=999)

        # Verify it was imported
        record = store.get_import_by_external_id("test-import-1")
        assert record.status == ImportStatus.IMPORTED
        assert record.firefly_id == 999

        # Reset for reimport
        firefly_id = store.reset_import_for_retry("test-import-1")
        assert firefly_id == 999

        # Verify it's reset to PENDING but keeps firefly_id
        record = store.get_import_by_external_id("test-import-1")
        assert record.status == ImportStatus.PENDING
        assert record.firefly_id == 999  # Kept for update

    def test_reset_nonexistent_import(self, store):
        """Reset returns None for nonexistent import."""
        result = store.reset_import_for_retry("nonexistent")
        assert result is None

    def test_get_import_by_document(self, store):
        """Get import by document ID."""
        store.create_import(
            external_id="test-import-2",
            document_id=123,
            payload_json="{}",
        )

        record = store.get_import_by_document(123)
        assert record is not None
        assert record.external_id == "test-import-2"

    def test_get_import_by_document_nonexistent(self, store):
        """Get import returns None for nonexistent document."""
        record = store.get_import_by_document(999)
        assert record is None


class TestProcessedExtractions:
    """Tests for get_processed_extractions (archive view)."""

    @pytest.fixture
    def store(self, temp_db):
        store = StateStore(temp_db)
        store.upsert_document(document_id=1, source_hash="a")
        store.upsert_document(document_id=2, source_hash="b")
        store.upsert_document(document_id=3, source_hash="c")
        return store

    def test_get_processed_extractions_empty(self, store):
        """No processed extractions initially."""
        processed = store.get_processed_extractions()
        assert len(processed) == 0

    def test_get_processed_extractions_rejected(self, store):
        """Rejected extractions appear in processed list."""
        ext_id = store.save_extraction(
            document_id=1,
            external_id="ext-1",
            extraction_json='{"doc": 1}',
            overall_confidence=0.7,
            review_state="REVIEW",
        )
        store.update_extraction_review(ext_id, "REJECTED")

        processed = store.get_processed_extractions()
        assert len(processed) == 1
        assert processed[0]["review_decision"] == "REJECTED"

    def test_get_processed_extractions_imported(self, store):
        """Imported extractions appear in processed list."""
        store.save_extraction(
            document_id=2,
            external_id="ext-2",
            extraction_json='{"doc": 2}',
            overall_confidence=0.9,
            review_state="AUTO",
        )
        store.create_import(
            external_id="ext-2",
            document_id=2,
            payload_json="{}",
        )
        store.update_import_success("ext-2", firefly_id=123)

        processed = store.get_processed_extractions()
        assert len(processed) == 1
        assert processed[0]["import_status"] == "IMPORTED"
        assert processed[0]["firefly_id"] == 123


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


class TestFireflyCacheSoftDelete:
    """Tests for soft delete functionality in Firefly cache."""

    @pytest.fixture
    def store(self, temp_db):
        return StateStore(temp_db)

    def test_soft_delete_firefly_cache_entry(self, store):
        """Test soft delete marks record instead of removing it."""
        # Insert a cache entry
        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2024-01-15",
            amount="50.00",
            description="Test transaction",
        )

        # Verify it exists
        entry = store.get_firefly_cache_entry(123)
        assert entry is not None
        assert entry["deleted_at"] is None

        # Soft delete
        result = store.soft_delete_firefly_cache(123)
        assert result is True

        # Entry still exists but is marked deleted
        entry = store.get_firefly_cache_entry(123)
        assert entry is not None
        assert entry["deleted_at"] is not None

    def test_soft_delete_nonexistent_entry(self, store):
        """Soft deleting nonexistent entry returns False."""
        result = store.soft_delete_firefly_cache(999)
        assert result is False

    def test_soft_delete_missing_transactions(self, store):
        """Test bulk soft delete of transactions no longer in Firefly."""
        # Insert multiple cache entries
        for i in range(1, 6):
            store.upsert_firefly_cache(
                firefly_id=i,
                type_="withdrawal",
                date="2024-01-15",
                amount=f"{i * 10}.00",
                description=f"Transaction {i}",
            )

        # Current Firefly only has IDs 1, 3, 5
        current_ids = {1, 3, 5}

        # Soft delete missing (IDs 2 and 4)
        deleted_count = store.soft_delete_missing_firefly_transactions(current_ids)
        assert deleted_count == 2

        # Verify 1, 3, 5 are not deleted
        for fid in [1, 3, 5]:
            entry = store.get_firefly_cache_entry(fid)
            assert entry["deleted_at"] is None

        # Verify 2, 4 are soft deleted
        for fid in [2, 4]:
            entry = store.get_firefly_cache_entry(fid)
            assert entry["deleted_at"] is not None

    def test_get_unmatched_excludes_soft_deleted(self, store):
        """Unmatched query excludes soft-deleted entries."""
        # Insert entries
        store.upsert_firefly_cache(
            firefly_id=1,
            type_="withdrawal",
            date="2024-01-15",
            amount="10.00",
        )
        store.upsert_firefly_cache(
            firefly_id=2,
            type_="withdrawal",
            date="2024-01-16",
            amount="20.00",
        )

        # Both should be unmatched initially
        unmatched = store.get_unmatched_firefly_transactions()
        assert len(unmatched) == 2

        # Soft delete one
        store.soft_delete_firefly_cache(1)

        # Only one should remain in unmatched
        unmatched = store.get_unmatched_firefly_transactions()
        assert len(unmatched) == 1
        assert unmatched[0]["firefly_id"] == 2

    def test_get_active_firefly_cache_count(self, store):
        """Test counting non-deleted cache entries."""
        # Insert 5 entries
        for i in range(1, 6):
            store.upsert_firefly_cache(
                firefly_id=i,
                type_="withdrawal",
                date="2024-01-15",
                amount=f"{i * 10}.00",
            )

        assert store.get_active_firefly_cache_count() == 5

        # Soft delete 2 entries
        store.soft_delete_firefly_cache(1)
        store.soft_delete_firefly_cache(2)

        assert store.get_active_firefly_cache_count() == 3

    def test_double_soft_delete_is_noop(self, store):
        """Soft deleting already deleted entry doesn't change timestamp."""
        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2024-01-15",
            amount="50.00",
        )

        # First soft delete
        store.soft_delete_firefly_cache(123)
        entry = store.get_firefly_cache_entry(123)
        first_deleted_at = entry["deleted_at"]

        # Second soft delete should return False (no update)
        result = store.soft_delete_firefly_cache(123)
        assert result is False

        # Timestamp unchanged
        entry = store.get_firefly_cache_entry(123)
        assert entry["deleted_at"] == first_deleted_at
