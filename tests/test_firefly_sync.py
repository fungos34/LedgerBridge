"""Tests for Firefly synchronization service and related StateStore methods."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from paperless_firefly.firefly_client import FireflyClient, FireflyTransaction
from paperless_firefly.services.firefly_sync import FireflySyncService, SyncResult
from paperless_firefly.state_store import StateStore


class TestFireflyCacheMethods:
    """Tests for StateStore firefly_cache methods."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore for each test."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    def test_upsert_firefly_cache_new(self, store: StateStore) -> None:
        """Test inserting a new firefly transaction into cache."""
        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test purchase",
            source_account="Checking",
            destination_account="Amazon",
            tags=["shopping", "online"],
        )

        entry = store.get_firefly_cache_entry(123)
        assert entry is not None
        assert entry["firefly_id"] == 123
        assert entry["type"] == "withdrawal"
        assert entry["amount"] == "99.99"
        assert entry["description"] == "Test purchase"
        assert entry["match_status"] == "UNMATCHED"

    def test_upsert_firefly_cache_update(self, store: StateStore) -> None:
        """Test updating an existing cached transaction."""
        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Original description",
        )

        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2025-01-15",
            amount="149.99",
            description="Updated description",
        )

        entry = store.get_firefly_cache_entry(123)
        assert entry["amount"] == "149.99"
        assert entry["description"] == "Updated description"

    def test_get_unmatched_firefly_transactions(self, store: StateStore) -> None:
        """Test getting unmatched cached transactions."""
        # Add a document for foreign key
        store.upsert_document(document_id=999, source_hash="hash1", title="Test Doc")

        # Add some transactions
        for i in range(3):
            store.upsert_firefly_cache(
                firefly_id=100 + i,
                type_="withdrawal",
                date=f"2025-01-{15 + i}",
                amount=str(10.0 * (i + 1)),
                description=f"Transaction {i}",
            )

        # Mark one as matched (with valid document_id)
        store.update_firefly_match_status(101, "MATCHED", document_id=999, confidence=0.95)

        unmatched = store.get_unmatched_firefly_transactions()
        assert len(unmatched) == 2
        assert all(t["match_status"] == "UNMATCHED" for t in unmatched)

    def test_update_firefly_match_status(self, store: StateStore) -> None:
        """Test updating match status of cached transaction."""
        # Add a document for foreign key
        store.upsert_document(document_id=456, source_hash="hash1", title="Test Doc")

        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test",
        )

        store.update_firefly_match_status(123, "MATCHED", document_id=456, confidence=0.87)

        entry = store.get_firefly_cache_entry(123)
        assert entry["match_status"] == "MATCHED"
        assert entry["matched_document_id"] == 456
        assert entry["match_confidence"] == 0.87

    def test_update_firefly_match_status_no_document(self, store: StateStore) -> None:
        """Test updating match status without document (e.g., rejected match)."""
        store.upsert_firefly_cache(
            firefly_id=123,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test",
        )

        store.update_firefly_match_status(123, "REJECTED", document_id=None, confidence=None)

        entry = store.get_firefly_cache_entry(123)
        assert entry["match_status"] == "REJECTED"
        assert entry["matched_document_id"] is None

    def test_clear_firefly_cache(self, store: StateStore) -> None:
        """Test clearing the entire cache."""
        for i in range(5):
            store.upsert_firefly_cache(
                firefly_id=100 + i,
                type_="withdrawal",
                date="2025-01-15",
                amount="10.00",
                description=f"Transaction {i}",
            )

        deleted = store.clear_firefly_cache()
        assert deleted == 5

        unmatched = store.get_unmatched_firefly_transactions()
        assert len(unmatched) == 0


