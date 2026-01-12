# Ollama Integration Test Commands

## Overview
These commands test the complete Ollama integration flow from within your Docker container, using the exact same prompt format and document structure as the production reinterpretation workflow.

---

## Method 1: Full Python Script (Recommended)

**Copy the script to the container and run it:**

```bash
# 1. Copy the test script to the running container
docker cp test_ollama_call.py <container_name>:/tmp/test_ollama.py

# 2. Run the test
docker exec -it <container_name> python /tmp/test_ollama.py
```

**Or run directly from your repo:**
```bash
docker exec -it <container_name> python /app/test_ollama_call.py
```

---

## Method 2: One-Liner Command (Copy-Paste Ready)

**Before running, modify these variables:**
- `OLLAMA_URL`: Usually `http://ollama:11434` (if using docker-compose) or `http://localhost:11434`
- `MODEL`: Your pulled model, e.g., `llama3.2:3b`, `mistral:7b`, `qwen2.5:3b`

```bash
docker exec -it <container_name> python3 -c "
import json
import httpx

OLLAMA_URL = 'http://ollama:11434'  # MODIFY THIS
MODEL = 'llama3.2:3b'  # MODIFY THIS
TIMEOUT = 120

system_prompt = '''You are a financial document analysis expert. Your mission is critical: extract and categorize ALL financial data from receipts and invoices accurately.

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
- Parse prices in ANY format: 2.99, 2,99, €2.99, EUR 2.99, \$2.99, 2,99€

### 4. SUGGEST DESCRIPTION
- Create a concise description summarizing the purchase
- Include key items or the nature of the transaction
- Example: \"Grocery shopping - milk, bread, cleaning supplies\"

### 5. DETERMINE TRANSACTION TYPE
- \"withdrawal\" = expense/purchase (most receipts)
- \"deposit\" = refund/income
- \"transfer\" = between own accounts (rare for receipts)

## RESPONSE FORMAT (strict JSON):
{
    \"suggestions\": {
        \"category\": {\"value\": \"PrimaryCategory\", \"confidence\": 0.85, \"reason\": \"Most items belong here\"},
        \"transaction_type\": {\"value\": \"withdrawal\", \"confidence\": 0.95, \"reason\": \"This is a purchase\"},
        \"destination_account\": {\"value\": \"Vendor Name\", \"confidence\": 0.90, \"reason\": \"Extracted from receipt header\"},
        \"description\": {\"value\": \"Descriptive summary\", \"confidence\": 0.80, \"reason\": \"Based on items purchased\"}
    },
    \"split_transactions\": [
        {\"amount\": 15.99, \"description\": \"Food items (milk, bread, eggs)\", \"category\": \"Groceries\"},
        {\"amount\": 8.49, \"description\": \"Cleaning supplies\", \"category\": \"Household\"}
    ],
    \"overall_confidence\": 0.85,
    \"analysis_notes\": \"Receipt from SuperMart with multiple categories\"
}

## CRITICAL RULES:
- Categories MUST be from the provided list ONLY
- Split amounts MUST sum to total (tolerance: ±0.05)
- ALWAYS include destination_account (vendor name)
- ALWAYS include description
- If document has line items → MUST suggest split_transactions
- Confidence scores: 0.0-1.0'''

user_message = '''ANALYZE THIS DOCUMENT AND EXTRACT ALL FINANCIAL DATA:

═══════════════════════════════════════════════════════════════
DOCUMENT INFORMATION:
═══════════════════════════════════════════════════════════════
Total Amount: 34.46
Date: 2026-01-12
Current Vendor: (not extracted yet)
Current Description: (not extracted yet)
Current Category: Groceries
Current Type: withdrawal
Invoice/Receipt Number: (not found)
OCR Quality: 92%

═══════════════════════════════════════════════════════════════
DOCUMENT CONTENT (OCR TEXT):
═══════════════════════════════════════════════════════════════
SUPERMARKT MÜLLER
Hauptstraße 42, 10115 Berlin
Tel: +49 30 123 4567

Kassenbon / Receipt
Datum: 12.01.2026     Zeit: 14:32

════════════════════════════════════════════
ARTIKEL                          MENGE  PREIS
════════════════════════════════════════════
Vollmilch 3,8%                  2x     3.58
Bio-Eier 10 Stk                 1x     4.29
Brot Vollkorn                   1x     2.49
Butter 250g                     1x     3.29
Käse Gouda 400g                 1x     5.99
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

═══════════════════════════════════════════════════════════════
LINKED BANK DATA (for verification):
═══════════════════════════════════════════════════════════════
Amount: -34.46
Date: 2026-01-12
Description: SUPERMARKT MUELLER BERLIN

═══════════════════════════════════════════════════════════════
AVAILABLE CATEGORIES (use ONLY these):
═══════════════════════════════════════════════════════════════
• Groceries
• Household
• Electronics
• Healthcare
• Transportation
• Entertainment

═══════════════════════════════════════════════════════════════
YOUR TASKS:
═══════════════════════════════════════════════════════════════
1. EXTRACT the vendor/merchant name from the document
2. IDENTIFY all line items with prices from the OCR text
3. CATEGORIZE each line item into the appropriate category
4. CREATE split_transactions if multiple items/categories exist
5. WRITE a descriptive summary
6. VERIFY the total amount matches line items

Respond with complete JSON including split_transactions if items are visible.'''

payload = {
    'model': MODEL,
    'messages': [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_message}
    ],
    'stream': False,
    'format': 'json'
}

print('=' * 80)
print('TESTING OLLAMA FROM DOCKER CONTAINER')
print('=' * 80)
print(f'URL: {OLLAMA_URL}')
print(f'Model: {MODEL}')
print(f'Sending request...')

try:
    timeout_config = httpx.Timeout(connect=10.0, read=float(TIMEOUT), write=30.0, pool=10.0)
    with httpx.Client(timeout=timeout_config) as client:
        response = client.post(f'{OLLAMA_URL}/api/chat', json=payload)
        
        if response.status_code == 200:
            result = response.json()
            content = result.get('message', {}).get('content', '')
            
            print(f'\\n✅ SUCCESS - Status: {response.status_code}')
            print(f'Model: {result.get(\"model\", \"unknown\")}')
            print(f'Response length: {len(content)} chars')
            print('\\n' + '=' * 80)
            print('RESPONSE:')
            print('=' * 80)
            print(content)
            
            # Try parsing JSON
            try:
                parsed = json.loads(content.strip())
                print('\\n' + '=' * 80)
                print('✅ JSON VALID')
                print('=' * 80)
                print(f'Fields: {list(parsed.keys())}')
                if 'suggestions' in parsed:
                    print(f'Suggested fields: {list(parsed[\"suggestions\"].keys())}')
                if 'split_transactions' in parsed:
                    print(f'Split transactions: {len(parsed[\"split_transactions\"])}')
                    for i, split in enumerate(parsed['split_transactions'], 1):
                        print(f'  {i}. {split.get(\"description\", \"?\")}: {split.get(\"amount\", 0)} ({split.get(\"category\", \"?\")})')
            except json.JSONDecodeError as e:
                print(f'\\n⚠️  JSON parsing failed: {e}')
        else:
            print(f'\\n❌ HTTP {response.status_code}')
            print(response.text[:500])
            
except httpx.TimeoutException:
    print(f'\\n❌ TIMEOUT after {TIMEOUT}s - Model may be too slow or not responding')
except httpx.ConnectError as e:
    print(f'\\n❌ CONNECTION ERROR: {e}')
    print('Check: 1) Ollama running, 2) Correct URL, 3) Network config')
except Exception as e:
    print(f'\\n❌ ERROR: {type(e).__name__}: {e}')
    import traceback
    traceback.print_exc()

print('\\n' + '=' * 80)
print('TEST COMPLETE')
print('=' * 80)
"
```

