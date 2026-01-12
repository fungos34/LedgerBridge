# Spark/LedgerBridge Functionality Analysis Report

**Version:** 1.0  
**Date:** January 7, 2026  
**Scope:** Firefly III integration, Paperless matching, Ollama LLM support

---

## Executive Summary

This report analyzes the current LedgerBridge/Spark implementation for Firefly III integration. The system is **substantially complete** for Spark v1.0 goals with robust implementations for:

- ✅ **Firefly Import Model:** Single-transaction creation with idempotency via `external_id`
- ✅ **Local LLM Integration:** Full Ollama integration with global/per-document opt-out
- ✅ **Paperless↔Firefly Matching:** Multi-signal matching engine with proposal workflow

**Key Gaps Identified:**
- ⚠️ **Split transactions:** Schema exists, but `build_firefly_payload()` creates single-split transactions only
- ⚠️ **Bank-first flow:** Matching exists, but import defaults to "document creates new transaction"

---

## Capability Matrix

| Feature | Status | Section |
|---------|--------|---------|
| **A. Firefly Import Model** | | |
| Single transaction creation | ✅ Implemented | A.1 |
| Idempotent external_id | ✅ Implemented | A.2 |
| Split transactions (multi-line) | ⚠️ Partial | A.3 |
| Update existing transaction | ✅ Implemented | A.4 |
| Category assignment | ✅ Implemented | A.5 |
| Provenance notes | ✅ Implemented | A.6 |
| **B. Local LLM (Ollama)** | | |
| Global opt-in/out | ✅ Implemented | B.1 |
| Per-document opt-out | ✅ Implemented | B.2 |
| Category suggestions | ✅ Implemented | B.3 |
| Split suggestions | ✅ Implemented | B.4 |
| Calibration period | ✅ Implemented | B.5 |
| Response caching | ✅ Implemented | B.6 |
| Privacy/redaction | ⚠️ Partial | B.7 |
| **C. Paperless↔Firefly Matching** | | |
| Multi-signal matching engine | ✅ Implemented | C.1 |
| Proposal workflow | ✅ Implemented | C.2 |
| Auto-linking | ✅ Implemented | C.3 |
| Manual linking | ✅ Implemented | C.4 |
| Linkage markers | ✅ Implemented | C.5 |
| Interpretation audit trail | ✅ Implemented | C.6 |
| "Cash without bill" entry | ❌ Missing | C.7 |

---

## A. Firefly Import Model

### A.1 Transaction Creation Flow

**Status:** ✅ Implemented

**Code Paths:**
- [firefly_client/client.py](../src/paperless_firefly/firefly_client/client.py) → `create_transaction()` (line 285)
- [schemas/firefly_payload.py](../src/paperless_firefly/schemas/firefly_payload.py) → `build_firefly_payload()` (line 168)
- [runner/main.py](../src/paperless_firefly/runner/main.py) → `cmd_import()` (line 376)

**Firefly API Endpoints Used:**
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/transactions` | POST | Create new transaction |
| `/api/v1/transactions/{id}` | PUT | Update existing transaction |
| `/api/v1/transactions/{id}` | GET | Fetch transaction details |
| `/api/v1/search/transactions` | GET | Find by external_id |
| `/api/v1/accounts` | GET | List/validate accounts |
| `/api/v1/categories` | GET | List categories |

**Current Behavior:**
```python
# From build_firefly_payload() - creates SINGLE split transaction
split = FireflyTransactionSplit(
    type=proposal.transaction_type.value,  # withdrawal/deposit/transfer
    date=proposal.date,
    amount=str(proposal.amount),
    description=proposal.description,
    source_name=source_name,
    destination_name=destination_name,
    category_name=proposal.category,
    external_id=proposal.external_id,
    notes=notes,  # Contains provenance
    ...
)
return FireflyTransactionStore(transactions=[split])
```

### A.2 Idempotency via External ID

**Status:** ✅ Implemented

**External ID Format:**
```
paperless:{doc_id}:{sha256[:16]}:{amount}:{date}
```

**Example:** `paperless:1234:abcdef1234567890:35.70:2024-11-18`

**Code Path:** [schemas/dedupe.py](../src/paperless_firefly/schemas/dedupe.py) → `generate_external_id()` (line 50)

**Dedup Logic in `create_transaction()`:**
```python
# 1. Check local external_id first
existing = self.find_by_external_id(external_id)
if existing:
    if skip_duplicates:
        return existing.id  # Return existing ID, don't create
    else:
        raise FireflyDuplicateError(external_id, existing.id)

