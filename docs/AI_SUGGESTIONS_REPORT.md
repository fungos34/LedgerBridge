# AI Suggestions for Review — Comprehensive Technical Report

This document provides a complete technical overview of how AI-powered suggestions are structured, restricted, implemented, and integrated into the Unified Review workflow.

---

## 1. Architecture Overview

The AI suggestions system is built on four main components:

| Component | Location | Purpose |
|-----------|----------|---------|
| **SparkAIService** | `src/paperless_firefly/spark_ai/service.py` | Core LLM interaction service |
| **AIJobQueueService** | `src/paperless_firefly/services/ai_queue.py` | Job scheduling and processing |
| **SQLite State Store** | `src/paperless_firefly/state_store/sqlite_store.py` | Job persistence and retrieval |
| **Background Worker** | `src/paperless_firefly/review/web/apps.py` | Async job processing loop |

---

## 2. Data Structures

### 2.1 FieldSuggestion (Per-Field Result)

```python
@dataclass
class FieldSuggestion:
    value: Any           # The suggested value
    confidence: float    # 0.0 to 1.0 confidence score
    reason: str          # Human-readable explanation
```

### 2.2 TransactionReviewSuggestion (Complete Response)

```python
@dataclass
class TransactionReviewSuggestion:
    suggestions: Dict[str, FieldSuggestion]  # Field name → suggestion
    split_transactions: Optional[List[Dict[str, Any]]]  # For multi-item invoices
    overall_confidence: float  # Aggregated confidence
```

The `suggestions` dictionary can contain keys for any of these fields:
- `description`, `amount`, `date`, `category_id`, `category_name`
- `source_account_id`, `source_account_name`
- `destination_account_id`, `destination_account_name`
- `budget_id`, `budget_name`, `bill_id`, `bill_name`
- `tags`, `notes`, `external_id`, `internal_reference`

---

## 3. Database Schema (ai_job_queue)

Defined in migration `010_ai_job_queue.py`:

```sql
CREATE TABLE IF NOT EXISTS ai_job_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    user_id TEXT,                    -- Multi-user restriction
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER DEFAULT 0,
    prompt_hash TEXT,                -- For caching
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    result_json TEXT,                -- Serialized suggestions
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3
);
```

**Key columns:**
- `user_id`: Links job to specific user (added in migration 011)
- `status`: `pending` → `running` → `completed` / `failed`
- `result_json`: Stores the serialized `TransactionReviewSuggestion`
- `prompt_hash`: SHA256 hash for cache lookups

---

## 4. User Restriction & Multi-User Support

### 4.1 User Ownership

Every AI job is associated with a `user_id`:

```python
def schedule_ai_job(self, document_id: int, user_id: Optional[str] = None, ...) -> int:
    # Job is created with user_id
```

When retrieving jobs, the user_id is used to filter:

```python
def get_ai_job_by_document(self, document_id: int, user_id: Optional[str] = None) -> Optional[Dict]:
    # Returns job only if user_id matches
```

### 4.2 Per-User Settings (UserProfile)

Each user has individual AI settings stored in `UserProfile`:

| Setting | Purpose |
|---------|---------|
| `ai_suggestions_enabled` | Global on/off toggle |
| `ai_schedule_enabled` | Enable time-based scheduling |
| `ai_active_start` / `ai_active_end` | Active hours (e.g., 02:00–06:00) |
| `ai_days_of_week` | Which days to run (JSON array) |
| `ai_model_preference` | Preferred model name |
| `ai_max_concurrent_jobs` | Parallelism limit |

### 4.3 Per-User Scheduling Logic

The background worker checks user schedules before processing:

```python
def _is_user_schedule_enabled(user_id: str) -> bool:
    # Check if user has scheduling enabled

def _is_within_user_active_hours(user_id: str) -> bool:
    # Check current time against user's active window
```

---

## 5. Field-by-Field Suggestion Implementation

### 5.1 Generation Flow

1. **Review Detail View** (`views.py`) detects document needs suggestions
2. **AIJobQueueService.schedule_job()** creates a pending job
3. **Background Worker** picks up the job
4. **SparkAIService.suggest_for_review()** generates suggestions
5. **Result stored** in `ai_job_queue.result_json`

### 5.2 Field Mapping

The LLM returns a JSON object, which is parsed into `FieldSuggestion` objects:

```python
suggestions = {}
for field_name, field_data in llm_response.get("suggestions", {}).items():
    suggestions[field_name] = FieldSuggestion(
        value=field_data.get("value"),
        confidence=field_data.get("confidence", 0.5),
        reason=field_data.get("reason", "")
    )
```

### 5.3 Suppression of User-Edited Fields

In `unified_review_detail` view (lines ~4970-4990):

```python
# Suppress AI suggestions for fields user has already edited
if form_data.get(field_name):
    ai_suggestions.pop(field_name, None)
```

This ensures AI doesn't override user decisions.

---

## 6. Context Passed to the AI

### 6.1 TransactionReviewPrompt Parameters

The prompt template (`prompts.py`) accepts extensive context:

| Parameter | Description |
|-----------|-------------|
| `amount` | Extracted transaction amount |
| `date` | Transaction date |
| `vendor_name` | Detected vendor/merchant |
| `ocr_content` | Full OCR text from document |
| `document_title` | Paperless document title |
| `document_tags` | Tags assigned in Paperless |
| `correspondent` | Paperless correspondent field |
| `categories` | **All Firefly categories** (for suggestion) |
| `accounts` | **All Firefly accounts** (for mapping) |
| `budgets` | Available budgets |
| `bills` | Recurring bills |
| `existing_tags` | Existing Firefly tags |
| `currency_code` | Expected currency |
| `user_locale` | For date/number formatting |
| `additional_context` | Any extra hints |

