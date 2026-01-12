"""
Dedupe key generation (CRITICAL).

This module defines THE deterministic external_id functions.
This is the ONLY way to generate external IDs in the system.

External ID Formats:
1. For Paperless-originated exports: {hash}:pl:{doc_id}
   - hash = SHA256(amount|date|source|destination)[:16]
   - pl = "paperless link" marker
   - doc_id = Paperless document ID

2. For Firefly transactions (no Paperless link): {hash}
   - hash = SHA256(amount|date|source|destination)[:16]
   - Used to prevent duplicates even without a paperless document

3. LEGACY format (backwards compatible): paperless:{doc_id}:{sha256[:16]}:{amount}:{date}
   - Still parseable for existing transactions
   - New exports should use format #1

The external_id must be:
- Stable: Same inputs always produce same output
- Collision-resistant: Different transactions produce different IDs
- Reproducible: Can be regenerated from stored data
- Deduplicated: Same transaction fields = same hash = blocked duplicate
"""

import hashlib
from dataclasses import dataclass
from decimal import Decimal

# ============================================================================
# SSOT Constants for External ID Generation
# ============================================================================

# Separator between hash and paperless link
EXTERNAL_ID_SEPARATOR = ":"

# Marker indicating a paperless document link
PAPERLESS_LINK_MARKER = "pl"

# Prefix for legacy format (still supported for parsing)
LEGACY_EXTERNAL_ID_PREFIX = "paperless:"

# Length of the hash prefix to use
HASH_PREFIX_LENGTH = 16


@dataclass
class ExternalIdComponents:
    """
    Components used to generate the external_id.

    Store these to allow regeneration and verification.
    """

    document_id: int | None  # None for Firefly-only transactions
    source_hash: str  # Full SHA256 or computed transaction hash
    amount: Decimal
    date: str  # YYYY-MM-DD

    @property
    def hash_prefix(self) -> str:
        """First 16 characters of source hash."""
        return self.source_hash[:HASH_PREFIX_LENGTH]

    @property
    def normalized_amount(self) -> str:
        """Amount normalized to 2 decimal places with dot separator."""
        return f"{self.amount:.2f}"


def _normalize_amount(amount: Decimal | str | float) -> str:
    """
    Normalize amount to consistent format for hashing.

    Args:
        amount: Amount in various formats

    Returns:
        Normalized amount string with 2 decimal places
    """
    if isinstance(amount, str):
        # Handle comma as decimal separator (European format)
        amount = amount.replace(",", ".")
        amount = Decimal(amount)
    elif isinstance(amount, float):
        amount = Decimal(str(amount))
    elif not isinstance(amount, Decimal):
        raise ValueError(f"amount must be Decimal, str, or float, got: {type(amount)}")

    return f"{amount:.2f}"


def _normalize_string(value: str | None) -> str:
    """Normalize a string for hashing (lowercase, strip whitespace)."""
    if not value:
        return ""
    return value.strip().lower()


def compute_transaction_hash(
    amount: Decimal | str | float,
    date: str,
    source: str | None = None,
    destination: str | None = None,
    description: str | None = None,
) -> str:
    """
    Compute a deterministic hash for a transaction based on its core fields.

    This is the SSOT function for generating transaction identity hashes.
    The hash is used to prevent duplicates regardless of the data source.

    Hash components (in order):
    - amount: Normalized to 2 decimal places
    - date: YYYY-MM-DD format
    - source: Source account name (normalized)
    - destination: Destination account name (normalized)
    - description: Transaction description (normalized, optional)

    Args:
        amount: Transaction amount
        date: Transaction date (YYYY-MM-DD)
        source: Source account name
        destination: Destination account name
        description: Transaction description (optional, improves uniqueness)

    Returns:
        64-character lowercase hex SHA256 hash

    Examples:
        >>> compute_transaction_hash("10.50", "2024-01-15", "Checking", "Groceries")
        '7a8b9c...'  # Deterministic hash
    """
    # Normalize all components
    normalized_amount = _normalize_amount(amount)
    normalized_date = date.strip() if date else ""
    normalized_source = _normalize_string(source)
    normalized_dest = _normalize_string(destination)
    normalized_desc = _normalize_string(description)

    # Build canonical string for hashing
    # Use pipe separator to avoid collisions
    canonical = f"{normalized_amount}|{normalized_date}|{normalized_source}|{normalized_dest}|{normalized_desc}"

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_external_id_v2(
    amount: Decimal | str | float,
    date: str,
    source: str | None = None,
    destination: str | None = None,
    description: str | None = None,
    document_id: int | None = None,
) -> str:
    """
    Generate external_id using the new hash-based format (v2).

    Format with Paperless link: {hash[:16]}:pl:{doc_id}
    Format without Paperless link: {hash[:16]}

    Args:
        amount: Transaction amount
        date: Transaction date (YYYY-MM-DD)
        source: Source account name
        destination: Destination account name
        description: Transaction description (optional)
        document_id: Paperless document ID (optional)

    Returns:
        External ID string

    Examples:
        >>> generate_external_id_v2("10.50", "2024-01-15", "Checking", "Store", document_id=123)
        '7a8b9c0d1e2f3456:pl:123'

        >>> generate_external_id_v2("10.50", "2024-01-15", "Checking", "Store")
        '7a8b9c0d1e2f3456'
    """
    # Validate date format
    if not date or len(date) < 10:
        raise ValueError(f"date must be in YYYY-MM-DD format, got: {date}")

    full_hash = compute_transaction_hash(amount, date, source, destination, description)
    hash_prefix = full_hash[:HASH_PREFIX_LENGTH]

    if document_id is not None:
        if not isinstance(document_id, int) or document_id < 0:
            raise ValueError(f"document_id must be a non-negative integer, got: {document_id}")
        return f"{hash_prefix}{EXTERNAL_ID_SEPARATOR}{PAPERLESS_LINK_MARKER}{EXTERNAL_ID_SEPARATOR}{document_id}"

    return hash_prefix


