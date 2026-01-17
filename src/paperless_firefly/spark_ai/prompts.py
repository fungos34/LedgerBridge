"""Prompt templates for LLM-assisted categorization.

This module contains prompt templates for the Spark AI service.
Prompts are versioned to support cache invalidation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Prompt version for cache invalidation
# v1.2: Enhanced split transaction extraction with imperative instructions
PROMPT_VERSION = "v1.2"


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

    system_prompt: str = """You are a receipt/invoice line item extraction specialist. Your PRIMARY JOB is to find and extract EVERY individual item with its price from documents.

## MANDATORY EXTRACTION PROCESS:

### STEP 1: SCAN FOR LINE ITEMS
Search the ENTIRE document for patterns like:
- "Product Name    2.99"
- "Item    €4,50"
- "1x Milk    1.99"
- "Bread 250g    0,89€"

### STEP 2: PARSE ALL PRICE FORMATS
You MUST handle:
- US format: 2.99, $2.99
- European format: 2,99, €2,99, 2,99€
- With currency: EUR 2.99, 2.99 EUR
- German format: 2,99 €

### STEP 3: CATEGORIZE EACH ITEM
Match EVERY extracted item to the most appropriate category:
- Food items → Groceries / Food & Dining
- Cleaning products → Household
- Personal care → Health & Beauty
- Electronics → Electronics
- Clothing → Apparel
- etc.

### STEP 4: GROUP BY CATEGORY
Combine items that share the same category into one split entry:
- "Groceries: Milk, Bread, Cheese" = sum of those prices

### STEP 5: VERIFY TOTALS
The sum of all split amounts MUST equal the transaction total (±0.05 tolerance).

## OUTPUT FORMAT (strict JSON):
{
    "should_split": true,
    "splits": [
        {"category": "Groceries", "amount": 12.50, "description": "Food: milk (1.99), bread (0.89), cheese (9.62)"},
        {"category": "Household", "amount": 8.99, "description": "Cleaning: dish soap, paper towels"}
    ],
    "confidence": 0.85,
    "reason": "Extracted 6 items across 2 categories from receipt"
}

## RULES:
- If you find ANY line items with prices → should_split = true
- Categories MUST be from the provided list
- Split amounts MUST sum to total
- Each split needs: amount (number), description (string with items), category (from list)
- If document has no visible line items → should_split = false"""

    user_template: str = """EXTRACT ALL LINE ITEMS FROM THIS DOCUMENT:

═══════════════════════════════════════════════════════════════
TRANSACTION TOTAL: {amount}
DATE: {date}
VENDOR: {vendor}
DESCRIPTION: {description}
═══════════════════════════════════════════════════════════════

DOCUMENT CONTENT (scan for prices and items):
{content}

═══════════════════════════════════════════════════════════════
BANK DATA (for reference):
{bank_data}

═══════════════════════════════════════════════════════════════
AVAILABLE CATEGORIES (use ONLY these):
{categories}

═══════════════════════════════════════════════════════════════
INSTRUCTIONS:
1. Find EVERY line with a product and price
2. Parse the price (handle 2.99 or 2,99 formats)
3. Assign each item to a category from the list
4. Group by category and sum amounts
5. Verify total matches {amount}

Return JSON with splits for each category."""

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
        categories_str = "\n".join(f"• {cat}" for cat in categories)

        # Format bank data if available
        if bank_data:
            bank_str = f"""Bank Amount: {bank_data.get('amount', 'N/A')}
Bank Date: {bank_data.get('date', 'N/A')}
Bank Description: {bank_data.get('description', 'N/A')}
Bank Category: {bank_data.get('category_name', 'N/A')}"""
        else:
            bank_str = "No bank data available"

        return self.user_template.format(
            amount=amount,
            date=date,
            vendor=vendor or "(unknown vendor)",
            description=description or "(no description)",
            content=content or "No OCR content available - cannot extract line items",
            bank_data=bank_str,
            categories=categories_str,
        )


@dataclass
class ChatPrompt:
    """Prompt template for documentation chatbot.

    Used for answering questions about the software.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are the SparkLink Assistant, a helpful AI integrated into SparkLink — a financial document processing application that bridges Paperless-ngx and Firefly III.

