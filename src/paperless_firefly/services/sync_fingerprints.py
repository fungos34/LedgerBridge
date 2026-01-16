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

from ..schemas.dedupe import generate_external_id_v2


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


def compute_budget_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly budget.

    Fingerprint components:
    - name (normalized: lowercase, stripped)

    Auto-budget settings are excluded as they may vary between instances.
    """
    name = str(data.get("name", "")).lower().strip()
    if not name:
        raise ValueError("Budget must have a name")

    content = f"budget:{name}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_rule_group_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly rule group.

    Fingerprint components:
    - title (normalized: lowercase, stripped)

    Order is excluded as it may vary between instances.
    """
    title = str(data.get("title", "")).lower().strip()
    if not title:
        raise ValueError("Rule group must have a title")

    content = f"rule_group:{title}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_currency_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly currency.

    Fingerprint components:
    - code (normalized: uppercase, stripped)

    Code is the only identifier needed as currencies are globally unique.
    """
    code = str(data.get("code", "")).upper().strip()
    if not code:
        raise ValueError("Currency must have a code")

    content = f"currency:{code}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_bill_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly bill.

    Fingerprint components:
    - name (normalized)
    - amount_min (normalized to 2 decimal places)
    - amount_max (normalized to 2 decimal places)

    Date and repeat frequency are excluded as they may vary.
    """
    name = str(data.get("name", "")).lower().strip()
    if not name:
        raise ValueError("Bill must have a name")

    # Normalize amounts
    amount_min = data.get("amount_min")
    amount_max = data.get("amount_max")

    try:
        amount_min = f"{float(amount_min):.2f}" if amount_min else "0.00"
    except (ValueError, TypeError):
        amount_min = "0.00"

    try:
        amount_max = f"{float(amount_max):.2f}" if amount_max else "0.00"
    except (ValueError, TypeError):
        amount_max = "0.00"

    content = f"bill:{name}:{amount_min}:{amount_max}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_rule_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly rule.

    Fingerprint components:
    - title (normalized)
    - triggers hash (sorted JSON of trigger definitions)
    - actions hash (sorted JSON of action definitions)

    This captures the semantic content of the rule.
    """
    title = str(data.get("title", "")).lower().strip()
    if not title:
        raise ValueError("Rule must have a title")

    # Hash triggers - sort for determinism
    triggers = data.get("triggers", [])
    if triggers:
        # Extract only type and value for fingerprinting
        trigger_data = [{"type": t.get("type", ""), "value": t.get("value", "")} for t in triggers]
        triggers_str = json.dumps(
            sorted(trigger_data, key=lambda t: (t["type"], t["value"])), sort_keys=True
        )
    else:
        triggers_str = "[]"
    triggers_hash = hashlib.sha256(triggers_str.encode()).hexdigest()[:8]

    # Hash actions - sort for determinism
    actions = data.get("actions", [])
    if actions:
        # Extract only type and value for fingerprinting
        action_data = [{"type": a.get("type", ""), "value": a.get("value", "")} for a in actions]
        actions_str = json.dumps(
            sorted(action_data, key=lambda a: (a["type"], a["value"])), sort_keys=True
        )
    else:
        actions_str = "[]"
    actions_hash = hashlib.sha256(actions_str.encode()).hexdigest()[:8]

    content = f"rule:{title}:{triggers_hash}:{actions_hash}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_recurrence_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly recurrence.

    Fingerprint components:
    - title (normalized)
    - first_date
    - repeat_freq

    This identifies the recurrence pattern.
    """
    title = str(data.get("title", "")).lower().strip()
    if not title:
        raise ValueError("Recurrence must have a title")

    first_date = str(data.get("first_date", ""))[:10]  # YYYY-MM-DD
    repeat_freq = str(data.get("repeat_freq", "")).lower().strip()

    content = f"recurrence:{title}:{first_date}:{repeat_freq}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_transaction_fingerprint(data: dict[str, Any]) -> str:
    """
    Compute fingerprint for a Firefly transaction.

    Uses the same algorithm as generate_external_id_v2 for consistency
    with the existing deduplication system.

    Fingerprint components:
    - date (YYYY-MM-DD)
    - amount
    - source_name
    - destination_name
    - description
    """
    date = str(data.get("date", ""))[:10]  # YYYY-MM-DD
    amount = str(data.get("amount", "0"))
    source_name = data.get("source_name", "") or ""
    destination_name = data.get("destination_name", "") or ""
    description = data.get("description", "") or ""

    # Use the existing external_id generation for consistency
    return generate_external_id_v2(
        amount=amount,
        date=date,
        source=source_name,
        destination=destination_name,
        description=description,
    )


