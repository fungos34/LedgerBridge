"""Tests for Firefly payload builder."""

from decimal import Decimal

import pytest

from paperless_firefly.schemas.finance_extraction import (
    FinanceExtraction,
    TransactionProposal,
    ConfidenceScores,
    Provenance,
    DocumentClassification,
    TransactionType,
    ReviewState,
)
from paperless_firefly.schemas.firefly_payload import (
    FireflyTransactionStore,
    FireflyTransactionSplit,
    build_firefly_payload,
    validate_firefly_payload,
)


def create_test_extraction() -> FinanceExtraction:
    """Create a valid test extraction."""
    proposal = TransactionProposal(
        transaction_type=TransactionType.WITHDRAWAL,
        date="2024-11-18",
        amount=Decimal("35.70"),
        currency="EUR",
        description="SPAR FIL. 5631 GRAZ",
        source_account="Checking Account",
        destination_account="SPAR",
        external_id="paperless:1234:abcdef1234567890:35.70:2024-11-18",
    )
    
    confidence = ConfidenceScores(
        overall=0.75,
        amount=0.85,
        date=0.90,
        review_state=ReviewState.REVIEW,
    )
    
    provenance = Provenance(
        parser_version="0.1.0",
        parsed_at="2024-11-19T10:00:00Z",
        extraction_strategy="ocr_heuristic",
    )
    
    classification = DocumentClassification(
        document_type="Receipt",
        correspondent="SPAR",
        tags=["finance/inbox"],
    )
    
    return FinanceExtraction(
        paperless_document_id=1234,
        source_hash="abcdef1234567890" + "0" * 48,
        paperless_url="http://localhost:8000/documents/1234/",
        raw_text="Sample OCR text",
        proposal=proposal,
        confidence=confidence,
        provenance=provenance,
        document_classification=classification,
    )