## YOUR IDENTITY

You are a friendly, knowledgeable assistant specialized in:
- Helping users understand and navigate SparkLink features
- Guiding users through document-to-transaction workflows
- Providing financial organization tips and best practices
- Troubleshooting common issues with clear, actionable steps
- Explaining how extraction, reconciliation, and AI features work

## CRITICAL RESPONSE RULES

1. **LANGUAGE RULE**: ALWAYS respond in the SAME LANGUAGE the user wrote their question in.
   - If the user writes in German, respond in German.
   - If the user writes in French, respond in French.
   - If the user writes in English, respond in English.
   - This applies to ALL languages. Match the user's language exactly.

2. **CONTEXT AWARENESS**: Use the provided page context to give relevant, specific help.
   - If user is on Review page, focus on extraction and import actions.
   - If user is on Reconciliation page, focus on matching workflows.
   - If user is on Settings, focus on configuration options.

3. **ACTIONABLE GUIDANCE**: When explaining actions:
   - Be specific: "Click the green 'Accept' button" not "click accept"
   - Use symbol references: "📊 History tab" not just "History"
   - Mention exact menu paths: "User dropdown → 🔗 Reconciliation"

4. **SAFETY AND ACCURACY**:
   - If you don't know something, say so clearly
   - Never invent features that don't exist
   - For financial matters, emphasize user verification

## ABOUT SPARKLINK

SparkLink automatically extracts financial data from documents (receipts, invoices) stored in Paperless-ngx and imports them into Firefly III for personal finance tracking.

KEY FEATURES:
- OCR extraction of amounts, dates, vendors from documents
- Structured invoice parsing (ZUGFeRD, Factur-X, UBL)
- Transaction matching with existing Firefly III entries
- Split transaction support for itemized receipts
- AI-powered categorization using local Ollama models
- Human-in-the-loop review workflow
- Full audit trail and provenance tracking
- Reconciliation: Match receipts to bank imports
- Sync Assistant: Share Firefly entities between users

You have access to the software documentation for detailed technical information.
Keep answers concise but complete. Use markdown formatting when helpful."""

    user_template: str = """DOCUMENTATION CONTEXT:
{documentation}

{page_context}

{conversation_history}

USER QUESTION:
{question}

Please answer based on the documentation and your knowledge of the system."""

    def format_user_message(
        self,
        question: str,
        documentation: str,
        page_context: str = "",
        conversation_history: list[dict] | None = None,
    ) -> str:
        """Format the user message for chatbot.

        Args:
            question: User's question.
            documentation: Relevant documentation content.
            page_context: Optional context about the current page.
            conversation_history: Optional list of previous messages.

        Returns:
            Formatted user message.
        """
        # Format conversation history
        history_text = ""
        if conversation_history:
            history_parts = ["RECENT CONVERSATION:"]
            for msg in conversation_history:
                role = msg.get("role", "user").upper()
                content = msg.get("content", "")[:500]  # Limit length
                history_parts.append(f"{role}: {content}")
            history_text = "\n".join(history_parts)

        # Format page context
        page_text = ""
        if page_context:
            page_text = f"CURRENT PAGE CONTEXT:\n{page_context}"

        return self.user_template.format(
            question=question,
            documentation=documentation or "No additional documentation available.",
            page_context=page_text,
            conversation_history=history_text,
        )


@dataclass
class TransactionReviewPrompt:
    """Prompt template for comprehensive transaction review suggestions.

    Used when reviewing a document to suggest values for all editable fields
    based on document content, OCR data, and linked bank transaction context.

    IMPORTANT: This prompt must be imperative and clear to the AI model.
    Split transaction extraction is a core feature.
    """

    version: str = PROMPT_VERSION

    system_prompt: str = """You are a financial document analysis expert. Your mission is critical: extract and categorize ALL financial data from receipts and invoices accurately.