class TestMatchProposalsMethods:
    """Tests for StateStore match_proposals methods."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore with test data."""
        db_path = tmp_path / "test.db"
        store = StateStore(str(db_path), run_migrations=True)

        # Add test document and firefly transaction
        store.upsert_document(document_id=1, source_hash="hash1", title="Invoice")
        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test purchase",
        )
        return store

    def test_create_match_proposal(self, store: StateStore) -> None:
        """Test creating a match proposal."""
        proposal_id = store.create_match_proposal(
            firefly_id=100,
            document_id=1,
            match_score=0.85,
            match_reasons=["amount_match", "date_close"],
        )

        assert proposal_id > 0

        proposal = store.get_proposal_by_id(proposal_id)
        assert proposal is not None
        assert proposal["firefly_id"] == 100
        assert proposal["document_id"] == 1
        assert proposal["match_score"] == 0.85
        assert proposal["status"] == "PENDING"

    def test_get_pending_proposals(self, store: StateStore) -> None:
        """Test getting pending proposals with joined data."""
        store.create_match_proposal(
            firefly_id=100, document_id=1, match_score=0.85, match_reasons=["amount_match"]
        )

        pending = store.get_pending_proposals()
        assert len(pending) == 1
        assert pending[0]["match_score"] == 0.85
        assert pending[0]["tx_amount"] == "99.99"
        assert pending[0]["doc_title"] == "Invoice"

    def test_update_proposal_status(self, store: StateStore) -> None:
        """Test updating proposal status."""
        proposal_id = store.create_match_proposal(firefly_id=100, document_id=1, match_score=0.85)

        store.update_proposal_status(proposal_id, "ACCEPTED")

        proposal = store.get_proposal_by_id(proposal_id)
        assert proposal["status"] == "ACCEPTED"
        assert proposal["reviewed_at"] is not None


class TestInterpretationRunsMethods:
    """Tests for StateStore interpretation_runs methods."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore with a test document."""
        db_path = tmp_path / "test.db"
        store = StateStore(str(db_path), run_migrations=True)
        # Add document for foreign key
        store.upsert_document(document_id=123, source_hash="hash123", title="Test Invoice")
        return store

    def test_create_interpretation_run(self, store: StateStore) -> None:
        """Test creating an interpretation run."""
        run_id = store.create_interpretation_run(
            document_id=123,
            firefly_id=None,
            external_id="ext-123",
            pipeline_version="1.0.0",
            inputs_summary={"amount": 99.99, "vendor": "Amazon"},
            final_state="CREATED_TRANSACTION",
            duration_ms=150,
            algorithm_version="spark-v1",
            suggested_category="Shopping",
            auto_applied=True,
            decision_source="rules",
        )

        assert run_id > 0

    def test_get_interpretation_runs(self, store: StateStore) -> None:
        """Test getting all runs for a document."""
        # Create multiple runs
        for i in range(3):
            store.create_interpretation_run(
                document_id=123,
                firefly_id=None,
                external_id=f"ext-{i}",
                pipeline_version="1.0.0",
                inputs_summary={"iteration": i},
                final_state="CREATED_TRANSACTION",
            )

        runs = store.get_interpretation_runs(123)
        assert len(runs) == 3
        # Should be ordered by timestamp DESC
        assert runs[0]["id"] >= runs[1]["id"]

    def test_get_latest_interpretation_run(self, store: StateStore) -> None:
        """Test getting the most recent run."""
        store.create_interpretation_run(
            document_id=123,
            firefly_id=None,
            external_id="ext-1",
            pipeline_version="1.0.0",
            inputs_summary={"first": True},
            final_state="FAILED",
        )

        store.create_interpretation_run(
            document_id=123,
            firefly_id=None,
            external_id="ext-2",
            pipeline_version="1.0.0",
            inputs_summary={"second": True},
            final_state="CREATED_TRANSACTION",
        )

        latest = store.get_latest_interpretation_run(123)
        assert latest is not None
        assert latest["final_state"] == "CREATED_TRANSACTION"


class TestLLMFeedbackMethods:
    """Tests for StateStore LLM feedback methods."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore with a test interpretation run."""
        db_path = tmp_path / "test.db"
        store = StateStore(str(db_path), run_migrations=True)

        # Add document for foreign key
        store.upsert_document(document_id=1, source_hash="hash1", title="Test Doc")

        # Create a run for feedback
        store.create_interpretation_run(
            document_id=1,
            firefly_id=None,
            external_id="ext-1",
            pipeline_version="1.0.0",
            inputs_summary={},
            final_state="AWAITING_REVIEW",
            llm_result={"category": "Shopping"},
        )
        return store

    def test_record_llm_feedback(self, store: StateStore) -> None:
        """Test recording feedback on LLM suggestion."""
        feedback_id = store.record_llm_feedback(
            run_id=1,
            suggested_category="Shopping",
            actual_category="Groceries",
            feedback_type="WRONG",
            notes="Was actually grocery shopping",
        )

        assert feedback_id > 0

    def test_get_llm_feedback_stats(self, store: StateStore) -> None:
        """Test getting feedback statistics."""
        # Record some feedback
        store.record_llm_feedback(
            run_id=1,
            suggested_category="Shopping",
            actual_category="Shopping",
            feedback_type="CORRECT",
        )
        store.record_llm_feedback(
            run_id=1,
            suggested_category="Dining",
            actual_category="Groceries",
            feedback_type="WRONG",
        )
        store.record_llm_feedback(
            run_id=1,
            suggested_category="Bills",
            actual_category="Bills",
            feedback_type="CORRECT",
        )

        stats = store.get_llm_feedback_stats()
        assert stats["total"] == 3
        assert stats["correct"] == 2
        assert stats["wrong"] == 1
        assert stats["accuracy"] == pytest.approx(0.666, rel=0.01)


