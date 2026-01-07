"""
Review workflow management.
"""

import json
from dataclasses import dataclass
from enum import Enum

from ..schemas.finance_extraction import FinanceExtraction
from ..state_store import ExtractionRecord, StateStore


class ReviewDecision(str, Enum):
    """User's decision during review."""

    ACCEPTED = "ACCEPTED"  # Accept as-is
    EDITED = "EDITED"  # Accepted with edits
    REJECTED = "REJECTED"  # Reject (don't import)
    SKIPPED = "SKIPPED"  # Skip for now, review later


@dataclass
class ReviewResult:
    """Result of a review session."""

    decision: ReviewDecision
    extraction: FinanceExtraction
    changes_made: list[str]  # List of fields that were changed


class ReviewWorkflow:
    """
    Manages the review workflow.

    Responsibilities:
    - Queue extractions for review
    - Record decisions
    - Apply user edits
    """

    def __init__(self, store: StateStore):
        """Initialize with state store."""
        self.store = store

    def get_pending_reviews(self) -> list[ExtractionRecord]:
        """Get all extractions pending review."""
        return self.store.get_extractions_for_review()

    def get_extraction(self, extraction_id: int) -> FinanceExtraction | None:
        """Load extraction by ID."""
        records = self.store.get_extractions_for_review()
        for record in records:
            if record.id == extraction_id:
                return FinanceExtraction.from_dict(json.loads(record.extraction_json))
        return None

    def record_decision(
        self,
        extraction_id: int,
        decision: ReviewDecision,
        updated_extraction: FinanceExtraction | None = None,
    ) -> None:
        """
        Record review decision.

        Args:
            extraction_id: ID of the extraction record
            decision: User's decision
            updated_extraction: Updated extraction if edits were made
        """
        updated_json = None
        if updated_extraction:
            updated_json = json.dumps(updated_extraction.to_dict())

        self.store.update_extraction_review(
            extraction_id=extraction_id,
            decision=decision.value,
            updated_json=updated_json,
        )

    def apply_edit(
        self,
        extraction: FinanceExtraction,
        field: str,
        value: str,
    ) -> FinanceExtraction:
        """
        Apply a single field edit to an extraction.

        Args:
            extraction: The extraction to edit
            field: Field name (e.g., "amount", "date", "vendor")
            value: New value

        Returns:
            Updated extraction
        """
        proposal = extraction.proposal

        if field == "amount":
            from decimal import Decimal

            proposal.amount = Decimal(value.replace(",", "."))
        elif field == "date":
            proposal.date = value
        elif field == "description":
            proposal.description = value
        elif field == "vendor" or field == "destination_account":
            proposal.destination_account = value
        elif field == "source_account":
            proposal.source_account = value
        elif field == "category":
            proposal.category = value
        elif field == "currency":
            proposal.currency = value.upper()
        elif field == "invoice_number":
            proposal.invoice_number = value

        # Regenerate external_id if critical fields changed
        if field in ("amount", "date"):
            from ..schemas.dedupe import generate_external_id

            proposal.external_id = generate_external_id(
                document_id=extraction.paperless_document_id,
                source_hash=extraction.source_hash,
                amount=proposal.amount,
                date=proposal.date,
            )

        return extraction