# 2. Handle Firefly 422 duplicate hash error
if e.status_code == 422 and "duplicate" in str(e.errors).lower():
    ...
```

### A.3 Split Transactions

**Status:** ⚠️ Partial (Schema exists, builder creates single split)

**Schema Support:**
- `FireflyTransactionSplit` dataclass supports `order` field for split ordering
- `FireflyTransactionStore.transactions` is a list (can hold multiple splits)
- `LineItem` dataclass in `finance_extraction.py` has `category` field (recently added)

**Current Gap:**
`build_firefly_payload()` always creates a single-split transaction:
```python
# CURRENT: Single split
return FireflyTransactionStore(transactions=[split])

# NEEDED: Multiple splits from line_items
if extraction.line_items and len(extraction.line_items) > 1:
    splits = []
    for idx, item in enumerate(extraction.line_items):
        splits.append(FireflyTransactionSplit(
            type=proposal.transaction_type.value,
            date=proposal.date,
            amount=str(item.total),
            description=item.description,
            category_name=item.category,
            order=idx,
            ...
        ))
    return FireflyTransactionStore(transactions=splits, group_title=proposal.description)
```

**Firefly API Split Support:**
✅ Firefly III supports splits natively - the `transactions` array can contain multiple items. All splits share: date, type, group_title. Each split has: amount, category, description.

**Example Fixture (Amazon 100€ split 80/15/5):**
```json
{
  "error_if_duplicate_hash": false,
  "apply_rules": true,
  "group_title": "Amazon Order #123-456",
  "transactions": [
    {
      "type": "withdrawal",
      "date": "2024-11-18",
      "amount": "80.00",
      "description": "Electronics - Headphones",
      "category_name": "Electronics",
      "source_name": "Girokonto",
      "destination_name": "Amazon",
      "external_id": "paperless:1234:abc123:100.00:2024-11-18",
      "order": 0
    },
    {
      "type": "withdrawal",
      "date": "2024-11-18",
      "amount": "15.00",
      "description": "Books",
      "category_name": "Books & Media",
      "source_name": "Girokonto",
      "destination_name": "Amazon",
      "order": 1
    },
    {
      "type": "withdrawal",
      "date": "2024-11-18",
      "amount": "5.00",
      "description": "Shipping",
      "category_name": "Shipping & Fees",
      "source_name": "Girokonto",
      "destination_name": "Amazon",
      "order": 2
    }
  ]
}
```

### A.4 Update Existing Transaction

**Status:** ✅ Implemented

**Code Paths:**
- `FireflyClient.update_transaction()` → PUT `/api/v1/transactions/{id}`
- `FireflyClient.update_transaction_linkage()` → Adds linkage markers to existing transaction

**Behavior:**
- Can update any transaction field via PUT
- Linkage update specifically adds: `external_id`, `internal_reference`, appends to `notes`

### A.5 Category Assignment

**Status:** ✅ Implemented

- `category_name` field in `FireflyTransactionSplit`
- Categories fetched from Firefly via `list_categories()`
- LLM can suggest categories from allowed set

### A.6 Provenance Notes

**Status:** ✅ Implemented

Every transaction includes audit notes:
```
Paperless doc_id=1234; source_hash=abcdef12345678; confidence=0.92; review_state=AUTO; parser=1.0.0
```

---

## B. Local LLM Integration (Ollama)

### B.1 Global Opt-In/Out

**Status:** ✅ Implemented

**Configuration:**
```yaml
# config.yaml
llm:
  enabled: false  # DEFAULT: OFF
  ollama_url: "http://localhost:11434"
  model_fast: "qwen2.5:3b-instruct-q4_K_M"
  model_fallback: "qwen2.5:7b-instruct-q4_K_M"
```

**Environment Override:**
```bash
SPARK_LLM_ENABLED=true  # Overrides config file
```

**Code Path:** [config.py](../src/paperless_firefly/config.py) → `LLMConfig` (line 45)

**Enforcement:**
```python
# spark_ai/service.py
@property
def is_enabled(self) -> bool:
    return self.llm_config.enabled