def generate_external_id(
    document_id: int,
    source_hash: str,
    amount: Decimal | str | float,
    date: str,
) -> str:
    """
    Generate deterministic external_id for Firefly III (LEGACY format).

    DEPRECATED: Use generate_external_id_v2() for new code.

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

    normalized_amount = _normalize_amount(amount)

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

    Supports both legacy and v2 formats:
    - Legacy: paperless:{doc_id}:{hash[:16]}:{amount}:{date}
    - V2 with link: {hash[:16]}:pl:{doc_id}
    - V2 without link: {hash[:16]}

    Args:
        external_id: The external_id string to parse

    Returns:
        ExternalIdComponents with parsed values

    Raises:
        ValueError: If the external_id format is invalid
    """
    if not external_id:
        raise ValueError("external_id cannot be empty")

    # Check for legacy format first
    if external_id.startswith(LEGACY_EXTERNAL_ID_PREFIX):
        parts = external_id.split(":")
        if len(parts) != 5:
            raise ValueError(
                f"Invalid legacy external_id format, expected 5 parts, got {len(parts)}"
            )

        try:
            document_id = int(parts[1])
            hash_prefix = parts[2]
            amount = Decimal(parts[3])
            date = parts[4]
        except (ValueError, IndexError) as e:
            raise ValueError(f"Failed to parse legacy external_id components: {e}") from e

        return ExternalIdComponents(
            document_id=document_id,
            source_hash=hash_prefix,
            amount=amount,
            date=date,
        )

    # V2 format: either {hash} or {hash}:pl:{doc_id}
    parts = external_id.split(EXTERNAL_ID_SEPARATOR)

    if len(parts) == 1:
        # Hash-only format (no Paperless link)
        hash_prefix = parts[0]
        if len(hash_prefix) != HASH_PREFIX_LENGTH:
            raise ValueError(
                f"Invalid hash length, expected {HASH_PREFIX_LENGTH}, got {len(hash_prefix)}"
            )
        return ExternalIdComponents(
            document_id=None,
            source_hash=hash_prefix,
            amount=Decimal("0"),  # Not encoded in v2 hash-only format
            date="",  # Not encoded in v2 hash-only format
        )

    if len(parts) == 3 and parts[1] == PAPERLESS_LINK_MARKER:
        # V2 format with Paperless link: {hash}:pl:{doc_id}
        hash_prefix = parts[0]
        if len(hash_prefix) != HASH_PREFIX_LENGTH:
            raise ValueError(
                f"Invalid hash length, expected {HASH_PREFIX_LENGTH}, got {len(hash_prefix)}"
            )
        try:
            document_id = int(parts[2])
        except ValueError as e:
            raise ValueError(f"Invalid document_id in external_id: {parts[2]}") from e

        return ExternalIdComponents(
            document_id=document_id,
            source_hash=hash_prefix,
            amount=Decimal("0"),  # Not encoded in v2 format
            date="",  # Not encoded in v2 format
        )

    raise ValueError(f"Unrecognized external_id format: {external_id[:30]}")


def is_spark_external_id(external_id: str | None) -> bool:
    """
    Check if an external_id was generated by Spark (either v1 or v2 format).

    Args:
        external_id: External ID to check

    Returns:
        True if this is a Spark-generated external_id
    """
    if not external_id:
        return False

    # Legacy format
    if external_id.startswith(LEGACY_EXTERNAL_ID_PREFIX):
        return True

    # V2 format with paperless link
    parts = external_id.split(EXTERNAL_ID_SEPARATOR)
    if len(parts) == 3 and parts[1] == PAPERLESS_LINK_MARKER:
        return True

    return False


def extract_document_id_from_external_id(external_id: str | None) -> int | None:
    """
    Extract Paperless document ID from an external_id if present.

    Args:
        external_id: External ID to parse

    Returns:
        Document ID if found, None otherwise
    """
    if not external_id:
        return None

    try:
        components = parse_external_id(external_id)
        return components.document_id
    except ValueError:
        return None
