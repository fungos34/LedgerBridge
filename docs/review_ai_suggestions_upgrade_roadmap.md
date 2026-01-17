# Review AI Suggestions Upgrade Roadmap

**Document Version:** 1.0  
**Created:** 2026-01-17  
**Status:** ✅ IMPLEMENTED (2026-01-17)

---

## Implementation Summary

All requirements from this roadmap have been implemented:

1. **Full Field Coverage** - AI suggestions now cover: amount, currency, source_account, destination_account, date, description, category, transaction_type, invoice_number
2. **Split Transactions** - AI can suggest split transactions with amount, description, category for each line
3. **Source Account Matching** - Firefly accounts now include IBAN, account_number, BIC for intelligent matching
4. **Existing Transaction Detection** - UI now shows candidates for linking instead of creating duplicates
5. **Code Cleanup** - Legacy llm_suggestion fallback removed, codebase consolidated
6. **Validation** - All new suggestion types validated against Firefly data
7. **Tests** - Comprehensive test coverage for new validation logic

### Files Modified
- `firefly_client/client.py` - Added `include_identifiers` to `list_accounts()`, consolidated `list_currencies()`
- `spark_ai/prompts.py` - Extended prompt with currencies, existing transactions sections
- `spark_ai/service.py` - Extended `suggest_for_review()` with new params and validation
- `services/ai_queue.py` - Updated `process_job()` to fetch and pass new data
- `review/web/views.py` - Added helpers for detailed accounts, currencies, candidates; updated context
- `review/web/templates/review/unified_review_detail.html` - Added AI suggestion displays for all fields, existing transaction UI, updated split tolerance

### Tests Added
- `test_spark_ai.py` - 7 new tests for currency, source_account, existing_transaction validation
- `test_clients.py` - 2 new tests for `list_accounts_with_identifiers` and `list_currencies`

---

## 1. Context Snapshot (What exists today)

### 1.1 AI Suggestions Pipeline Overview

The current AI suggestions system operates through the following components:

#### Core Service: SparkAIService
- **Location:** [src/paperless_firefly/spark_ai/service.py](../src/paperless_firefly/spark_ai/service.py)
- **Key Method:** `suggest_for_review()` (lines 981-1200)
- **Dataclasses:**
  - `FieldSuggestion(value, confidence, reason)` — single field suggestion
  - `TransactionReviewSuggestion(suggestions, overall_confidence, analysis_notes, model, from_cache, split_transactions)` — complete response
- **Caching:** SHA256-based cache key with taxonomy version tracking
- **Validation:** Categories validated against Firefly taxonomy; transaction_type validated against `["withdrawal", "deposit", "transfer"]`

#### AI Job Queue
- **Migration:** [migrations/010_ai_job_queue.py](../src/paperless_firefly/state_store/migrations/010_ai_job_queue.py)
- **Schema:**
  ```sql
  ai_job_queue (
      id, document_id, extraction_id, external_id,
      status,  -- PENDING, PROCESSING, COMPLETED, FAILED, CANCELLED
      priority, scheduled_at, scheduled_for,
      started_at, completed_at,
      suggestions_json,  -- Stores TransactionReviewSuggestion.to_dict()
      error_message, retry_count, max_retries, created_by, notes
  )
  ```
- **Service:** [src/paperless_firefly/services/ai_queue.py](../src/paperless_firefly/services/ai_queue.py)
  - `schedule_job()`, `process_job()`, `get_job_suggestions()`, `get_next_jobs()`

#### Prompts
- **Location:** [src/paperless_firefly/spark_ai/prompts.py](../src/paperless_firefly/spark_ai/prompts.py)
- **Key Class:** `TransactionReviewPrompt` (lines 310-560)
- **Prompt Version:** `v1.2`
- **Parameters accepted:**
  - `amount`, `date`, `vendor`, `description`, `current_category`, `current_type`
  - `invoice_number`, `ocr_confidence`, `document_content`, `bank_transaction`
  - `previous_decisions`, `categories`, `source_accounts`, `current_source_account`

