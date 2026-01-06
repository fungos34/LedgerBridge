"""
Dedupe key generation (CRITICAL).

This module defines THE deterministic external_id function.
This is the ONLY way to generate external IDs in the system.

Format: paperless:{doc_id}:{sha256[:16]}:{amount}:{date}

The external_id must be:
- Stable: Same inputs always produce same output
- Collision-resistant: Different documents produce different IDs
- Reproducible: Can be regenerated from stored data
"""

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Union


@dataclass
class ExternalIdComponents:
    """
    Components used to generate the external_id.

    Store these to allow regeneration and verification.
    """

    document_id: int
    source_hash: str  # Full SHA256
    amount: Decimal
    date: str  # YYYY-MM-DD

    @property
    def hash_prefix(self) -> str:
        """First 16 characters of source hash."""
        return self.source_hash[:16]

    @property
    def normalized_amount(self) -> str:
        """Amount normalized to 2 decimal places with dot separator."""
        return f"{self.amount:.2f}"


def generate_external_id(
    document_id: int,
    source_hash: str,
    amount: Union[Decimal, str, float],
    date: str,
) -> str:
    """
    Generate deterministic external_id for Firefly III.

    Format: paperless:{doc_id}:{sha256[:16]}:{amount}:{date}

    Args:
        document_id: Paperless document ID (integer)
        source_hash: SHA256 hash of original file bytes (64 hex chars)
        amount: Transaction amount (will be normalized to 2 decimals)
        date: Transaction date in YYYY-MM-DD format

    Returns:
        Deterministic external_id string

    Raises:
        ValueError: If any input is invalid

    Examples:
        >>> generate_external_id(1234, "abc123...", Decimal("35.70"), "2024-11-18")
        'paperless:1234:abc123...:35.70:2024-11-18'
    """
    # Validate document_id
    if not isinstance(document_id, int) or document_id < 0:
        raise ValueError(f"document_id must be a non-negative integer, got: {document_id}")

    # Validate source_hash
    if not source_hash or len(source_hash) < 16:
        raise ValueError(
            f"source_hash must be at least 16 characters, got: {len(source_hash) if source_hash else 0}"
        )

    # Validate and normalize amount
    if isinstance(amount, str):
        # Handle comma as decimal separator (European format)
        amount = amount.replace(",", ".")
        amount = Decimal(amount)
    elif isinstance(amount, float):
        amount = Decimal(str(amount))
    elif not isinstance(amount, Decimal):
        raise ValueError(f"amount must be Decimal, str, or float, got: {type(amount)}")

    # Normalize to 2 decimal places
    normalized_amount = f"{amount:.2f}"

    # Validate date format (basic check)
    if not date or len(date) != 10 or date[4] != "-" or date[7] != "-":
        raise ValueError(f"date must be in YYYY-MM-DD format, got: {date}")

    # Generate external_id
    hash_prefix = source_hash[:16]
    external_id = f"paperless:{document_id}:{hash_prefix}:{normalized_amount}:{date}"

    return external_id


def compute_file_hash(file_bytes: bytes) -> str:
    """
    Compute SHA256 hash of file bytes.

    Args:
        file_bytes: Raw file content

    Returns:
        64-character lowercase hex string
    """
    return hashlib.sha256(file_bytes).hexdigest()


def parse_external_id(external_id: str) -> ExternalIdComponents:
    """
    Parse an external_id back into its components.

    Args:
        external_id: The external_id string to parse

    Returns:
        ExternalIdComponents with parsed values

    Raises:
        ValueError: If the external_id format is invalid
    """
    if not external_id.startswith("paperless:"):
        raise ValueError(
            f"Invalid external_id prefix, expected 'paperless:', got: {external_id[:20]}"
        )

    parts = external_id.split(":")
    if len(parts) != 5:
        raise ValueError(f"Invalid external_id format, expected 5 parts, got {len(parts)}")

    try:
        document_id = int(parts[1])
        hash_prefix = parts[2]
        amount = Decimal(parts[3])
        date = parts[4]
    except (ValueError, IndexError) as e:
        raise ValueError(f"Failed to parse external_id components: {e}")

    return ExternalIdComponents(
        document_id=document_id,
        source_hash=hash_prefix,  # Note: Only prefix is stored in external_id
        amount=amount,
        date=date,
    )
