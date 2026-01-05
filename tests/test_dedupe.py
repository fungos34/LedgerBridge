"""Tests for dedupe module - external_id generation."""

from decimal import Decimal

import pytest

from paperless_firefly.schemas.dedupe import (
    generate_external_id,
    compute_file_hash,
    parse_external_id,
    ExternalIdComponents,
)


class TestGenerateExternalId:
    """Tests for external_id generation."""
    
    def test_basic_generation(self):
        """Test basic external_id generation."""
        external_id = generate_external_id(
            document_id=1234,
            source_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            amount=Decimal("35.70"),
            date="2024-11-18",
        )
        
        assert external_id == "paperless:1234:abcdef1234567890:35.70:2024-11-18"
    
    def test_deterministic(self):
        """Same inputs produce same output (deterministic)."""
        params = {
            "document_id": 5678,
            "source_hash": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "amount": Decimal("100.00"),
            "date": "2024-01-15",
        }
        
        id1 = generate_external_id(**params)
        id2 = generate_external_id(**params)
        id3 = generate_external_id(**params)
        
        assert id1 == id2 == id3
    
    def test_amount_normalization(self):
        """Amount is normalized to 2 decimal places."""
        base_params = {
            "document_id": 1,
            "source_hash": "a" * 64,
            "date": "2024-01-01",
        }
        
        # Different input formats, same normalized output
        id1 = generate_external_id(**base_params, amount=Decimal("35.7"))
        id2 = generate_external_id(**base_params, amount="35.70")
        id3 = generate_external_id(**base_params, amount=35.70)
        id4 = generate_external_id(**base_params, amount="35,70")  # European format
        
        assert "35.70" in id1
        assert id1 == id2 == id3 == id4
    
    def test_hash_prefix_used(self):
        """Only first 16 characters of hash are used."""
        long_hash = "abcdef1234567890" + "x" * 48
        
        external_id = generate_external_id(
            document_id=1,
            source_hash=long_hash,
            amount=Decimal("10.00"),
            date="2024-01-01",
        )
        
        assert "abcdef1234567890" in external_id
        assert "x" not in external_id
    
    def test_different_inputs_different_outputs(self):
        """Different inputs produce different outputs (collision resistant)."""
        base = {
            "source_hash": "a" * 64,
            "amount": Decimal("10.00"),
            "date": "2024-01-01",
        }
        
        id1 = generate_external_id(document_id=1, **base)
        id2 = generate_external_id(document_id=2, **base)
        
        assert id1 != id2
    
    def test_invalid_document_id(self):
        """Negative document_id raises ValueError."""
        with pytest.raises(ValueError, match="document_id"):
            generate_external_id(
                document_id=-1,
                source_hash="a" * 64,
                amount=Decimal("10.00"),
                date="2024-01-01",
            )
    
    def test_short_hash(self):
        """Hash shorter than 16 chars raises ValueError."""
        with pytest.raises(ValueError, match="source_hash"):
            generate_external_id(
                document_id=1,
                source_hash="short",
                amount=Decimal("10.00"),
                date="2024-01-01",
            )
    
    def test_invalid_date_format(self):
        """Invalid date format raises ValueError."""
        with pytest.raises(ValueError, match="date"):
            generate_external_id(
                document_id=1,
                source_hash="a" * 64,
                amount=Decimal("10.00"),
                date="18.11.2024",  # Wrong format
            )


class TestParseExternalId:
    """Tests for external_id parsing."""
    
    def test_roundtrip(self):
        """Generate then parse should return original components."""
        original_id = generate_external_id(
            document_id=1234,
            source_hash="abcdef1234567890" + "0" * 48,
            amount=Decimal("35.70"),
            date="2024-11-18",
        )
        
        parsed = parse_external_id(original_id)
        
        assert parsed.document_id == 1234
        assert parsed.hash_prefix == "abcdef1234567890"
        assert parsed.amount == Decimal("35.70")
        assert parsed.date == "2024-11-18"
    
    def test_invalid_prefix(self):
        """Invalid prefix raises ValueError."""
        with pytest.raises(ValueError, match="prefix"):
            parse_external_id("firefly:123:abc:10.00:2024-01-01")
    
    def test_invalid_format(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError, match="format"):
            parse_external_id("paperless:123:abc")


class TestComputeFileHash:
    """Tests for file hash computation."""
    
    def test_deterministic(self):
        """Same bytes produce same hash."""
        data = b"test file content"
        
        hash1 = compute_file_hash(data)
        hash2 = compute_file_hash(data)
        
        assert hash1 == hash2
    
    def test_hash_format(self):
        """Hash is 64-character lowercase hex."""
        data = b"test"
        
        hash_result = compute_file_hash(data)
        
        assert len(hash_result) == 64
        assert hash_result == hash_result.lower()
        assert all(c in "0123456789abcdef" for c in hash_result)
    
    def test_different_content_different_hash(self):
        """Different content produces different hash."""
        hash1 = compute_file_hash(b"content A")
        hash2 = compute_file_hash(b"content B")
        
        assert hash1 != hash2
