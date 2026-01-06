"""Tests for confidence scoring."""

from decimal import Decimal

import pytest

from paperless_firefly.confidence import ConfidenceScorer, ConfidenceThresholds
from paperless_firefly.schemas.finance_extraction import (
    ConfidenceScores,
    FinanceExtraction,
    Provenance,
    ReviewState,
    TransactionProposal,
    TransactionType,
)


class TestConfidenceScorer:
    """Tests for confidence scorer."""

    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    @pytest.fixture
    def custom_scorer(self):
        return ConfidenceScorer(
            thresholds=ConfidenceThresholds(
                auto_threshold=0.90,
                review_threshold=0.70,
            )
        )

    def test_auto_review_state(self, scorer):
        """High confidence results in AUTO state."""
        scores = ConfidenceScores(
            overall=0.90,
            amount=0.85,
            date=0.80,
        )

        state = scorer.compute_review_state(scores)

        assert state == ReviewState.AUTO

    def test_review_state(self, scorer):
        """Medium confidence results in REVIEW state."""
        scores = ConfidenceScores(
            overall=0.70,
            amount=0.75,
            date=0.65,
        )

        state = scorer.compute_review_state(scores)

        assert state == ReviewState.REVIEW

    def test_manual_state(self, scorer):
        """Low confidence results in MANUAL state."""
        scores = ConfidenceScores(
            overall=0.40,
            amount=0.45,
            date=0.35,
        )

        state = scorer.compute_review_state(scores)

        assert state == ReviewState.MANUAL

    def test_custom_thresholds(self, custom_scorer):
        """Custom thresholds are respected."""
        scores = ConfidenceScores(
            overall=0.85,  # Below custom auto threshold
            amount=0.75,
            date=0.70,
        )

        state = custom_scorer.compute_review_state(scores)

        # With custom threshold 0.90, this should be REVIEW not AUTO
        assert state == ReviewState.REVIEW

    def test_critical_fields_required_for_auto(self, scorer):
        """Auto requires minimum confidence on critical fields."""
        scores = ConfidenceScores(
            overall=0.90,
            amount=0.50,  # Below minimum
            date=0.80,
        )

        state = scorer.compute_review_state(scores)

        # Despite high overall, low amount confidence prevents AUTO
        assert state == ReviewState.REVIEW


class TestConfidenceStrategyAdjustment:
    """Tests for strategy-based confidence adjustment."""

    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    def test_ocr_base_confidence(self, scorer):
        """OCR strategy has base confidence applied."""
        scores = ConfidenceScores(
            overall=0.80,
            amount=0.80,
            date=0.80,
        )

        adjusted = scorer.adjust_for_strategy(scores, "ocr_heuristic")

        # OCR has 0.50 base, same as normalization baseline
        # So scores should stay roughly the same
        assert adjusted.overall == pytest.approx(0.80, abs=0.1)

    def test_factur_x_high_confidence(self, scorer):
        """Factur-X strategy gets confidence boost."""
        scores = ConfidenceScores(
            overall=0.60,
            amount=0.60,
            date=0.60,
        )

        adjusted = scorer.adjust_for_strategy(scores, "factur_x")

        # Factur-X has 0.95 base, should boost confidence
        assert adjusted.overall > scores.overall

    def test_fallback_low_confidence(self, scorer):
        """Fallback strategy has lower confidence."""
        scores = ConfidenceScores(
            overall=0.80,
            amount=0.80,
            date=0.80,
        )

        adjusted = scorer.adjust_for_strategy(scores, "fallback")

        # Fallback has 0.20 base, should reduce confidence
        assert adjusted.overall < scores.overall


class TestExtractionValidation:
    """Tests for extraction validation."""

    @pytest.fixture
    def scorer(self):
        return ConfidenceScorer()

    def create_extraction(self, **overrides) -> FinanceExtraction:
        """Create test extraction with optional overrides."""
        proposal_defaults = {
            "transaction_type": TransactionType.WITHDRAWAL,
            "date": "2024-11-18",
            "amount": Decimal("35.70"),
            "currency": "EUR",
            "description": "Test transaction",
            "external_id": "paperless:123:abc:35.70:2024-11-18",
        }
        proposal_defaults.update(overrides.get("proposal", {}))

        return FinanceExtraction(
            paperless_document_id=123,
            source_hash="a" * 64,
            paperless_url="http://localhost/docs/123/",
            raw_text="Test",
            proposal=TransactionProposal(**proposal_defaults),
            confidence=ConfidenceScores(overall=0.75, amount=0.80, date=0.80),
            provenance=Provenance(),
        )

    def test_valid_extraction(self, scorer):
        """Valid extraction has no issues."""
        extraction = self.create_extraction()

        issues = scorer.validate_extraction(extraction)

        assert issues == []

    def test_missing_amount(self, scorer):
        """Missing amount is flagged."""
        extraction = self.create_extraction(proposal={"amount": Decimal("0")})

        issues = scorer.validate_extraction(extraction)

        assert any("amount" in i.lower() for i in issues)

    def test_missing_date(self, scorer):
        """Missing date is flagged."""
        extraction = self.create_extraction(proposal={"date": ""})

        issues = scorer.validate_extraction(extraction)

        assert any("date" in i.lower() for i in issues)

    def test_invalid_date_format(self, scorer):
        """Invalid date format is flagged."""
        extraction = self.create_extraction(proposal={"date": "18.11.2024"})

        issues = scorer.validate_extraction(extraction)

        assert any("date" in i.lower() for i in issues)

    def test_missing_external_id(self, scorer):
        """Missing external_id is flagged."""
        extraction = self.create_extraction(proposal={"external_id": ""})

        issues = scorer.validate_extraction(extraction)

        assert any("external_id" in i.lower() for i in issues)

    def test_large_amount_warning(self, scorer):
        """Unusually large amount is flagged."""
        extraction = self.create_extraction(proposal={"amount": Decimal("500000")})

        issues = scorer.validate_extraction(extraction)

        assert any("large" in i.lower() for i in issues)
