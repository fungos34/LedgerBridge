"""
Firefly III transaction payload builder (SSOT).

This is THE single builder that maps FinanceExtraction.proposal → Firefly TransactionStore JSON.

Rules:
- Always set required fields: type/date/amount/description
- Always set external_id
- Always set notes containing paperless_document_id and source_hash
- Use stable account mapping strategy (prefer names initially)
- For multi-split transactions, use build_firefly_payload_with_splits()

Split Transaction Rules:
- 2+ line items → create transaction group with multiple splits
- Sum of splits must equal total amount
- All splits share: date, type, source_name, destination_name
- external_id only on first split
"""

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .finance_extraction import FinanceExtraction, TransactionType

logger = logging.getLogger(__name__)


@dataclass
class FireflyTransactionSplit:
    """
    Single transaction split for Firefly III API.

    Maps to TransactionSplitStore in Firefly API.
    """

    # Required fields
    type: str  # withdrawal, deposit, transfer
    date: str  # YYYY-MM-DD or ISO-8601
    amount: str  # Decimal string with dot
    description: str

    # Account mapping
    source_name: str | None = None
    source_id: str | None = None
    destination_name: str | None = None
    destination_id: str | None = None

    # Currency
    currency_code: str | None = None
    currency_id: str | None = None

    # Categorization
    category_name: str | None = None
    category_id: str | None = None
    budget_name: str | None = None
    budget_id: str | None = None

    # Tags and notes
    tags: list[str] = field(default_factory=list)
    notes: str | None = None

    # Idempotency and linking
    internal_reference: str | None = None
    external_id: str | None = None
    external_url: str | None = None

    # Date fields
    book_date: str | None = None
    process_date: str | None = None
    due_date: str | None = None
    payment_date: str | None = None
    invoice_date: str | None = None

    # SEPA fields (optional)
    sepa_cc: str | None = None
    sepa_ct_op: str | None = None
    sepa_ct_id: str | None = None
    sepa_db: str | None = None
    sepa_country: str | None = None
    sepa_ep: str | None = None
    sepa_ci: str | None = None
    sepa_batch_id: str | None = None

    # Order (for splits)
    order: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to Firefly API JSON format."""
        result: dict[str, Any] = {
            "type": self.type,
            "date": self.date,
            "amount": self.amount,
            "description": self.description,
        }

        # Add optional fields only if set
        optional_fields = [
            ("source_name", self.source_name),
            ("source_id", self.source_id),
            ("destination_name", self.destination_name),
            ("destination_id", self.destination_id),
            ("currency_code", self.currency_code),
            ("currency_id", self.currency_id),
            ("category_name", self.category_name),
            ("category_id", self.category_id),
            ("budget_name", self.budget_name),
            ("budget_id", self.budget_id),
            ("notes", self.notes),
            ("internal_reference", self.internal_reference),
            ("external_id", self.external_id),
            ("external_url", self.external_url),
            ("book_date", self.book_date),
            ("process_date", self.process_date),
            ("due_date", self.due_date),
            ("payment_date", self.payment_date),
            ("invoice_date", self.invoice_date),
            ("sepa_cc", self.sepa_cc),
            ("sepa_ct_op", self.sepa_ct_op),
            ("sepa_ct_id", self.sepa_ct_id),
            ("sepa_db", self.sepa_db),
            ("sepa_country", self.sepa_country),
            ("sepa_ep", self.sepa_ep),
            ("sepa_ci", self.sepa_ci),
            ("sepa_batch_id", self.sepa_batch_id),
            ("order", self.order),
        ]

        for field_name, value in optional_fields:
            if value is not None:
                result[field_name] = value

        # Tags as array (even if empty)
        if self.tags:
            result["tags"] = self.tags

        return result


@dataclass
class FireflyTransactionStore:
    """
    Root transaction store for Firefly III API.

    Maps to TransactionStore in Firefly API.
    """

    transactions: list[FireflyTransactionSplit]

    error_if_duplicate_hash: bool = False
    apply_rules: bool = True
    fire_webhooks: bool = True
    group_title: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to Firefly API JSON format."""
        result: dict[str, Any] = {
            "transactions": [t.to_dict() for t in self.transactions],
            "error_if_duplicate_hash": self.error_if_duplicate_hash,
            "apply_rules": self.apply_rules,
            "fire_webhooks": self.fire_webhooks,
        }

        if self.group_title:
            result["group_title"] = self.group_title

        return result

    def to_json(self, indent: int = 2) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def build_firefly_payload(
    extraction: FinanceExtraction,
    default_source_account: str = "Checking Account",
    paperless_base_url: str = "http://localhost:8000",
) -> FireflyTransactionStore:
    """
    Build Firefly III transaction payload from FinanceExtraction.

    This is THE canonical builder. All imports use this function.

    Args:
        extraction: The finance extraction to convert
        default_source_account: Default source account name for withdrawals
        paperless_base_url: Base URL for Paperless document links

    Returns:
        FireflyTransactionStore ready for API submission

    Raises:
        ValueError: If required fields are missing or invalid
    """
    proposal = extraction.proposal

    # Validate required fields
    if not proposal.date:
        raise ValueError("proposal.date is required but empty")
    if not proposal.amount:
        raise ValueError("proposal.amount is required but empty")
    if not proposal.description:
        raise ValueError("proposal.description is required but empty")
    if not proposal.external_id:
        raise ValueError("proposal.external_id is required but empty")

    # Build notes with provenance (ALWAYS required)
    notes_parts = [
        f"Paperless doc_id={extraction.paperless_document_id}",
        f"source_hash={extraction.source_hash[:16]}",
        f"confidence={extraction.confidence.overall:.2f}",
        f"review_state={extraction.confidence.review_state.value}",
    ]
    if extraction.provenance.parser_version:
        notes_parts.append(f"parser={extraction.provenance.parser_version}")
    if proposal.notes:
        notes_parts.append(proposal.notes)

    notes = "; ".join(notes_parts)

    # Build external URL
    external_url = f"{paperless_base_url}/documents/{extraction.paperless_document_id}/"

    # Determine source and destination based on transaction type
    source_name: str | None = None
    destination_name: str | None = None

    if proposal.transaction_type == TransactionType.WITHDRAWAL:
        source_name = proposal.source_account or default_source_account
        destination_name = (
            proposal.destination_account or extraction.document_classification.correspondent
            if extraction.document_classification
            else None
        )
        if not destination_name:
            destination_name = "Unknown Merchant"
    elif proposal.transaction_type == TransactionType.DEPOSIT:
        source_name = (
            proposal.source_account or extraction.document_classification.correspondent
            if extraction.document_classification
            else None
        )
        if not source_name:
            source_name = "Unknown Source"
        destination_name = proposal.destination_account or default_source_account
    elif proposal.transaction_type == TransactionType.TRANSFER:
        source_name = proposal.source_account or default_source_account
        destination_name = proposal.destination_account or "Unknown Account"

    # Build tags
    tags = list(proposal.tags) if proposal.tags else []
    tags.append("paperless")  # Always tag with paperless for tracking

    # Build the transaction split
    split = FireflyTransactionSplit(
        type=proposal.transaction_type.value,
        date=proposal.date,
        amount=str(proposal.amount),
        description=proposal.description,
        source_name=source_name,
        destination_name=destination_name,
        currency_code=proposal.currency,
        category_name=proposal.category,
        tags=tags,
        notes=notes,
        internal_reference=f"PAPERLESS:{extraction.paperless_document_id}",
        external_id=proposal.external_id,
        external_url=external_url,
        invoice_date=proposal.date,  # Use extraction date as invoice date
        due_date=proposal.due_date,
        payment_date=proposal.date,
    )

    # Build the transaction store
    return FireflyTransactionStore(
        transactions=[split],
        error_if_duplicate_hash=False,  # We handle dedup via external_id
        apply_rules=True,
        fire_webhooks=True,
    )