def suggest_category(self, ...):
    if not self.is_enabled:
        logger.debug("LLM service disabled, skipping suggestion")
        return None
```

### B.2 Per-Document Opt-Out

**Status:** ✅ Implemented

**Database Column:**
```sql
-- extractions table
llm_opt_out BOOLEAN DEFAULT FALSE
```

**UI Toggle:** "Use AI suggestions" checkbox in detail.html

**Code Paths:**
- [state_store/sqlite_store.py](../src/paperless_firefly/state_store/sqlite_store.py) → `update_extraction_llm_opt_out()` (line 429)
- [review/web/views.py](../src/paperless_firefly/review/web/views.py) → `toggle_llm_opt_out()` (line 817)

**Precedence Rules:**
1. If global `enabled=false` → No LLM runs
2. If global `enabled=true` AND document `llm_opt_out=true` → No LLM for this document
3. If global `enabled=true` AND document `llm_opt_out=false` → LLM runs

### B.3 Category Suggestions

**Status:** ✅ Implemented

**Code Path:** [spark_ai/service.py](../src/paperless_firefly/spark_ai/service.py) → `suggest_category()` (line 133)

**What is sent to LLM:**
| Input | Purpose |
|-------|---------|
| `amount` | Transaction amount |
| `date` | Transaction date |
| `vendor` | Merchant name |
| `description` | Transaction description |
| `categories` | Allowed category list (validation) |

**LLM Output Contract:**
```json
{
  "category": "Groceries",
  "confidence": 0.85,
  "reason": "Receipt mentions REWE supermarket"
}
```

**Validation:** Category must exist in allowed set, else suggestion rejected.

### B.4 Split Suggestions

**Status:** ✅ Implemented

**Code Path:** [spark_ai/service.py](../src/paperless_firefly/spark_ai/service.py) → `suggest_splits()` (line 247)

**Additional Input:** `content` (document text for line item detection)

**Output:**
```json
{
  "should_split": true,
  "splits": [
    {"category": "Electronics", "amount": 80.0, "description": "Headphones"},
    {"category": "Books", "amount": 15.0, "description": "Novel"},
    {"category": "Shipping", "amount": 5.0, "description": "Delivery fee"}
  ],
  "confidence": 0.78,
  "reason": "Multiple distinct item categories detected"
}
```

### B.5 Calibration Period

**Status:** ✅ Implemented

**Config:**
```yaml
llm:
  calibration_count: 100  # Force review for first 100 suggestions
  green_threshold: 0.85   # Auto-apply only above this
```

**Logic:**
```python
@property
def is_calibrating(self) -> bool:
    suggestion_count = self.store.get_llm_suggestion_count()
    return suggestion_count < self.llm_config.calibration_count

def should_auto_apply(self, confidence: float) -> bool:
    if not self.is_enabled:
        return False
    if self.is_calibrating:
        return False  # Force human review during calibration
    return confidence >= self.llm_config.green_threshold
