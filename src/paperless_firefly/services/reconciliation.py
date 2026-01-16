"""Bank reconciliation orchestration service.

This service implements the Phase 4A reconciliation pipeline as specified in Spark v1.0.
It provides a deterministic, idempotent reconciliation process that:
- Synchronizes Firefly state into local cache
- Identifies unlinked transactions using linkage semantics
- Generates match proposals via the Matching Engine
- Persists proposals and confidence metadata
- Automatically links transactions when confidence >= threshold
- Writes linkage markers back to Firefly
- Records an InterpretationRun for every attempt
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from paperless_firefly.matching.engine import MatchingEngine, MatchResult
from paperless_firefly.schemas.linkage import build_linkage_markers
from paperless_firefly.services.firefly_sync import FireflySyncService

if TYPE_CHECKING:
    from paperless_firefly.config import Config
    from paperless_firefly.firefly_client import FireflyClient
    from paperless_firefly.state_store import StateStore

logger = logging.getLogger(__name__)


class ReconciliationState(str, Enum):
    """Possible states for a reconciliation run."""

    SYNCING = "SYNCING"
    MATCHING = "MATCHING"
    PROPOSING = "PROPOSING"
    AUTO_LINKING = "AUTO_LINKING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DecisionSource(str, Enum):
    """Source of the reconciliation decision."""

    RULES = "RULES"
    LLM = "LLM"
    USER = "USER"
    AUTO = "AUTO"


@dataclass
class ReconciliationResult:
    """Result of a reconciliation run."""

    state: ReconciliationState
    transactions_synced: int = 0
    transactions_skipped: int = 0
    proposals_created: int = 0
    proposals_existing: int = 0
    auto_linked: int = 0
    interpretation_runs_created: int = 0
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Return True if reconciliation completed without fatal errors."""
        return self.state == ReconciliationState.COMPLETED


