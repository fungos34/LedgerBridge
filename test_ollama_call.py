#!/usr/bin/env python3
"""
Standalone script to test Ollama integration from within the Docker container.
This simulates a typical document reinterpretation request.

Usage from deployment server:
    docker exec -it <container_name> python -c "$(cat <<'EOF'
    [PASTE THE COMMAND FROM BELOW]
    EOF
    )"

Or save as test_ollama.py and run:
    docker exec -it <container_name> python /app/test_ollama.py
"""

import json
import httpx

# Configuration - MODIFY THESE VALUES FOR YOUR DEPLOYMENT
OLLAMA_URL = "http://ollama:11434"  # or http://localhost:11434 if testing locally
MODEL = "qwen2.5:3b-instruct-q4_K_M"  # or whatever model you have pulled
TIMEOUT_SECONDS = 200000000

# Sample document OCR content (typical receipt)
DOCUMENT_CONTENT = """REWE Markt GmbH
Hauptstraße 123
10115 Berlin
Tel: +49 30 12345678

Receipt #2024-001234
Date: 2024-01-15 14:32

ITEMS:
Fresh milk 3.5% 1L          2.99 €
Whole wheat bread            3.49 €
Free range eggs (10)         4.29 €
Organic tomatoes             2.89 €
Dish soap                    3.99 €
Kitchen sponges             1.99 €
AA Batteries pack            5.99 €

SUBTOTAL                    24.47 €
VAT 19%                      4.65 €
═══════════════════════════════
TOTAL                       30.46 EUR

Thank you for shopping!
Receipt #: REC-2024-00523"""

# Now create the Python command
payload = {
    "amount": "30.49",
    "date": "2026-01-10",
    "vendor": None,
    "description": None,
    "current_category": "Groceries",
    "current_type": "withdrawal",
    "invoice_number": None,
    "ocr_confidence": 0.92,
    "document_content": """SUPERMARKT MÜLLER
Hauptstraße 42, 10115 Berlin
Tel: +49 30 123 4567
USt-IdNr: DE123456789

Kassenbon / Receipt
Datum: 12.01.2026     Zeit: 14:32
Kasse: 3         Bon-Nr: 8274

════════════════════════════════════════════
ARTIKEL                          MENGE  PREIS
════════════════════════════════════════════
Vollmilch 3,8%                  2x     3.58
Bio-Eier 10 Stk                 1x     4.29
Brot Vollkorn                   1x     2.49
Butter 250g                     1x     3.29
Käse Gouda 400g                 1x    

 5.99
Äpfel 1kg                       1x     2.49
Bananen 1kg                     1x     1.99
Putzmittel Allzweck            1x     3.99
Küchentücher                   1x     2.49
═══════════════════════════════════════
Zwischensumme                      28.47
MwSt 7%                             1.12
MwSt 19%                            3.15
───────────────────────────────────────
GESAMT                             34.46 EUR

Zahlungsart: EC-Karte
Vielen Dank für Ihren Einkauf!""",
    "bank_transaction": {
        "amount": "-34.46",
        "date": "2026-01-12",
        "description": "SUPERMARKT MUELLER BERLIN",
        "category_name": "Groceries",
        "source_account": "My Checking Account",
        "destination_account": "Supermarkt Müller"
    },
    "previous_decisions": [],
    "categories": [
        "Groceries",
        "Household",
        "Electronics",
        "Healthcare",
        "Transportation",
        "Entertainment",
        "Dining Out",
        "Utilities",
        "Insurance",
        "Personal Care"
    ]
}

