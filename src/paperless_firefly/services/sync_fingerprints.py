"""
Fingerprint utilities for Firefly Sync Assistant.

Fingerprints are stable SHA256 hashes that identify entities across different
Firefly III instances. Unlike Firefly IDs (which are instance-specific),
fingerprints are computed from the entity's semantic content and can be used
to detect duplicates across different users' Firefly instances.

SSOT: This module is the single source of truth for all fingerprint computation.
"""

import hashlib
import json
from typing import Any


def compute_fingerprint(entity_type: str, data: dict[str, Any]) -> str:
    """
    Compute a fingerprint for any supported entity type.

    Args:
        entity_type: One of 'category', 'tag', 'account', 'piggy_bank'
        data: Entity data dictionary

    Returns:
        64-character lowercase hex SHA256 hash

    Raises:
        ValueError: If entity_type is not supported
    """
    if entity_type not in FINGERPRINT_FUNCTIONS:
        raise ValueError(f"Unsupported entity type: {entity_type}")

    return FINGERPRINT_FUNCTIONS[entity_type](data)


def compute_category_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly category.

    Fingerprint components:
    - name (normalized: lowercase, stripped)

    Notes are intentionally excluded as they may vary between instances
    while the category itself is semantically the same.
    """
    name = str(data.get("name", "")).lower().strip()
    if not name:
        raise ValueError("Category must have a name")

    content = f"category:{name}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_tag_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly tag.

    Fingerprint components:
    - tag name (normalized: lowercase, stripped)

    Firefly III uses 'tag' as the field name for tag names.
    Description is excluded as it may vary.
    """
    # Firefly III uses 'tag' for the name field
    name = str(data.get("tag", data.get("name", ""))).lower().strip()
    if not name:
        raise ValueError("Tag must have a name")

    content = f"tag:{name}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_account_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly account.

    Fingerprint components:
    - type (e.g., 'asset', 'expense', 'revenue')
    - name (normalized)
    - currency_code

    These three fields together uniquely identify an account's purpose.
    Other fields like opening_balance are instance-specific.
    """
    account_type = str(data.get("type", "")).lower().strip()
    name = str(data.get("name", "")).lower().strip()
    currency = str(data.get("currency_code", "EUR")).upper().strip()

    if not name:
        raise ValueError("Account must have a name")
    if not account_type:
        raise ValueError("Account must have a type")

    content = f"account:{account_type}:{name}:{currency}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_piggy_bank_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly piggy bank.

    Fingerprint components:
    - name (normalized)
    - target_amount (normalized to 2 decimal places)

    The target amount is included because piggy banks with the same name
    but different goals are semantically different.
    Account linkage is excluded as it's instance-specific.
    """
    name = str(data.get("name", "")).lower().strip()
    if not name:
        raise ValueError("Piggy bank must have a name")

    # Normalize target amount to 2 decimal places
    target_amount = data.get("target_amount")
    if target_amount is not None:
        try:
            target_amount = f"{float(target_amount):.2f}"
        except (ValueError, TypeError):
            target_amount = "0.00"
    else:
        target_amount = "0.00"

    content = f"piggy:{name}:{target_amount}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Registry of fingerprint functions for extensibility
FINGERPRINT_FUNCTIONS = {
    "category": compute_category_fingerprint,
    "tag": compute_tag_fingerprint,
    "account": compute_account_fingerprint,
    "piggy_bank": compute_piggy_bank_fingerprint,
}


def normalize_entity_data(entity_type: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize entity data for storage and display.

    Extracts relevant fields and provides consistent structure.

    Args:
        entity_type: One of 'category', 'tag', 'account', 'piggy_bank'
        raw_data: Raw data from Firefly API (attributes dict)

    Returns:
        Normalized data dictionary
    """
    if entity_type == "category":
        return {
            "name": raw_data.get("name", ""),
            "notes": raw_data.get("notes"),
        }
    elif entity_type == "tag":
        return {
            "tag": raw_data.get("tag", ""),
            "description": raw_data.get("description"),
        }
    elif entity_type == "account":
        return {
            "name": raw_data.get("name", ""),
            "type": raw_data.get("type", ""),
            "currency_code": raw_data.get("currency_code", "EUR"),
            "notes": raw_data.get("notes"),
            "iban": raw_data.get("iban"),
            "account_number": raw_data.get("account_number"),
        }
    elif entity_type == "piggy_bank":
        return {
            "name": raw_data.get("name", ""),
            "target_amount": raw_data.get("target_amount"),
            "current_amount": raw_data.get("current_amount"),
            "notes": raw_data.get("notes"),
        }
    else:
        raise ValueError(f"Unsupported entity type: {entity_type}")


def get_entity_name(entity_type: str, data: dict[str, Any]) -> str:
    """
    Extract display name from entity data.

    Args:
        entity_type: Entity type
        data: Entity data (normalized or raw)

    Returns:
        Display name string
    """
    if entity_type == "tag":
        return data.get("tag", data.get("name", "Unknown"))
    return data.get("name", "Unknown")