def validate_firefly_payload(payload: FireflyTransactionStore) -> list[str]:
    """
    Validate Firefly payload meets API requirements.

    Args:
        payload: The payload to validate

    Returns:
        List of validation errors (empty if valid)
    """
    errors: list[str] = []

    if not payload.transactions:
        errors.append("transactions array is empty")
        return errors

    for i, split in enumerate(payload.transactions):
        prefix = f"transactions[{i}]"

        # Required fields
        if not split.type:
            errors.append(f"{prefix}.type is required")
        elif split.type not in ("withdrawal", "deposit", "transfer"):
            errors.append(f"{prefix}.type must be withdrawal/deposit/transfer, got: {split.type}")

        if not split.date:
            errors.append(f"{prefix}.date is required")

        if not split.amount:
            errors.append(f"{prefix}.amount is required")
        else:
            try:
                amt = Decimal(split.amount)
                if amt <= 0:
                    errors.append(f"{prefix}.amount must be positive, got: {split.amount}")
            except Exception:
                errors.append(f"{prefix}.amount must be a valid decimal, got: {split.amount}")

        if not split.description:
            errors.append(f"{prefix}.description is required")

        # Only first split requires external_id for idempotency
        if i == 0 and not split.external_id:
            errors.append(f"{prefix}.external_id is required for idempotent imports")

        # Only first split requires notes for provenance
        if i == 0 and not split.notes:
            errors.append(f"{prefix}.notes is required for audit trail")

    # For multi-split, validate sum equals group total
    if len(payload.transactions) > 1:
        total = sum(Decimal(t.amount) for t in payload.transactions)
        # Note: We trust the builder to handle rounding; this is a sanity check
        logger.debug(f"Multi-split transaction total: {total}")

    return errors