# Build the prompt (matching TransactionReviewPrompt format)
system_prompt = """You are a financial document analysis expert. Your mission is critical: extract and categorize ALL financial data from receipts and invoices accurately.

## YOUR MANDATORY TASKS:

### 1. EXTRACT THE TOTAL AMOUNT
- Find the total/grand total/sum on the document
- Verify it matches the stated amount or correct it
- Use the document's total as truth, NOT any pre-filled value

### 2. IDENTIFY THE VENDOR/MERCHANT
- Extract the store name, company, or merchant from the document header
- Look for business names, logos, addresses at the top
- This MUST be filled in - every receipt has a vendor

### 3. CREATE SPLIT TRANSACTIONS FOR LINE ITEMS
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

### 4. SUGGEST DESCRIPTION
- Create a concise description summarizing the purchase
- Include key items or the nature of the transaction
- Example: "Grocery shopping - milk, bread, cleaning supplies"

### 5. DETERMINE TRANSACTION TYPE
- "withdrawal" = expense/purchase (most receipts)
- "deposit" = refund/income
- "transfer" = between own accounts (rare for receipts)

## RESPONSE FORMAT (strict JSON):
{
    "suggestions": {
        "category": {"value": "PrimaryCategory", "confidence": 0.85, "reason": "Most items belong here"},
        "transaction_type": {"value": "withdrawal", "confidence": 0.95, "reason": "This is a purchase"},
        "destination_account": {"value": "Vendor Name", "confidence": 0.90, "reason": "Extracted from receipt header"},
        "description": {"value": "Descriptive summary", "confidence": 0.80, "reason": "Based on items purchased"}
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
- Split amounts MUST sum to total (tolerance: ±0.05)
- ALWAYS include destination_account (vendor name)
- ALWAYS include description
- If document has line items → MUST suggest split_transactions
- Confidence scores: 0.0-1.0"""

# Format categories
categories_str = "\n".join(f"• {cat}" for cat in payload["categories"])

# Format bank transaction
bank_str = f"""Amount: {payload['bank_transaction']['amount']}
Date: {payload['bank_transaction']['date']}
Description: {payload['bank_transaction']['description']}
Category: {payload['bank_transaction']['category_name']}
Source: {payload['bank_transaction']['source_account']}
Destination: {payload['bank_transaction']['destination_account']}"""

user_message = f"""ANALYZE THIS DOCUMENT AND EXTRACT ALL FINANCIAL DATA:

═══════════════════════════════════════════════════════════════
DOCUMENT INFORMATION:
═══════════════════════════════════════════════════════════════
Total Amount: {payload['amount']}
Date: {payload['date']}
Current Vendor: {payload['vendor'] or "(not extracted yet)"}
Current Description: {payload['description'] or "(not extracted yet)"}
Current Category: {payload['current_category'] or "(not set)"}
Current Type: {payload['current_type'] or "withdrawal"}
Invoice/Receipt Number: {payload['invoice_number'] or "(not found)"}
OCR Quality: {int(payload['ocr_confidence'] * 100)}%

═══════════════════════════════════════════════════════════════
DOCUMENT CONTENT (OCR TEXT):
═══════════════════════════════════════════════════════════════
{payload['document_content']}

═══════════════════════════════════════════════════════════════
LINKED BANK DATA (for verification):
═══════════════════════════════════════════════════════════════
{bank_str}

═══════════════════════════════════════════════════════════════
PREVIOUS AI DECISIONS:
═══════════════════════════════════════════════════════════════
No previous AI decisions

═══════════════════════════════════════════════════════════════
AVAILABLE CATEGORIES (use ONLY these):
═══════════════════════════════════════════════════════════════
{categories_str}

═══════════════════════════════════════════════════════════════
YOUR TASKS:
═══════════════════════════════════════════════════════════════
1. EXTRACT the vendor/merchant name from the document
2. IDENTIFY all line items with prices from the OCR text
3. CATEGORIZE each line item into the appropriate category
4. CREATE split_transactions if multiple items/categories exist
5. WRITE a descriptive summary
6. VERIFY the total amount matches line items

Respond with complete JSON including split_transactions if items are visible."""

# Build Ollama API request
ollama_payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message}
    ],
    "stream": False,
    "format": "json"
}