class TestBuildFireflyPayload:
    """Tests for Firefly payload builder."""
    
    def test_basic_build(self):
        """Build basic payload from extraction."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        
        assert isinstance(payload, FireflyTransactionStore)
        assert len(payload.transactions) == 1
        
        split = payload.transactions[0]
        assert split.type == "withdrawal"
        assert split.date == "2024-11-18"
        assert split.amount == "35.70"
        assert split.description == "SPAR FIL. 5631 GRAZ"
    
    def test_required_fields_present(self):
        """All required Firefly fields are present."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        # Required fields
        assert split.type is not None
        assert split.date is not None
        assert split.amount is not None
        assert split.description is not None
        
        # Our required fields
        assert split.external_id is not None
        assert split.notes is not None
        assert "paperless" in split.notes.lower()
    
    def test_external_id_preserved(self):
        """External ID is correctly preserved."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        assert split.external_id == extraction.proposal.external_id
    
    def test_notes_contain_provenance(self):
        """Notes include document provenance info."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        assert "doc_id=1234" in split.notes
        assert "source_hash=" in split.notes
        assert "confidence=" in split.notes
    
    def test_account_mapping_withdrawal(self):
        """Withdrawal maps source to asset, destination to expense."""
        extraction = create_test_extraction()
        extraction.proposal.transaction_type = TransactionType.WITHDRAWAL
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        assert split.source_name == "Checking Account"
        assert split.destination_name == "SPAR"
    
    def test_account_mapping_deposit(self):
        """Deposit maps source to revenue, destination to asset."""
        extraction = create_test_extraction()
        extraction.proposal.transaction_type = TransactionType.DEPOSIT
        extraction.proposal.source_account = "Client A"
        extraction.proposal.destination_account = "Checking Account"
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        assert split.source_name == "Client A"
        assert split.destination_name == "Checking Account"
    
    def test_tags_include_paperless(self):
        """Tags always include 'paperless' for tracking."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        split = payload.transactions[0]
        
        assert "paperless" in split.tags
    
    def test_to_dict_format(self):
        """Payload converts to correct JSON structure."""
        extraction = create_test_extraction()
        
        payload = build_firefly_payload(extraction)
        data = payload.to_dict()
        
        assert "transactions" in data
        assert isinstance(data["transactions"], list)
        assert "error_if_duplicate_hash" in data
        assert "apply_rules" in data
    
    def test_missing_date_raises(self):
        """Missing date raises ValueError."""
        extraction = create_test_extraction()
        extraction.proposal.date = ""
        
        with pytest.raises(ValueError, match="date"):
            build_firefly_payload(extraction)
    
    def test_missing_amount_raises(self):
        """Missing amount raises ValueError."""
        extraction = create_test_extraction()
        extraction.proposal.amount = Decimal("0")
        
        with pytest.raises(ValueError, match="amount"):
            build_firefly_payload(extraction)
    
    def test_missing_external_id_raises(self):
        """Missing external_id raises ValueError."""
        extraction = create_test_extraction()
        extraction.proposal.external_id = ""
        
        with pytest.raises(ValueError, match="external_id"):
            build_firefly_payload(extraction)


class TestValidateFireflyPayload:
    """Tests for payload validation."""
    
    def test_valid_payload(self):
        """Valid payload has no errors."""
        extraction = create_test_extraction()
        payload = build_firefly_payload(extraction)
        
        errors = validate_firefly_payload(payload)
        
        assert errors == []
    
    def test_empty_transactions(self):
        """Empty transactions array is invalid."""
        payload = FireflyTransactionStore(transactions=[])
        
        errors = validate_firefly_payload(payload)
        
        assert len(errors) > 0
        assert any("empty" in e for e in errors)
    
    def test_missing_type(self):
        """Missing type is invalid."""
        split = FireflyTransactionSplit(
            type="",
            date="2024-01-01",
            amount="10.00",
            description="Test",
            external_id="test:123",
            notes="Test notes",
        )
        payload = FireflyTransactionStore(transactions=[split])
        
        errors = validate_firefly_payload(payload)
        
        assert any("type" in e for e in errors)
    
    def test_invalid_type(self):
        """Invalid type value is caught."""
        split = FireflyTransactionSplit(
            type="invalid_type",
            date="2024-01-01",
            amount="10.00",
            description="Test",
            external_id="test:123",
            notes="Test notes",
        )
        payload = FireflyTransactionStore(transactions=[split])
        
        errors = validate_firefly_payload(payload)
        
        assert any("type" in e for e in errors)
    
    def test_negative_amount(self):
        """Negative amount is invalid."""
        split = FireflyTransactionSplit(
            type="withdrawal",
            date="2024-01-01",
            amount="-10.00",
            description="Test",
            external_id="test:123",
            notes="Test notes",
        )
        payload = FireflyTransactionStore(transactions=[split])
        
        errors = validate_firefly_payload(payload)
        
        assert any("positive" in e for e in errors)
    
    def test_missing_external_id(self):
        """Missing external_id is flagged."""
        split = FireflyTransactionSplit(
            type="withdrawal",
            date="2024-01-01",
            amount="10.00",
            description="Test",
            notes="Test notes",
        )
        payload = FireflyTransactionStore(transactions=[split])
        
        errors = validate_firefly_payload(payload)
        
        assert any("external_id" in e for e in errors)


class TestFinanceExtractionSerialization:
    """Tests for FinanceExtraction serialization."""
    
    def test_to_dict_roundtrip(self):
        """Extraction can be serialized and deserialized."""
        original = create_test_extraction()
        
        data = original.to_dict()
        restored = FinanceExtraction.from_dict(data)
        
        assert restored.paperless_document_id == original.paperless_document_id
        assert restored.source_hash == original.source_hash
        assert restored.proposal.amount == original.proposal.amount
        assert restored.proposal.date == original.proposal.date
        assert restored.confidence.overall == original.confidence.overall
    
    def test_to_dict_contains_all_fields(self, sample_extraction_dict):
        """Serialized dict contains all required fields."""
        extraction = FinanceExtraction.from_dict(sample_extraction_dict)
        data = extraction.to_dict()
        
        assert "paperless_document_id" in data
        assert "source_hash" in data
        assert "proposal" in data
        assert "confidence" in data
        assert "provenance" in data