---

## Method 3: Quick Connectivity Test

**Test if Ollama is reachable:**

```bash
docker exec -it <container_name> python3 -c "
import httpx
url = 'http://ollama:11434/api/tags'  # MODIFY URL if needed
try:
    r = httpx.get(url, timeout=5)
    print(f'✅ Ollama reachable: {r.status_code}')
    print(f'Available models: {r.json()}')
except Exception as e:
    print(f'❌ Cannot reach Ollama: {e}')
"
```

---

## Method 4: Using curl (Minimal Test)

```bash
docker exec -it <container_name> curl -X POST http://ollama:11434/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:3b",
    "messages": [
      {"role": "user", "content": "Reply with valid JSON: {\"test\": \"success\"}"}
    ],
    "stream": false,
    "format": "json"
  }'
```

---

## Troubleshooting

### Error: Connection refused
**Cause:** Ollama service not running or wrong URL

**Solutions:**
```bash
# Check if Ollama container is running
docker ps | grep ollama

# Check network connectivity
docker exec <container_name> curl http://ollama:11434/api/tags

# Try localhost instead of service name
# In the command, change: OLLAMA_URL = 'http://localhost:11434'
```

### Error: Timeout
**Cause:** Model is slow or not pulled

**Solutions:**
```bash
# Check what models are available
docker exec <ollama_container> ollama list

# Pull the model if missing
docker exec <ollama_container> ollama pull llama3.2:3b

# Increase timeout in the command
# Change: TIMEOUT = 300  # 5 minutes
```