#### Storage & Retrieval
- **State Store:** [src/paperless_firefly/state_store/sqlite_store.py](../src/paperless_firefly/state_store/sqlite_store.py)
  - `schedule_ai_job()`, `get_ai_job_by_document()`, `complete_ai_job()`, `fail_ai_job()`
- **suggestions_json format:**
  ```json
  {
    "suggestions": {
      "category": {"value": "...", "confidence": 0.85, "reason": "..."},
      "description": {"value": "...", "confidence": 0.80, "reason": "..."},
      ...
    },
    "split_transactions": [
      {"amount": 15.99, "description": "...", "category": "..."}
    ],
    "overall_confidence": 0.85,
    "analysis_notes": "..."
  }
  ```

#### Review UI Integration
- **View:** [src/paperless_firefly/review/web/views.py](../src/paperless_firefly/review/web/views.py) — `unified_review_detail()` (lines 4800-5050)
- **Template:** [unified_review_detail.html](../src/paperless_firefly/review/web/templates/review/unified_review_detail.html)
- **Context Variables:**
  - `ai_job_status` — job metadata
  - `ai_suggestions` — unwrapped suggestions dict
  - `llm_suggestion` — legacy fallback for category-only suggestions
- **JavaScript Functions:**
  - `acceptAiField(fieldName, value)` — apply single suggestion
  - `applyAllAiSuggestions()` — apply all suggestions
  - `applySplitSuggestions(splits)` — apply split line items

### 1.2 Current Field Coverage

| Field | AI Suggestion Key | UI Field ID | Implemented |
|-------|------------------|-------------|-------------|
| Amount | `amount` | `#amount` | ⚠️ Partial (in prompt, not shown in UI) |
| Currency | `currency` | `#currency` | ❌ Not implemented |
| Date | `date` | `#date` | ✅ Yes |
| Description | `description` | `#description` | ✅ Yes |
| Source Account | `source_account` | `#source_account` | ⚠️ In prompt, not validated/shown |
| Destination/Vendor | `destination_account` | `#destination_account` | ✅ Yes |
| Category | `category` | `#category` | ✅ Yes |
| Transaction Type | `transaction_type` | `#transaction_type` | ⚠️ In prompt, not shown in UI |
| Invoice Number | `invoice_number` | `#invoice_number` | ⚠️ In prompt, not shown in UI |
| Split Transactions | `split_transactions` | Dynamic | ✅ Yes |

### 1.3 Authentication Pattern

All endpoints use `@login_required` decorator from Django. Auth routes are:
- `/login/` — LoginView
- `/logout/` — LogoutView
- `/register/` — `register_user` (no decorator, checks settings)

---

## 2. Requirements (Re-interpreted precisely)

### 2.1 MUST Requirements

| ID | Requirement |
|----|-------------|
| M1 | AI MUST provide suggestions for ALL review form fields: amount, currency, source_account, destination_account, category, description, date, transaction_type, invoice_number |
| M2 | AI MUST suggest split transactions with: amount, category, description (optional), tags (if UI supports) |
| M3 | Source account prompt context MUST include account identifiers: IBAN, account_number, display name, type |
| M4 | AI MUST suggest whether to create a new transaction OR link to an existing Firefly transaction |
| M5 | There MUST be exactly one canonical code path for: generating suggestions, storing suggestions_json, rendering suggestions, accepting/applying suggestions |
| M6 | Split amounts MUST sum to total amount (within ±0.05 tolerance) or display "unbalanced" warning |
| M7 | All endpoints MUST require authentication except: `/login/`, `/logout/`, `/register/` |

### 2.2 SHOULD Requirements

| ID | Requirement |
|----|-------------|
| S1 | Currency SHOULD be validated against Firefly's supported currencies |
| S2 | Source account suggestion SHOULD match by IBAN/account_number when visible in OCR |
| S3 | Existing transaction detection SHOULD use amount, date, account, and description signals |
| S4 | AI suggestions SHOULD be suppressed for user-edited fields |

### 2.3 MAY Requirements

