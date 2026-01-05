"""Integration tests with mocked HTTP."""

import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

import pytest
import responses

from paperless_firefly.paperless_client import PaperlessClient, PaperlessDocument
from paperless_firefly.firefly_client import FireflyClient
from paperless_firefly.extractors import ExtractorRouter
from paperless_firefly.schemas.firefly_payload import build_firefly_payload
from paperless_firefly.schemas.dedupe import compute_file_hash


class TestPaperlessClientIntegration:
    """Integration tests for Paperless client with mocked API."""
    
    BASE_URL = "http://paperless.local:8000"
    TOKEN = "test-token-123"
    
    @pytest.fixture
    def client(self):
        return PaperlessClient(
            base_url=self.BASE_URL,
            token=self.TOKEN,
        )
    
    @responses.activate
    def test_list_documents(self, client):
        """Test listing documents."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/",
            json={
                "count": 2,
                "next": None,
                "results": [
                    {
                        "id": 1,
                        "title": "Doc 1",
                        "content": "Content 1",
                        "created": "2024-01-01",
                        "added": "2024-01-02T10:00:00Z",
                        "modified": "2024-01-02T10:00:00Z",
                        "correspondent": 1,
                        "document_type": 1,
                        "tags": [1],
                    },
                    {
                        "id": 2,
                        "title": "Doc 2",
                        "content": "Content 2",
                        "created": "2024-01-03",
                        "added": "2024-01-04T10:00:00Z",
                        "modified": "2024-01-04T10:00:00Z",
                        "correspondent": 2,
                        "document_type": 1,
                        "tags": [1, 2],
                    },
                ],
            },
            status=200,
        )
        
        docs = list(client.list_documents())
        
        assert len(docs) == 2
        assert docs[0].id == 1
        assert docs[1].id == 2
    
    @responses.activate
    def test_get_document(self, client):
        """Test getting single document."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/123/",
            json={
                "id": 123,
                "title": "Test Document",
                "content": "OCR content here",
                "created": "2024-11-18",
                "added": "2024-11-19T08:00:00Z",
                "modified": "2024-11-19T08:00:00Z",
                "correspondent": 5,
                "document_type": 2,
                "tags": [1, 3],
                "original_file_name": "receipt.pdf",
            },
            status=200,
        )
        
        # Mock tag/correspondent/type lookups
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/tags/1/",
            json={"id": 1, "name": "finance/inbox"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/tags/3/",
            json={"id": 3, "name": "receipt"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/correspondents/5/",
            json={"id": 5, "name": "SPAR"},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/document_types/2/",
            json={"id": 2, "name": "Receipt"},
            status=200,
        )
        
        doc = client.get_document(123)
        
        assert doc.id == 123
        assert doc.title == "Test Document"
        assert doc.correspondent == "SPAR"
        assert doc.document_type == "Receipt"
        assert "finance/inbox" in doc.tags
    
    @responses.activate
    def test_download_original(self, client):
        """Test downloading original file."""
        file_content = b"PDF file content here"
        
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/documents/123/download/",
            body=file_content,
            status=200,
            headers={"Content-Disposition": 'attachment; filename="receipt.pdf"'},
        )
        
        content, filename = client.download_original(123)
        
        assert content == file_content
        assert "receipt" in filename.lower()
    
    @responses.activate
    def test_connection_test(self, client):
        """Test connection test."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/",
            json={"status": "ok"},
            status=200,
        )
        
        assert client.test_connection() is True
    
    @responses.activate
    def test_connection_failure(self, client):
        """Test connection failure handling."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/",
            body=Exception("Connection refused"),
        )
        
        assert client.test_connection() is False


