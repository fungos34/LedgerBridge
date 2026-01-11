"""Tests for the Spark matching engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from paperless_firefly.matching.engine import MatchingEngine, MatchResult, MatchScore
from paperless_firefly.state_store import StateStore


class TestMatchScore:
    """Tests for MatchScore dataclass."""

    def test_weighted_score_calculation(self) -> None:
        """Test weighted score is calculated correctly."""
        score = MatchScore(signal="amount", score=0.8, weight=0.4, detail="test")
        assert score.weighted_score == pytest.approx(0.32)

    def test_zero_weight_gives_zero_weighted(self) -> None:
        """Test zero weight gives zero weighted score."""
        score = MatchScore(signal="test", score=1.0, weight=0.0, detail="test")
        assert score.weighted_score == 0.0


class TestMatchResult:
    """Tests for MatchResult dataclass."""

    def test_is_confident_above_threshold(self) -> None:
        """Test is_confident returns True above threshold."""
        result = MatchResult(firefly_id=1, document_id=2, total_score=0.95)
        assert result.is_confident is True

    def test_is_confident_below_threshold(self) -> None:
        """Test is_confident returns False below threshold."""
        result = MatchResult(firefly_id=1, document_id=2, total_score=0.80)
        assert result.is_confident is False

    def test_is_confident_at_threshold(self) -> None:
        """Test is_confident returns True at exactly threshold."""
        result = MatchResult(firefly_id=1, document_id=2, total_score=0.90)
        assert result.is_confident is True

    def test_to_dict_serialization(self) -> None:
        """Test to_dict includes all fields."""
        result = MatchResult(
            firefly_id=100,
            document_id=200,
            total_score=0.85,
            signals=[MatchScore(signal="amount", score=0.9, weight=0.4, detail="exact")],
            reasons=["amount_match"],
        )
        data = result.to_dict()

        assert data["firefly_id"] == 100
        assert data["document_id"] == 200
        assert data["total_score"] == 0.85
        assert len(data["signals"]) == 1
        assert data["signals"][0]["signal"] == "amount"
        assert data["signals"][0]["weighted_score"] == pytest.approx(0.36)
        assert data["reasons"] == ["amount_match"]


class TestMatchingEngine:
    """Tests for MatchingEngine."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore for each test."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    @pytest.fixture
    def mock_config(self) -> MagicMock:
        """Create mock config with default reconciliation settings."""
        config = MagicMock()
        config.reconciliation.date_tolerance_days = 7
        config.reconciliation.auto_match_threshold = 0.90
        return config

    @pytest.fixture
    def engine(self, store: StateStore, mock_config: MagicMock) -> MatchingEngine:
        """Create MatchingEngine with test fixtures."""
        return MatchingEngine(state_store=store, config=mock_config)

    # Amount scoring tests

    def test_score_amount_exact_match(self, engine: MatchingEngine) -> None:
        """Test exact amount match gives score of 1.0."""
        score = engine._score_amount(Decimal("99.99"), Decimal("99.99"))
        assert score.score == 1.0
        assert score.signal == "amount"
        assert "exact" in score.detail

    def test_score_amount_within_1_percent(self, engine: MatchingEngine) -> None:
        """Test amount within 1% gives high score."""
        score = engine._score_amount(Decimal("100.00"), Decimal("100.50"))
        assert score.score == pytest.approx(0.95)

    def test_score_amount_within_5_percent(self, engine: MatchingEngine) -> None:
        """Test amount within 5% gives moderate score."""
        score = engine._score_amount(Decimal("100.00"), Decimal("104.00"))
        assert score.score == pytest.approx(0.7)

    def test_score_amount_mismatch(self, engine: MatchingEngine) -> None:
        """Test large amount difference gives zero score."""
        score = engine._score_amount(Decimal("100.00"), Decimal("200.00"))
        assert score.score == 0.0

    def test_score_amount_missing_extracted(self, engine: MatchingEngine) -> None:
        """Test missing extracted amount gives zero score."""
        score = engine._score_amount(None, Decimal("100.00"))
        assert score.score == 0.0
        assert "missing" in score.detail

    # Date scoring tests

    def test_score_date_same_day(self, engine: MatchingEngine) -> None:
        """Test same day gives score of 1.0."""
        date1 = datetime(2025, 1, 15)
        date2 = datetime(2025, 1, 15)
        score = engine._score_date(date1, date2)
        assert score.score == 1.0
        assert "same day" in score.detail

    def test_score_date_within_tolerance(self, engine: MatchingEngine) -> None:
        """Test date within tolerance gives partial score."""
        date1 = datetime(2025, 1, 15)
        date2 = datetime(2025, 1, 18)  # 3 days apart
        score = engine._score_date(date1, date2)
        assert score.score > 0.3
        assert "3 days" in score.detail

    def test_score_date_outside_tolerance(self, engine: MatchingEngine) -> None:
        """Test date far outside tolerance gives zero score."""
        date1 = datetime(2025, 1, 15)
        date2 = datetime(2025, 3, 1)  # 45 days apart, well beyond any tolerance
        score = engine._score_date(date1, date2)
        assert score.score == 0.0

    def test_score_date_missing(self, engine: MatchingEngine) -> None:
        """Test missing date gives zero score."""
        score = engine._score_date(None, datetime(2025, 1, 15))
        assert score.score == 0.0

    # Description scoring tests

    def test_score_description_exact(self, engine: MatchingEngine) -> None:
        """Test exact description match gives score of 1.0."""
        score = engine._score_description("Amazon Purchase", "amazon purchase")
        assert score.score == 1.0
        assert "exact" in score.detail

    def test_score_description_contains(self, engine: MatchingEngine) -> None:
        """Test contains relationship gives high score."""
        score = engine._score_description("Amazon", "Amazon Purchase Order #12345")
        assert score.score == 0.8
        assert "contains" in score.detail

    def test_score_description_word_overlap(self, engine: MatchingEngine) -> None:
        """Test word overlap gives partial score."""
        score = engine._score_description(
            "Amazon order electronics", "Amazon electronics department"
        )
        assert score.score > 0.3
        assert "overlap" in score.detail

    def test_score_description_no_match(self, engine: MatchingEngine) -> None:
        """Test no common words gives zero score."""
        score = engine._score_description("Coffee shop", "Electronics store")
        assert score.score == 0.0

    def test_score_description_empty(self, engine: MatchingEngine) -> None:
        """Test empty description gives zero score."""
        score = engine._score_description("", "Amazon")
        assert score.score == 0.0

    # Vendor scoring tests

    def test_score_vendor_exact(self, engine: MatchingEngine) -> None:
        """Test exact vendor match gives score of 1.0."""
        score = engine._score_vendor("Amazon", "amazon")
        assert score.score == 1.0

    def test_score_vendor_contains(self, engine: MatchingEngine) -> None:
        """Test contains relationship gives high score."""
        score = engine._score_vendor("Amazon", "Amazon.com LLC")
        assert score.score == 0.85

    def test_score_vendor_first_word(self, engine: MatchingEngine) -> None:
        """Test first word match gives moderate score."""
        score = engine._score_vendor("Amazon Web Services", "Amazon Prime")
        assert score.score == 0.6

    def test_score_vendor_no_match(self, engine: MatchingEngine) -> None:
        """Test no match gives zero score."""
        score = engine._score_vendor("Amazon", "Google")
        assert score.score == 0.0

    # Amount parsing tests

    def test_parse_amount_string(self, engine: MatchingEngine) -> None:
        """Test parsing amount from string."""
        assert engine._parse_amount("99.99") == Decimal("99.99")

    def test_parse_amount_with_currency(self, engine: MatchingEngine) -> None:
        """Test parsing amount with currency symbols."""
        assert engine._parse_amount("$99.99") == Decimal("99.99")
        assert engine._parse_amount("€99.99") == Decimal("99.99")

    def test_parse_amount_with_commas(self, engine: MatchingEngine) -> None:
        """Test parsing amount with thousand separators."""
        assert engine._parse_amount("1,234.56") == Decimal("1234.56")

    def test_parse_amount_float(self, engine: MatchingEngine) -> None:
        """Test parsing amount from float."""
        assert engine._parse_amount(99.99) == Decimal("99.99")

    def test_parse_amount_none(self, engine: MatchingEngine) -> None:
        """Test parsing None returns None."""
        assert engine._parse_amount(None) is None

    # Date parsing tests

    def test_parse_date_iso_format(self, engine: MatchingEngine) -> None:
        """Test parsing ISO format date."""
        result = engine._parse_date("2025-01-15")
        assert result == datetime(2025, 1, 15)

    def test_parse_date_iso_with_time(self, engine: MatchingEngine) -> None:
        """Test parsing ISO format with time."""
        result = engine._parse_date("2025-01-15T10:30:00")
        assert result is not None
        assert result.date() == datetime(2025, 1, 15).date()

    def test_parse_date_datetime_passthrough(self, engine: MatchingEngine) -> None:
        """Test datetime object passes through."""
        dt = datetime(2025, 1, 15, 10, 30)
        assert engine._parse_date(dt) == dt

    def test_parse_date_none(self, engine: MatchingEngine) -> None:
        """Test parsing None returns None."""
        assert engine._parse_date(None) is None

    # Integration tests

    def test_find_matches_no_cached_transactions(
        self, engine: MatchingEngine, store: StateStore
    ) -> None:
        """Test find_matches with empty cache returns empty list."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test")

        extraction = {"amount": "99.99", "date": "2025-01-15", "vendor": "Amazon"}
        results = engine.find_matches(document_id=1, extraction=extraction)

        assert results == []

    def test_find_matches_returns_sorted_results(
        self, engine: MatchingEngine, store: StateStore
    ) -> None:
        """Test find_matches returns results sorted by score."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test")

        # Add cached transactions
        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Amazon Order",
            destination_account="Amazon",
        )
        store.upsert_firefly_cache(
            firefly_id=101,
            type_="withdrawal",
            date="2025-01-20",  # Wrong date
            amount="50.00",  # Wrong amount
            description="Grocery shopping",
            destination_account="Walmart",
        )

        extraction = {
            "amount": "99.99",
            "date": "2025-01-15",
            "vendor": "Amazon",
            "description": "Amazon Purchase",
        }
        results = engine.find_matches(document_id=1, extraction=extraction)

        assert len(results) >= 1
        # Best match should be first
        assert results[0].firefly_id == 100
        assert results[0].total_score > 0.5

    def test_find_matches_filters_low_scores(
        self, engine: MatchingEngine, store: StateStore
    ) -> None:
        """Test find_matches filters out very low score matches."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test")

        # Add transaction that won't match at all
        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2024-01-15",  # Wrong year
            amount="999.99",  # Wrong amount
            description="Something completely different",
            destination_account="Unrelated Company",
        )

        extraction = {
            "amount": "50.00",
            "date": "2025-01-15",
            "vendor": "Amazon",
            "description": "Amazon Purchase",
        }
        results = engine.find_matches(document_id=1, extraction=extraction)

        # Should filter out low-score match
        assert len(results) == 0

    def test_create_proposals_creates_records(
        self, engine: MatchingEngine, store: StateStore
    ) -> None:
        """Test create_proposals creates proposal records."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test Invoice")

        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Amazon Order",
            destination_account="Amazon",
        )

        extraction = {
            "amount": "99.99",
            "date": "2025-01-15",
            "vendor": "Amazon",
            "description": "Amazon Purchase",
        }
        proposal_ids = engine.create_proposals(document_id=1, extraction=extraction)

        assert len(proposal_ids) >= 1

        # Verify proposal was created
        proposal = store.get_proposal_by_id(proposal_ids[0])
        assert proposal is not None
        assert proposal["firefly_id"] == 100
        assert proposal["document_id"] == 1
        assert proposal["match_score"] > 0.5

    def test_create_proposals_auto_matches_high_confidence(
        self, engine: MatchingEngine, store: StateStore, mock_config: MagicMock
    ) -> None:
        """Test create_proposals auto-matches when score exceeds threshold."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test Invoice")

        # Perfect match transaction
        store.upsert_firefly_cache(
            firefly_id=100,
            type_="withdrawal",
            date="2025-01-15",
            amount="99.99",
            description="Amazon Order",
            destination_account="Amazon",
        )

        # Set low threshold for testing
        mock_config.reconciliation.auto_match_threshold = 0.5

        extraction = {
            "amount": "99.99",
            "date": "2025-01-15",
            "vendor": "Amazon",
            "description": "Amazon Order",
        }
        engine.create_proposals(document_id=1, extraction=extraction)

        # Verify transaction was auto-matched
        tx = store.get_firefly_cache_entry(100)
        assert tx["match_status"] == "MATCHED"
        assert tx["matched_document_id"] == 1

    def test_max_results_limits_output(self, engine: MatchingEngine, store: StateStore) -> None:
        """Test max_results parameter limits returned matches."""
        store.upsert_document(document_id=1, source_hash="hash1", title="Test")

        # Add multiple similar transactions
        for i in range(10):
            store.upsert_firefly_cache(
                firefly_id=100 + i,
                type_="withdrawal",
                date=f"2025-01-{15 + i % 5}",
                amount="99.99",
                description=f"Amazon Order #{i}",
                destination_account="Amazon",
            )

        extraction = {
            "amount": "99.99",
            "date": "2025-01-15",
            "vendor": "Amazon",
        }
        results = engine.find_matches(document_id=1, extraction=extraction, max_results=3)

        assert len(results) <= 3
