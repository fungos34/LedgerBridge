"""
Linkage marker constants and utilities (SSOT).

This module defines THE single source of truth for linkage markers
used to connect Paperless documents to Firefly transactions.

Linkage Markers:
1. external_id: Two formats supported
   - Legacy: "paperless:{doc_id}:{hash}:{amount}:{date}"
   - V2: "{hash}:pl:{doc_id}" (new format for better deduplication)
2. internal_reference: "PAPERLESS:{doc_id}"
3. notes: "Paperless doc_id={doc_id}"

A transaction is "unlinked" (not connected to Spark/LedgerBridge) if:
- external_id is NOT a Spark-generated ID AND
- internal_reference does NOT contain INTERNAL_REFERENCE_PREFIX AND
- notes do NOT contain NOTES_MARKER_PREFIX
"""

from typing import NamedTuple

from .dedupe import (
    LEGACY_EXTERNAL_ID_PREFIX,
    PAPERLESS_LINK_MARKER,
    extract_document_id_from_external_id,
    is_spark_external_id,
)

# === Linkage Marker Constants ===

# External ID prefix for Paperless-originated transactions (legacy format)
EXTERNAL_ID_PREFIX = LEGACY_EXTERNAL_ID_PREFIX

# Internal reference format
INTERNAL_REFERENCE_PREFIX = "PAPERLESS:"

# Notes marker
NOTES_MARKER_PREFIX = "Paperless doc_id="


class LinkageMarkers(NamedTuple):
    """Linkage markers for a transaction."""

    external_id: str | None
    internal_reference: str | None
    notes_marker: str | None


def build_linkage_markers(document_id: int, external_id: str) -> LinkageMarkers:
    """
    Build all linkage markers for a document.

    Args:
        document_id: Paperless document ID
        external_id: Full external_id (e.g., "paperless:123:abc:10.00:2024-01-01" or "abc123:pl:123")

    Returns:
        LinkageMarkers tuple with all three markers
    """
    return LinkageMarkers(
        external_id=external_id,
        internal_reference=f"{INTERNAL_REFERENCE_PREFIX}{document_id}",
        notes_marker=f"{NOTES_MARKER_PREFIX}{document_id}",
    )


def is_linked_to_spark(
    external_id: str | None,
    internal_reference: str | None,
    notes: str | None,
) -> bool:
    """
    Check if a transaction is linked to Spark/LedgerBridge.

    A transaction is linked if ANY of the following are true:
    - external_id is a Spark-generated ID (legacy or v2 format)
    - internal_reference contains "PAPERLESS:"
    - notes contain "Paperless doc_id="

    Args:
        external_id: Transaction's external_id
        internal_reference: Transaction's internal_reference
        notes: Transaction's notes

    Returns:
        True if linked, False otherwise
    """
    # Check external_id using the SSOT function from dedupe
    if is_spark_external_id(external_id):
        return True
    if internal_reference and INTERNAL_REFERENCE_PREFIX in internal_reference:
        return True
    if notes and NOTES_MARKER_PREFIX in notes:
        return True
    return False


def extract_document_id_from_markers(
    external_id: str | None = None,
    internal_reference: str | None = None,
    notes: str | None = None,
) -> int | None:
    """
    Extract Paperless document ID from linkage markers.

    Tries external_id first (both v2 and legacy formats), then internal_reference, then notes.

    Returns:
        Document ID if found, None otherwise
    """
    # Try external_id using SSOT function (handles both v2 and legacy formats)
    doc_id = extract_document_id_from_external_id(external_id)
    if doc_id is not None:
        return doc_id

    # Try internal_reference (format: PAPERLESS:{doc_id})
    if internal_reference and INTERNAL_REFERENCE_PREFIX in internal_reference:
        try:
            # Find the prefix and extract the number after it
            start = internal_reference.find(INTERNAL_REFERENCE_PREFIX)
            if start >= 0:
                rest = internal_reference[start + len(INTERNAL_REFERENCE_PREFIX) :]
                # Extract until non-digit
                num_str = ""
                for char in rest:
                    if char.isdigit():
                        num_str += char
                    else:
                        break
                if num_str:
                    return int(num_str)
        except ValueError:
            pass

    # Try notes (format: ...Paperless doc_id={doc_id}...)
    if notes and NOTES_MARKER_PREFIX in notes:
        try:
            start = notes.find(NOTES_MARKER_PREFIX)
            if start >= 0:
                rest = notes[start + len(NOTES_MARKER_PREFIX) :]
                num_str = ""
                for char in rest:
                    if char.isdigit():
                        num_str += char
                    else:
                        break
                if num_str:
                    return int(num_str)
        except ValueError:
            pass

    return None