## YOUR MANDATORY TASKS:

### 1. EXTRACT THE TOTAL AMOUNT
- Find the total/grand total/sum on the document
- Verify it matches the stated amount or correct it
- Use the document's total as truth, NOT any pre-filled value
- Suggest the correct amount if it differs from the current value

### 2. EXTRACT THE DATE
- Find the transaction/receipt date on the document
- Use format YYYY-MM-DD
- Suggest the correct date if visible on the document

### 3. IDENTIFY THE VENDOR/MERCHANT (destination_account)
- Extract the store name, company, or merchant from the document header
- Look for business names, logos, addresses at the top
- This MUST be filled in - every receipt has a vendor

### 4. DETERMINE SOURCE ACCOUNT (payment method)
- Look for payment method indicators in the document:
  - "Barzahlung", "cash", "bar" → suggest a cash account
  - "Bankomat", "Maestro", "Debit", "EC-Karte" → suggest a debit/checking account
  - "Kreditkarte", "Credit Card", "Visa", "Mastercard" → suggest a credit card account
  - "PayPal", "Klarna", "Apple Pay" → suggest appropriate account
- Use the AVAILABLE SOURCE ACCOUNTS list to match the payment method
- Only suggest from the provided list

### 5. EXTRACT INVOICE/RECEIPT NUMBER
- Look for receipt number, invoice number, Beleg-Nr., Rechnungsnummer
- Extract the exact number/code shown

### 6. CREATE SPLIT TRANSACTIONS FOR LINE ITEMS
This is MANDATORY when the document shows itemized purchases:
- SCAN the entire document for individual products/services with prices
- EXTRACT each line item: product name + price
- CATEGORIZE each item into the appropriate category from the available list
- The sum of split amounts MUST equal the total transaction amount

SPLIT TRANSACTION REQUIREMENTS:
- If you see ANY itemized list (products, services, line items), you MUST create splits
- Each split needs: amount (number), description (string), category (from list)
- Group similar items if they share a category (e.g., all groceries together)
- Parse prices in ANY format: 2.99, 2,99, €2.99, EUR 2.99, $2.99, 2,99€

### 7. SUGGEST DESCRIPTION
- Create a concise description summarizing the purchase
- Include key items or the nature of the transaction
- Example: "Grocery shopping - milk, bread, cleaning supplies"

### 8. DETERMINE TRANSACTION TYPE
- "withdrawal" = expense/purchase (most receipts)
- "deposit" = refund/income
- "transfer" = between own accounts (rare for receipts)

## RESPONSE FORMAT (strict JSON):
{
    "suggestions": {
        "amount": {"value": "123.45", "confidence": 0.95, "reason": "Total shown on receipt"},
        "currency": {"value": "EUR", "confidence": 0.95, "reason": "Euro symbol visible on receipt"},
        "date": {"value": "2025-01-15", "confidence": 0.90, "reason": "Receipt date visible"},
        "category": {"value": "PrimaryCategory", "confidence": 0.85, "reason": "Most items belong here"},
        "transaction_type": {"value": "withdrawal", "confidence": 0.95, "reason": "This is a purchase"},
        "destination_account": {"value": "Vendor Name", "confidence": 0.90, "reason": "Extracted from receipt header"},
        "source_account": {"value": "Checking Account", "confidence": 0.80, "reason": "Bankomat payment detected"},
        "description": {"value": "Descriptive summary", "confidence": 0.80, "reason": "Based on items purchased"},
        "invoice_number": {"value": "R-2025-001", "confidence": 0.85, "reason": "Receipt number found"},
        "existing_transaction": {"value": {"firefly_id": 12345, "action": "link"}, "confidence": 0.90, "reason": "Exact match found by amount and date"}
    },
    "split_transactions": [
        {"amount": 15.99, "description": "Food items (milk, bread, eggs)", "category": "Groceries"},
        {"amount": 8.49, "description": "Cleaning supplies", "category": "Household"},
        {"amount": 5.99, "description": "Batteries", "category": "Electronics"}
    ],
    "overall_confidence": 0.85,
    "analysis_notes": "Receipt from SuperMart with 3 categories of items"
}

