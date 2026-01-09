"""
Centralized field help text for form tooltips (SSOT).

This module defines help text for all form fields in the review UI.
All tooltips should be sourced from here to maintain consistency.

Per AGENT_ARCHITECTURE.md Section 4: No duplicated literals across modules.
"""

# Field help text dictionary - SSOT for all form tooltips
FIELD_HELP: dict[str, str] = {
    # Amount & Currency
    "amount": (
        "The total monetary value of this transaction. "
        "For invoices, this is the gross amount including tax. "
        "Must be a positive decimal number."
    ),
    "currency": (
        "The three-letter ISO 4217 currency code (e.g., EUR, USD, GBP). "
        "This should match your Firefly III default currency or the currency "
        "specified on the receipt/invoice."
    ),
    # Date fields
    "transaction_date": (
        "The date the payment was initiated by the payer. "
        "NOT the bank processing date, NOT the receipt date, NOT the date "
        "funds arrived. For invoices, use the invoice date. For receipts, "
        "use the date printed on the receipt. Format: YYYY-MM-DD."
    ),
    "date": (
        "The date the payment was initiated by the payer. "
        "NOT the bank processing date, NOT the receipt date, NOT the date "
        "funds arrived. For invoices, use the invoice date. For receipts, "
        "use the date printed on the receipt. Format: YYYY-MM-DD."
    ),
    "due_date": (
        "The date by which payment is expected (for invoices). "
        "Leave empty for receipts or if not applicable."
    ),
    # Description
    "description": (
        "A brief description of the transaction. "
        "This will appear in your Firefly III transaction list. "
        "Typically: Vendor name + date or invoice purpose."
    ),
    # Accounts
    "source_account": (
        "The bank account from which the payment is made (for withdrawals). "
        "Select your asset account in Firefly III that will be debited. "
        "This is a required field for creating transactions."
    ),
    "destination_account": (
        "The vendor or recipient of the payment (for withdrawals). "
        "For expenses, this is typically the store or service provider name. "
        "Firefly III will create an expense account if it doesn't exist."
    ),
    # Classification
    "category": (
        "The spending category for this transaction (e.g., Groceries, Utilities). "
        "Categories help organize your finances and generate reports in Firefly III. "
        "Select from existing categories or type a new one."
    ),
    "transaction_type": (
        "The type of transaction: "
        "Withdrawal = money going out (expense), "
        "Deposit = money coming in (income), "
        "Transfer = moving between your own accounts."
    ),
    # Reference
    "invoice_number": (
        "The invoice or receipt number for reference. "
        "Useful for matching with bank statements or vendor records. "
        "Leave empty if not visible on the document."
    ),
    # Split transactions
    "split_amount": (
        "The amount for this split line. "
        "The sum of all split amounts must equal the total transaction amount."
    ),
    "split_description": (
        "A description for this specific split line. "
        "Helps identify what portion of the total this split represents."
    ),
    "split_category": (
        "The category for this split line. " "Different splits can have different categories."
    ),
    # Bank transaction linking
    "linked_transaction": (
        "Link this document to an existing bank transaction in Firefly III. "
        "Use this when you already have the transaction imported from your bank "
        "and want to attach the receipt/invoice as documentation."
    ),
    # External ID (read-only)
    "external_id": (
        "Auto-generated unique identifier for deduplication. "
        "Based on document hash, amount, and date. "
        "Changes automatically if amount or date are modified."
    ),
    # LLM settings
    "llm_opt_out": (
        "When enabled, AI will assist with category and split suggestions. "
        "Disable to rely only on rule-based extraction. "
        "AI suggestions are advisory only and require your confirmation."
    ),
}

# Field labels - for consistent labeling across the UI
FIELD_LABELS: dict[str, str] = {
    "amount": "Amount",
    "currency": "Currency",
    "transaction_date": "Transaction Date",
    "date": "Transaction Date",
    "due_date": "Due Date",
    "description": "Description",
    "source_account": "Source Account",
    "destination_account": "Destination / Vendor",
    "category": "Category",
    "transaction_type": "Transaction Type",
    "invoice_number": "Invoice / Receipt Number",
    "split_amount": "Amount",
    "split_description": "Description",
    "split_category": "Category",
    "linked_transaction": "Link to Bank Transaction",
    "external_id": "External ID",
    "llm_opt_out": "Use AI Suggestions",
}

# Required fields - for validation
REQUIRED_FIELDS: set[str] = {
    "amount",
    "currency",
    "date",
    "description",
    "source_account",
    "transaction_type",
}


def get_field_help(field_name: str) -> str:
    """Get help text for a field.

    Args:
        field_name: The field name to look up.

    Returns:
        Help text string, or empty string if not found.
    """
    return FIELD_HELP.get(field_name, "")


def get_field_label(field_name: str) -> str:
    """Get label text for a field.

    Args:
        field_name: The field name to look up.

    Returns:
        Label text string, or the field name titlecased if not found.
    """
    return FIELD_LABELS.get(field_name, field_name.replace("_", " ").title())


def is_required_field(field_name: str) -> bool:
    """Check if a field is required.

    Args:
        field_name: The field name to check.

    Returns:
        True if the field is required.
    """
    return field_name in REQUIRED_FIELDS


def get_all_field_help() -> dict[str, str]:
    """Get all field help text as a dictionary.

    Returns:
        Copy of FIELD_HELP dictionary.
    """
    return FIELD_HELP.copy()