| ID | Requirement |
|----|-------------|
| Y1 | Tags MAY be suggested per-split if UI supports split-level tags |
| Y2 | Confidence threshold MAY be configurable for auto-accepting suggestions |

---

## 3. Field Inventory & Mapping to UI

| UI Field Name | Field ID | Backend Suggestion Key | Firefly Validation/Mapping |
|---------------|----------|------------------------|---------------------------|
| Amount | `amount` | `amount` | Decimal, required, > 0 |
| Currency | `currency` | `currency` | 3-letter ISO code, validate against Firefly currencies |
| Transaction Date | `date` | `date` | YYYY-MM-DD format |
| Description | `description` | `description` | String, required |
| Source Account | `source_account` | `source_account` | MUST match an asset account name from Firefly |
| Destination/Vendor | `destination_account` | `destination_account` | String (will auto-create expense account) |
| Category | `category` | `category` | MUST match category name from Firefly taxonomy |
| Transaction Type | `transaction_type` | `transaction_type` | MUST be `withdrawal`, `deposit`, or `transfer` |
| Invoice Number | `invoice_number` | `invoice_number` | String, optional |
| Split Transactions | — | `split_transactions` | Array of `{amount, description, category, tags?}` |

### 3.1 Split Transaction Schema

```json
{
  "split_transactions": [
    {
      "amount": 15.99,
      "description": "Food items (milk, bread)",
      "category": "Groceries",
      "tags": ["food", "weekly"]  // Optional
    }
  ]
}
```

**Validation Rules:**
- `amount`: Decimal, required, > 0
- `description`: String, optional but recommended
- `category`: MUST match Firefly taxonomy
- Sum of all split amounts MUST equal main transaction amount (±0.05)

---

## 4. Firefly Data Requirements & Retrieval Plan

### 4.1 Current Account Fetching

**Location:** [views.py](../src/paperless_firefly/review/web/views.py) lines 1293-1302

```python
accounts = firefly_client.list_accounts("asset")
source_accounts = [acc.name for acc in accounts]
```

**Problem:** Only fetches `name`, not IBAN/account_number.

### 4.2 Required Changes to FireflyClient