```

### B.6 Response Caching

**Status:** ✅ Implemented

**Cache Key Components:**
- Prompt version
- Taxonomy version (hash of category list)
- Input parameters (amount, date, vendor, description)

**Invalidation:** Cache invalidated when:
- Prompt version changes
- Category taxonomy changes
- Cache TTL expires (default 30 days)

**Code:** `_build_cache_key()` in service.py, `set_llm_cache()` / `get_llm_cache()` in state_store

### B.7 Privacy & Data Governance

**Status:** ⚠️ Partial

**Implemented:**
- Ollama runs locally (no external API calls)
- Minimal context sent (amount, date, vendor, description - not full OCR)
- No API tokens/secrets sent to LLM

**Gaps:**
- No explicit PII redaction utility (`redaction.py` not implemented)
- IBAN/account number redaction not enforced before LLM call

**Confirmation:** When `llm.enabled=true` and `llm.ollama_url` points to localhost, all LLM processing is local. No remote model calls exist in codebase.

### B.8 Failure Modes

| Failure | Behavior |
|---------|----------|
| Ollama down | `suggest_category()` returns `None`, extraction proceeds without LLM |
| Model not found | Logs error, returns `None` |
| Timeout | Configurable `timeout_seconds`, returns `None` on timeout |
| Invalid JSON response | Strips markdown fences, retries with fallback model |
| Invalid category | Logs warning, returns `None` |

---

## C. Paperless↔Firefly Matching

### C.1 Matching Engine

**Status:** ✅ Implemented

**Code Path:** [matching/engine.py](../src/paperless_firefly/matching/engine.py)

**Signal Weights:**
| Signal | Weight | Details |
|--------|--------|---------|
| Amount | 0.40 | Exact match or within tolerance |
| Date | 0.25 | Within configurable window (default 7 days) |
| Description | 0.20 | Fuzzy text matching |
| Vendor | 0.15 | Direct name matching |

**Scoring:**
```python
def find_matches(self, document_id, extraction, max_results=5):
    for tx in cached_transactions:
        amount_score = self._score_amount(extracted_amount, tx_amount)
        date_score = self._score_date(extracted_date, tx_date)
        desc_score = self._score_description(extracted_desc, tx_desc)
        vendor_score = self._score_vendor(extracted_vendor, tx_vendor)
        
        total_score = sum(s.weighted_score for s in signals)
        
        if total_score >= 0.30:  # Minimum threshold
            results.append(MatchResult(...))
```

**Configuration:**
```yaml
reconciliation:
  date_tolerance_days: 7
  auto_match_threshold: 0.90
  proposal_threshold: 0.60
```

### C.2 Proposal Workflow

**Status:** ✅ Implemented

**Database Tables:**
```sql
-- Cached Firefly transactions
CREATE TABLE firefly_cache (
    firefly_id INTEGER PRIMARY KEY,
    external_id TEXT,
    type TEXT NOT NULL,
    date TEXT NOT NULL,
    amount TEXT NOT NULL,
    match_status TEXT DEFAULT 'UNMATCHED',
    matched_document_id INTEGER,
    match_confidence REAL,
    ...
);

-- Match proposals for review
CREATE TABLE match_proposals (
    id INTEGER PRIMARY KEY,
    firefly_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    match_score REAL NOT NULL,
    match_reasons TEXT,  -- JSON array
    status TEXT DEFAULT 'PENDING',  -- PENDING, ACCEPTED, REJECTED
    ...
);
```

**Code Path:** [services/reconciliation.py](../src/paperless_firefly/services/reconciliation.py)

### C.3 Auto-Linking

**Status:** ✅ Implemented

**Conditions for Auto-Link:**
1. Match score ≥ `auto_match_threshold` (0.90)
2. No other high-confidence proposals for same transaction (prevents ambiguous auto-link)
3. Transaction not already linked

**Code:**
```python
def _process_auto_links(self, result, dry_run):
    for firefly_id, proposals in proposals_by_tx.items():
        high_confidence = [p for p in proposals if p["match_score"] >= self.auto_match_threshold]
        
        if len(high_confidence) == 0:
            continue  # No auto-link candidate
        
        if len(high_confidence) > 1:
            logger.info("Skipping auto-link: ambiguous")
            continue
        
        # Single high-confidence match - auto-link
        self._execute_link(...)
```

### C.4 Manual Linking

**Status:** ✅ Implemented

**Code Path:** `ReconciliationService.manual_link()`, `link_proposal()`

**UI Actions:**
- "Link to Bank Transaction" button in review detail
- Confirmation page before linking
- Manual match override (user can link any document to any transaction)

### C.5 Linkage Markers

**Status:** ✅ Implemented

When a link is created, these markers are written to Firefly:

| Field | Value | Purpose |
|-------|-------|---------|
| `external_id` | `paperless:{doc_id}:{hash}:{amount}:{date}` | Primary linkage key |
| `internal_reference` | `SPARK:{doc_id}` | Alternative lookup |
| `notes` (appended) | `[Spark] Linked to Paperless document #{doc_id}` | Human-readable audit |

**Code Path:** [schemas/linkage.py](../src/paperless_firefly/schemas/linkage.py) → `build_linkage_markers()`

### C.6 Audit Trail (InterpretationRun)

**Status:** ✅ Implemented