### 6.2 System Prompt (Excerpt)

```
You are a financial document analysis assistant. Your task is to extract 
and suggest transaction details from document content.

Given the document text and available Firefly III entities, suggest:
1. Transaction description (clear, concise)
2. Amount (if visible in document)
3. Date (transaction or invoice date)
4. Category (from provided list)
5. Source and destination accounts
6. Relevant tags
7. Any notes or references

Return valid JSON only...
```

### 6.3 Taxonomy Context (Categories, Accounts, etc.)

The AI receives the **complete Firefly taxonomy** so it can suggest valid entity IDs:

```python
# From SparkAIService._get_cached_taxonomy()
taxonomy = {
    "categories": [...],  # All Firefly categories
    "accounts": [...],    # All Firefly accounts  
    "budgets": [...],     # All budgets
    "bills": [...],       # All bills
    "tags": [...]         # All tags
}
```

This is cached with a version hash to avoid repeated API calls.

---

## 7. Which AI is Queried

### 7.1 LLM Configuration

The system uses **Ollama** as the LLM backend:

```yaml
# config.yaml
ollama:
  enabled: true
  base_url: "http://localhost:11434"
  model_fast: "llama3.2:3b"      # Quick responses
  model_fallback: "llama3.1:8b"  # Higher quality fallback
  timeout: 120
```

### 7.2 Model Selection Logic

```python
def _select_model(self, task_complexity: str = "normal") -> str:
    if task_complexity == "simple":
        return self.config.model_fast
    return self.config.model_fallback
```

### 7.3 Request Format

```python
response = self._ollama_client.generate(
    model=selected_model,
    prompt=formatted_prompt,
    format="json",  # Force JSON output
    options={"temperature": 0.1}  # Low temperature for consistency
)
```

---

## 8. Caching System

### 8.1 Cache Key Generation

```python
def _generate_cache_key(self, document_id: int, prompt_hash: str, taxonomy_version: str) -> str:
    return hashlib.sha256(
        f"{document_id}:{prompt_hash}:{taxonomy_version}".encode()
    ).hexdigest()
```

### 8.2 Cache Invalidation

Cache is invalidated when:
- Taxonomy changes (new categories/accounts added)
- Document content changes
- User forces re-interpretation

---

## 9. UI Integration

### 9.1 Template Display (`unified_review_detail.html`)

For each field with a suggestion:

```html
{% if ai_suggestions.description %}
<div class="ai-suggestion">
    <span class="suggested-value">{{ ai_suggestions.description.value }}</span>
    <span class="confidence">{{ ai_suggestions.description.confidence|floatformat:0 }}%</span>
    <button class="accept-btn" data-field="description" 
            data-value="{{ ai_suggestions.description.value }}">
        Accept
    </button>
</div>
{% endif %}
```

### 9.2 Accept Button JavaScript

```javascript
$('.accept-btn').click(function() {
    const field = $(this).data('field');
    const value = $(this).data('value');
    $(`#id_${field}`).val(value);
    $(this).closest('.ai-suggestion').fadeOut();
});
```

---

## 10. Background Processing

### 10.1 Worker Loop (`apps.py`)

```python
def _ai_queue_worker_loop():
    while not _stop_event.is_set():
        _process_ai_queue_with_intervals()
        time.sleep(30)  # Poll every 30 seconds
```

### 10.2 Job Processing (`ai_queue.py`)

```python
def process_job(self, job_id: int) -> bool:
    job = self.state_store.get_ai_job(job_id)
    
    # Check opt-out
    if self._is_document_opted_out(job["document_id"]):
        self.state_store.fail_ai_job(job_id, "Document opted out")
        return False
    
    # Fetch fresh document data
    document = self.paperless_client.get_document(job["document_id"])
    
    # Generate suggestions
    result = self.spark_ai.suggest_for_review(
        document_id=job["document_id"],
        ocr_content=document.get("content", ""),
        ...
    )
    
    # Store result
    self.state_store.complete_ai_job(job_id, result.to_dict())
    return True
```

---

## 11. API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/ai/schedule/{doc_id}` | POST | Schedule AI job for document |
| `/api/ai/status/{doc_id}` | GET | Get job status and suggestions |
| `/api/ai/cancel/{doc_id}` | POST | Cancel pending job |
| `/api/ai/reschedule/{doc_id}` | POST | Force re-interpretation |

---

## 12. Error Handling & Retries

```python
# In sqlite_store.py
def fail_ai_job(self, job_id: int, error_message: str):
    job = self.get_ai_job(job_id)
    if job["retry_count"] < job["max_retries"]:
        # Reschedule with incremented retry count
        self._reschedule_job(job_id)
    else:
        # Mark as permanently failed
        self._update_job_status(job_id, "failed", error=error_message)
```

---

## 13. Security & Privacy

1. **No PII in logs**: Suggestions are logged at DEBUG level only
2. **User isolation**: Jobs are filtered by user_id
3. **Opt-out support**: Documents can be excluded from AI processing
4. **Local LLM**: Ollama runs locally, no data leaves the server
5. **Redaction**: Sensitive patterns can be redacted before LLM processing

---

## Summary

The AI suggestions system provides:
- **Asynchronous processing** via background worker
- **Per-user isolation** with individual settings and schedules
- **Field-by-field suggestions** with confidence scores and explanations
- **Rich context** including full Firefly taxonomy
- **Local LLM execution** via Ollama (no cloud dependency)
- **Caching** for efficiency with taxonomy-aware invalidation
- **Graceful degradation** with retry logic and opt-out support

The system is designed to be **advisory only** — all suggestions require explicit user acceptance, and user edits always take precedence over AI suggestions.
