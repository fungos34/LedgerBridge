"""Matching engine for correlating Paperless documents with Firefly transactions.

This module implements the Spark v1.0 matching system as specified in the
SPARK_EVALUATION_REPORT.md. It performs multi-signal matching between
extracted document data and existing Firefly transactions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from paperless_firefly.config import Config, ReconciliationConfig
    from paperless_firefly.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass
class MatchScore:
    """Individual signal contribution to a match score."""

    signal: str
    score: float
    weight: float
    detail: str

    @property
    def weighted_score(self) -> float:
        """Get the weighted score for this signal."""
        return self.score * self.weight


@dataclass
class MatchResult:
    """Result of matching a document extraction to a Firefly transaction."""

    firefly_id: int
    document_id: int
    total_score: float
    signals: list[MatchScore] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_confident(self) -> bool:
        """Return True if match score exceeds auto-match threshold."""
        # Default threshold is 0.90 as per spec
        return self.total_score >= 0.90

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "firefly_id": self.firefly_id,
            "document_id": self.document_id,
            "total_score": self.total_score,
            "signals": [
                {
                    "signal": s.signal,
                    "score": s.score,
                    "weight": s.weight,
                    "weighted_score": s.weighted_score,
                    "detail": s.detail,
                }
                for s in self.signals
            ],
            "reasons": self.reasons,
        }


class MatchingEngine:
    """Engine for matching Paperless document extractions to Firefly transactions.

    The matching engine uses multiple signals to score potential matches:
    - Amount: Exact match or within tolerance (highest weight)
    - Date: Within configurable tolerance window
    - Description: Fuzzy text matching
    - Vendor/Correspondent: Direct name matching

    Matches are scored 0-1 and proposals are created for human review
    unless the score exceeds the auto_match_threshold.
    """

    # Signal weights (sum to 1.0)
    WEIGHT_AMOUNT = 0.40
    WEIGHT_DATE = 0.25
    WEIGHT_DESCRIPTION = 0.20
    WEIGHT_VENDOR = 0.15

    def __init__(
        self,
        state_store: StateStore,
        config: Config,
    ) -> None:
        """Initialize the matching engine.

        Args:
            state_store: State store for accessing cached data.
            config: Application configuration.
        """
        self.store = state_store
        self.config = config
        self.recon_config: ReconciliationConfig = config.reconciliation

    def find_matches(
        self,
        document_id: int,
        extraction: dict,
        max_results: int = 5,
    ) -> list[MatchResult]:
        """Find matching Firefly transactions for a document extraction.

        Args:
            document_id: Paperless document ID.
            extraction: Extraction data including amount, date, vendor, etc.
            max_results: Maximum number of match results to return.

        Returns:
            List of MatchResult sorted by score descending.
        """
        results: list[MatchResult] = []

        # Get unmatched cached transactions
        cached_transactions = self.store.get_unmatched_firefly_transactions()

        if not cached_transactions:
            logger.debug("No unmatched transactions in cache for matching")
            return results

        extracted_amount = self._parse_amount(extraction.get("amount"))
        extracted_date = self._parse_date(extraction.get("date"))
        extracted_vendor = extraction.get("vendor") or extraction.get("correspondent")
        extracted_description = extraction.get("description", "")

        for tx in cached_transactions:
            signals: list[MatchScore] = []
            reasons: list[str] = []

            # Amount matching
            tx_amount = self._parse_amount(tx.get("amount"))
            amount_score = self._score_amount(extracted_amount, tx_amount)
            signals.append(amount_score)
            if amount_score.score > 0.5:
                reasons.append(f"amount_match ({amount_score.detail})")

            # Date matching
            tx_date = self._parse_date(tx.get("date"))
            date_score = self._score_date(extracted_date, tx_date)
            signals.append(date_score)
            if date_score.score > 0.5:
                reasons.append(f"date_close ({date_score.detail})")

            # Description matching
            tx_description = tx.get("description", "")
            desc_score = self._score_description(extracted_description, tx_description)
            signals.append(desc_score)
            if desc_score.score > 0.5:
                reasons.append(f"description_match ({desc_score.detail})")

            # Vendor matching
            tx_vendor = tx.get("destination_account") or tx.get("source_account")
            vendor_score = self._score_vendor(extracted_vendor, tx_vendor)
            signals.append(vendor_score)
            if vendor_score.score > 0.5:
                reasons.append(f"vendor_match ({vendor_score.detail})")

            # Calculate total weighted score
            total_score = sum(s.weighted_score for s in signals)

            # Only include if score is above minimum threshold
            if total_score >= 0.30:  # Minimum viable match
                results.append(
                    MatchResult(
                        firefly_id=tx["firefly_id"],
                        document_id=document_id,
                        total_score=total_score,
                        signals=signals,
                        reasons=reasons,
                    )
                )

        # Sort by score descending
        results.sort(key=lambda r: r.total_score, reverse=True)

        return results[:max_results]

    def create_proposals(
        self,
        document_id: int,
        extraction: dict,
    ) -> list[int]:
        """Find matches and create proposals for review.

        Args:
            document_id: Paperless document ID.
            extraction: Extraction data.

        Returns:
            List of created proposal IDs.
        """
        matches = self.find_matches(document_id, extraction)
        proposal_ids: list[int] = []

        for match in matches:
            proposal_id = self.store.create_match_proposal(
                firefly_id=match.firefly_id,
                document_id=match.document_id,
                match_score=match.total_score,
                match_reasons=match.reasons,
            )
            proposal_ids.append(proposal_id)

            logger.info(
                "Created match proposal %d: doc %d -> tx %d (score: %.2f)",
                proposal_id,
                document_id,
                match.firefly_id,
                match.total_score,
            )

            # If auto-match threshold exceeded, update status
            if match.total_score >= self.recon_config.auto_match_threshold:
                logger.info(
                    "Match proposal %d exceeds auto-match threshold (%.2f >= %.2f)",
                    proposal_id,
                    match.total_score,
                    self.recon_config.auto_match_threshold,
                )
                self.store.update_proposal_status(proposal_id, "AUTO_MATCHED")
                self.store.update_firefly_match_status(
                    match.firefly_id,
                    status="MATCHED",
                    document_id=document_id,
                    confidence=match.total_score,
                )

        return proposal_ids

    def _score_amount(
        self,
        extracted: Decimal | None,
        transaction: Decimal | None,
    ) -> MatchScore:
        """Score amount similarity.

        Args:
            extracted: Amount from document extraction.
            transaction: Amount from Firefly transaction.

        Returns:
            MatchScore for amount signal.
        """
        if extracted is None or transaction is None:
            return MatchScore(
                signal="amount",
                score=0.0,
                weight=self.WEIGHT_AMOUNT,
                detail="missing",
            )

        # Exact match
        if extracted == transaction:
            return MatchScore(
                signal="amount",
                score=1.0,
                weight=self.WEIGHT_AMOUNT,
                detail=f"exact: {extracted}",
            )

        # Within 1% tolerance
        if transaction != 0:
            diff_pct = abs((extracted - transaction) / transaction)
            if diff_pct <= 0.01:
                return MatchScore(
                    signal="amount",
                    score=0.95,
                    weight=self.WEIGHT_AMOUNT,
                    detail=f"~1%: {extracted} vs {transaction}",
                )
            if diff_pct <= 0.05:
                return MatchScore(
                    signal="amount",
                    score=0.7,
                    weight=self.WEIGHT_AMOUNT,
                    detail=f"~5%: {extracted} vs {transaction}",
                )

        return MatchScore(
            signal="amount",
            score=0.0,
            weight=self.WEIGHT_AMOUNT,
            detail=f"mismatch: {extracted} vs {transaction}",
        )

    def _score_date(
        self,
        extracted: datetime | None,
        transaction: datetime | None,
    ) -> MatchScore:
        """Score date proximity.

        Args:
            extracted: Date from document extraction.
            transaction: Date from Firefly transaction.

        Returns:
            MatchScore for date signal.
        """
        if extracted is None or transaction is None:
            return MatchScore(
                signal="date",
                score=0.0,
                weight=self.WEIGHT_DATE,
                detail="missing",
            )

        days_diff = abs((extracted.date() - transaction.date()).days)
        tolerance = self.recon_config.date_tolerance_days

        if days_diff == 0:
            return MatchScore(
                signal="date",
                score=1.0,
                weight=self.WEIGHT_DATE,
                detail="same day",
            )

        if days_diff <= tolerance:
            # Linear decay within tolerance
            score = 1.0 - (days_diff / (tolerance + 1))
            return MatchScore(
                signal="date",
                score=max(score, 0.3),
                weight=self.WEIGHT_DATE,
                detail=f"{days_diff} days",
            )

        return MatchScore(
            signal="date",
            score=0.0,
            weight=self.WEIGHT_DATE,
            detail=f">{tolerance} days",
        )

    def _score_description(
        self,
        extracted: str | None,
        transaction: str | None,
    ) -> MatchScore:
        """Score description similarity using simple fuzzy matching.

        Args:
            extracted: Description from document.
            transaction: Description from Firefly transaction.

        Returns:
            MatchScore for description signal.
        """
        if not extracted or not transaction:
            return MatchScore(
                signal="description",
                score=0.0,
                weight=self.WEIGHT_DESCRIPTION,
                detail="missing",
            )

        # Normalize strings
        ext_lower = extracted.lower().strip()
        tx_lower = transaction.lower().strip()

        # Exact match
        if ext_lower == tx_lower:
            return MatchScore(
                signal="description",
                score=1.0,
                weight=self.WEIGHT_DESCRIPTION,
                detail="exact",
            )

        # Contains check
        if ext_lower in tx_lower or tx_lower in ext_lower:
            return MatchScore(
                signal="description",
                score=0.8,
                weight=self.WEIGHT_DESCRIPTION,
                detail="contains",
            )

        # Word overlap (simple Jaccard-like)
        ext_words = set(ext_lower.split())
        tx_words = set(tx_lower.split())
        if ext_words and tx_words:
            intersection = ext_words & tx_words
            union = ext_words | tx_words
            jaccard = len(intersection) / len(union)
            if jaccard > 0.3:
                return MatchScore(
                    signal="description",
                    score=jaccard,
                    weight=self.WEIGHT_DESCRIPTION,
                    detail=f"overlap: {len(intersection)} words",
                )

        return MatchScore(
            signal="description",
            score=0.0,
            weight=self.WEIGHT_DESCRIPTION,
            detail="no match",
        )

    def _score_vendor(
        self,
        extracted: str | None,
        transaction: str | None,
    ) -> MatchScore:
        """Score vendor/correspondent similarity.

        Args:
            extracted: Vendor name from document.
            transaction: Account name from Firefly transaction.

        Returns:
            MatchScore for vendor signal.
        """
        if not extracted or not transaction:
            return MatchScore(
                signal="vendor",
                score=0.0,
                weight=self.WEIGHT_VENDOR,
                detail="missing",
            )

        ext_lower = extracted.lower().strip()
        tx_lower = transaction.lower().strip()

        # Exact match
        if ext_lower == tx_lower:
            return MatchScore(
                signal="vendor",
                score=1.0,
                weight=self.WEIGHT_VENDOR,
                detail="exact",
            )

        # Contains check (handles "Amazon.com" vs "Amazon")
        if ext_lower in tx_lower or tx_lower in ext_lower:
            return MatchScore(
                signal="vendor",
                score=0.85,
                weight=self.WEIGHT_VENDOR,
                detail="contains",
            )

        # Check first significant word
        ext_first = ext_lower.split()[0] if ext_lower else ""
        tx_first = tx_lower.split()[0] if tx_lower else ""
        if ext_first and tx_first and ext_first == tx_first:
            return MatchScore(
                signal="vendor",
                score=0.6,
                weight=self.WEIGHT_VENDOR,
                detail="first word",
            )

        return MatchScore(
            signal="vendor",
            score=0.0,
            weight=self.WEIGHT_VENDOR,
            detail="no match",
        )

    def _parse_amount(self, value: str | float | None) -> Decimal | None:
        """Parse amount to Decimal.

        Args:
            value: Amount as string or float.

        Returns:
            Decimal or None if parsing fails.
        """
        if value is None:
            return None
        try:
            if isinstance(value, str):
                # Remove currency symbols and whitespace
                cleaned = value.replace("$", "").replace("€", "").replace(",", "").strip()
                return Decimal(cleaned)
            return Decimal(str(value))
        except Exception:
            return None

    def _parse_date(self, value: str | datetime | None) -> datetime | None:
        """Parse date to datetime.

        Args:
            value: Date as string or datetime.

        Returns:
            datetime or None if parsing fails.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            # Try common formats
            for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"]:
                try:
                    return datetime.strptime(value[:10], fmt[:10])
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    def score_candidate(
        self,
        extraction: dict,
        candidate: dict,
    ) -> MatchResult:
        """
        Score a single candidate transaction against an extraction.

        This is a standalone entry point for scoring a single candidate,
        useful for API calls and UI previews where the full find_matches
        flow is not needed.

        Args:
            extraction: Dict with amount, date, vendor, description, correspondent
            candidate: Dict with amount, date, description, destination_account/source_account,
                       and firefly_id

        Returns:
            MatchResult with score breakdown
        """
        signals: list[MatchScore] = []
        reasons: list[str] = []

        extracted_amount = self._parse_amount(extraction.get("amount"))
        extracted_date = self._parse_date(extraction.get("date"))
        extracted_vendor = extraction.get("vendor") or extraction.get("correspondent")
        extracted_description = extraction.get("description", "")

        tx_amount = self._parse_amount(candidate.get("amount"))
        tx_date = self._parse_date(candidate.get("date"))
        tx_vendor = candidate.get("destination_account") or candidate.get("source_account")
        tx_description = candidate.get("description", "")

        # Amount matching
        amount_score = self._score_amount(extracted_amount, tx_amount)
        signals.append(amount_score)
        if amount_score.score > 0.5:
            reasons.append(f"amount_match ({amount_score.detail})")

        # Date matching
        date_score = self._score_date(extracted_date, tx_date)
        signals.append(date_score)
        if date_score.score > 0.5:
            reasons.append(f"date_close ({date_score.detail})")

        # Description matching
        desc_score = self._score_description(extracted_description, tx_description)
        signals.append(desc_score)
        if desc_score.score > 0.5:
            reasons.append(f"description_match ({desc_score.detail})")

        # Vendor matching
        vendor_score = self._score_vendor(extracted_vendor, tx_vendor)
        signals.append(vendor_score)
        if vendor_score.score > 0.5:
            reasons.append(f"vendor_match ({vendor_score.detail})")

        total_score = sum(s.weighted_score for s in signals)

        return MatchResult(
            firefly_id=candidate.get("firefly_id", 0),
            document_id=extraction.get("document_id", 0),
            total_score=total_score,
            signals=signals,
            reasons=reasons,
        )