**Database Table:**
```sql
CREATE TABLE interpretation_runs (
    id INTEGER PRIMARY KEY,
    document_id INTEGER,
    firefly_id INTEGER,
    external_id TEXT,
    run_timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    pipeline_version TEXT NOT NULL,
    inputs_summary TEXT NOT NULL,  -- JSON
    rules_applied TEXT,            -- JSON
    llm_result TEXT,               -- JSON (nullable)
    final_state TEXT NOT NULL,
    suggested_category TEXT,
    decision_source TEXT,          -- RULES, LLM, USER, AUTO
    firefly_write_action TEXT,     -- NONE, CREATE_NEW, UPDATE_EXISTING
    firefly_target_id INTEGER,
    linkage_marker_written TEXT,   -- JSON
    ...
);
```

**Every Reconciliation Action Records:**
- What inputs were used
- Which rules/LLM was applied
- What decision was made
- What was written to Firefly

### C.7 "Cash Without Bill" Entry

**Status:** ❌ Missing

**Requirement:** Allow manual entry form for cash transactions without a Paperless document (no bank booking to match).

**Current Gap:** No dedicated "manual cash entry" form exists. Workaround: Create a placeholder Paperless document.

---

## D. Test Plan

### D.1 Split Transaction Test Fixture

```python
# tests/test_firefly_payload.py

def test_amazon_split_80_15_5():
    """Test Amazon 100€ order split into 80/15/5 for Electronics/Books/Shipping."""
    extraction = FinanceExtraction(
        paperless_document_id=1234,
        source_hash="abc123def456" * 4,
        paperless_url="http://paperless:8000/documents/1234/",
        raw_text="Amazon Order...",
        proposal=TransactionProposal(
            transaction_type=TransactionType.WITHDRAWAL,
            date="2024-11-18",
            amount=Decimal("100.00"),
            currency="EUR",
            description="Amazon Order #123-456",
            source_account="Girokonto",
            destination_account="Amazon",
            external_id="paperless:1234:abc123def456:100.00:2024-11-18",
        ),
        line_items=[
            LineItem(description="Electronics - Headphones", total=Decimal("80.00"), category="Electronics", position=1),
            LineItem(description="Books", total=Decimal("15.00"), category="Books & Media", position=2),
            LineItem(description="Shipping", total=Decimal("5.00"), category="Shipping & Fees", position=3),
        ],
        confidence=ConfidenceScores(overall=0.90, ...),
        provenance=Provenance(...),
    )
    
    payload = build_firefly_payload_with_splits(extraction)
    
    # Assertions
    assert len(payload.transactions) == 3
    assert payload.group_title == "Amazon Order #123-456"
    
    assert payload.transactions[0].amount == "80.00"
    assert payload.transactions[0].category_name == "Electronics"
    
    assert payload.transactions[1].amount == "15.00"
    assert payload.transactions[1].category_name == "Books & Media"
    
    assert payload.transactions[2].amount == "5.00"
    assert payload.transactions[2].category_name == "Shipping & Fees"
    
    # Sum must equal total
    total = sum(Decimal(t.amount) for t in payload.transactions)
    assert total == Decimal("100.00")
```

### D.2 Expected Firefly API Response

```json
{
  "data": {
    "type": "transaction_groups",
    "id": "999",
    "attributes": {
      "group_title": "Amazon Order #123-456",
      "transactions": [
        {
          "transaction_journal_id": "1001",
          "type": "withdrawal",
          "date": "2024-11-18",
          "amount": "80.00",
          "description": "Electronics - Headphones",
          "category_name": "Electronics",
          "external_id": "paperless:1234:abc123def456:100.00:2024-11-18",
          "order": 0
        },
        {
          "transaction_journal_id": "1002",
          "type": "withdrawal",
          "date": "2024-11-18",
          "amount": "15.00",
          "description": "Books",
          "category_name": "Books & Media",
          "order": 1
        },
        {
          "transaction_journal_id": "1003",
          "type": "withdrawal",
          "date": "2024-11-18",
          "amount": "5.00",
          "description": "Shipping",
          "category_name": "Shipping & Fees",
          "order": 2
        }
      ]
    }
  }
}
```

### D.3 LLM Opt-Out Test