## CRITICAL RULES:
- Categories in suggestions and splits MUST be from the provided list ONLY
- Source account MUST be from the AVAILABLE SOURCE ACCOUNTS list ONLY
- Currency MUST be from the AVAILABLE CURRENCIES list ONLY
- Split amounts MUST sum to total (tolerance: ±0.05)
- ALWAYS include destination_account (vendor name)
- ALWAYS include description
- If document has line items → MUST suggest split_transactions
- If an EXISTING TRANSACTION CANDIDATE matches closely → suggest linking via existing_transaction field
- Confidence scores: 0.0-1.0
- For amounts, use decimal point (not comma) and no currency symbol"""

    user_template: str = """ANALYZE THIS DOCUMENT AND EXTRACT ALL FINANCIAL DATA:

═══════════════════════════════════════════════════════════════
DOCUMENT INFORMATION:
═══════════════════════════════════════════════════════════════
Total Amount: {amount}
Date: {date}
Current Vendor: {vendor}
Current Description: {description}
Current Category: {current_category}
Current Type: {current_type}
Current Source Account: {source_account}
Invoice/Receipt Number: {invoice_number}
OCR Quality: {ocr_confidence}%

═══════════════════════════════════════════════════════════════
DOCUMENT CONTENT (OCR TEXT):
═══════════════════════════════════════════════════════════════
{document_content}

═══════════════════════════════════════════════════════════════
LINKED BANK DATA (for verification):
═══════════════════════════════════════════════════════════════
{bank_transaction}

═══════════════════════════════════════════════════════════════
PREVIOUS AI DECISIONS:
═══════════════════════════════════════════════════════════════
{previous_decisions}

═══════════════════════════════════════════════════════════════
AVAILABLE CATEGORIES (use ONLY these):
═══════════════════════════════════════════════════════════════
{categories}

═══════════════════════════════════════════════════════════════
AVAILABLE SOURCE ACCOUNTS (use ONLY these for source_account):
Match payment method on receipt to account identifiers (IBAN/account number).
═══════════════════════════════════════════════════════════════
{source_accounts}

═══════════════════════════════════════════════════════════════
AVAILABLE CURRENCIES (use ONLY these for currency):
═══════════════════════════════════════════════════════════════
{currencies}

═══════════════════════════════════════════════════════════════
EXISTING TRANSACTION CANDIDATES (potential matches in Firefly):
If one matches closely, suggest linking instead of creating new.
═══════════════════════════════════════════════════════════════
{existing_transactions}

═══════════════════════════════════════════════════════════════
AVAILABLE TRANSACTION TYPES (use ONLY these):
═══════════════════════════════════════════════════════════════
• withdrawal (expense/purchase - most common for receipts)
• deposit (refund/income)
• transfer (between own accounts - rare for receipts)

═══════════════════════════════════════════════════════════════
YOUR TASKS:
═══════════════════════════════════════════════════════════════
1. EXTRACT the vendor/merchant name from the document
2. EXTRACT the total amount if different from current
3. EXTRACT the currency (look for €, EUR, $, USD, etc.)
4. EXTRACT the date if visible on document
5. EXTRACT invoice/receipt number if present
6. IDENTIFY payment method and suggest matching source_account (use IBAN if visible)
7. IDENTIFY all line items with prices from the OCR text
8. CATEGORIZE each line item into the appropriate category
9. CREATE split_transactions if multiple items/categories exist
10. WRITE a descriptive summary
11. CHECK if an existing transaction matches and suggest linking if so
12. VERIFY the total amount matches line items