print("=" * 80)
print("TESTING OLLAMA INTEGRATION FROM DOCKER CONTAINER")
print("=" * 80)
print(f"\nOllama URL: {OLLAMA_URL}")
print(f"Model: {MODEL}")
print(f"Timeout: {TIMEOUT_SECONDS}s")
print(f"\nPayload size: {len(json.dumps(ollama_payload))} bytes")
print(f"System prompt: {len(system_prompt)} chars")
print(f"User message: {len(user_message)} chars")
print("\n" + "=" * 80)
print("SENDING REQUEST TO OLLAMA...")
print("=" * 80)

try:
    # Make the request
    timeout_config = httpx.Timeout(
        connect=10.0,
        read=float(TIMEOUT_SECONDS),
        write=30.0,
        pool=10.0
    )
    
    with httpx.Client(timeout=timeout_config) as client:
        response = client.post(
            f"{OLLAMA_URL}/api/chat",
            json=ollama_payload
        )
        
        print(f"\nHTTP Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            content = result.get("message", {}).get("content", "")
            
            print("\n" + "=" * 80)
            print("✅ SUCCESS - OLLAMA RESPONSE RECEIVED")
            print("=" * 80)
            print(f"\nModel used: {result.get('model', 'unknown')}")
            print(f"Response length: {len(content)} chars")
            print(f"\nRaw response content:")
            print("-" * 80)
            print(content)
            print("-" * 80)
            
            # Try to parse as JSON
            try:
                parsed = json.loads(content.strip())
                print("\n" + "=" * 80)
                print("✅ JSON PARSING SUCCESSFUL")
                print("=" * 80)
                print(f"\nParsed response:")
                print(json.dumps(parsed, indent=2))
                
                # Validate structure
                has_suggestions = "suggestions" in parsed
                has_splits = "split_transactions" in parsed
                has_confidence = "overall_confidence" in parsed
                
                print("\n" + "=" * 80)
                print("VALIDATION RESULTS")
                print("=" * 80)
                print(f"✓ Has 'suggestions' field: {has_suggestions}")
                print(f"✓ Has 'split_transactions' field: {has_splits}")
                print(f"✓ Has 'overall_confidence' field: {has_confidence}")
                
                if has_suggestions:
                    print(f"\nSuggested fields: {list(parsed['suggestions'].keys())}")
                if has_splits:
                    print(f"Number of split transactions: {len(parsed['split_transactions'])}")
                    
            except json.JSONDecodeError as e:
                print("\n" + "=" * 80)
                print("⚠️  WARNING - JSON PARSING FAILED")
                print("=" * 80)
                print(f"Error: {e}")
                print("This might need the _parse_json_response() method from service.py")
                
        else:
            print("\n" + "=" * 80)
            print(f"❌ ERROR - HTTP {response.status_code}")
            print("=" * 80)
            print(response.text)
            
except httpx.TimeoutException:
    print("\n" + "=" * 80)
    print(f"❌ TIMEOUT - No response after {TIMEOUT_SECONDS}s")
    print("=" * 80)
    print("Possible issues:")
    print("  - Ollama not running")
    print("  - Model not pulled (try: docker exec <container> ollama pull qwen2.5:3b-instruct-q4_K_M)")
    print("  - Network connectivity issues")
    print("  - Model too slow for timeout setting")
    
except httpx.ConnectError as e:
    print("\n" + "=" * 80)
    print("❌ CONNECTION ERROR")
    print("=" * 80)
    print(f"Error: {e}")
    print("\nPossible issues:")
    print("  - Ollama service not running")
    print(f"  - Wrong URL: {OLLAMA_URL}")
    print("  - Check docker-compose.yml network configuration")
    print("  - Try: docker exec <container> curl http://ollama:11434/api/tags")
    
except Exception as e:
    print("\n" + "=" * 80)
    print("❌ UNEXPECTED ERROR")
    print("=" * 80)
    print(f"Error type: {type(e).__name__}")
    print(f"Error message: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
print("TEST COMPLETE")
print("=" * 80)