```python
def test_llm_global_disabled():
    """When LLM globally disabled, no suggestions generated."""
    config = Config(llm=LLMConfig(enabled=False))
    service = SparkAIService(store, config)
    
    result = service.suggest_category(amount="50.00", date="2024-11-18", vendor="REWE")
    assert result is None

def test_llm_per_document_opt_out():
    """Per-document opt-out prevents LLM even when globally enabled."""
    store.update_extraction_llm_opt_out(extraction_id=123, opt_out=True)
    
    # LLM should not be invoked for opted-out documents
    # (enforcement in reconciliation service)
```

### D.4 Matching Engine Test

```python
def test_matching_exact_amount_date():
    """Exact amount + date within window = high score."""
    extraction = {"amount": Decimal("50.00"), "date": "2024-11-18", "vendor": "REWE"}
    cached_tx = {"amount": "50.00", "date": "2024-11-19", "destination_account": "REWE"}
    
    results = engine.find_matches(document_id=1, extraction=extraction)
    
    assert len(results) > 0
    assert results[0].total_score >= 0.90  # Should auto-link

def test_matching_ambiguous_multiple_candidates():
    """Multiple high-score matches = no auto-link."""
    # Two cached transactions with same amount/date
    # Engine should return both, but auto-linker should skip
```

---

## E. Code Pointer Summary

| Component | File | Key Functions |
|-----------|------|---------------|
| Transaction Builder | `schemas/firefly_payload.py` | `build_firefly_payload()`, `FireflyTransactionSplit` |
| External ID | `schemas/dedupe.py` | `generate_external_id()`, `parse_external_id()` |
| Firefly API Client | `firefly_client/client.py` | `create_transaction()`, `update_transaction()`, `list_transactions()` |
| Matching Engine | `matching/engine.py` | `MatchingEngine.find_matches()`, `create_proposals()` |
| Reconciliation | `services/reconciliation.py` | `ReconciliationService.run_reconciliation()` |
| LLM Service | `spark_ai/service.py` | `SparkAIService.suggest_category()`, `suggest_splits()` |
| LLM Prompts | `spark_ai/prompts.py` | `CategoryPrompt`, `SplitPrompt` |
| State Store | `state_store/sqlite_store.py` | All persistence methods |
| Config | `config.py` | `LLMConfig`, `ReconciliationConfig` |
| Web Views | `review/web/views.py` | `review_detail()`, `save_extraction()`, `toggle_llm_opt_out()` |

---

## F. Recommendations

### Immediate (v1.0 completion)

1. **Implement split transaction builder:**
   - Modify `build_firefly_payload()` to create multiple splits from `line_items`
   - Add `group_title` support
   - Ensure split amounts sum to total

2. **Add "Cash Without Bill" form:**
   - New view/template for manual cash entry
   - Creates `FinanceExtraction` without Paperless document
   - Allows direct Firefly transaction creation

### Post-v1.0

3. **PII Redaction:** Implement `spark_ai/redaction.py` for IBAN/account masking before LLM

4. **Bank CSV Import:** Direct MT940/CAMT parsing (currently delegated to Firefly Data Importer)

---

## G. API Field Mapping

### Paperless → FinanceExtraction

| Paperless Field | FinanceExtraction Field |
|-----------------|-------------------------|
| `id` | `paperless_document_id` |
| `title` | `paperless_title` |
| `content` | `raw_text` |
| `correspondent.name` | `document_classification.correspondent` |
| `document_type.name` | `document_classification.document_type` |
| `tags[].name` | `document_classification.tags` |

### FinanceExtraction → Firefly

| FinanceExtraction Field | Firefly Transaction Field |
|-------------------------|---------------------------|
| `proposal.transaction_type` | `type` |
| `proposal.date` | `date` |
| `proposal.amount` | `amount` |
| `proposal.description` | `description` |
| `proposal.source_account` | `source_name` |
| `proposal.destination_account` | `destination_name` |
| `proposal.currency` | `currency_code` |
| `proposal.category` | `category_name` |
| `proposal.tags` | `tags` |
| `proposal.external_id` | `external_id` |
| (generated) | `internal_reference` = `PAPERLESS:{doc_id}` |
| (generated) | `notes` = provenance string |
| `paperless_url` + doc_id | `external_url` |

---

*Report generated January 7, 2026*