class TestLLMCacheMethods:
    """Tests for StateStore LLM cache methods."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    def test_set_and_get_llm_cache(self, store: StateStore) -> None:
        """Test caching and retrieving LLM response."""
        cache_key = "hash:abc123"

        store.set_llm_cache(
            cache_key=cache_key,
            model="qwen2.5:7b",
            prompt_version="v1",
            taxonomy_version="2025-01",
            response_json='{"category": "Shopping"}',
            ttl_days=30,
        )

        cached = store.get_llm_cache(cache_key)
        assert cached is not None
        assert cached["model"] == "qwen2.5:7b"
        assert cached["response_json"] == '{"category": "Shopping"}'
        assert cached["hit_count"] == 1  # Incremented on read

    def test_get_llm_cache_miss(self, store: StateStore) -> None:
        """Test cache miss returns None."""
        cached = store.get_llm_cache("nonexistent-key")
        assert cached is None

    def test_llm_cache_expiry(self, store: StateStore) -> None:
        """Test that expired cache entries are not returned."""
        # Set cache with 0 TTL (already expired)
        store.set_llm_cache(
            cache_key="expired-key",
            model="qwen2.5:7b",
            prompt_version="v1",
            taxonomy_version="2025-01",
            response_json="{}",
            ttl_days=-1,  # Already expired
        )

        cached = store.get_llm_cache("expired-key")
        assert cached is None

    def test_clear_expired_llm_cache(self, store: StateStore) -> None:
        """Test clearing expired cache entries."""
        # Add valid entry
        store.set_llm_cache(
            cache_key="valid-key",
            model="qwen2.5:7b",
            prompt_version="v1",
            taxonomy_version="2025-01",
            response_json="{}",
            ttl_days=30,
        )

        # Add expired entry
        store.set_llm_cache(
            cache_key="expired-key",
            model="qwen2.5:7b",
            prompt_version="v1",
            taxonomy_version="2025-01",
            response_json="{}",
            ttl_days=-1,
        )

        deleted = store.clear_expired_llm_cache()
        assert deleted == 1

        # Valid entry should still exist
        assert store.get_llm_cache("valid-key") is not None


class TestFireflySyncService:
    """Tests for FireflySyncService."""

    @pytest.fixture
    def mock_firefly(self) -> MagicMock:
        """Create mock Firefly client."""
        mock = MagicMock(spec=FireflyClient)
        mock.list_categories.return_value = []
        mock.get_unlinked_transactions.return_value = []
        mock.list_transactions.return_value = []
        return mock

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    @pytest.fixture
    def mock_config(self) -> MagicMock:
        """Create mock config."""
        return MagicMock()

    @pytest.fixture
    def service(
        self, mock_firefly: MagicMock, store: StateStore, mock_config: MagicMock
    ) -> FireflySyncService:
        """Create FireflySyncService with mocks."""
        return FireflySyncService(
            firefly_client=mock_firefly,
            state_store=store,
            config=mock_config,
        )

    def test_sync_transactions_empty(
        self, service: FireflySyncService, mock_firefly: MagicMock
    ) -> None:
        """Test sync with no transactions."""
        result = service.sync_transactions()

        assert result.success
        assert result.transactions_synced == 0
        assert result.transactions_skipped == 0
        mock_firefly.get_unlinked_transactions.assert_called_once()

    def test_sync_transactions_caches_unlinked(
        self,
        service: FireflySyncService,
        mock_firefly: MagicMock,
        store: StateStore,
    ) -> None:
        """Test that unlinked transactions are cached."""
        mock_firefly.get_unlinked_transactions.return_value = [
            FireflyTransaction(
                id=100,
                type="withdrawal",
                date="2025-01-15",
                amount=99.99,
                description="Test purchase",
                source_name="Checking",
                destination_name="Amazon",
            ),
            FireflyTransaction(
                id=101,
                type="withdrawal",
                date="2025-01-16",
                amount=49.99,
                description="Another purchase",
                source_name="Checking",
                destination_name="Target",
            ),
        ]

        result = service.sync_transactions()

        assert result.success
        assert result.transactions_synced == 2
        assert result.transactions_skipped == 0

        # Verify cached
        unmatched = store.get_unmatched_firefly_transactions()
        assert len(unmatched) == 2

    def test_sync_transactions_skips_linked(
        self,
        service: FireflySyncService,
        mock_firefly: MagicMock,
    ) -> None:
        """Test that linked transactions are skipped."""
        mock_firefly.get_unlinked_transactions.return_value = [
            FireflyTransaction(
                id=100,
                type="withdrawal",
                date="2025-01-15",
                amount=99.99,
                description="Test purchase",
                source_name="Checking",
                destination_name="Amazon",
                external_id="paperless:123",  # Already linked
            ),
        ]

        result = service.sync_transactions()

        assert result.success
        assert result.transactions_synced == 0
        assert result.transactions_skipped == 1

    def test_sync_transactions_full_sync(
        self,
        service: FireflySyncService,
        mock_firefly: MagicMock,
        store: StateStore,
    ) -> None:
        """Test full sync clears cache first."""
        # Pre-populate cache
        store.upsert_firefly_cache(
            firefly_id=999,
            type_="withdrawal",
            date="2025-01-01",
            amount="50.00",
            description="Old transaction",
        )

        mock_firefly.list_transactions.return_value = [
            FireflyTransaction(
                id=100,
                type="withdrawal",
                date="2025-01-15",
                amount=99.99,
                description="New transaction",
                source_name="Checking",
                destination_name="Amazon",
            ),
        ]

        result = service.sync_transactions(full_sync=True)

        assert result.success
        assert result.transactions_synced == 1

        # Old entry should be gone
        assert store.get_firefly_cache_entry(999) is None
        # New entry should exist
        assert store.get_firefly_cache_entry(100) is not None

    def test_mark_matched(
        self,
        service: FireflySyncService,
        store: StateStore,
    ) -> None:
        """Test marking a transaction as matched."""
        # Add document for foreign key
        store.upsert_document(document_id=123, source_hash="hash123", title="Test Doc")

        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test",
        )

        service.mark_matched(firefly_id=100, document_id=123, confidence=0.92)

        entry = store.get_firefly_cache_entry(100)
        assert entry["match_status"] == "MATCHED"
        assert entry["matched_document_id"] == 123
        assert entry["match_confidence"] == 0.92

    def test_get_sync_stats(
        self,
        service: FireflySyncService,
        mock_firefly: MagicMock,
        store: StateStore,
    ) -> None:
        """Test getting sync statistics."""
        # Add some cached transactions
        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Test 1",
        )
        store.upsert_firefly_cache(
            firefly_id=101,
            type_="withdrawal",
            date="2025-01-16",
            amount="49.99",
            description="Test 2",
        )

        # Sync categories to populate the internal dict
        from paperless_firefly.firefly_client import FireflyCategory

        mock_firefly.list_categories.return_value = [
            FireflyCategory(id=1, name="Shopping"),
            FireflyCategory(id=2, name="Groceries"),
        ]
        service._sync_categories()

        stats = service.get_sync_stats()
        assert stats["cached_unmatched"] == 2
        assert stats["categories_loaded"] == 2


class TestSyncResult:
    """Tests for SyncResult dataclass."""

    def test_sync_result_success_no_errors(self) -> None:
        """Test success property when no errors."""
        result = SyncResult(
            transactions_synced=10,
            transactions_skipped=2,
            categories_synced=5,
            duration_ms=500,
            errors=[],
        )
        assert result.success is True

    def test_sync_result_failure_with_errors(self) -> None:
        """Test success property when there are errors."""
        result = SyncResult(
            transactions_synced=5,
            transactions_skipped=0,
            categories_synced=5,
            duration_ms=500,
            errors=["API timeout", "Rate limited"],
        )
        assert result.success is False