# Update registry with all fingerprint functions
FINGERPRINT_FUNCTIONS.update(
    {
        "budget": compute_budget_fingerprint,
        "rule_group": compute_rule_group_fingerprint,
        "currency": compute_currency_fingerprint,
        "bill": compute_bill_fingerprint,
        "rule": compute_rule_fingerprint,
        "recurrence": compute_recurrence_fingerprint,
        "transaction": compute_transaction_fingerprint,
    }
)


def normalize_entity_data(entity_type: str, raw_data: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize entity data for storage and display.

    Extracts relevant fields and provides consistent structure.

    Args:
        entity_type: One of the supported entity types
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
    elif entity_type == "budget":
        return {
            "name": raw_data.get("name", ""),
            "auto_budget_type": raw_data.get("auto_budget_type"),
            "auto_budget_amount": raw_data.get("auto_budget_amount"),
            "auto_budget_period": raw_data.get("auto_budget_period"),
            "notes": raw_data.get("notes"),
        }
    elif entity_type == "rule_group":
        return {
            "title": raw_data.get("title", ""),
            "order": raw_data.get("order"),
            "active": raw_data.get("active", True),
            "description": raw_data.get("description"),
        }
    elif entity_type == "currency":
        return {
            "code": raw_data.get("code", ""),
            "name": raw_data.get("name", ""),
            "symbol": raw_data.get("symbol", ""),
            "decimal_places": raw_data.get("decimal_places", 2),
            "enabled": raw_data.get("enabled", False),
        }
    elif entity_type == "bill":
        return {
            "name": raw_data.get("name", ""),
            "amount_min": raw_data.get("amount_min"),
            "amount_max": raw_data.get("amount_max"),
            "date": raw_data.get("date"),
            "repeat_freq": raw_data.get("repeat_freq"),
            "skip": raw_data.get("skip", 0),
            "active": raw_data.get("active", True),
            "notes": raw_data.get("notes"),
        }
    elif entity_type == "rule":
        return {
            "title": raw_data.get("title", ""),
            "rule_group_id": raw_data.get("rule_group_id"),
            "rule_group_title": raw_data.get("rule_group_title"),
            "order": raw_data.get("order"),
            "active": raw_data.get("active", True),
            "strict": raw_data.get("strict", False),
            "triggers": raw_data.get("triggers", []),
            "actions": raw_data.get("actions", []),
            "description": raw_data.get("description"),
        }
    elif entity_type == "recurrence":
        return {
            "title": raw_data.get("title", ""),
            "first_date": raw_data.get("first_date"),
            "repeat_freq": raw_data.get("repeat_freq"),
            "latest_date": raw_data.get("latest_date"),
            "repetitions": raw_data.get("repetitions", []),
            "transactions": raw_data.get("transactions", []),
            "notes": raw_data.get("notes"),
            "active": raw_data.get("active", True),
        }
    elif entity_type == "transaction":
        return {
            "type": raw_data.get("type", ""),
            "date": raw_data.get("date", ""),
            "amount": raw_data.get("amount", ""),
            "description": raw_data.get("description", ""),
            "source_name": raw_data.get("source_name"),
            "destination_name": raw_data.get("destination_name"),
            "category_name": raw_data.get("category_name"),
            "tags": raw_data.get("tags", []),
            "notes": raw_data.get("notes"),
            "external_id": raw_data.get("external_id"),
            "internal_reference": raw_data.get("internal_reference"),
            "splits": raw_data.get("splits", []),
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
    elif entity_type in ("rule_group", "rule", "recurrence"):
        return data.get("title", "Unknown")
    elif entity_type == "currency":
        code = data.get("code", "")
        name = data.get("name", "")
        return f"{code} - {name}" if name else code or "Unknown"
    elif entity_type == "transaction":
        desc = data.get("description", "")
        date = str(data.get("date", ""))[:10]
        amount = data.get("amount", "")
        return f"{date}: {desc} ({amount})" if desc else f"{date}: ({amount})"
    return data.get("name", "Unknown")