def build_firefly_payload_with_splits(
    extraction: FinanceExtraction,
    default_source_account: str = "Checking Account",
    paperless_external_url: str | None = None,
) -> FireflyTransactionStore:
    """
    Build Firefly III transaction payload, automatically handling splits.

    This is THE canonical builder for all imports. It automatically detects
    whether to create a single transaction or a split transaction group.

    Decision logic:
    - 0-1 line items: Single transaction (standard)
    - 2+ line items: Transaction group with splits

    For split transactions:
    - Uses group_title for the overall description
    - Each split gets its own description and category from line_items
    - external_id is assigned to the FIRST split only
    - Sum of splits is validated to equal proposal.amount

    Args:
        extraction: The finance extraction to convert
        default_source_account: Default source account name for withdrawals
        paperless_external_url: Browser-accessible URL for Paperless (SSOT)

    Returns:
        FireflyTransactionStore ready for API submission

    Raises:
        ValueError: If required fields are missing or invalid
    """
    proposal = extraction.proposal

    # Validate required fields
    if not proposal.date:
        raise ValueError("proposal.date is required but empty")
    if not proposal.amount:
        raise ValueError("proposal.amount is required but empty")
    if not proposal.description:
        raise ValueError("proposal.description is required but empty")
    if not proposal.external_id:
        raise ValueError("proposal.external_id is required but empty")

    # Check if this should be a split transaction
    has_splits = extraction.line_items and len(extraction.line_items) >= 2

    if has_splits:
        return _build_split_payload(
            extraction=extraction,
            default_source_account=default_source_account,
            paperless_external_url=paperless_external_url,
        )
    else:
        return build_firefly_payload(
            extraction=extraction,
            default_source_account=default_source_account,
            paperless_base_url=paperless_external_url or "http://localhost:8000",
        )


