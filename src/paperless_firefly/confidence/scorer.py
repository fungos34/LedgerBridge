"""
Confidence scoring implementation.
"""

from dataclasses import dataclass
from typing import Optional

from ..schemas.finance_extraction import (
    ConfidenceScores,
    FinanceExtraction,
    ReviewState,
)


@dataclass
class ConfidenceThresholds:
    """Configurable thresholds for review state determination."""

    auto_threshold: float = 0.85  # Above this: AUTO import
    review_threshold: float = 0.60  # Above this: REVIEW, below: MANUAL

    # Minimum field confidences for AUTO
    min_amount_confidence: float = 0.7
    min_date_confidence: float = 0.6
    min_vendor_confidence: float = 0.4


class ConfidenceScorer:
    """
    Computes and validates confidence scores.

    Confidence sources (in order of trust):
    1. Embedded XML (Factur-X, UBL): 0.95-1.0
    2. PDF text layer: 0.7-0.9
    3. OCR heuristics: 0.3-0.7
    """

    # Base confidence by extraction strategy
    STRATEGY_BASE_CONFIDENCE = {
        "factur_x": 0.95,
        "ubl": 0.95,
        "zugferd": 0.95,
        "pdf_text": 0.75,
        "ocr_heuristic": 0.50,
        "fallback": 0.20,
        "none": 0.10,
    }

    def __init__(self, thresholds: Optional[ConfidenceThresholds] = None):
        """Initialize scorer with thresholds."""
        self.thresholds = thresholds or ConfidenceThresholds()

    def compute_review_state(self, scores: ConfidenceScores) -> ReviewState:
        """
        Compute review state from confidence scores.

        Rules:
        - AUTO: Overall >= auto_threshold AND all critical fields above minimums
        - REVIEW: Overall >= review_threshold
        - MANUAL: Otherwise
        """
        # Check critical field minimums for AUTO
        critical_fields_ok = (
            scores.amount >= self.thresholds.min_amount_confidence
            and scores.date >= self.thresholds.min_date_confidence
        )

        if scores.overall >= self.thresholds.auto_threshold and critical_fields_ok:
            return ReviewState.AUTO
        elif scores.overall >= self.thresholds.review_threshold:
            return ReviewState.REVIEW
        else:
            return ReviewState.MANUAL

    def adjust_for_strategy(
        self,
        scores: ConfidenceScores,
        strategy: str,
    ) -> ConfidenceScores:
        """
        Adjust confidence scores based on extraction strategy.

        Different strategies have inherent reliability differences.
        """
        base = self.STRATEGY_BASE_CONFIDENCE.get(strategy, 0.30)

        # Apply strategy multiplier
        multiplier = base / 0.50  # Normalize around OCR baseline

        new_scores = ConfidenceScores(
            overall=min(1.0, scores.overall * multiplier),
            amount=min(1.0, scores.amount * multiplier),
            date=min(1.0, scores.date * multiplier),
            currency=min(1.0, scores.currency * multiplier),
            description=min(1.0, scores.description * multiplier),
            vendor=min(1.0, scores.vendor * multiplier),
            invoice_number=min(1.0, scores.invoice_number * multiplier),
            line_items=min(1.0, scores.line_items * multiplier),
            auto_threshold=self.thresholds.auto_threshold,
            review_threshold=self.thresholds.review_threshold,
        )

        new_scores.review_state = self.compute_review_state(new_scores)
        return new_scores

    def validate_extraction(self, extraction: FinanceExtraction) -> list[str]:
        """
        Validate extraction and return list of issues.

        Used to flag problems that should lower confidence or
        require manual review.
        """
        issues = []

        proposal = extraction.proposal

        # Required field checks
        if not proposal.amount or proposal.amount <= 0:
            issues.append("Amount is missing or invalid")

        if not proposal.date:
            issues.append("Date is missing")
        elif not self._is_valid_date(proposal.date):
            issues.append(f"Date format invalid: {proposal.date}")

        if not proposal.description:
            issues.append("Description is missing")

        if not proposal.external_id:
            issues.append("external_id is missing")

        # Sanity checks
        if proposal.amount and proposal.amount > 100000:
            issues.append(f"Amount unusually large: {proposal.amount}")

        if not proposal.currency:
            issues.append("Currency is missing")

        # Confidence consistency
        if extraction.confidence.overall > 0.9 and extraction.confidence.amount < 0.5:
            issues.append("Overall confidence inconsistent with amount confidence")

        return issues

    def _is_valid_date(self, date_str: str) -> bool:
        """Check if date string is valid YYYY-MM-DD format."""
        if not date_str or len(date_str) != 10:
            return False

        try:
            year = int(date_str[0:4])
            month = int(date_str[5:7])
            day = int(date_str[8:10])

            return (
                date_str[4] == "-"
                and date_str[7] == "-"
                and 1900 <= year <= 2100
                and 1 <= month <= 12
                and 1 <= day <= 31
            )
        except (ValueError, IndexError):
            return False