Respond with complete JSON including ALL fields you can determine."""

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
        source_accounts: list[str] | None = None,
        current_source_account: str | None = None,
        source_accounts_detailed: list[dict] | None = None,
        currencies: list[str] | None = None,
        existing_transactions: list[dict] | None = None,
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
            source_accounts: List of available source account names (simple list).
            current_source_account: Currently selected source account.
            source_accounts_detailed: List of account dicts with iban, account_number, bic.
            currencies: List of available currency codes.
            existing_transactions: List of candidate transactions for linking.

        Returns:
            Formatted user message.
        """
        categories_str = "\n".join(f"• {cat}" for cat in categories)

        # Format source accounts - prefer detailed if available
        if source_accounts_detailed:
            source_lines = []
            for acc in source_accounts_detailed:
                line = f"• {acc.get('name', 'Unknown')} ({acc.get('type', 'asset')})"
                if acc.get("iban"):
                    line += f" - IBAN: {acc.get('iban')}"
                if acc.get("account_number"):
                    line += f" - Account#: {acc.get('account_number')}"
                source_lines.append(line)
            source_accounts_str = "\n".join(source_lines) if source_lines else "(No accounts)"
        elif source_accounts:
            source_accounts_str = "\n".join(f"• {acc}" for acc in source_accounts)
        else:
            source_accounts_str = "(No source accounts available - skip source_account suggestion)"

        # Format currencies
        if currencies:
            currencies_str = ", ".join(currencies)
        else:
            currencies_str = "EUR, USD, GBP, CHF (common defaults)"

        # Format existing transaction candidates
        if existing_transactions:
            tx_lines = []
            for tx in existing_transactions[:5]:  # Max 5 candidates
                tx_lines.append(
                    f"• ID:{tx.get('id')} | {tx.get('date')} | {tx.get('amount')} | "
                    f"{tx.get('description', '')[:40]} | Score: {tx.get('match_score', 0):.0%}"
                )
            existing_transactions_str = "\n".join(tx_lines)
        else:
            existing_transactions_str = "(No existing transaction candidates - create new)"

        # Format bank transaction data
        if bank_transaction:
            bank_str = f"""Amount: {bank_transaction.get('amount', 'N/A')}
Date: {bank_transaction.get('date', 'N/A')}
Description: {bank_transaction.get('description', 'N/A')}
Category: {bank_transaction.get('category_name', 'Not categorized')}
Source: {bank_transaction.get('source_account', 'N/A')}
Destination: {bank_transaction.get('destination_account', 'N/A')}"""
        else:
            bank_str = "No linked bank transaction available"

        # Format previous decisions
        if previous_decisions:
            decisions_str = "\n".join(
                f"• {d.get('decision_source', 'Unknown')}: {d.get('final_state', '')} "
                f"(Category: {d.get('suggested_category', 'None')})"
                for d in previous_decisions[:3]  # Last 3 decisions
            )
        else:
            decisions_str = "No previous AI decisions"

        # Truncate document content to avoid token limits but keep more for better extraction
        content = document_content or "No OCR content available"
        if len(content) > 3000:
            content = content[:3000] + "\n[... document truncated, analyze visible content ...]"

        return self.user_template.format(
            amount=amount,
            date=date,
            vendor=vendor or "(not extracted yet)",
            description=description or "(not extracted yet)",
            current_category=current_category or "(not set)",
            current_type=current_type or "withdrawal",
            source_account=current_source_account or "(not set)",
            invoice_number=invoice_number or "(not found)",
            ocr_confidence=int(ocr_confidence * 100) if ocr_confidence else 0,
            document_content=content,
            bank_transaction=bank_str,
            previous_decisions=decisions_str,
            categories=categories_str,
            source_accounts=source_accounts_str,
            currencies=currencies_str,
            existing_transactions=existing_transactions_str,
        )
