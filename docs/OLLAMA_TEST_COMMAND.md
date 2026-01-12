# Ollama Integration Test Commands - NO TIMEOUT

## Overview

**IMPORTANT**: All scheduled AI jobs now run with **NO TIMEOUT**. LLM inference can take minutes or even hours for complex documents, and the process will wait indefinitely until a response is received.

---

## Method 1: Full Python Script (Recommended)

**Copy the script to the container and run it:**

```bash
# Run the test script (waits indefinitely for response)
docker exec -it <container_name> python /app/test_ollama_call.py
```

---

## Method 2: One-Liner Command (Copy-Paste Ready)

**Variables to modify:**
- `OLLAMA_URL`: Usually `http://ollama:11434` (docker-compose) or `http://localhost:11434`
- `MODEL`: Your pulled model, e.g., `llama3.2:3b`, `qwen2.5:3b-instruct-q4_K_M`

```bash
docker exec -it <container_name> python3 -c "
import json
import httpx

OLLAMA_URL = 'http://ollama:11434'  # MODIFY THIS
MODEL = 'qwen2.5:3b-instruct-q4_K_M'  # MODIFY THIS

system_prompt = '''You are a financial document analysis expert. Extract and categorize ALL financial data from receipts and invoices accurately.

RESPONSE FORMAT (strict JSON):
{
    \"suggestions\": {
        \"category\": {\"value\": \"PrimaryCategory\", \"confidence\": 0.85, \"reason\": \"...\"},
        \"transaction_type\": {\"value\": \"withdrawal\", \"confidence\": 0.95, \"reason\": \"...\"},
        \"destination_account\": {\"value\": \"Vendor Name\", \"confidence\": 0.90, \"reason\": \"...\"},
        \"description\": {\"value\": \"Descriptive summary\", \"confidence\": 0.80, \"reason\": \"...\"}
    },
    \"split_transactions\": [
        {\"amount\": 15.99, \"description\": \"Food items\", \"category\": \"Groceries\"},
        {\"amount\": 8.49, \"description\": \"Cleaning supplies\", \"category\": \"Household\"}
    ],
    \"overall_confidence\": 0.85,
    \"analysis_notes\": \"...\"
}'''

user_message = '''ANALYZE THIS DOCUMENT:

Total Amount: 34.46
Date: 2026-01-12

DOCUMENT CONTENT:
SUPERMARKT MÜLLER
Datum: 12.01.2026

ARTIKEL:
Vollmilch 3,8%   2x     3.58
Bio-Eier 10 Stk  1x     4.29
Brot Vollkorn    1x     2.49
Putzmittel       1x     3.99

GESAMT          34.46 EUR

AVAILABLE CATEGORIES:
• Groceries
• Household
• Electronics

Respond with JSON including split_transactions.'''

payload = {
    'model': MODEL,
    'messages': [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_message}
    ],
    'stream': False,
    'format': 'json'
}

print('=' * 60)
print('TESTING OLLAMA (NO TIMEOUT - WILL WAIT INDEFINITELY)')
print('=' * 60)
print(f'URL: {OLLAMA_URL}')
print(f'Model: {MODEL}')
print('Sending request...')

try:
    # NO TIMEOUT - wait as long as needed
    with httpx.Client(timeout=None) as client:
        response = client.post(f'{OLLAMA_URL}/api/chat', json=payload)
        
        if response.status_code == 200:
            result = response.json()
            content = result.get('message', {}).get('content', '')
            print(f'\\n✅ SUCCESS')
            print(f'Response: {content[:500]}...' if len(content) > 500 else f'Response: {content}')
        else:
            print(f'\\n❌ HTTP {response.status_code}')
            
except httpx.ConnectError as e:
    print(f'\\n❌ CONNECTION ERROR: {e}')
except Exception as e:
    print(f'\\n❌ ERROR: {e}')

print('\\n' + '=' * 60)
"
```

---

## Method 3: Quick Connectivity Test

```bash
docker exec -it <container_name> python3 -c "
import httpx
url = 'http://ollama:11434/api/tags'
try:
    r = httpx.get(url, timeout=5)
    print(f'✅ Ollama reachable: {r.status_code}')
    print(f'Available models: {r.json()}')
except Exception as e:
    print(f'❌ Cannot reach Ollama: {e}')
"
```

---

## No Timeout Guarantee

The production code in `_run_ai_job_now` uses `no_timeout=True` which:

1. **Concurrency slot acquisition**: Waits indefinitely for a slot (`timeout=None`)
2. **HTTP request**: Uses `httpx.Client(timeout=None)` - no read/write/connect timeout

This ensures that scheduled AI jobs will **NEVER** be interrupted due to timeout, regardless of how long the LLM takes to respond.

---

## Troubleshooting

### Connection Error
- Check if Ollama is running: `docker ps | grep ollama`
- Test connectivity: `docker exec <container> curl http://ollama:11434/api/tags`

### Model Not Found
- List models: `docker exec <ollama_container> ollama list`
- Pull model: `docker exec <ollama_container> ollama pull qwen2.5:3b-instruct-q4_K_M`

### Process Seems Stuck
This is **normal** - the process is waiting for the LLM. Check Ollama logs:
```bash
docker logs -f <ollama_container>
```