class ReconciliationService:
    """Orchestrates the bank reconciliation pipeline.

    This service is safe to run repeatedly (idempotent):
    - Skips already-linked transactions
    - Skips documents with existing pending proposals
    - Never creates duplicate proposals
    - Never auto-links ambiguous matches
    - Always writes linkage markers when linking
    - Always creates InterpretationRun records

    Usage:
        service = ReconciliationService(firefly_client, state_store, config)
        result = service.run_reconciliation()
    """

    PIPELINE_VERSION = "spark-v1.0"

    def __init__(
        self,
        firefly_client: FireflyClient,
        state_store: StateStore,
        config: Config,
        user_id: int | None = None,
    ) -> None:
        """Initialize the reconciliation service.

        Args:
            firefly_client: Client for Firefly III API.
            state_store: State store for persistence.
            config: Application configuration.
            user_id: User ID to filter transactions/documents by for multi-user isolation.
                     If None, operates on all records (superuser mode).
        """
        self.firefly = firefly_client
        self.store = state_store
        self.config = config
        self.user_id = user_id

        # Initialize sub-services
        self.sync_service = FireflySyncService(firefly_client, state_store, config)
        self.matching_engine = MatchingEngine(state_store, config)

        # Configuration
        self.auto_match_threshold = config.reconciliation.auto_match_threshold
        self.date_tolerance_days = config.reconciliation.date_tolerance_days
        # Bank-first mode configuration (SSOT)
        self.bank_first_mode = config.reconciliation.bank_first_mode
        self.require_manual_confirmation = config.reconciliation.require_manual_confirmation_for_new

    def run_reconciliation(
        self,
        full_sync: bool = False,
        dry_run: bool = False,
        skip_sync: bool = False,
    ) -> ReconciliationResult:
        """Run the full reconciliation pipeline.

        This is the main entry point for reconciliation. It:
        1. Syncs Firefly transactions to local cache (unless skip_sync=True)
        2. Matches unlinked transactions against Paperless documents
        3. Creates proposals for potential matches
        4. Auto-links high-confidence matches
        5. Records all decisions in interpretation_runs

        Bank-First Mode (default):
        - Documents are matched against existing Firefly transactions first
        - New transactions are only created with explicit user confirmation
        - Prevents accidental duplicate transactions

        Args:
            full_sync: If True, clear cache and sync all transactions.
            dry_run: If True, don't write to Firefly or create proposals.
            skip_sync: If True, skip fetching data from Firefly/Paperless APIs.
                       Use this when you want to match only against already-cached data.

        Returns:
            ReconciliationResult with statistics and status.
        """
        start_time = time.time()
        result = ReconciliationResult(state=ReconciliationState.SYNCING)

        try:
            # Phase 1: Sync Firefly transactions (unless skipped)
            if skip_sync:
                logger.info("Skipping sync phase (skip_sync=True) - using cached data only")
            else:
                logger.info("Starting reconciliation - Phase 1: Sync")
                sync_result = self.sync_service.sync_transactions(full_sync=full_sync)
                result.transactions_synced = sync_result.transactions_synced
                result.transactions_skipped = sync_result.transactions_skipped

                if sync_result.errors:
                    result.errors.extend(sync_result.errors)

            # Phase 2: Match and propose
            result.state = ReconciliationState.MATCHING
            logger.info("Reconciliation - Phase 2: Matching")
            self._process_unmatched_transactions(result, dry_run)

            # Phase 3: Auto-link high-confidence matches
            result.state = ReconciliationState.AUTO_LINKING
            logger.info("Reconciliation - Phase 3: Auto-linking")
            self._process_auto_links(result, dry_run)

            result.state = ReconciliationState.COMPLETED
            logger.info(
                "Reconciliation completed: %d synced, %d proposals, %d auto-linked",
                result.transactions_synced,
                result.proposals_created,
                result.auto_linked,
            )

        except Exception as e:
            logger.exception("Reconciliation failed: %s", e)
            result.state = ReconciliationState.FAILED
            result.errors.append(f"Fatal error: {e}")

        result.duration_ms = int((time.time() - start_time) * 1000)
        return result

    def _process_unmatched_transactions(
        self,
        result: ReconciliationResult,
        dry_run: bool,
    ) -> None:
        """Process unmatched transactions and create proposals.

        For each unmatched cached Firefly transaction, finds matching
        Paperless extractions and creates proposals.

        Bank-First Decision Order (SSOT):
        1. If document already linked → update existing (never create new)
        2. If match found → propose link / auto-link
        3. If no match + bank_first_mode → require manual confirmation
        4. Only create new if explicitly marked as manual transaction

        Args:
            result: ReconciliationResult to update.
            dry_run: If True, don't persist proposals.
        """
        # Get all extractions that are approved and not yet imported
        extractions = self._get_eligible_extractions()
        logger.debug("Found %d eligible extractions for matching", len(extractions))

        for extraction in extractions:
            document_id = extraction["document_id"]

            # Step 1: Check if document is ALREADY linked (SSOT)
            existing_link = self._get_existing_link(document_id)
            if existing_link:
                logger.debug(
                    "Document %d already linked to Firefly tx %d, skipping match",
                    document_id,
                    existing_link,
                )
                result.proposals_existing += 1
                continue

            # Skip if already has pending proposals
            if self._has_pending_proposals(document_id):
                result.proposals_existing += 1
                continue

            # Find matches using the matching engine (filtered by user_id)
            matches = self.matching_engine.find_matches(
                document_id=document_id,
                extraction=self._extraction_to_dict(extraction),
                user_id=self.user_id,
            )

            if not matches:
                continue

            # Create proposals for matches (if not dry run)
            for match in matches:
                if dry_run:
                    result.proposals_created += 1
                    continue

                # Check if proposal already exists for this pair
                if self._proposal_exists(match.firefly_id, document_id):
                    result.proposals_existing += 1
                    continue

                proposal_id = self._create_proposal(match)
                if proposal_id:
                    result.proposals_created += 1

                    # Record interpretation run for proposal creation
                    self._record_interpretation_run(
                        document_id=document_id,
                        firefly_id=match.firefly_id,
                        final_state="PROPOSAL_CREATED",
                        match=match,
                        decision_source=DecisionSource.RULES,
                        auto_applied=False,
                    )
                    result.interpretation_runs_created += 1

    def _process_auto_links(
        self,
        result: ReconciliationResult,
        dry_run: bool,
    ) -> None:
        """Process proposals eligible for auto-linking.

        Auto-links proposals where:
        - Confidence >= auto_match_threshold
        - No other proposals for the same transaction/document with similar confidence
        - Transaction is not already linked

        Args:
            result: ReconciliationResult to update.
            dry_run: If True, don't write to Firefly.
        """
        pending_proposals = self.store.get_pending_proposals()

        # Group proposals by firefly_id to detect ambiguous matches
        proposals_by_tx: dict[int, list[dict]] = {}
        for proposal in pending_proposals:
            tx_id = proposal["firefly_id"]
            if tx_id not in proposals_by_tx:
                proposals_by_tx[tx_id] = []
            proposals_by_tx[tx_id].append(proposal)

        for firefly_id, proposals in proposals_by_tx.items():
            # Skip if ambiguous (multiple proposals for same transaction)
            high_confidence = [
                p for p in proposals if p["match_score"] >= self.auto_match_threshold
            ]

            if len(high_confidence) == 0:
                # No proposal meets threshold
                continue

            if len(high_confidence) > 1:
                # Ambiguous: multiple high-confidence matches
                logger.info(
                    "Skipping auto-link for tx %d: %d ambiguous high-confidence proposals",
                    firefly_id,
                    len(high_confidence),
                )
                continue

            # Single high-confidence match - auto-link it
            proposal = high_confidence[0]
            document_id = proposal["document_id"]

            if dry_run:
                result.auto_linked += 1
                continue

            success = self._execute_link(
                proposal_id=proposal["id"],
                firefly_id=firefly_id,
                document_id=document_id,
                confidence=proposal["match_score"],
            )

            if success:
                result.auto_linked += 1
                result.interpretation_runs_created += 1

    def link_proposal(
        self,
        proposal_id: int,
        user_decision: bool = True,
    ) -> bool:
        """Manually link or reject a proposal (called from UI).

        Args:
            proposal_id: The proposal to process.
            user_decision: True to accept, False to reject.

        Returns:
            True if operation succeeded.
        """
        proposal = self.store.get_proposal_by_id(proposal_id)
        if not proposal:
            logger.error("Proposal %d not found", proposal_id)
            return False

        if proposal["status"] != "PENDING":
            logger.warning(
                "Proposal %d is not pending (status=%s)", proposal_id, proposal["status"]
            )
            return False

        if user_decision:
            # Accept - execute link
            return self._execute_link(
                proposal_id=proposal_id,
                firefly_id=proposal["firefly_id"],
                document_id=proposal["document_id"],
                confidence=proposal["match_score"],
                decision_source=DecisionSource.USER,
            )
        else:
            # Reject
            self.store.update_proposal_status(proposal_id, "REJECTED")
            self._record_interpretation_run(
                document_id=proposal["document_id"],
                firefly_id=proposal["firefly_id"],
                final_state="REJECTED",
                decision_source=DecisionSource.USER,
                auto_applied=False,
            )
            return True

    def manual_link(
        self,
        firefly_id: int,
        document_id: int,
    ) -> bool:
        """Manually link a transaction to a document (user override).

        Creates a linkage even when no proposal exists.

        Args:
            firefly_id: Firefly transaction ID.
            document_id: Paperless document ID.

        Returns:
            True if link was created successfully.
        """
        # Verify transaction exists in cache
        tx = self.store.get_firefly_cache_entry(firefly_id)
        if not tx:
            logger.error("Transaction %d not in cache", firefly_id)
            return False

        # Verify document exists
        if not self.store.document_exists(document_id):
            logger.error("Document %d not found", document_id)
            return False

        return self._execute_link(
            proposal_id=None,
            firefly_id=firefly_id,
            document_id=document_id,
            confidence=1.0,  # Manual link = full confidence
            decision_source=DecisionSource.USER,
        )

    def _execute_link(
        self,
        proposal_id: int | None,
        firefly_id: int,
        document_id: int,
        confidence: float,
        decision_source: DecisionSource = DecisionSource.AUTO,
    ) -> bool:
        """Execute a link between a Firefly transaction and Paperless document.

        This is the core linking function that:
        1. Writes linkage markers to Firefly
        2. Updates local cache status
        3. Updates proposal status (if applicable)
        4. Records interpretation run

        Args:
            proposal_id: Optional proposal ID that initiated this link.
            firefly_id: Firefly transaction ID.
            document_id: Paperless document ID.
            confidence: Match confidence score.
            decision_source: Source of the decision.

        Returns:
            True if link was created successfully.
        """
        start_time = time.time()

        try:
            # Get external_id from extraction
            extraction = self.store.get_extraction_by_document(document_id)
            external_id = extraction.external_id if extraction else f"plf-{document_id}"

            # Step 1: Build linkage markers
            markers = build_linkage_markers(document_id, external_id)

            # Step 2: Write linkage markers to Firefly
            logger.info(
                "Writing linkage markers to Firefly tx %d for doc %d",
                firefly_id,
                document_id,
            )
            success = self.firefly.update_transaction_linkage(
                transaction_id=firefly_id,
                external_id=markers.external_id,
                internal_reference=markers.internal_reference,
                notes_to_append=markers.notes_marker,
            )

            if not success:
                logger.error(
                    "Failed to write linkage markers to Firefly for tx %d",
                    firefly_id,
                )
                self._record_interpretation_run(
                    document_id=document_id,
                    firefly_id=firefly_id,
                    final_state="LINKAGE_WRITE_FAILED",
                    decision_source=decision_source,
                    auto_applied=False,
                    duration_ms=int((time.time() - start_time) * 1000),
                )
                return False

            # Step 3: Update local cache status
            self.store.update_firefly_match_status(
                firefly_id=firefly_id,
                status="MATCHED",
                document_id=document_id,
                confidence=confidence,
            )

            # Step 4: Update proposal status (if applicable)
            if proposal_id:
                self.store.update_proposal_status(proposal_id, "ACCEPTED")

            # Step 5: Record interpretation run
            duration_ms = int((time.time() - start_time) * 1000)
            markers_dict = {
                "external_id": markers.external_id,
                "internal_reference": markers.internal_reference,
                "notes_marker": markers.notes_marker,
            }
            self._record_interpretation_run(
                document_id=document_id,
                firefly_id=firefly_id,
                final_state="LINKED",
                decision_source=decision_source,
                auto_applied=(decision_source == DecisionSource.AUTO),
                duration_ms=duration_ms,
                linkage_marker_written=markers_dict,
                firefly_write_action="UPDATE_LINKAGE",
                firefly_target_id=firefly_id,
            )

            logger.info(
                "Successfully linked tx %d to doc %d (source=%s, confidence=%.2f)",
                firefly_id,
                document_id,
                decision_source.value,
                confidence,
            )
            return True

        except Exception as e:
            logger.exception("Error executing link for tx %d: %s", firefly_id, e)
            self._record_interpretation_run(
                document_id=document_id,
                firefly_id=firefly_id,
                final_state="LINK_ERROR",
                decision_source=decision_source,
                auto_applied=False,
            )
            return False

    def _create_proposal(self, match: MatchResult) -> int | None:
        """Create a match proposal in the database.

        Args:
            match: MatchResult from the matching engine.

        Returns:
            Proposal ID or None if creation failed.
        """
        try:
            return self.store.create_match_proposal(
                firefly_id=match.firefly_id,
                document_id=match.document_id,
                match_score=match.total_score,
                match_reasons=match.reasons,
            )
        except Exception as e:
            logger.exception("Failed to create proposal: %s", e)
            return None

    def _record_interpretation_run(
        self,
        document_id: int,
        firefly_id: int | None,
        final_state: str,
        decision_source: DecisionSource,
        auto_applied: bool,
        match: MatchResult | None = None,
        duration_ms: int | None = None,
        linkage_marker_written: dict | None = None,
        firefly_write_action: str | None = None,
        firefly_target_id: int | None = None,
        user_id: int | None = None,
    ) -> int:
        """Record an interpretation run for audit purposes.

        Every reconciliation attempt MUST create an interpretation run.
        Interpretation runs are strictly private - each user can only see
        their own AI/interpretation data.

        Args:
            document_id: Paperless document ID.
            firefly_id: Optional Firefly transaction ID.
            final_state: Final state of the run.
            decision_source: Source of the decision.
            auto_applied: Whether this was auto-applied.
            match: Optional match result for inputs summary.
            duration_ms: Optional duration in milliseconds.
            linkage_marker_written: Optional linkage markers that were written.
            firefly_write_action: Optional action taken on Firefly.
            firefly_target_id: Optional Firefly target ID.
            user_id: Owner user ID for privacy isolation.

        Returns:
            The created run ID.
        """
        inputs_summary: dict = {}
        rules_applied = None

        if match:
            inputs_summary = {
                "firefly_id": match.firefly_id,
                "document_id": match.document_id,
                "total_score": match.total_score,
            }
            rules_applied = [
                {"signal": s.signal, "score": s.score, "weight": s.weight} for s in match.signals
            ]

        # Get external_id from extraction if available
        extraction = self.store.get_extraction_by_document(document_id)
        external_id = extraction.external_id if extraction else None

        return self.store.create_interpretation_run(
            document_id=document_id,
            firefly_id=firefly_id,
            external_id=external_id,
            pipeline_version=self.PIPELINE_VERSION,
            inputs_summary=inputs_summary,
            final_state=final_state,
            duration_ms=duration_ms,
            algorithm_version=self.PIPELINE_VERSION,
            rules_applied=rules_applied,
            auto_applied=auto_applied,
            decision_source=decision_source.value,
            firefly_write_action=firefly_write_action,
            firefly_target_id=firefly_target_id,
            linkage_marker_written=linkage_marker_written,
            user_id=user_id,
        )

    def _get_eligible_extractions(self) -> list[dict]:
        """Get extractions eligible for reconciliation.

        Returns extractions that are:
        - Approved (review_decision = APPROVED)
        - Not yet imported to Firefly (or import failed)
        - Not opted out of LLM

        Returns:
            List of extraction records.
        """
        # Get approved extractions that haven't been fully processed
        with self.store._transaction() as conn:
            rows = conn.execute(
                """
                SELECT e.* FROM extractions e
                LEFT JOIN imports i ON e.document_id = i.document_id
                WHERE e.review_decision = 'APPROVED'
                AND (i.status IS NULL OR i.status != 'IMPORTED')
                AND (e.llm_opt_out IS NULL OR e.llm_opt_out = 0)
                ORDER BY e.created_at ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def _get_existing_link(self, document_id: int) -> int | None:
        """Check if document is already linked to a Firefly transaction.

        Checks multiple linkage sources (SSOT):
        1. Local cache match_status
        2. Firefly external_id lookup
        3. Firefly internal_reference lookup

        Args:
            document_id: Paperless document ID

        Returns:
            Firefly transaction ID if linked, None otherwise
        """
        # Check local cache first
        with self.store._transaction() as conn:
            row = conn.execute(
                """
                SELECT firefly_id FROM firefly_cache
                WHERE matched_document_id = ? AND match_status = 'MATCHED'
                """,
                (document_id,),
            ).fetchone()
            if row:
                return row["firefly_id"]

        # Check external_id lookup via Firefly API
        external_id_pattern = f"paperless:{document_id}:"
        try:
            # Search in Firefly cache for matching external_id prefix
            with self.store._transaction() as conn:
                row = conn.execute(
                    """
                    SELECT firefly_id FROM firefly_cache
                    WHERE external_id LIKE ?
                    """,
                    (external_id_pattern + "%",),
                ).fetchone()
                if row:
                    return row["firefly_id"]
        except Exception as e:
            logger.debug("External ID lookup failed: %s", e)

        return None

    def _has_pending_proposals(self, document_id: int) -> bool:
        """Check if document has pending proposals.

        Args:
            document_id: Paperless document ID.

        Returns:
            True if document has pending proposals.
        """
        with self.store._transaction() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as count FROM match_proposals
                WHERE document_id = ? AND status = 'PENDING'
                """,
                (document_id,),
            ).fetchone()
            return row["count"] > 0 if row else False

    def can_create_new_transaction(
        self, document_id: int, is_manual: bool = False
    ) -> tuple[bool, str]:
        """Check if a new Firefly transaction can be created for a document.

        Bank-First Mode Rules (SSOT):
        - If document already linked → False (update instead)
        - If matches exist → False (link to existing)
        - If bank_first_mode + require_confirmation → False unless is_manual
        - Otherwise → True

        Args:
            document_id: Paperless document ID
            is_manual: Whether this is explicitly marked as a manual transaction

        Returns:
            Tuple of (can_create, reason_if_not)
        """
        # Check if already linked
        existing = self._get_existing_link(document_id)
        if existing:
            return False, f"Document already linked to Firefly transaction {existing}"

        # Check if there are pending proposals
        if self._has_pending_proposals(document_id):
            return False, "Document has pending match proposals - review those first"

        # If manual transaction, always allow
        if is_manual:
            return True, "Manual transaction confirmed"

        # In bank-first mode, require explicit confirmation
        if self.bank_first_mode and self.require_manual_confirmation:
            return False, (
                "Bank-first mode: No matching bank transaction found. "
                "Please confirm this is a manual/cash transaction before creating."
            )

        return True, "No existing match, creating new transaction"

    def create_manual_transaction(
        self,
        document_id: int,
        is_cash: bool = False,
        notes: str | None = None,
    ) -> tuple[bool, str | int]:
        """Create a new Firefly transaction for a document without a bank booking.

        This is the explicit "manual/cash transaction" flow for:
        - Cash purchases without bank record
        - Manual entries that won't match any bank import

        Args:
            document_id: Paperless document ID
            is_cash: Whether this is a cash transaction
            notes: Optional notes explaining why this is manual

        Returns:
            Tuple of (success, firefly_id_or_error)
        """
        # Verify document exists and is approved
        extraction = self.store.get_extraction_by_document(document_id)
        if not extraction:
            return False, "Document extraction not found"

        # Double-check we're not duplicating
        existing = self._get_existing_link(document_id)
        if existing:
            return False, f"Document already linked to transaction {existing}"

        try:
            # Build payload (with or without splits)
            from paperless_firefly.schemas.firefly_payload import build_firefly_payload_with_splits

            # Reconstruct FinanceExtraction from stored JSON
            extraction_obj = self._load_extraction_object(document_id)
            if not extraction_obj:
                return False, "Failed to load extraction data"

            # Add manual transaction marker to notes
            manual_notes = "MANUAL TRANSACTION - "
            if is_cash:
                manual_notes += "Cash payment without bank booking. "
            else:
                manual_notes += "No matching bank transaction. "
            if notes:
                manual_notes += notes

            extraction_obj.proposal.notes = (
                (extraction_obj.proposal.notes or "") + " " + manual_notes
            )

            # Build payload
            paperless_external_url = self.config.paperless.get_external_url()
            payload = build_firefly_payload_with_splits(
                extraction=extraction_obj,
                default_source_account=self.config.firefly.default_source_account,
                paperless_external_url=paperless_external_url,
            )

            # Create transaction in Firefly
            result = self.firefly.create_transaction(payload.to_dict())

            if result and result.get("id"):
                firefly_id = int(result["id"])

                # Record interpretation run
                self._record_interpretation_run(
                    document_id=document_id,
                    firefly_id=firefly_id,
                    final_state="MANUAL_CREATED",
                    decision_source=DecisionSource.USER,
                    auto_applied=False,
                    firefly_write_action="CREATE_MANUAL",
                    firefly_target_id=firefly_id,
                )

                logger.info("Created manual transaction %d for doc %d", firefly_id, document_id)
                return True, firefly_id

            return False, "Failed to create transaction in Firefly"

        except Exception as e:
            logger.exception("Error creating manual transaction for doc %d: %s", document_id, e)
            return False, str(e)

    def _load_extraction_object(self, document_id: int):
        """Load a FinanceExtraction object from stored JSON.

        Args:
            document_id: Paperless document ID

        Returns:
            FinanceExtraction object or None
        """
        from paperless_firefly.schemas.finance_extraction import FinanceExtraction

        record = self.store.get_extraction_by_document(document_id)
        if not record or not record.extraction_json:
            return None

        try:
            data = json.loads(record.extraction_json)
            return FinanceExtraction.from_dict(data)
        except Exception as e:
            logger.error("Failed to parse extraction JSON for doc %d: %s", document_id, e)
            return None

    def _proposal_exists(self, firefly_id: int, document_id: int) -> bool:
        """Check if a proposal already exists for this tx/doc pair.

        Args:
            firefly_id: Firefly transaction ID.
            document_id: Paperless document ID.

        Returns:
            True if proposal exists (any status).
        """
        with self.store._transaction() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as count FROM match_proposals
                WHERE firefly_id = ? AND document_id = ?
                """,
                (firefly_id, document_id),
            ).fetchone()
            return row["count"] > 0 if row else False

    def _extraction_to_dict(self, extraction: dict) -> dict:
        """Convert extraction record to dict for matching engine.

        Args:
            extraction: Extraction record from database.

        Returns:
            Dict with amount, date, vendor, description fields.
        """
        extraction_data: dict = {}
        if extraction.get("extraction_json"):
            try:
                extraction_data = json.loads(extraction["extraction_json"])
            except json.JSONDecodeError:
                pass

        return {
            "amount": extraction_data.get("total_gross") or extraction_data.get("amount"),
            "date": extraction_data.get("date") or extraction_data.get("invoice_date"),
            "vendor": extraction_data.get("vendor") or extraction_data.get("correspondent"),
            "description": extraction_data.get("description", ""),
        }

    def get_reconciliation_status(self) -> dict:
        """Get current reconciliation status and statistics.

        Returns:
            Dict with cache stats, proposal stats, and link stats.
        """
        with self.store._transaction() as conn:
            # Cache stats
            cached = conn.execute("SELECT COUNT(*) as count FROM firefly_cache").fetchone()
            unmatched = conn.execute(
                "SELECT COUNT(*) as count FROM firefly_cache WHERE match_status = 'UNMATCHED'"
            ).fetchone()
            matched = conn.execute(
                "SELECT COUNT(*) as count FROM firefly_cache WHERE match_status = 'MATCHED'"
            ).fetchone()

            # Proposal stats
            pending = conn.execute(
                "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'PENDING'"
            ).fetchone()
            accepted = conn.execute(
                "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'ACCEPTED'"
            ).fetchone()
            rejected = conn.execute(
                "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'REJECTED'"
            ).fetchone()
            auto_matched = conn.execute(
                "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'AUTO_MATCHED'"
            ).fetchone()

            # Interpretation runs
            total_runs = conn.execute(
                "SELECT COUNT(*) as count FROM interpretation_runs"
            ).fetchone()
            auto_runs = conn.execute(
                "SELECT COUNT(*) as count FROM interpretation_runs WHERE auto_applied = 1"
            ).fetchone()

            return {
                "cache": {
                    "total": cached["count"] if cached else 0,
                    "unmatched": unmatched["count"] if unmatched else 0,
                    "matched": matched["count"] if matched else 0,
                },
                "proposals": {
                    "pending": pending["count"] if pending else 0,
                    "accepted": accepted["count"] if accepted else 0,
                    "rejected": rejected["count"] if rejected else 0,
                    "auto_matched": auto_matched["count"] if auto_matched else 0,
                },
                "interpretation_runs": {
                    "total": total_runs["count"] if total_runs else 0,
                    "auto_applied": auto_runs["count"] if auto_runs else 0,
                },
            }

    def set_extraction_llm_opt_out(self, document_id: int, opt_out: bool) -> bool:
        """Set LLM opt-out status for an extraction.

        Args:
            document_id: Paperless document ID.
            opt_out: True to opt out of LLM processing.

        Returns:
            True if update succeeded.
        """
        try:
            with self.store._transaction() as conn:
                conn.execute(
                    "UPDATE extractions SET llm_opt_out = ? WHERE document_id = ?",
                    (1 if opt_out else 0, document_id),
                )
            return True
        except Exception as e:
            logger.exception("Failed to set LLM opt-out for doc %d: %s", document_id, e)
            return False

    def rerun_interpretation(self, document_id: int) -> bool:
        """Re-run interpretation for a document.

        This clears any pending proposals and creates new ones based on
        current matching rules.

        Args:
            document_id: Paperless document ID.

        Returns:
            True if re-run succeeded.
        """
        try:
            # Get current extraction (returns ExtractionRecord or None)
            extraction = self.store.get_extraction_by_document(document_id)
            if not extraction:
                logger.error("No extraction found for doc %d", document_id)
                return False

            # Convert ExtractionRecord to dict for matching
            extraction_dict = {
                "external_id": extraction.external_id,
                "extraction_json": extraction.extraction_json,
            }

            # Clear pending proposals for this document
            with self.store._transaction() as conn:
                conn.execute(
                    "DELETE FROM match_proposals WHERE document_id = ? AND status = 'PENDING'",
                    (document_id,),
                )

            # Reset cache entries that were matched to this document
            with self.store._transaction() as conn:
                conn.execute(
                    """
                    UPDATE firefly_cache
                    SET match_status = 'UNMATCHED', matched_document_id = NULL, match_confidence = NULL
                    WHERE matched_document_id = ?
                    """,
                    (document_id,),
                )

            # Find new matches (filtered by user_id)
            matches = self.matching_engine.find_matches(
                document_id=document_id,
                extraction=self._extraction_to_dict(extraction_dict),
                user_id=self.user_id,
            )

            # Create new proposals
            for match in matches:
                self._create_proposal(match)
                self._record_interpretation_run(
                    document_id=document_id,
                    firefly_id=match.firefly_id,
                    final_state="PROPOSAL_CREATED",
                    match=match,
                    decision_source=DecisionSource.RULES,
                    auto_applied=False,
                )

            logger.info(
                "Re-ran interpretation for doc %d: created %d proposals",
                document_id,
                len(matches),
            )
            return True

        except Exception as e:
            logger.exception("Failed to re-run interpretation for doc %d: %s", document_id, e)
            return False
