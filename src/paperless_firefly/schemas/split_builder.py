"""
Split transaction payload builder (SSOT).

This module provides the SINGLE canonical implementation for building
multi-split Firefly III transaction payloads.

Core Invariants:
- Sum of splits must equal total amount (with configurable rounding strategy)
- All splits share: date, type, source_name, destination_name (for consistency)
- Each split has: amount, category, description, order
- external_id is assigned to the FIRST split only (Firefly linkage key)
- Re-import is idempotent: same input → same output (deterministic)

Rounding Strategy (SSOT):
- Default: DISTRIBUTE_REMAINDER - last split absorbs rounding differences
- Alternative: PROPORTIONAL - distribute proportionally (if needed)

Amount Sign Convention (SSOT):
- All amounts must be positive (regardless of transaction type)
- Transaction type determines the sign context (withdrawal = expense, deposit = income)
- This matches Firefly III API semantics
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .finance_extraction import FinanceExtraction, LineItem

logger = logging.getLogger(__name__)

# Rounding precision for currency amounts
CURRENCY_PRECISION = Decimal("0.01")


class RoundingStrategy(str, Enum):
    """Strategy for handling rounding in splits."""

    DISTRIBUTE_REMAINDER = "distribute_remainder"  # Last split absorbs difference
    PROPORTIONAL = "proportional"  # Distribute proportionally (not yet implemented)


class SplitValidationError(Exception):
    """Raised when split validation fails."""

    pass


class AmountValidationError(Exception):
    """Raised when amount validation fails (SSOT for amount constraints)."""

    pass


def validate_amount(
    amount: Decimal | float | str,
    *,
    field_name: str = "amount",
    allow_zero: bool = False,
    max_amount: Decimal | None = None,
) -> Decimal:
    """Validate and normalize a transaction amount (SSOT for amount validation).

    This is THE central validation function for all amounts in the system.

    Amount Sign Convention:
    - All amounts MUST be positive (Firefly III API requirement)
    - Transaction type (withdrawal/deposit/transfer) determines semantic
    - Negative amounts are rejected with clear error messages

    Args:
        amount: The amount to validate (Decimal, float, or string)
        field_name: Name for error messages (e.g., "split amount", "total")
        allow_zero: Whether zero is a valid value (default: False)
        max_amount: Optional maximum allowed amount (sanity check)

    Returns:
        Validated Decimal amount, quantized to CURRENCY_PRECISION

    Raises:
        AmountValidationError: If amount is invalid (negative, zero when not allowed, etc.)

    Examples:
        >>> validate_amount(Decimal("10.00"))
        Decimal('10.00')
        >>> validate_amount("-5.00")  # Raises AmountValidationError
        >>> validate_amount("0.00", allow_zero=True)
        Decimal('0.00')
    """
    # Convert to Decimal
    try:
        if isinstance(amount, str):
            amount = Decimal(amount.strip())
        elif isinstance(amount, float):
            amount = Decimal(str(amount))
        elif not isinstance(amount, Decimal):
            amount = Decimal(amount)
    except Exception as e:
        raise AmountValidationError(f"{field_name}: Invalid amount format - {e}") from e

    # Quantize to standard precision
    amount = amount.quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP)

    # Enforce positive amounts (SSOT: Firefly API requires positive)
    if amount < 0:
        raise AmountValidationError(
            f"{field_name}: Amount must be positive, got {amount}. "
            f"Use transaction type (withdrawal/deposit) to indicate direction."
        )

    # Check zero
    if not allow_zero and amount == 0:
        raise AmountValidationError(f"{field_name}: Amount cannot be zero (got {amount})")

    # Sanity check on maximum
    if max_amount is not None and amount > max_amount:
        raise AmountValidationError(f"{field_name}: Amount {amount} exceeds maximum {max_amount}")

    return amount


def normalize_amount_for_firefly(amount: Decimal | float | str) -> str:
    """Normalize an amount for Firefly API submission.

    Takes the absolute value and formats as a decimal string.
    This ensures negative amounts from OCR or parsing become positive.

    Args:
        amount: Amount value (may be negative from OCR)

    Returns:
        Positive decimal string (e.g., "10.50")
    """
    if isinstance(amount, str):
        amount = Decimal(amount.strip())
    elif isinstance(amount, float):
        amount = Decimal(str(amount))
    elif not isinstance(amount, Decimal):
        amount = Decimal(amount)

    # Take absolute value and quantize
    result = abs(amount).quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP)
    return str(result)


@dataclass
class SplitItem:
    """A single split in a transaction group."""

    amount: Decimal
    description: str
    category: str | None
    order: int
    # For deterministic key generation
    _position_in_source: int | None = None

    def stable_key(self) -> str:
        """Generate a stable key for reconciliation.

        Key components:
        - Position in source (if available)
        - Normalized description (lowercase, stripped)
        - Amount (as string)

        This allows identifying "same" splits across re-imports.
        """
        desc_norm = self.description.lower().strip()[:50] if self.description else ""
        amount_str = str(self.amount.quantize(CURRENCY_PRECISION))
        pos = self._position_in_source or self.order
        return f"{pos}:{desc_norm}:{amount_str}"


@dataclass
class SplitTransactionPayload:
    """Complete split transaction ready for Firefly API.

    This is the OUTPUT of the split builder.
    """

    # Shared fields across all splits
    transaction_type: str  # withdrawal, deposit, transfer
    date: str  # YYYY-MM-DD
    source_name: str
    destination_name: str
    currency_code: str | None
    group_title: str
    tags: list[str]

    # Individual splits
    splits: list[SplitItem]

    # Linkage (on first split only)
    external_id: str
    internal_reference: str
    notes: str
    external_url: str | None

    # Validation
    total_amount: Decimal

    def validate(self) -> list[str]:
        """Validate the split transaction.

        Returns:
            List of validation errors (empty if valid).
        """
        errors: list[str] = []

        if not self.splits:
            errors.append("No splits defined")
            return errors

        # Validate sum equals total
        split_sum = sum(s.amount for s in self.splits)
        if split_sum != self.total_amount:
            diff = abs(split_sum - self.total_amount)
            errors.append(f"Split sum ({split_sum}) != total ({self.total_amount}), diff={diff}")

        # Validate all amounts positive
        for i, split in enumerate(self.splits):
            if split.amount <= 0:
                errors.append(f"Split {i} has non-positive amount: {split.amount}")

        # Validate descriptions not empty
        for i, split in enumerate(self.splits):
            if not split.description or not split.description.strip():
                errors.append(f"Split {i} has empty description")

        return errors

    def to_firefly_payload(self) -> dict:
        """Convert to Firefly API JSON format.

        Returns:
            Dict ready for POST/PUT to /api/v1/transactions
        """
        errors = self.validate()
        if errors:
            raise SplitValidationError(f"Invalid split transaction: {errors}")

        transactions = []
        for idx, split in enumerate(self.splits):
            split_dict = {
                "type": self.transaction_type,
                "date": self.date,
                "amount": str(split.amount.quantize(CURRENCY_PRECISION)),
                "description": split.description,
                "source_name": self.source_name,
                "destination_name": self.destination_name,
                "order": split.order,
            }

            # Optional fields
            if split.category:
                split_dict["category_name"] = split.category
            if self.currency_code:
                split_dict["currency_code"] = self.currency_code
            if self.tags:
                split_dict["tags"] = self.tags

            # First split gets linkage markers
            if idx == 0:
                split_dict["external_id"] = self.external_id
                split_dict["internal_reference"] = self.internal_reference
                split_dict["notes"] = self.notes
                if self.external_url:
                    split_dict["external_url"] = self.external_url

            transactions.append(split_dict)

        return {
            "error_if_duplicate_hash": False,
            "apply_rules": True,
            "fire_webhooks": True,
            "group_title": self.group_title,
            "transactions": transactions,
        }


def build_splits_from_line_items(
    line_items: list[LineItem],
    total_amount: Decimal,
    default_category: str | None = None,
    rounding_strategy: RoundingStrategy = RoundingStrategy.DISTRIBUTE_REMAINDER,
) -> list[SplitItem]:
    """Build SplitItems from LineItem list with rounding correction.

    Args:
        line_items: Source line items with amounts
        total_amount: Expected total (from proposal.amount)
        default_category: Fallback category if line item has none
        rounding_strategy: How to handle sum != total

    Returns:
        List of SplitItems with amounts summing to total_amount

    Raises:
        SplitValidationError: If line items are invalid or irreconcilable
    """
    if not line_items:
        raise SplitValidationError("No line items provided")

    # Extract amounts from line items
    raw_splits: list[SplitItem] = []
    for idx, item in enumerate(line_items):
        amount = item.total or item.unit_price or Decimal("0")
        if amount <= 0:
            logger.warning(f"Line item {idx} has zero/negative amount, skipping")
            continue

        raw_splits.append(
            SplitItem(
                amount=amount.quantize(CURRENCY_PRECISION, rounding=ROUND_HALF_UP),
                description=item.description or f"Item {idx + 1}",
                category=item.category or default_category,
                order=idx,
                _position_in_source=item.position or idx + 1,
            )
        )

    if not raw_splits:
        raise SplitValidationError("All line items had zero/negative amounts")

    # Calculate sum and difference
    split_sum = sum(s.amount for s in raw_splits)
    difference = total_amount - split_sum

    if difference == Decimal("0"):
        return raw_splits

    # Apply rounding strategy
    if rounding_strategy == RoundingStrategy.DISTRIBUTE_REMAINDER:
        # Add/subtract difference to last split
        if abs(difference) <= Decimal("1.00"):
            # Small difference - adjust last split
            raw_splits[-1].amount += difference
            logger.debug(f"Applied rounding correction of {difference} to last split")
        else:
            # Large difference - this is likely a data error
            raise SplitValidationError(
                f"Split sum ({split_sum}) differs from total ({total_amount}) by {difference}. "
                "Difference too large for automatic correction."
            )

    return raw_splits


def generate_split_external_id(
    document_id: int,
    source_hash: str,
    total_amount: Decimal,
    date: str,
    split_count: int,
) -> str:
    """Generate external_id for split transactions.

    Format: paperless:{doc_id}:{hash[:16]}:{amount}:{date}:splits{count}

    The split count is included to differentiate from single transactions
    and ensure re-import idempotency.
    """
    hash_short = source_hash[:16] if source_hash else "unknown"
    amount_str = str(total_amount.quantize(CURRENCY_PRECISION))
    return f"paperless:{document_id}:{hash_short}:{amount_str}:{date}:splits{split_count}"


def build_split_transaction_payload(
    extraction: FinanceExtraction,
    default_source_account: str = "Checking Account",
    paperless_external_url: str | None = None,
) -> SplitTransactionPayload:
    """Build a split transaction payload from FinanceExtraction.

    This is THE canonical split builder. Use this for multi-line transactions.

    Args:
        extraction: Source extraction with line_items
        default_source_account: Default asset account for withdrawals
        paperless_external_url: Browser-accessible Paperless URL (SSOT)

    Returns:
        SplitTransactionPayload ready for Firefly API

    Raises:
        SplitValidationError: If extraction cannot produce valid splits
    """
    proposal = extraction.proposal

    if not extraction.line_items or len(extraction.line_items) < 2:
        raise SplitValidationError(
            f"Split transactions require 2+ line items, got {len(extraction.line_items or [])}"
        )

    # Build splits from line items
    splits = build_splits_from_line_items(
        line_items=extraction.line_items,
        total_amount=proposal.amount,
        default_category=proposal.category,
    )

    # Determine accounts based on transaction type
    from .finance_extraction import TransactionType

    if proposal.transaction_type == TransactionType.WITHDRAWAL:
        source_name = proposal.source_account or default_source_account
        destination_name = (
            proposal.destination_account
            or (
                extraction.document_classification.correspondent
                if extraction.document_classification
                else None
            )
            or "Unknown Merchant"
        )
    elif proposal.transaction_type == TransactionType.DEPOSIT:
        source_name = (
            proposal.source_account
            or (
                extraction.document_classification.correspondent
                if extraction.document_classification
                else None
            )
            or "Unknown Source"
        )
        destination_name = proposal.destination_account or default_source_account
    else:  # TRANSFER
        source_name = proposal.source_account or default_source_account
        destination_name = proposal.destination_account or "Unknown Account"

    # Build external_id for splits
    external_id = generate_split_external_id(
        document_id=extraction.paperless_document_id,
        source_hash=extraction.source_hash,
        total_amount=proposal.amount,
        date=proposal.date,
        split_count=len(splits),
    )

    # Build notes with provenance
    notes_parts = [
        f"Paperless doc_id={extraction.paperless_document_id}",
        f"source_hash={extraction.source_hash[:16]}",
        f"confidence={extraction.confidence.overall:.2f}",
        f"review_state={extraction.confidence.review_state.value}",
        f"splits={len(splits)}",
    ]
    if extraction.provenance.parser_version:
        notes_parts.append(f"parser={extraction.provenance.parser_version}")
    notes = "; ".join(notes_parts)

    # Build group title
    group_title = (
        proposal.description or f"Transaction from doc #{extraction.paperless_document_id}"
    )

    # Tags
    tags = list(proposal.tags) if proposal.tags else []
    tags.append("paperless")
    tags.append("split-transaction")

    # External URL for browser (use external URL, not internal)
    external_url = None
    if paperless_external_url:
        external_url = (
            f"{paperless_external_url.rstrip('/')}/documents/{extraction.paperless_document_id}/"
        )

    return SplitTransactionPayload(
        transaction_type=proposal.transaction_type.value,
        date=proposal.date,
        source_name=source_name,
        destination_name=destination_name,
        currency_code=proposal.currency,
        group_title=group_title,
        tags=tags,
        splits=splits,
        external_id=external_id,
        internal_reference=f"PAPERLESS:{extraction.paperless_document_id}",
        notes=notes,
        external_url=external_url,
        total_amount=proposal.amount,
    )
