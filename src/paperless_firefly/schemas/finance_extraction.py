"""
Canonical extracted finance object (SSOT).

This is THE single source of truth for extracted finance data.
No other module may invent another "extraction schema".
Everything maps into/out of this.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class TransactionType(str, Enum):
    """Transaction type for Firefly III."""

    WITHDRAWAL = "withdrawal"
    DEPOSIT = "deposit"
    TRANSFER = "transfer"


class ReviewState(str, Enum):
    """
    Review requirement based on confidence scores.

    AUTO: High confidence, can be imported automatically
    REVIEW: Medium confidence, user should confirm
    MANUAL: Low confidence, user must review and likely edit
    """

    AUTO = "AUTO"
    REVIEW = "REVIEW"
    MANUAL = "MANUAL"


@dataclass
class DocumentClassification:
    """Semantic classification from Paperless."""

    document_type: Optional[str] = None  # e.g., "Receipt", "Invoice"
    correspondent: Optional[str] = None  # e.g., "SPAR", "Amazon"
    tags: list[str] = field(default_factory=list)
    storage_path: Optional[str] = None


@dataclass
class LineItem:
    """Individual line item from an invoice/receipt."""

    description: str
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    total: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None  # As percentage, e.g., 20 for 20%
    position: Optional[int] = None


@dataclass
class TransactionProposal:
    """
    Proposed transaction fields for Firefly III import.

    This is the best-effort proposal generated from extraction.
    All fields should be populated when possible, with confidence
    scores indicating reliability.
    """

    # Required fields for Firefly
    transaction_type: TransactionType
    date: str  # ISO format YYYY-MM-DD
    amount: Decimal  # Always positive, with dot as decimal separator
    currency: str  # ISO code, e.g., "EUR"
    description: str

    # Account mapping
    source_account: Optional[str] = None  # Firefly asset account name/id
    destination_account: Optional[str] = None  # Merchant or expense account

    # Optional categorization
    category: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    # Provenance (always included in notes)
    notes: Optional[str] = None

    # Dedupe key (deterministic)
    external_id: str = ""

    # Invoice-specific fields
    invoice_number: Optional[str] = None
    due_date: Optional[str] = None  # ISO format YYYY-MM-DD
    payment_reference: Optional[str] = None

    # Tax details
    total_net: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None


@dataclass
class ConfidenceScores:
    """
    Confidence scoring for extraction.

    All scores are floats in range [0.0, 1.0].
    Higher = more confident.
    """

    overall: float

    # Per-field confidence
    amount: float = 0.0
    date: float = 0.0
    currency: float = 0.0
    description: float = 0.0
    vendor: float = 0.0
    invoice_number: float = 0.0
    line_items: float = 0.0

    # Computed review state
    review_state: ReviewState = ReviewState.MANUAL

    # Thresholds used
    auto_threshold: float = 0.85
    review_threshold: float = 0.60

    def compute_review_state(self) -> ReviewState:
        """Compute review state based on overall confidence."""
        if self.overall >= self.auto_threshold:
            return ReviewState.AUTO
        elif self.overall >= self.review_threshold:
            return ReviewState.REVIEW
        else:
            return ReviewState.MANUAL


@dataclass
class Provenance:
    """Audit trail and reproducibility information."""

    source_system: str = "paperless"
    parser_version: str = ""
    parsed_at: str = ""  # ISO timestamp
    ruleset_id: Optional[str] = None
    extraction_strategy: Optional[str] = None  # e.g., "ocr_heuristic", "factur_x"


@dataclass
class StructuredPayload:
    """Detected embedded structured data (e.g., Factur-X XML)."""

    payload_type: str  # e.g., "Factur-X", "UBL", "ZUGFeRD"
    raw_content: str
    parsed_data: Optional[dict] = None


@dataclass
class FinanceExtraction:
    """
    CANONICAL extracted finance object (SSOT).

    This is the single source of truth for all extracted finance data.
    Every module in the pipeline uses this schema exclusively.

    Required fields are marked; everything else is best-effort.
    """

    # Required: Document identity
    paperless_document_id: int
    source_hash: str  # SHA256 of original file bytes
    paperless_url: str

    # Required: OCR/text content
    raw_text: str

    # Required: Best-effort proposal
    proposal: TransactionProposal

    # Required: Confidence assessment
    confidence: ConfidenceScores

    # Required: Audit trail
    provenance: Provenance

    # Optional: Document metadata
    paperless_title: Optional[str] = None
    document_classification: Optional[DocumentClassification] = None

    # Optional: Embedded structured data (Factur-X, UBL, etc.)
    structured_payloads: list[StructuredPayload] = field(default_factory=list)

    # Optional: Line items
    line_items: list[LineItem] = field(default_factory=list)

    # Timestamps
    created_at: str = ""  # ISO timestamp when extraction was created

    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON storage."""
        return {
            "paperless_document_id": self.paperless_document_id,
            "source_hash": self.source_hash,
            "paperless_url": self.paperless_url,
            "paperless_title": self.paperless_title,
            "raw_text": self.raw_text,
            "document_classification": (
                {
                    "document_type": self.document_classification.document_type,
                    "correspondent": self.document_classification.correspondent,
                    "tags": self.document_classification.tags,
                    "storage_path": self.document_classification.storage_path,
                }
                if self.document_classification
                else None
            ),
            "proposal": {
                "transaction_type": self.proposal.transaction_type.value,
                "date": self.proposal.date,
                "amount": str(self.proposal.amount),
                "currency": self.proposal.currency,
                "description": self.proposal.description,
                "source_account": self.proposal.source_account,
                "destination_account": self.proposal.destination_account,
                "category": self.proposal.category,
                "tags": self.proposal.tags,
                "notes": self.proposal.notes,
                "external_id": self.proposal.external_id,
                "invoice_number": self.proposal.invoice_number,
                "due_date": self.proposal.due_date,
                "payment_reference": self.proposal.payment_reference,
                "total_net": str(self.proposal.total_net) if self.proposal.total_net else None,
                "tax_amount": str(self.proposal.tax_amount) if self.proposal.tax_amount else None,
                "tax_rate": str(self.proposal.tax_rate) if self.proposal.tax_rate else None,
            },
            "line_items": [
                {
                    "description": item.description,
                    "quantity": str(item.quantity) if item.quantity else None,
                    "unit_price": str(item.unit_price) if item.unit_price else None,
                    "total": str(item.total) if item.total else None,
                    "tax_rate": str(item.tax_rate) if item.tax_rate else None,
                    "position": item.position,
                }
                for item in self.line_items
            ],
            "confidence": {
                "overall": self.confidence.overall,
                "amount": self.confidence.amount,
                "date": self.confidence.date,
                "currency": self.confidence.currency,
                "description": self.confidence.description,
                "vendor": self.confidence.vendor,
                "invoice_number": self.confidence.invoice_number,
                "line_items": self.confidence.line_items,
                "review_state": self.confidence.review_state.value,
            },
            "provenance": {
                "source_system": self.provenance.source_system,
                "parser_version": self.provenance.parser_version,
                "parsed_at": self.provenance.parsed_at,
                "ruleset_id": self.provenance.ruleset_id,
                "extraction_strategy": self.provenance.extraction_strategy,
            },
            "structured_payloads": [
                {
                    "payload_type": sp.payload_type,
                    "raw_content": sp.raw_content,
                    "parsed_data": sp.parsed_data,
                }
                for sp in self.structured_payloads
            ],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FinanceExtraction":
        """Deserialize from dictionary."""
        doc_class = None
        if data.get("document_classification"):
            dc = data["document_classification"]
            doc_class = DocumentClassification(
                document_type=dc.get("document_type"),
                correspondent=dc.get("correspondent"),
                tags=dc.get("tags", []),
                storage_path=dc.get("storage_path"),
            )

        proposal_data = data["proposal"]
        proposal = TransactionProposal(
            transaction_type=TransactionType(proposal_data["transaction_type"]),
            date=proposal_data["date"],
            amount=Decimal(proposal_data["amount"]),
            currency=proposal_data["currency"],
            description=proposal_data["description"],
            source_account=proposal_data.get("source_account"),
            destination_account=proposal_data.get("destination_account"),
            category=proposal_data.get("category"),
            tags=proposal_data.get("tags", []),
            notes=proposal_data.get("notes"),
            external_id=proposal_data.get("external_id", ""),
            invoice_number=proposal_data.get("invoice_number"),
            due_date=proposal_data.get("due_date"),
            payment_reference=proposal_data.get("payment_reference"),
            total_net=(
                Decimal(proposal_data["total_net"]) if proposal_data.get("total_net") else None
            ),
            tax_amount=(
                Decimal(proposal_data["tax_amount"]) if proposal_data.get("tax_amount") else None
            ),
            tax_rate=Decimal(proposal_data["tax_rate"]) if proposal_data.get("tax_rate") else None,
        )

        conf_data = data["confidence"]
        confidence = ConfidenceScores(
            overall=conf_data["overall"],
            amount=conf_data.get("amount", 0.0),
            date=conf_data.get("date", 0.0),
            currency=conf_data.get("currency", 0.0),
            description=conf_data.get("description", 0.0),
            vendor=conf_data.get("vendor", 0.0),
            invoice_number=conf_data.get("invoice_number", 0.0),
            line_items=conf_data.get("line_items", 0.0),
            review_state=ReviewState(conf_data.get("review_state", "MANUAL")),
        )

        prov_data = data["provenance"]
        provenance = Provenance(
            source_system=prov_data.get("source_system", "paperless"),
            parser_version=prov_data.get("parser_version", ""),
            parsed_at=prov_data.get("parsed_at", ""),
            ruleset_id=prov_data.get("ruleset_id"),
            extraction_strategy=prov_data.get("extraction_strategy"),
        )

        line_items = []
        for item_data in data.get("line_items", []):
            line_items.append(
                LineItem(
                    description=item_data["description"],
                    quantity=Decimal(item_data["quantity"]) if item_data.get("quantity") else None,
                    unit_price=(
                        Decimal(item_data["unit_price"]) if item_data.get("unit_price") else None
                    ),
                    total=Decimal(item_data["total"]) if item_data.get("total") else None,
                    tax_rate=Decimal(item_data["tax_rate"]) if item_data.get("tax_rate") else None,
                    position=item_data.get("position"),
                )
            )

        structured_payloads = []
        for sp_data in data.get("structured_payloads", []):
            structured_payloads.append(
                StructuredPayload(
                    payload_type=sp_data["payload_type"],
                    raw_content=sp_data["raw_content"],
                    parsed_data=sp_data.get("parsed_data"),
                )
            )

        return cls(
            paperless_document_id=data["paperless_document_id"],
            source_hash=data["source_hash"],
            paperless_url=data["paperless_url"],
            paperless_title=data.get("paperless_title"),
            raw_text=data["raw_text"],
            document_classification=doc_class,
            proposal=proposal,
            line_items=line_items,
            confidence=confidence,
            provenance=provenance,
            structured_payloads=structured_payloads,
            created_at=data.get("created_at", ""),
        )
