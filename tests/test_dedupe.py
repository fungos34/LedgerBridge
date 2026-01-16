"""Tests for dedupe module - external_id generation."""

from decimal import Decimal

import pytest

from paperless_firefly.schemas.dedupe import (
    HASH_PREFIX_LENGTH,
    PAPERLESS_LINK_MARKER,
    compute_file_hash,
    compute_transaction_hash,
    extract_document_id_from_external_id,
    generate_external_id,
    generate_external_id_v2,
    is_spark_external_id,
    parse_external_id,
)


class TestGenerateExternalId:
    """Tests for external_id generation (legacy format)."""

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


class TestGenerateExternalIdV2:
    """Tests for external_id v2 generation (hash-based format)."""

    def test_basic_generation_with_doc_id(self):
        """Test v2 external_id generation with document ID."""
        external_id = generate_external_id_v2(
            amount="35.70",
            date="2024-11-18",
            source="Checking Account",
            destination="Grocery Store",
            document_id=123,
        )

        # Format: {hash}:pl:{doc_id}
        parts = external_id.split(":")
        assert len(parts) == 3
        assert len(parts[0]) == HASH_PREFIX_LENGTH
        assert parts[1] == PAPERLESS_LINK_MARKER
        assert parts[2] == "123"

    def test_basic_generation_without_doc_id(self):
        """Test v2 external_id generation without document ID."""
        external_id = generate_external_id_v2(
            amount="35.70",
            date="2024-11-18",
            source="Checking Account",
            destination="Grocery Store",
        )

        # Format: {hash} (no suffix)
        assert ":" not in external_id
        assert len(external_id) == HASH_PREFIX_LENGTH

    def test_deterministic(self):
        """Same inputs produce same output (deterministic)."""
        params = {
            "amount": "100.00",
            "date": "2024-01-15",
            "source": "Bank Account",
            "destination": "Store",
            "document_id": 456,
        }

        id1 = generate_external_id_v2(**params)
        id2 = generate_external_id_v2(**params)
        id3 = generate_external_id_v2(**params)

        assert id1 == id2 == id3

    def test_different_amounts_different_hashes(self):
        """Different amounts produce different hashes."""
        base = {
            "date": "2024-01-01",
            "source": "Account A",
            "destination": "Account B",
        }

        id1 = generate_external_id_v2(amount="10.00", **base)
        id2 = generate_external_id_v2(amount="20.00", **base)

        assert id1 != id2

    def test_different_sources_different_hashes(self):
        """Different source accounts produce different hashes."""
        base = {
            "amount": "10.00",
            "date": "2024-01-01",
            "destination": "Store",
        }

        id1 = generate_external_id_v2(source="Checking", **base)
        id2 = generate_external_id_v2(source="Savings", **base)

        assert id1 != id2

    def test_case_insensitive_accounts(self):
        """Account names are normalized (case insensitive)."""
        params = {
            "amount": "10.00",
            "date": "2024-01-01",
            "destination": "Store",
        }

        id1 = generate_external_id_v2(source="Checking", **params)
        id2 = generate_external_id_v2(source="checking", **params)
        id3 = generate_external_id_v2(source="CHECKING", **params)

        assert id1 == id2 == id3


class TestComputeTransactionHash:
    """Tests for compute_transaction_hash function."""

    def test_deterministic(self):
        """Same inputs produce same hash."""
        hash1 = compute_transaction_hash("10.00", "2024-01-01", "Source", "Dest")
        hash2 = compute_transaction_hash("10.00", "2024-01-01", "Source", "Dest")

        assert hash1 == hash2

    def test_hash_format(self):
        """Hash is 64-character lowercase hex."""
        result = compute_transaction_hash("10.00", "2024-01-01", "Source", "Dest")

        assert len(result) == 64
        assert result == result.lower()
        assert all(c in "0123456789abcdef" for c in result)

    def test_none_accounts_handled(self):
        """None values for accounts are handled."""
        # Should not raise
        result = compute_transaction_hash("10.00", "2024-01-01", None, None)
        assert len(result) == 64

    def test_description_affects_hash(self):
        """Description changes the hash."""
        hash1 = compute_transaction_hash(
            "10.00", "2024-01-01", "Source", "Dest", description="Groceries"
        )
        hash2 = compute_transaction_hash(
            "10.00", "2024-01-01", "Source", "Dest", description="Rent"
        )

        assert hash1 != hash2


class TestParseExternalId:
    """Tests for external_id parsing."""

    def test_roundtrip_legacy(self):
        """Generate then parse legacy format should return original components."""
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

    def test_parse_v2_with_doc_id(self):
        """Parse v2 format with document ID."""
        external_id = "abcdef1234567890:pl:123"
        parsed = parse_external_id(external_id)

        assert parsed.document_id == 123
        assert parsed.hash_prefix == "abcdef1234567890"

    def test_parse_v2_without_doc_id(self):
        """Parse v2 format without document ID (hash only)."""
        external_id = "abcdef1234567890"
        parsed = parse_external_id(external_id)

        assert parsed.document_id is None
        assert parsed.hash_prefix == "abcdef1234567890"

    def test_invalid_prefix(self):
        """Unrecognized format raises ValueError."""
        with pytest.raises(ValueError, match="Unrecognized"):
            parse_external_id("firefly:123:abc:10.00:2024-01-01")

    def test_invalid_format(self):
        """Invalid format raises ValueError."""
        with pytest.raises(ValueError, match="format"):
            parse_external_id("paperless:123:abc")

    def test_invalid_v2_hash_length(self):
        """V2 format with wrong hash length raises ValueError."""
        with pytest.raises(ValueError, match="hash length"):
            parse_external_id("short:pl:123")


class TestIsSparkExternalId:
    """Tests for is_spark_external_id function."""

    def test_legacy_format(self):
        """Legacy format is recognized as Spark ID."""
        assert is_spark_external_id("paperless:123:abc123:10.00:2024-01-01") is True

    def test_v2_format_with_doc_id(self):
        """V2 format with doc ID is recognized as Spark ID."""
        assert is_spark_external_id("abcdef1234567890:pl:123") is True

    def test_hash_only_not_spark(self):
        """Hash-only format is NOT a Spark ID (no paperless link)."""
        assert is_spark_external_id("abcdef1234567890") is False

    def test_none_not_spark(self):
        """None is not a Spark ID."""
        assert is_spark_external_id(None) is False

    def test_empty_string_not_spark(self):
        """Empty string is not a Spark ID."""
        assert is_spark_external_id("") is False


class TestExtractDocumentIdFromExternalId:
    """Tests for extract_document_id_from_external_id function."""

    def test_legacy_format(self):
        """Extract doc ID from legacy format."""
        doc_id = extract_document_id_from_external_id("paperless:456:abc123:10.00:2024-01-01")
        assert doc_id == 456

    def test_v2_format(self):
        """Extract doc ID from v2 format."""
        doc_id = extract_document_id_from_external_id("abcdef1234567890:pl:789")
        assert doc_id == 789

    def test_hash_only_no_doc_id(self):
        """Hash-only format has no document ID."""
        doc_id = extract_document_id_from_external_id("abcdef1234567890")
        assert doc_id is None

    def test_none_returns_none(self):
        """None input returns None."""
        assert extract_document_id_from_external_id(None) is None


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
