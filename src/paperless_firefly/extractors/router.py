"""
Extractor router - chooses and applies extraction strategies.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from ..paperless_client import PaperlessDocument
from ..schemas.dedupe import generate_external_id
from ..schemas.finance_extraction import (
    ConfidenceScores,
    DocumentClassification,
    FinanceExtraction,
    LineItem,
    Provenance,
    ReviewState,
    TransactionProposal,
    TransactionType,
)
from .base import BaseExtractor, ExtractionResult
from .einvoice_extractor import EInvoiceExtractor
from .ocr_extractor import OCRTextExtractor


class ExtractorRouter:
    """
    Routes extraction to the appropriate strategy.

    Tries extractors in priority order:
    1. E-Invoice XML (ZUGFeRD, Factur-X, UBL, XRechnung) - highest confidence
    2. PDF text layer - medium confidence
    3. OCR text heuristics - lowest confidence
    """

    VERSION = "0.2.0"

    def __init__(self):
        """Initialize with default extractors."""
        self.extractors: list[BaseExtractor] = [
            # E-invoice extractors (highest priority)
            EInvoiceExtractor(),
            # OCR fallback (lowest priority)
            OCRTextExtractor(),
        ]
        # Sort by priority (highest first)
        self.extractors.sort(key=lambda e: -e.priority)

    def extract(
        self,
        document: PaperlessDocument,
        file_bytes: bytes,
        source_hash: str,
        paperless_base_url: str = "http://localhost:8000",
        default_source_account: str = "Checking Account",
    ) -> FinanceExtraction:
        """
        Extract finance data from a Paperless document.

        Args:
            document: Paperless document with OCR content
            file_bytes: Original file bytes
            source_hash: SHA256 hash of file
            paperless_base_url: Base URL for Paperless links
            default_source_account: Default source account for withdrawals

        Returns:
            FinanceExtraction with proposal and confidence scores
        """
        content = document.content or ""

        # Try extractors in order
        extraction_result: Optional[ExtractionResult] = None
        extractor_name = "none"

        for extractor in self.extractors:
            if extractor.can_extract(content, file_bytes):
                extraction_result = extractor.extract(content, file_bytes)
                extractor_name = extractor.name

                # If we got good results, stop trying
                if extraction_result and extraction_result.amount_confidence > 0.3:
                    break

        # Build FinanceExtraction from result
        if not extraction_result:
            extraction_result = ExtractionResult(extraction_strategy="fallback")

        # Determine transaction type (default to withdrawal for invoices/receipts)
        transaction_type = self._determine_transaction_type(document, extraction_result)

        # Build proposal
        amount = extraction_result.amount or Decimal("0.00")
        date = extraction_result.date or datetime.now().strftime("%Y-%m-%d")
        currency = extraction_result.currency or "EUR"

        # Generate external_id
        external_id = generate_external_id(
            document_id=document.id,
            source_hash=source_hash,
            amount=amount,
            date=date,
        )

        # Determine vendor/destination
        vendor = extraction_result.vendor
        if not vendor and document.correspondent:
            vendor = document.correspondent

        description = extraction_result.description or document.title or f"Document {document.id}"

        # Build notes with provenance
        notes_parts = [
            f"Extracted from Paperless document {document.id}",
        ]
        if extraction_result.invoice_number:
            notes_parts.append(f"Invoice: {extraction_result.invoice_number}")

        proposal = TransactionProposal(
            transaction_type=transaction_type,
            date=date,
            amount=amount,
            currency=currency,
            description=description,
            source_account=(
                default_source_account if transaction_type == TransactionType.WITHDRAWAL else None
            ),
            destination_account=vendor,
            tags=list(document.tags) if document.tags else [],
            notes="; ".join(notes_parts),
            external_id=external_id,
            invoice_number=extraction_result.invoice_number,
            total_net=extraction_result.total_net,
            tax_amount=extraction_result.tax_amount,
            tax_rate=extraction_result.tax_rate,
        )

        # Build confidence scores
        overall_confidence = self._compute_overall_confidence(extraction_result)
        confidence = ConfidenceScores(
            overall=overall_confidence,
            amount=extraction_result.amount_confidence,
            date=extraction_result.date_confidence,
            currency=extraction_result.currency_confidence,
            description=extraction_result.description_confidence,
            vendor=extraction_result.vendor_confidence,
            invoice_number=extraction_result.invoice_number_confidence,
            line_items=extraction_result.line_items_confidence,
        )
        confidence.review_state = confidence.compute_review_state()

        # Build document classification
        classification = DocumentClassification(
            document_type=document.document_type,
            correspondent=document.correspondent,
            tags=document.tags,
        )

        # Build provenance
        provenance = Provenance(
            source_system="paperless",
            parser_version=self.VERSION,
            parsed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            extraction_strategy=extractor_name,
        )

        # Build line items
        line_items = []
        for i, item_data in enumerate(extraction_result.line_items):
            line_items.append(
                LineItem(
                    description=item_data.get("description", ""),
                    quantity=(
                        Decimal(str(item_data["quantity"])) if item_data.get("quantity") else None
                    ),
                    unit_price=(
                        Decimal(str(item_data["unit_price"]))
                        if item_data.get("unit_price")
                        else None
                    ),
                    total=Decimal(str(item_data["total"])) if item_data.get("total") else None,
                    tax_rate=(
                        Decimal(str(item_data["tax_rate"])) if item_data.get("tax_rate") else None
                    ),
                    position=i + 1,
                )
            )

        return FinanceExtraction(
            paperless_document_id=document.id,
            source_hash=source_hash,
            paperless_url=f"{paperless_base_url}/documents/{document.id}/",
            paperless_title=document.title,
            raw_text=content,
            document_classification=classification,
            proposal=proposal,
            line_items=line_items,
            confidence=confidence,
            provenance=provenance,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def _determine_transaction_type(
        self,
        document: PaperlessDocument,
        result: ExtractionResult,
    ) -> TransactionType:
        """Determine transaction type from document context."""
        # Check document type
        doc_type = (document.document_type or "").lower()

        # Receipts and invoices are typically withdrawals (expenses)
        if any(kw in doc_type for kw in ["receipt", "invoice", "rechnung", "beleg", "quittung"]):
            return TransactionType.WITHDRAWAL

        # Credit notes, refunds might be deposits
        if any(kw in doc_type for kw in ["credit", "gutschrift", "refund", "rückerstattung"]):
            return TransactionType.DEPOSIT

        # Check tags
        tags_lower = [t.lower() for t in document.tags] if document.tags else []
        if any("income" in t or "einnahme" in t for t in tags_lower):
            return TransactionType.DEPOSIT

        # Default to withdrawal (most common for document-based transactions)
        return TransactionType.WITHDRAWAL

    def _compute_overall_confidence(self, result: ExtractionResult) -> float:
        """
        Compute overall confidence from individual field confidences.

        Weights:
        - Amount: 40% (most critical)
        - Date: 30% (very important)
        - Vendor: 20% (important for categorization)
        - Other: 10%
        """
        weights = {
            "amount": 0.4,
            "date": 0.3,
            "vendor": 0.2,
            "other": 0.1,
        }

        # Calculate weighted average
        other_avg = (
            result.currency_confidence
            + result.description_confidence
            + result.invoice_number_confidence
        ) / 3.0

        overall = (
            result.amount_confidence * weights["amount"]
            + result.date_confidence * weights["date"]
            + result.vendor_confidence * weights["vendor"]
            + other_avg * weights["other"]
        )

        # Clamp to [0, 1]
        return max(0.0, min(1.0, overall))