class TestFireflyClientIntegration:
    """Integration tests for Firefly client with mocked API."""
    
    BASE_URL = "http://firefly.local:8080"
    TOKEN = "firefly-token-456"
    
    @pytest.fixture
    def client(self):
        return FireflyClient(
            base_url=self.BASE_URL,
            token=self.TOKEN,
        )
    
    @responses.activate
    def test_create_transaction(self, client):
        """Test creating a transaction."""
        # Mock search (no existing)
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={"data": []},
            status=200,
        )
        
        # Mock create
        responses.add(
            responses.POST,
            f"{self.BASE_URL}/api/v1/transactions",
            json={
                "data": {
                    "type": "transactions",
                    "id": "999",
                    "attributes": {
                        "transactions": [
                            {
                                "type": "withdrawal",
                                "date": "2024-11-18",
                                "amount": "35.70",
                                "description": "Test",
                            }
                        ]
                    }
                }
            },
            status=200,
        )
        
        from paperless_firefly.schemas.firefly_payload import (
            FireflyTransactionStore,
            FireflyTransactionSplit,
        )
        
        payload = FireflyTransactionStore(
            transactions=[
                FireflyTransactionSplit(
                    type="withdrawal",
                    date="2024-11-18",
                    amount="35.70",
                    description="Test transaction",
                    external_id="test:123",
                    notes="Test notes",
                )
            ]
        )
        
        result = client.create_transaction(payload)
        
        assert result == 999
    
    @responses.activate
    def test_find_existing_transaction(self, client):
        """Test finding existing transaction by external_id."""
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={
                "data": [
                    {
                        "type": "transactions",
                        "id": "123",
                        "attributes": {
                            "transactions": [
                                {
                                    "type": "withdrawal",
                                    "date": "2024-01-01",
                                    "amount": "10.00",
                                    "description": "Existing",
                                    "external_id": "paperless:1:abc:10.00:2024-01-01",
                                }
                            ]
                        }
                    }
                ]
            },
            status=200,
        )
        
        tx = client.find_by_external_id("paperless:1:abc:10.00:2024-01-01")
        
        assert tx is not None
        assert tx.id == 123
        assert tx.external_id == "paperless:1:abc:10.00:2024-01-01"
    
    @responses.activate
    def test_skip_duplicate(self, client):
        """Test skipping duplicate transaction."""
        # Mock search returns existing
        responses.add(
            responses.GET,
            f"{self.BASE_URL}/api/v1/search/transactions",
            json={
                "data": [
                    {
                        "type": "transactions",
                        "id": "123",
                        "attributes": {
                            "transactions": [
                                {
                                    "type": "withdrawal",
                                    "date": "2024-01-01",
                                    "amount": "10.00",
                                    "description": "Existing",
                                    "external_id": "test:123",
                                }
                            ]
                        }
                    }
                ]
            },
            status=200,
        )
        
        from paperless_firefly.schemas.firefly_payload import (
            FireflyTransactionStore,
            FireflyTransactionSplit,
        )
        
        payload = FireflyTransactionStore(
            transactions=[
                FireflyTransactionSplit(
                    type="withdrawal",
                    date="2024-01-01",
                    amount="10.00",
                    description="Test",
                    external_id="test:123",
                    notes="Test",
                )
            ]
        )
        
        # Should return existing ID, not create new
        result = client.create_transaction(payload, skip_duplicates=True)
        
        assert result == 123


class TestEndToEndExtraction:
    """End-to-end extraction tests."""
    
    def test_extraction_to_payload(self, sample_ocr_receipt):
        """Test full extraction to Firefly payload flow."""
        # Create mock document
        doc = PaperlessDocument(
            id=12345,
            title="SPAR Receipt",
            content=sample_ocr_receipt,
            created="2024-11-18",
            added="2024-11-19T08:00:00Z",
            modified="2024-11-19T08:00:00Z",
            correspondent="SPAR",
            document_type="Receipt",
            tags=["finance/inbox"],
        )
        
        # Mock file bytes
        file_bytes = b"PDF content"
        source_hash = compute_file_hash(file_bytes)
        
        # Extract
        router = ExtractorRouter()
        extraction = router.extract(
            document=doc,
            file_bytes=file_bytes,
            source_hash=source_hash,
        )
        
        # Verify extraction
        assert extraction.paperless_document_id == 12345
        assert extraction.proposal.amount > 0
        assert extraction.proposal.date == "2024-11-18"
        assert extraction.proposal.external_id != ""
        
        # Build Firefly payload
        payload = build_firefly_payload(extraction)
        
        # Verify payload
        assert len(payload.transactions) == 1
        split = payload.transactions[0]
        assert split.type == "withdrawal"
        assert split.external_id == extraction.proposal.external_id
        assert "paperless" in split.notes.lower()  # case-insensitive
        
        # Validate payload
        from paperless_firefly.schemas.firefly_payload import validate_firefly_payload
        errors = validate_firefly_payload(payload)
        assert errors == [], f"Validation errors: {errors}"
    
    def test_deterministic_extraction(self, sample_ocr_receipt):
        """Same input produces same output (deterministic)."""
        doc = PaperlessDocument(
            id=100,
            title="Test",
            content=sample_ocr_receipt,
            created="2024-11-18",
            added="2024-11-19T08:00:00Z",
            modified="2024-11-19T08:00:00Z",
        )
        
        file_bytes = b"Test content"
        source_hash = compute_file_hash(file_bytes)
        
        router = ExtractorRouter()
        
        extraction1 = router.extract(doc, file_bytes, source_hash)
        extraction2 = router.extract(doc, file_bytes, source_hash)
        
        # Same external_id
        assert extraction1.proposal.external_id == extraction2.proposal.external_id
        
        # Same amount
        assert extraction1.proposal.amount == extraction2.proposal.amount
        
        # Same date
        assert extraction1.proposal.date == extraction2.proposal.date
