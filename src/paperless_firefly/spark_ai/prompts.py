"""Prompt templates for LLM-assisted categorization.

This module contains prompt templates for the Spark AI service.
Prompts are versioned to support cache invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "v1.1"


@dataclass
class CategoryPrompt:
    """Prompt template for category suggestion.

    Attributes:
        version: Prompt version for cache invalidation.
        system_prompt: System message setting LLM behavior.
        user_template: Template for user message with placeholders.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are a financial categorization assistant.
Your task is to suggest the most appropriate category for a financial transaction
based on the transaction details and available categories.

Rules:
1. Only suggest categories from the provided list
2. If uncertain, suggest the most general applicable category
3. Provide a brief reason for your choice
4. Include a confidence score from 0.0 to 1.0

Respond in JSON format:
{
    "category": "CategoryName",
    "confidence": 0.85,
    "reason": "Brief explanation"
}"""

    user_template: str = """Categorize this transaction:

Transaction Details:
- Amount: {amount}
- Date: {date}
- Vendor/Payee: {vendor}
- Description: {description}

Available Categories:
{categories}

Provide your suggestion in JSON format."""

    def format_user_message(
        self,
        amount: str,
        date: str,
        vendor: str | None,
        description: str | None,
        categories: list[str],
    ) -> str:
        """Format the user message with transaction details.

        Args:
            amount: Transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            categories: List of available category names.

        Returns:
            Formatted user message.
        """
        categories_str = "\n".join(f"- {cat}" for cat in categories)
        return self.user_template.format(
            amount=amount,
            date=date,
            vendor=vendor or "Unknown",
            description=description or "No description",
            categories=categories_str,
        )


@dataclass
class SplitPrompt:
    """Prompt template for split transaction suggestions.

    Used when a transaction might need to be split across categories.
    Enhanced to extract line items with prices from OCR text.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are a financial document analysis assistant specialized in extracting itemized purchases from receipts and invoices.

Your task is to analyze document content (OCR text) and determine:
1. Whether the transaction should be split into multiple line items
2. Extract individual items with their prices and assign appropriate categories

EXTRACTION RULES:
- Look for itemized lists with prices (e.g., "Milk 2.99", "Bread €1.50")
- Parse prices in various formats: 2.99, 2,99, €2.99, EUR 2.99, $2.99
- Handle German/European formats (comma as decimal separator)
- Identify product names and match them to the most appropriate category
- Split amounts MUST sum to the total transaction amount (within ±0.05 tolerance)
- If you cannot extract clear line items, set should_split to false

CATEGORY MATCHING:
- Match items to the most specific category available
- For groceries: Food & Dining, Groceries, Household, etc.
- For mixed purchases (e.g., supermarket with cleaning supplies + food), split appropriately

OUTPUT FORMAT (strict JSON):
{
    "should_split": true,
    "splits": [
        {"category": "Groceries", "amount": 12.50, "description": "Food items (milk, bread, cheese)"},
        {"category": "Household", "amount": 8.99, "description": "Cleaning supplies"}
    ],
    "confidence": 0.85,
    "reason": "Extracted 2 distinct item categories from receipt"
}

If no split is needed:
{
    "should_split": false,
    "splits": [],
    "confidence": 0.90,
    "reason": "Single category transaction or cannot extract line items"
}"""

    user_template: str = """Analyze this document for split transactions:

TRANSACTION DETAILS:
- Total Amount: {amount}
- Date: {date}
- Vendor/Payee: {vendor}
- Description: {description}

OCR/DOCUMENT CONTENT:
{content}

LINKED BANK TRANSACTION DATA:
{bank_data}

AVAILABLE CATEGORIES:
{categories}

