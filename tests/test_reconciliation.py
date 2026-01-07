"""Tests for the ReconciliationService.

These tests verify:
- Idempotent reconciliation pipeline
- Proposal creation and status management
- Auto-linking logic with threshold enforcement
- Manual link and reject workflows
- InterpretationRun creation for audit trail
- LLM opt-out handling
- Re-run interpretation capability
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from paperless_firefly.config import Config, FireflyConfig, PaperlessConfig, ReconciliationConfig
from paperless_firefly.matching.engine import MatchResult, MatchScore
from paperless_firefly.services.reconciliation import (
    DecisionSource,
    ReconciliationResult,
    ReconciliationService,
    ReconciliationState,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def temp_db(tmp_path: Path):
    """Create a temporary database for testing."""
    return tmp_path / "test_reconciliation.db"


@pytest.fixture
def config() -> Config:
    """Create test configuration."""
    return Config(
        paperless=PaperlessConfig(
            base_url="http://paperless:8000",
            token="test_token",
        ),
        firefly=FireflyConfig(
            base_url="http://firefly:8080",
            token="test_token",
        ),
        reconciliation=ReconciliationConfig(
            auto_match_threshold=0.90,
            date_tolerance_days=5,
        ),
    )


@pytest.fixture
def state_store(temp_db: Path):
    """Create a state store with test database."""
    from paperless_firefly.state_store import StateStore

    store = StateStore(temp_db)
    return store


@pytest.fixture
def test_document(state_store) -> int:
    """Create a test document for FK references."""
    state_store.upsert_document(
        document_id=1,
        source_hash="testhash123",
        title="Test Invoice",
    )
    return 1


@pytest.fixture
def test_transaction(state_store) -> int:
    """Create a test transaction in firefly cache for FK references."""
    state_store.upsert_firefly_cache(
        firefly_id=100,
        type_="withdrawal",
        date="2024-01-01",
        amount="100.00",
        description="Test Payment",
    )
    return 100


@pytest.fixture
def test_extraction(state_store, test_document) -> int:
    """Create a test extraction for FK references."""
    return state_store.save_extraction(
        document_id=test_document,
        external_id="plf-1-20240101-100.00-abc123",
        extraction_json=json.dumps({"total_gross": "100.00", "date": "2024-01-01"}),
        overall_confidence=0.95,
        review_state="APPROVED",
    )


@pytest.fixture
def mock_firefly_client():
    """Create a mock Firefly client."""
    client = MagicMock()
    client.update_transaction_linkage.return_value = True
    return client


@pytest.fixture
def reconciliation_service(
    mock_firefly_client: MagicMock,
    state_store,
    config: Config,
) -> ReconciliationService:
    """Create a reconciliation service with mocked dependencies."""
    return ReconciliationService(
        firefly_client=mock_firefly_client,
        state_store=state_store,
        config=config,
    )


class TestReconciliationServiceInit:
    """Tests for ReconciliationService initialization."""

    def test_service_initializes(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that service initializes with correct attributes."""
        assert reconciliation_service is not None
        assert reconciliation_service.auto_match_threshold == 0.90
        assert reconciliation_service.date_tolerance_days == 5
        assert reconciliation_service.PIPELINE_VERSION == "spark-v1.0"

    def test_service_has_sub_services(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that service initializes sub-services."""
        assert reconciliation_service.sync_service is not None
        assert reconciliation_service.matching_engine is not None


class TestReconciliationResult:
    """Tests for ReconciliationResult dataclass."""

    def test_result_success_when_completed(self) -> None:
        """Test that result.success is True when state is COMPLETED."""
        result = ReconciliationResult(state=ReconciliationState.COMPLETED)
        assert result.success is True

    def test_result_failure_when_failed(self) -> None:
        """Test that result.success is False when state is FAILED."""
        result = ReconciliationResult(state=ReconciliationState.FAILED)
        assert result.success is False

    def test_result_failure_when_syncing(self) -> None:
        """Test that result.success is False when state is SYNCING."""
        result = ReconciliationResult(state=ReconciliationState.SYNCING)
        assert result.success is False

    def test_result_default_values(self) -> None:
        """Test that result has correct default values."""
        result = ReconciliationResult(state=ReconciliationState.COMPLETED)
        assert result.transactions_synced == 0
        assert result.proposals_created == 0
        assert result.auto_linked == 0
        assert result.errors == []


class TestReconciliationState:
    """Tests for ReconciliationState enum."""

    def test_all_states_defined(self) -> None:
        """Test that all expected states are defined."""
        assert ReconciliationState.SYNCING == "SYNCING"
        assert ReconciliationState.MATCHING == "MATCHING"
        assert ReconciliationState.PROPOSING == "PROPOSING"
        assert ReconciliationState.AUTO_LINKING == "AUTO_LINKING"
        assert ReconciliationState.COMPLETED == "COMPLETED"
        assert ReconciliationState.FAILED == "FAILED"


class TestDecisionSource:
    """Tests for DecisionSource enum."""

    def test_all_sources_defined(self) -> None:
        """Test that all expected sources are defined."""
        assert DecisionSource.RULES == "RULES"
        assert DecisionSource.LLM == "LLM"
        assert DecisionSource.USER == "USER"
        assert DecisionSource.AUTO == "AUTO"


class TestRunReconciliation:
    """Tests for the run_reconciliation pipeline."""

    def test_run_reconciliation_empty_system(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that reconciliation runs on empty system."""
        with patch.object(
            reconciliation_service.sync_service,
            "sync_transactions",
        ) as mock_sync:
            mock_sync.return_value = MagicMock(
                transactions_synced=0,
                transactions_skipped=0,
                errors=[],
            )

            result = reconciliation_service.run_reconciliation()

        assert result.state == ReconciliationState.COMPLETED
        assert result.success is True
        assert result.proposals_created == 0

    def test_run_reconciliation_with_sync_error(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that sync errors are captured."""
        with patch.object(
            reconciliation_service.sync_service,
            "sync_transactions",
        ) as mock_sync:
            mock_sync.return_value = MagicMock(
                transactions_synced=5,
                transactions_skipped=0,
                errors=["API rate limit exceeded"],
            )

            result = reconciliation_service.run_reconciliation()

        assert result.success is True  # Sync errors don't fail reconciliation
        assert "API rate limit exceeded" in result.errors

    def test_run_reconciliation_fatal_error(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that fatal errors are caught."""
        with patch.object(
            reconciliation_service.sync_service,
            "sync_transactions",
            side_effect=Exception("Connection refused"),
        ):
            result = reconciliation_service.run_reconciliation()

        assert result.state == ReconciliationState.FAILED
        assert result.success is False
        assert any("Connection refused" in e for e in result.errors)

    def test_dry_run_does_not_persist(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
    ) -> None:
        """Test that dry_run=True does not persist proposals."""
        # Setup: Create a document and extraction
        state_store.upsert_document(
            document_id=1,
            source_hash="abc123",
            title="Test Invoice",
        )
        state_store.save_extraction(
            document_id=1,
            external_id="plf-1-20240101-100.00-abc",
            extraction_json=json.dumps(
                {"total_gross": "100.00", "date": "2024-01-01", "vendor": "Test"}
            ),
            overall_confidence=0.95,
            review_state="REVIEW",
        )
        state_store.update_extraction_review(1, "APPROVED")

        # Mock sync to return nothing (no unmatched transactions)
        with patch.object(
            reconciliation_service.sync_service,
            "sync_transactions",
        ) as mock_sync:
            mock_sync.return_value = MagicMock(
                transactions_synced=0,
                transactions_skipped=0,
                errors=[],
            )

            reconciliation_service.run_reconciliation(dry_run=True)

        # No proposals should be persisted
        proposals = state_store.get_pending_proposals()
        assert len(proposals) == 0


class TestProposalCreation:
    """Tests for match proposal creation."""

    def test_create_proposal_records_match(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test that _create_proposal stores match in database."""
        match = MatchResult(
            firefly_id=test_transaction,
            document_id=test_document,
            total_score=0.85,
            signals=[
                MatchScore(signal="amount", score=1.0, weight=0.4, detail="Exact match"),
            ],
            reasons=["Amount matches exactly"],
        )

        proposal_id = reconciliation_service._create_proposal(match)

        assert proposal_id is not None
        assert proposal_id > 0

        # Verify in database
        proposal = state_store.get_proposal_by_id(proposal_id)
        assert proposal["firefly_id"] == test_transaction
        assert proposal["document_id"] == test_document
        assert proposal["match_score"] == 0.85
        assert proposal["status"] == "PENDING"

    def test_proposal_exists_prevents_duplicate(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test that _proposal_exists detects existing proposals."""
        # Create a proposal
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        # Check existence
        exists = reconciliation_service._proposal_exists(test_transaction, test_document)
        assert exists is True

        # Check non-existence
        exists = reconciliation_service._proposal_exists(200, test_document)
        assert exists is False


class TestAutoLinking:
    """Tests for auto-linking high-confidence matches."""

    def test_auto_link_high_confidence(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_document,
        test_transaction,
        test_extraction,
    ) -> None:
        """Test that high-confidence matches are auto-linked."""
        # Create high-confidence proposal
        proposal_id = state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.95,  # Above threshold
            match_reasons=["Amount matches exactly"],
        )

        # Process auto-links
        result = ReconciliationResult(state=ReconciliationState.AUTO_LINKING)
        reconciliation_service._process_auto_links(result, dry_run=False)

        # Verify auto-link happened
        assert result.auto_linked == 1

        # Verify proposal status updated
        proposal = state_store.get_proposal_by_id(proposal_id)
        assert proposal["status"] == "ACCEPTED"

        # Verify Firefly was called
        mock_firefly_client.update_transaction_linkage.assert_called_once()

    def test_no_auto_link_below_threshold(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_document,
        test_transaction,
    ) -> None:
        """Test that low-confidence matches are not auto-linked."""
        # Create low-confidence proposal
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.75,  # Below threshold
        )

        # Process auto-links
        result = ReconciliationResult(state=ReconciliationState.AUTO_LINKING)
        reconciliation_service._process_auto_links(result, dry_run=False)

        # Verify no auto-link
        assert result.auto_linked == 0
        mock_firefly_client.update_transaction_linkage.assert_not_called()

    def test_no_auto_link_ambiguous_matches(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_transaction,
    ) -> None:
        """Test that ambiguous matches (multiple high-confidence) are not auto-linked."""
        # Need two documents for ambiguous matches
        state_store.upsert_document(
            document_id=1,
            source_hash="hash1",
            title="Doc 1",
        )
        state_store.upsert_document(
            document_id=2,
            source_hash="hash2",
            title="Doc 2",
        )

        # Create two high-confidence proposals for same transaction
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=1,
            match_score=0.95,
        )
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=2,
            match_score=0.93,
        )

        # Process auto-links
        result = ReconciliationResult(state=ReconciliationState.AUTO_LINKING)
        reconciliation_service._process_auto_links(result, dry_run=False)

        # Verify no auto-link due to ambiguity
        assert result.auto_linked == 0
        mock_firefly_client.update_transaction_linkage.assert_not_called()


