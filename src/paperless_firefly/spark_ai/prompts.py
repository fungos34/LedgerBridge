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