Extract line items with prices from the content above and assign categories. Provide your analysis in JSON format."""

    def format_user_message(
        self,
        amount: str,
        date: str,
        vendor: str | None,
        description: str | None,
        content: str | None,
        categories: list[str],
        bank_data: dict | None = None,
    ) -> str:
        """Format the user message for split analysis.

        Args:
            amount: Total transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            content: Additional document content (OCR text, etc.).
            categories: List of available category names.
            bank_data: Optional linked bank transaction data.

        Returns:
            Formatted user message.
        """
        categories_str = "\n".join(f"- {cat}" for cat in categories)
        
        # Format bank data if available
        if bank_data:
            bank_str = f"""- Bank Amount: {bank_data.get('amount', 'N/A')}
- Bank Date: {bank_data.get('date', 'N/A')}
- Bank Description: {bank_data.get('description', 'N/A')}
- Bank Category: {bank_data.get('category_name', 'N/A')}"""
        else:
            bank_str = "Not available"
        
        return self.user_template.format(
            amount=amount,
            date=date,
            vendor=vendor or "Unknown",
            description=description or "No description",
            content=content or "No OCR content available",
            bank_data=bank_str,
            categories=categories_str,
        )


@dataclass
class ChatPrompt:
    """Prompt template for documentation chatbot.

    Used for answering questions about the software.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are a helpful assistant for SparkLink, a financial document processing application that bridges Paperless-ngx and Firefly III.

ABOUT SPARKLINK:
SparkLink automatically extracts financial data from documents (receipts, invoices) stored in Paperless-ngx and imports them into Firefly III for personal finance tracking.

KEY FEATURES:
- OCR extraction of amounts, dates, vendors from documents
- Structured invoice parsing (ZUGFeRD, Factur-X, UBL)
- Transaction matching with existing Firefly III entries
- Split transaction support for itemized receipts
- AI-powered categorization using local Ollama models
- Human-in-the-loop review workflow
- Full audit trail and provenance tracking

You have access to the software documentation for detailed technical information.
Answer questions helpfully and accurately based on the provided context.
If you don't know something, say so rather than making things up.

Keep answers concise but complete. Use markdown formatting when helpful."""

    user_template: str = """DOCUMENTATION CONTEXT:
{documentation}

USER QUESTION:
{question}

Please answer based on the documentation and your knowledge of the system."""

    def format_user_message(
        self,
        question: str,
        documentation: str,
    ) -> str:
        """Format the user message for chatbot.

        Args:
            question: User's question.
            documentation: Relevant documentation content.

        Returns:
            Formatted user message.
        """
        return self.user_template.format(
            question=question,
            documentation=documentation or "No additional documentation available.",
        )


@dataclass
class TransactionReviewPrompt:
    """Prompt template for comprehensive transaction review suggestions.

    Used when reviewing a document to suggest values for all editable fields
    based on document content, OCR data, and linked bank transaction context.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are a financial document review assistant helping categorize and verify transactions.

Your task is to analyze the provided document and suggest values for transaction fields.
You have access to:
1. Document data (extracted from OCR or structured invoice)
2. Linked bank transaction data (if available) for verification
3. Previously applied rules or decisions

IMPORTANT RULES:
1. For 'category': ONLY suggest from the available categories list
2. For 'transaction_type': ONLY use 'withdrawal', 'deposit', or 'transfer'
3. Preserve existing good values - only suggest changes that improve accuracy
4. Use linked bank data to verify/correct amounts and dates
5. Provide confidence scores (0.0-1.0) for each suggestion
6. Be conservative - if uncertain, keep the original value
7. If the document shows LINE ITEMS with different categories, suggest split_transactions

Respond in JSON format:
{
    "suggestions": {
        "category": {"value": "CategoryName", "confidence": 0.85, "reason": "..."},
        "transaction_type": {"value": "withdrawal", "confidence": 0.95, "reason": "..."},
        "destination_account": {"value": "Vendor Name", "confidence": 0.80, "reason": "..."},
        "description": {"value": "Description text", "confidence": 0.75, "reason": "..."}
    },
    "split_transactions": [
        {"amount": 29.99, "description": "Item 1", "category": "Category1"},
        {"amount": 15.50, "description": "Item 2", "category": "Category2"}
    ],
    "overall_confidence": 0.82,
    "analysis_notes": "Brief analysis summary"
}