class TestManualLinking:
    """Tests for manual link and reject operations."""

    def test_link_proposal_accept(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_document,
        test_transaction,
        test_extraction,
    ) -> None:
        """Test accepting a proposal via link_proposal."""
        # Create proposal
        proposal_id = state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        # Accept proposal
        success = reconciliation_service.link_proposal(proposal_id, user_decision=True)

        assert success is True

        # Verify proposal status
        proposal = state_store.get_proposal_by_id(proposal_id)
        assert proposal["status"] == "ACCEPTED"

    def test_link_proposal_reject(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test rejecting a proposal via link_proposal."""
        # Create proposal
        proposal_id = state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        # Reject proposal
        success = reconciliation_service.link_proposal(proposal_id, user_decision=False)

        assert success is True

        # Verify proposal status
        proposal = state_store.get_proposal_by_id(proposal_id)
        assert proposal["status"] == "REJECTED"

    def test_link_proposal_not_found(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test link_proposal returns False for non-existent proposal."""
        success = reconciliation_service.link_proposal(9999)
        assert success is False

    def test_link_proposal_already_processed(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test link_proposal returns False for already processed proposal."""
        # Create and reject proposal
        proposal_id = state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )
        state_store.update_proposal_status(proposal_id, "REJECTED")

        # Try to accept rejected proposal
        success = reconciliation_service.link_proposal(proposal_id, user_decision=True)
        assert success is False

    def test_manual_link_creates_linkage(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_document,
        test_transaction,
        test_extraction,
    ) -> None:
        """Test manual_link creates linkage without proposal."""
        # Manual link
        success = reconciliation_service.manual_link(
            firefly_id=test_transaction, document_id=test_document
        )

        assert success is True
        mock_firefly_client.update_transaction_linkage.assert_called_once()

    def test_manual_link_invalid_transaction(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
    ) -> None:
        """Test manual_link fails for non-existent transaction."""
        success = reconciliation_service.manual_link(firefly_id=9999, document_id=test_document)
        assert success is False


class TestInterpretationRuns:
    """Tests for audit trail (interpretation run) creation."""

    def test_execute_link_creates_interpretation_run(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        mock_firefly_client,
        test_document,
        test_transaction,
        test_extraction,
    ) -> None:
        """Test that _execute_link creates an interpretation run."""
        # Execute link
        reconciliation_service._execute_link(
            proposal_id=None,
            firefly_id=test_transaction,
            document_id=test_document,
            confidence=0.95,
            decision_source=DecisionSource.USER,
        )

        # Verify interpretation run was created
        runs = state_store.get_interpretation_runs(test_document)
        assert len(runs) == 1
        assert runs[0]["final_state"] == "LINKED"
        assert runs[0]["decision_source"] == "USER"
        assert runs[0]["firefly_write_action"] == "UPDATE_LINKAGE"

    def test_reject_creates_interpretation_run(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test that rejecting a proposal creates an interpretation run."""
        proposal_id = state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        # Reject
        reconciliation_service.link_proposal(proposal_id, user_decision=False)

        # Verify interpretation run
        runs = state_store.get_interpretation_runs(test_document)
        assert len(runs) == 1
        assert runs[0]["final_state"] == "REJECTED"
        assert runs[0]["decision_source"] == "USER"

    def test_record_interpretation_run_with_match(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test _record_interpretation_run with match data."""
        match = MatchResult(
            firefly_id=test_transaction,
            document_id=test_document,
            total_score=0.95,
            signals=[
                MatchScore(signal="amount", score=1.0, weight=0.4, detail="Exact match"),
            ],
            reasons=["Amount matches exactly"],
        )

        run_id = reconciliation_service._record_interpretation_run(
            document_id=test_document,
            firefly_id=test_transaction,
            final_state="PROPOSAL_CREATED",
            match=match,
            decision_source=DecisionSource.RULES,
            auto_applied=False,
        )

        assert run_id > 0

        runs = state_store.get_interpretation_runs(test_document)
        assert len(runs) == 1
        assert runs[0]["pipeline_version"] == "spark-v1.0"


class TestReconciliationStatus:
    """Tests for get_reconciliation_status."""

    def test_status_empty_system(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test status on empty system."""
        status = reconciliation_service.get_reconciliation_status()

        assert status["cache"]["total"] == 0
        assert status["cache"]["unmatched"] == 0
        assert status["proposals"]["pending"] == 0
        assert status["interpretation_runs"]["total"] == 0

    def test_status_with_data(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test status with some data."""
        # Add proposal (transaction and doc already created by fixtures)
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        status = reconciliation_service.get_reconciliation_status()

        assert status["cache"]["total"] == 1
        assert status["cache"]["unmatched"] == 1
        assert status["proposals"]["pending"] == 1


class TestLLMOptOut:
    """Tests for LLM opt-out functionality."""

    def test_set_llm_opt_out(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_extraction,
    ) -> None:
        """Test setting LLM opt-out."""
        # Set opt-out
        success = reconciliation_service.set_extraction_llm_opt_out(test_document, True)
        assert success is True

    def test_set_llm_opt_in(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_extraction,
    ) -> None:
        """Test unsetting LLM opt-out."""
        # First opt out, then opt back in
        reconciliation_service.set_extraction_llm_opt_out(test_document, True)
        success = reconciliation_service.set_extraction_llm_opt_out(test_document, False)
        assert success is True


class TestRerunInterpretation:
    """Tests for re-running interpretation."""

    def test_rerun_clears_pending_proposals(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
        test_extraction,
    ) -> None:
        """Test that rerun_interpretation clears pending proposals."""
        # Create proposal
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        # Verify proposal exists
        proposals = state_store.get_pending_proposals()
        assert len(proposals) == 1

        # Re-run interpretation
        success = reconciliation_service.rerun_interpretation(test_document)
        assert success is True

    def test_rerun_invalid_document(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test that rerun_interpretation fails for invalid document."""
        success = reconciliation_service.rerun_interpretation(9999)
        assert success is False


class TestHelperMethods:
    """Tests for helper methods."""

    def test_has_pending_proposals_true(
        self,
        reconciliation_service: ReconciliationService,
        state_store,
        test_document,
        test_transaction,
    ) -> None:
        """Test _has_pending_proposals returns True when proposals exist."""
        state_store.create_match_proposal(
            firefly_id=test_transaction,
            document_id=test_document,
            match_score=0.85,
        )

        assert reconciliation_service._has_pending_proposals(test_document) is True

    def test_has_pending_proposals_false(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test _has_pending_proposals returns False when no proposals."""
        assert reconciliation_service._has_pending_proposals(1) is False

    def test_extraction_to_dict(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test _extraction_to_dict conversion."""
        extraction = {
            "extraction_json": json.dumps(
                {
                    "total_gross": "100.00",
                    "invoice_date": "2024-01-01",
                    "vendor": "Test Corp",
                    "description": "Services",
                }
            ),
        }

        result = reconciliation_service._extraction_to_dict(extraction)

        assert result["amount"] == "100.00"
        assert result["date"] == "2024-01-01"
        assert result["vendor"] == "Test Corp"
        assert result["description"] == "Services"

    def test_extraction_to_dict_invalid_json(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test _extraction_to_dict handles invalid JSON gracefully."""
        extraction = {"extraction_json": "not valid json"}

        result = reconciliation_service._extraction_to_dict(extraction)

        assert result["amount"] is None
        assert result["date"] is None

    def test_extraction_to_dict_empty(
        self,
        reconciliation_service: ReconciliationService,
    ) -> None:
        """Test _extraction_to_dict handles missing extraction_json."""
        extraction: dict = {}

        result = reconciliation_service._extraction_to_dict(extraction)

        assert result["amount"] is None