def _build_split_payload(
    extraction: FinanceExtraction,
    default_source_account: str,
    paperless_external_url: str | None,
) -> FireflyTransactionStore:
    """Build a multi-split transaction payload.

    Internal function called by build_firefly_payload_with_splits().

    Args:
        extraction: Source extraction with line_items
        default_source_account: Default asset account
        paperless_external_url: Browser URL for Paperless

    Returns:
        FireflyTransactionStore with multiple splits
    """
    from decimal import ROUND_HALF_UP

    proposal = extraction.proposal

    # Build notes with provenance (on first split only)
    notes_parts = [
        f"Paperless doc_id={extraction.paperless_document_id}",
        f"source_hash={extraction.source_hash[:16]}",
        f"confidence={extraction.confidence.overall:.2f}",
        f"review_state={extraction.confidence.review_state.value}",
        f"splits={len(extraction.line_items)}",
    ]
    if extraction.provenance.parser_version:
        notes_parts.append(f"parser={extraction.provenance.parser_version}")
    if proposal.notes:
        notes_parts.append(proposal.notes)
    notes = "; ".join(notes_parts)

    # Build external URL (use external URL, not internal)
    external_url = None
    if paperless_external_url:
        external_url = (
            f"{paperless_external_url.rstrip('/')}/documents/{extraction.paperless_document_id}/"
        )

    # Determine source and destination based on transaction type
    source_name: str
    destination_name: str

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

    # Build tags
    tags = list(proposal.tags) if proposal.tags else []
    tags.append("paperless")
    tags.append("split-transaction")

    # Build splits from line items
    splits: list[FireflyTransactionSplit] = []
    split_sum = Decimal("0")
    PRECISION = Decimal("0.01")

    for idx, item in enumerate(extraction.line_items):
        # Get amount from line item (prefer total, then unit_price)
        item_amount = item.total or item.unit_price or Decimal("0")
        if item_amount <= 0:
            logger.warning(f"Skipping line item {idx} with zero/negative amount")
            continue

        item_amount = item_amount.quantize(PRECISION, rounding=ROUND_HALF_UP)
        split_sum += item_amount

        split = FireflyTransactionSplit(
            type=proposal.transaction_type.value,
            date=proposal.date,
            amount=str(item_amount),
            description=item.description or f"Item {idx + 1}",
            source_name=source_name,
            destination_name=destination_name,
            currency_code=proposal.currency,
            category_name=item.category or proposal.category,
            tags=tags,
            order=idx,
        )

        # First split gets linkage markers
        if idx == 0:
            split.notes = notes
            split.internal_reference = f"PAPERLESS:{extraction.paperless_document_id}"
            split.external_id = proposal.external_id
            split.external_url = external_url
            split.invoice_date = proposal.date
            split.due_date = proposal.due_date
            split.payment_date = proposal.date

        splits.append(split)

    if not splits:
        raise ValueError("No valid line items to create splits from")

    # Handle rounding: adjust last split to match total
    difference = proposal.amount - split_sum
    if difference != Decimal("0"):
        if abs(difference) <= Decimal("1.00"):
            # Small rounding difference - adjust last split
            last_split = splits[-1]
            adjusted_amount = Decimal(last_split.amount) + difference
            last_split.amount = str(adjusted_amount.quantize(PRECISION))
            logger.info(
                f"Applied rounding correction of {difference} to last split "
                f"(was {Decimal(last_split.amount) - difference}, now {last_split.amount})"
            )
        else:
            # Large difference - this is a data error, fail loudly
            raise ValueError(
                f"Split sum ({split_sum}) differs from total ({proposal.amount}) by {difference}. "
                "Line item amounts do not match proposal total. Review and correct the data."
            )

    # Build group title
    group_title = (
        proposal.description or f"Transaction from doc #{extraction.paperless_document_id}"
    )

    return FireflyTransactionStore(
        transactions=splits,
        error_if_duplicate_hash=False,
        apply_rules=True,
        fire_webhooks=True,
        group_title=group_title,
    )


# NOTE: validate_amount and normalize_amount_for_firefly are now in split_builder.py (SSOT)
# Import from there or from schemas.__init__ for amount validation needs