Only include fields where you have a meaningful suggestion (confidence > 0.5).
Only include split_transactions if the document clearly shows multiple line items with different categories.
Split amounts should sum to the total transaction amount."""

    user_template: str = """Review this transaction and suggest values for the form fields:

CURRENT DOCUMENT DATA:
- Amount: {amount}
- Date: {date}
- Vendor/Payee: {vendor}
- Description: {description}
- Current Category: {current_category}
- Current Transaction Type: {current_type}
- Invoice Number: {invoice_number}
- OCR Confidence: {ocr_confidence}%

DOCUMENT CONTENT (OCR/Structured):
{document_content}

LINKED BANK TRANSACTION:
{bank_transaction}

PREVIOUS DECISIONS:
{previous_decisions}

AVAILABLE CATEGORIES:
{categories}

AVAILABLE TRANSACTION TYPES:
- withdrawal (expense/purchase)
- deposit (income/refund)
- transfer (between own accounts)

Analyze this data and provide suggestions for any fields that could be improved or filled in.
Prioritize accuracy over completeness - only suggest values you're confident about.

SPLIT TRANSACTIONS:
If the document shows multiple line items with different categories (e.g., a receipt with groceries and household items),
suggest split_transactions with amounts, descriptions, and categories. The amounts should sum to the total.
Only suggest splits if clearly indicated by the document content."""

    def format_user_message(
        self,
        amount: str,
        date: str,
        vendor: str | None,
        description: str | None,
        current_category: str | None,
        current_type: str | None,
        invoice_number: str | None,
        ocr_confidence: float,
        document_content: str | None,
        bank_transaction: dict | None,
        previous_decisions: list[dict] | None,
        categories: list[str],
    ) -> str:
        """Format the user message for transaction review.

        Args:
            amount: Transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            current_category: Currently assigned category.
            current_type: Currently assigned transaction type.
            invoice_number: Invoice/receipt number if extracted.
            ocr_confidence: Overall OCR confidence percentage.
            document_content: Raw OCR text or structured content.
            bank_transaction: Linked bank transaction data if available.
            previous_decisions: List of previous interpretation decisions.
            categories: List of available category names.

        Returns:
            Formatted user message.
        """
        categories_str = "\n".join(f"- {cat}" for cat in categories)

        # Format bank transaction data
        if bank_transaction:
            bank_str = f"""Amount: {bank_transaction.get('amount', 'N/A')}
Date: {bank_transaction.get('date', 'N/A')}
Description: {bank_transaction.get('description', 'N/A')}
Category: {bank_transaction.get('category_name', 'Not categorized')}
Source: {bank_transaction.get('source_account', 'N/A')}
Destination: {bank_transaction.get('destination_account', 'N/A')}"""
        else:
            bank_str = "No linked bank transaction"

        # Format previous decisions
        if previous_decisions:
            decisions_str = "\n".join(
                f"- {d.get('decision_source', 'Unknown')}: {d.get('final_state', '')} "
                f"(Category: {d.get('suggested_category', 'None')})"
                for d in previous_decisions[:3]  # Last 3 decisions
            )
        else:
            decisions_str = "No previous decisions"

        # Truncate document content to avoid token limits
        content = document_content or "No content available"
        if len(content) > 2000:
            content = content[:2000] + "\n... (truncated)"

        return self.user_template.format(
            amount=amount,
            date=date,
            vendor=vendor or "Unknown",
            description=description or "No description",
            current_category=current_category or "Not set",
            current_type=current_type or "withdrawal",
            invoice_number=invoice_number or "Not found",
            ocr_confidence=int(ocr_confidence * 100) if ocr_confidence else 0,
            document_content=content,
            bank_transaction=bank_str,
            previous_decisions=decisions_str,
            categories=categories_str,
        )