### Error: Model not found
**Cause:** Specified model not pulled

**Solutions:**
```bash
# List available models
docker exec <ollama_container> ollama list

# Pull a smaller/faster model
docker exec <ollama_container> ollama pull qwen2.5:3b  # Faster than llama3.2

# Update MODEL variable in command to match available model
```

### Response is not valid JSON
**Cause:** Model didn't follow instructions

**Solutions:**
- Try a different model (some are better at JSON)
- The production code has `_parse_json_response()` that handles malformed JSON
- Check if `"format": "json"` is in the Ollama payload

---

## Expected Output

When working correctly, you should see:

```
════════════════════════════════════════════════════════════════════════════════
TESTING OLLAMA FROM DOCKER CONTAINER
════════════════════════════════════════════════════════════════════════════════
URL: http://ollama:11434
Model: llama3.2:3b
Sending request...

✅ SUCCESS - Status: 200
Model: llama3.2:3b
Response length: 847 chars

════════════════════════════════════════════════════════════════════════════════
RESPONSE:
════════════════════════════════════════════════════════════════════════════════
{
  "suggestions": {
    "category": {"value": "Groceries", "confidence": 0.95, "reason": "Most items are food products"},
    "transaction_type": {"value": "withdrawal", "confidence": 0.99, "reason": "This is a purchase receipt"},
    "destination_account": {"value": "Supermarkt Müller", "confidence": 0.98, "reason": "Extracted from header"},
    "description": {"value": "Grocery shopping - dairy, eggs, bread, produce, cleaning", "confidence": 0.90, "reason": "Summary of purchased items"}
  },
  "split_transactions": [
    {"amount": 20.13, "description": "Food items (milk, eggs, bread, butter, cheese, fruit)", "category": "Groceries"},
    {"amount": 6.48, "description": "Cleaning supplies (detergent, sponges)", "category": "Household"}
  ],
  "overall_confidence": 0.92,
  "analysis_notes": "German supermarket receipt with itemized groceries and household items"
}

════════════════════════════════════════════════════════════════════════════════
✅ JSON VALID
════════════════════════════════════════════════════════════════════════════════
Fields: ['suggestions', 'split_transactions', 'overall_confidence', 'analysis_notes']
Suggested fields: ['category', 'transaction_type', 'destination_account', 'description']
Split transactions: 2
  1. Food items (milk, eggs, bread, butter, cheese, fruit): 20.13 (Groceries)
  2. Cleaning supplies (detergent, sponges): 6.48 (Household)

════════════════════════════════════════════════════════════════════════════════
TEST COMPLETE
════════════════════════════════════════════════════════════════════════════════
```

---

## Integration with Production Code

The command uses the **exact same**:
- System prompt from `TransactionReviewPrompt`
- User message format with document content, bank data, categories
- Ollama API endpoint `/api/chat`
- JSON format enforcement
- Request structure (model, messages, stream, format)

This means if the test works, the production reinterpretation flow will work identically.

---

## Quick Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://ollama:11434` | Ollama service endpoint |
| `MODEL` | `llama3.2:3b` | Model name (must be pulled) |
| `TIMEOUT` | `120` | Timeout in seconds |

**Container names** (replace `<container_name>`):