**Location:** [client.py](../src/paperless_firefly/firefly_client/client.py#L493)

The `list_accounts()` method currently returns:
```python
{
    "id": ...,
    "name": ...,
    "type": ...,
    "currency_code": ...
}
```

**MUST add:** `iban`, `account_number`, `bic`

### 4.3 Currencies Retrieval

Firefly III supports currency listing via `/api/v1/currencies`. Need to add:
- `list_currencies()` method in FireflyClient
- Cache currencies for prompt validation

### 4.4 Existing Transaction Lookup

For "existing transaction candidate" detection, need:
1. Recent transactions from same source account (last 90 days)
2. Filter by amount match (±10% or exact)
3. Filter by date proximity (±7 days)

**Implementation:** Use existing `list_transactions()` or add `search_transactions()` method.

---

## 5. "Existing Target Transaction" Detection Strategy

### 5.1 Available Matching Signals

| Signal | Source | Weight |
|--------|--------|--------|
| Amount match | OCR extraction vs Firefly transaction | 0.40 |
| Date match | ±7 days | 0.25 |
| Account match | Source account IBAN/name | 0.20 |
| Description/vendor similarity | Fuzzy match | 0.15 |

### 5.2 Matching Rules

1. **Exact match (score ≥ 0.95):** Amount exact, date same day, account matches → Strong candidate
2. **Likely match (score ≥ 0.75):** Amount ±1%, date ±3 days, account matches → Suggest linking
3. **Possible match (score ≥ 0.50):** Amount ±5%, date ±7 days → Show as option

### 5.3 User Presentation

Add new suggestion field:
```json
{
  "suggestions": {
    ...
    "existing_transaction": {
      "value": {
        "firefly_id": 12345,
        "description": "Existing transaction description",
        "amount": "99.99",
        "date": "2025-01-10",
        "match_score": 0.92
      },
      "confidence": 0.92,
      "reason": "Exact amount and date match with account"
    }
  }
}
```

### 5.4 UI Extension Required

Current UI only supports "Create new transaction". Need to add:
- Radio buttons: "Create new" vs "Link to existing"
- If "Link to existing" selected, show transaction details and link button

---

## 6. Split Transactions: Full Suggestion Coverage

### 6.1 Existing Schema (to preserve)

```python
@dataclass
class TransactionReviewSuggestion:
    suggestions: dict[str, FieldSuggestion]
    overall_confidence: float
    analysis_notes: str
    model: str
    from_cache: bool = False
    split_transactions: list[dict] | None = None
```

### 6.2 Split Structure (current)

```python
split_transactions = [
    {
        "amount": float,
        "description": str,
        "category": str | None  # Must match taxonomy
    }
]
```

### 6.3 Required Extensions

Add optional fields to each split:
```python
{
    "amount": float,          # Required
    "description": str,       # Optional
    "category": str | None,   # Must match taxonomy
    "tags": list[str] | None, # Optional, must match existing tags
    "currency": str | None    # Optional, inherit from main transaction
}
```

### 6.4 Sum Validation Logic

```python
def validate_splits(splits: list[dict], total_amount: Decimal) -> tuple[bool, str]:
    """Validate split transactions sum to total."""
    if not splits:
        return True, ""
    
    split_sum = sum(Decimal(str(s.get("amount", 0))) for s in splits)
    tolerance = Decimal("0.05")
    
    if abs(split_sum - total_amount) <= tolerance:
        return True, ""
    else:
        diff = abs(split_sum - total_amount)
        return False, f"Split sum ({split_sum}) differs from total ({total_amount}) by {diff}"
```

---

## 7. Prompt & Parsing Changes

### 7.1 TransactionReviewPrompt Modifications

**File:** [prompts.py](../src/paperless_firefly/spark_ai/prompts.py#L310)

#### 7.1.1 Add Source Account Identifiers Section

Current:
```python
═══════════════════════════════════════════════════════════════
AVAILABLE SOURCE ACCOUNTS (use ONLY these for source_account):
═══════════════════════════════════════════════════════════════
{source_accounts}
```

Change to:
```python
═══════════════════════════════════════════════════════════════
AVAILABLE SOURCE ACCOUNTS (use ONLY these for source_account):
═══════════════════════════════════════════════════════════════
{source_accounts_detailed}

Note: Match payment method indicators to account identifiers:
- If IBAN visible on receipt, match to account with that IBAN
- "EC-Karte", "Maestro" → match to debit/checking account
- "Kreditkarte", "Visa", "Mastercard" → match to credit card account
```

#### 7.1.2 Update format_user_message() Signature

```python
def format_user_message(
    self,
    ...
    source_accounts: list[str] | None = None,  # DEPRECATED, use source_accounts_detailed
    source_accounts_detailed: list[dict] | None = None,  # NEW: with iban, account_number
    currencies: list[str] | None = None,  # NEW: valid currency codes
    existing_transactions: list[dict] | None = None,  # NEW: candidates for linking
    ...
) -> str:
```

#### 7.1.3 Add Currency and Existing Transaction Sections

```
═══════════════════════════════════════════════════════════════
AVAILABLE CURRENCIES:
═══════════════════════════════════════════════════════════════
{currencies}

═══════════════════════════════════════════════════════════════
EXISTING TRANSACTION CANDIDATES (potential matches):
═══════════════════════════════════════════════════════════════
{existing_transactions}

If an existing transaction matches this document closely, suggest linking 
instead of creating a new transaction.
```

#### 7.1.4 Update System Prompt Response Format

Add to expected JSON output:
```json
{
    "suggestions": {
        "amount": {"value": "123.45", "confidence": 0.95, "reason": "..."},
        "currency": {"value": "EUR", "confidence": 0.95, "reason": "..."},
        "source_account": {"value": "Checking Account", "confidence": 0.80, "reason": "..."},
        "existing_transaction": {
            "value": {"firefly_id": 12345, "action": "link"},
            "confidence": 0.90,
            "reason": "Exact match found"
        }
    },
    ...
}
```

### 7.2 Response Parsing Modifications

**File:** [service.py](../src/paperless_firefly/spark_ai/service.py#L1100)

#### 7.2.1 Add Validation Functions

```python
def _validate_currency(self, currency: str, valid_currencies: list[str]) -> bool:
    """Validate currency against Firefly's supported currencies."""
    return currency.upper() in [c.upper() for c in valid_currencies]

def _validate_source_account(
    self, account_name: str, valid_accounts: list[dict]
) -> bool:
    """Validate source account against available accounts."""
    return any(
        acc.get("name", "").lower() == account_name.lower()
        for acc in valid_accounts
    )
```

#### 7.2.2 Extended Parsing in suggest_for_review()

Add validation for new fields:
```python
# Validate currency
if field == "currency":
    if not self._validate_currency(field_data["value"], currencies):
        logger.warning("Invalid currency '%s'", field_data["value"])
        continue

# Validate source_account
if field == "source_account":
    if not self._validate_source_account(field_data["value"], source_accounts_detailed):
        logger.warning("Invalid source_account '%s'", field_data["value"])
        continue

# Validate existing_transaction structure
if field == "existing_transaction":
    if not isinstance(field_data.get("value"), dict):
        continue
    if "firefly_id" not in field_data["value"]:
        continue
```

---

## 8. "No Duplicated Code" Cleanup Plan

### 8.1 Identified Duplications

| Location | Duplication | Keep/Remove |
|----------|-------------|-------------|
| Template line 1066-1074 | `llm_suggestion` fallback for category | **REMOVE** |
| Template line 1036 | Dual condition `ai_suggestions.category.value or llm_suggestion` | **SIMPLIFY** |
| View line 4813-4815 | `_get_llm_suggestion_for_document()` call | **REMOVE** |
| View context line 5016 | `llm_suggestion` context variable | **REMOVE** |
| `acceptAIField` vs `acceptAiField` | Two functions, same purpose (lines 2194, 2665) | **MERGE** |

### 8.2 Canonical Approach

**Single pathway for AI suggestions:**

1. **Generation:** `AIJobQueueService.process_job()` → `SparkAIService.suggest_for_review()`
2. **Storage:** `state_store.complete_ai_job(job_id, suggestions_json)`
3. **Retrieval:** `state_store.get_ai_job_by_document()` → parse `suggestions_json`
4. **Rendering:** Template uses only `ai_suggestions` context variable
5. **Acceptance:** Single `acceptAiField(fieldName, value)` JavaScript function

### 8.3 Deletion Plan

#### 8.3.1 Remove `llm_suggestion` Legacy Path

**views.py:**
- Remove call to `_get_llm_suggestion_for_document()` (line 4815)
- Remove `llm_suggestion` from context (line 5016)

**unified_review_detail.html:**
- Remove `{% elif llm_suggestion %}` block (lines 1066-1074)
- Remove `or llm_suggestion` from conditions

#### 8.3.2 Merge Duplicate JS Functions

Keep `acceptAiField(fieldName, value)` (line 2194), remove `acceptAIField(fieldId)` (line 2665).

### 8.4 Verification

- Run full test suite after removal
- Manually verify AI suggestions display and acceptance still works
- Check no references to removed variables remain

---

## 9. Roadmap (Step-by-Step Implementation)

### Phase 1: Firefly Data Enhancement (Accounts + Currencies)

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 1.1 | `firefly_client/client.py`: Extend `list_accounts()` to return IBAN, account_number, bic | Unit test: verify new fields returned |
| 1.2 | `firefly_client/client.py`: Add `list_currencies()` method | Unit test: verify currencies returned |
| 1.3 | `views.py`: Update AI interpretation to fetch detailed accounts | Integration test: verify accounts passed to prompt |

### Phase 2: Prompt Enhancement

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 2.1 | `prompts.py`: Add `source_accounts_detailed` parameter and template section | Unit test: verify formatted output |
| 2.2 | `prompts.py`: Add `currencies` parameter and validation section | Unit test: verify formatted output |
| 2.3 | `prompts.py`: Add `existing_transactions` parameter and section | Unit test: verify formatted output |
| 2.4 | `prompts.py`: Update system prompt to request all fields + existing_transaction | Manual test: verify LLM response includes all fields |

### Phase 3: Service Layer Extensions

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 3.1 | `service.py`: Add currency validation | Unit test: valid/invalid currencies |
| 3.2 | `service.py`: Add source_account validation against detailed list | Unit test: valid/invalid accounts |
| 3.3 | `service.py`: Parse and validate `existing_transaction` suggestion | Unit test: valid/invalid structure |
| 3.4 | `service.py`: Update `suggest_for_review()` signature and implementation | Integration test: full suggestion flow |

### Phase 4: UI Field Coverage

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 4.1 | `unified_review_detail.html`: Add AI suggestion display for `amount` field | Manual test: verify display |
| 4.2 | `unified_review_detail.html`: Add AI suggestion display for `currency` field | Manual test: verify display |
| 4.3 | `unified_review_detail.html`: Add AI suggestion display for `source_account` field | Manual test: verify display |
| 4.4 | `unified_review_detail.html`: Add AI suggestion display for `transaction_type` field | Manual test: verify display |
| 4.5 | `unified_review_detail.html`: Add AI suggestion display for `invoice_number` field | Manual test: verify display |
| 4.6 | `unified_review_detail.html`: Update `applyAllAiSuggestions()` to include new fields | Manual test: verify Apply All works |

### Phase 5: Existing Transaction Detection

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 5.1 | `views.py`: Fetch candidate transactions for AI context | Unit test: candidates returned |
| 5.2 | `unified_review_detail.html`: Add "Link to existing" radio option | Manual test: UI displays |
| 5.3 | `unified_review_detail.html`: Add transaction candidate display | Manual test: candidate shown |
| 5.4 | `views.py`: Handle link-to-existing form submission | Integration test: link created |

### Phase 6: Deduplication Cleanup

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 6.1 | `views.py`: Remove `_get_llm_suggestion_for_document()` usage | Test suite passes |
| 6.2 | `views.py`: Remove `llm_suggestion` from context | Test suite passes |
| 6.3 | `unified_review_detail.html`: Remove `llm_suggestion` fallback blocks | Manual test: no regressions |
| 6.4 | `unified_review_detail.html`: Merge duplicate JS functions | Manual test: acceptance works |

### Phase 7: Split Transaction Enhancements

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 7.1 | `service.py`: Add sum validation for splits | Unit test: validation logic |
| 7.2 | `prompts.py`: Request currency per-split if different | Unit test: prompt output |
| 7.3 | `unified_review_detail.html`: Show "unbalanced" warning if sum mismatch | Manual test: warning displays |

### Phase 8: Tests and Security

| Step | Files/Modules | Verification |
|------|---------------|--------------|
| 8.1 | Add unit tests for new parsing/validation | `pytest tests/test_spark_ai.py` |
| 8.2 | Add integration tests for full suggestion flow | `pytest tests/test_ai_queue.py` |
| 8.3 | Add/verify auth tests for all endpoints | `pytest tests/test_auth_web.py` |
| 8.4 | Run full test suite | `pytest` all pass |

---

## 10. Test Plan

### 10.1 Unit Tests

| Test File | Test Case | Coverage |
|-----------|-----------|----------|
| `test_spark_ai.py` | `test_currency_validation_valid` | Currency in list accepted |
| `test_spark_ai.py` | `test_currency_validation_invalid` | Currency not in list rejected |
| `test_spark_ai.py` | `test_source_account_validation_valid` | Account name matches |
| `test_spark_ai.py` | `test_source_account_validation_by_iban` | Account matched by IBAN |
| `test_spark_ai.py` | `test_existing_transaction_parsing` | Parses firefly_id correctly |
| `test_spark_ai.py` | `test_split_sum_validation_balanced` | Sum matches total |
| `test_spark_ai.py` | `test_split_sum_validation_unbalanced` | Warning returned |
| `test_clients.py` | `test_list_accounts_includes_iban` | IBAN returned |
| `test_clients.py` | `test_list_currencies` | Currencies returned |

### 10.2 Integration Tests

| Test File | Test Case | Coverage |
|-----------|-----------|----------|
| `test_ai_queue.py` | `test_full_suggestion_flow_all_fields` | All fields suggested |
| `test_ai_queue.py` | `test_suggestions_include_source_account` | source_account validated |
| `test_ai_queue.py` | `test_suggestions_include_existing_transaction` | existing_transaction parsed |
| `test_web_review.py` | `test_accept_ai_suggestion_all_fields` | All fields accepted via JS |
| `test_web_review.py` | `test_link_existing_transaction` | Link action creates linkage |

### 10.3 Security Tests

| Test File | Test Case | Coverage |
|-----------|-----------|----------|
| `test_auth_web.py` | `test_all_endpoints_require_auth` | Unauthenticated → 302 to login |
| `test_auth_web.py` | `test_auth_routes_accessible` | Login/register accessible |
| `test_auth_web.py` | `test_api_endpoints_require_auth` | API endpoints return 403 |

### 10.4 Regression Tests

| Test File | Test Case | Coverage |
|-----------|-----------|----------|
| `test_spark_ai.py` | `test_suggest_for_review_backward_compatible` | Old API still works |
| `test_web_review.py` | `test_no_llm_suggestion_fallback` | No reference to removed variable |
| `test_unified_review.py` | `test_ai_suggestions_display` | Suggestions render correctly |

---

## 11. Definition of Done

### Checklist

- [ ] AI suggests ALL review form fields:
  - [ ] `amount` with validation
  - [ ] `currency` validated against Firefly currencies
  - [ ] `source_account` with IBAN/account_number matching
  - [ ] `destination_account` (vendor)
  - [ ] `category` validated against taxonomy
  - [ ] `description`
  - [ ] `date` in YYYY-MM-DD format
  - [ ] `transaction_type` validated
  - [ ] `invoice_number`

- [ ] AI suggests split transactions:
  - [ ] `amount` per split
  - [ ] `category` per split (validated)
  - [ ] `description` per split (optional)
  - [ ] Sum validation with ±0.05 tolerance
  - [ ] Unbalanced warning displayed if mismatch

- [ ] Existing transaction detection:
  - [ ] Candidate transactions fetched
  - [ ] `existing_transaction` suggestion parsed
  - [ ] UI supports "Link to existing" option
  - [ ] Link action creates proper linkage

- [ ] No duplicated code paths:
  - [ ] `llm_suggestion` variable removed from context
  - [ ] `_get_llm_suggestion_for_document()` not called in detail view
  - [ ] Single `acceptAiField()` JS function
  - [ ] No `{% elif llm_suggestion %}` in template

- [ ] All tests passing:
  - [ ] Unit tests for parsing/validation
  - [ ] Integration tests for full flow
  - [ ] Security tests for auth
  - [ ] Regression tests for backward compatibility

- [ ] All endpoints authenticated:
  - [ ] Except `/login/`, `/logout/`, `/register/`
  - [ ] Test verifies unauthenticated access fails

---

## Appendix A: File Reference

| File | Purpose |
|------|---------|
| `src/paperless_firefly/spark_ai/service.py` | Core AI service |
| `src/paperless_firefly/spark_ai/prompts.py` | Prompt templates |
| `src/paperless_firefly/services/ai_queue.py` | Job queue service |
| `src/paperless_firefly/state_store/sqlite_store.py` | Database operations |
| `src/paperless_firefly/firefly_client/client.py` | Firefly API client |
| `src/paperless_firefly/review/web/views.py` | Web views |
| `src/paperless_firefly/review/web/urls.py` | URL routing |
| `src/paperless_firefly/review/web/templates/review/unified_review_detail.html` | Review UI template |
| `tests/test_spark_ai.py` | AI service tests |
| `tests/test_ai_queue.py` | Queue tests |
| `tests/test_auth_web.py` | Auth tests |
