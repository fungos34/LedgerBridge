# Spark Architecture Evaluation Report

**Analysis of LedgerBridge → Spark Transition**  
**Version:** 1.1  
**Date:** 2026-01-07  
**Reconciliation Status:** ✅ Verified against codebase

---

## Executive Summary

This report evaluates the current LedgerBridge codebase against the proposed Spark architecture concept. The analysis identifies **highly reusable components** (70%), **moderate refactor targets** (20%), and **deprecated paths** (10%), enabling a phased transition roadmap.

**Key Finding:** LedgerBridge's core architecture—SSOT schemas, extractor patterns, state store abstraction—directly maps to Spark's requirements. The primary evolution is expanding context sources (Firefly introspection) and coverage scope (bank reconciliation, cash+receipt flows).

---

## Table of Contents

1. [Architecture Mapping](#1-architecture-mapping)
2. [Reusable Components](#2-reusable-components)
3. [Refactor Targets](#3-refactor-targets)
4. [Deprecated Paths](#4-deprecated-paths)
5. [Gap Analysis](#5-gap-analysis)
6. [Local LLM Integration (Ollama)](#6-local-llm-integration-ollama)
7. [Phased Roadmap](#7-phased-roadmap)
8. [Risk Assessment](#8-risk-assessment)
9. [Decisions & Rationale](#9-decisions--rationale)

---

## 1. Architecture Mapping

### 1.1 Spark Principles vs LedgerBridge Implementation

| Spark Principle | LedgerBridge Status | Gap Level |
|-----------------|---------------------|-----------|
| **Firefly as SSOT** | ⚠️ Partial - Firefly is write target, not read source | Medium |
| **Bank CSV Integration** | ❌ Not implemented | High |
| **Cash+Receipt Flow** | ⚠️ Partial - Creates withdrawal from asset account to expense account (receipt-to-expense), does not model cash funding transfer | Medium |
| **Cash-Only Reconciliation** | ❌ No Firefly introspection | High |
| **Hash-Based Matching** | ✅ `external_id` with SHA256 prefix | Low |
| **Unified Review Form** | ⚠️ Works for receipts, no split reconciliation | Medium |
| **Traffic Light Confidence** | ✅ AUTO/REVIEW/MANUAL (0.85/0.60) | Low |
| **Tax Relevance** | ⚠️ `tax_amount`/`tax_rate` exist, no first-class handling | Medium |
| **Line Items Model** | ✅ `LineItem` dataclass exists | Low |

### 1.2 Current Data Flow

```
                    LedgerBridge (Current)
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Paperless-ngx ──► Extractor Router ──► State Store        │
│        │                  │                   │             │
│   (PDF/XML)          (ZUGFeRD/OCR)     (SQLite)             │
│        │                  │                   │             │
│        └────► FinanceExtraction ◄─────────────┘             │
│                      │                                      │
│                      ▼                                      │
│               Review Web UI                                 │
│                      │                                      │
│                      ▼                                      │
│           FireflyTransactionStore ──► Firefly III           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 Spark Target Data Flow

```
                         Spark (Target)
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   ┌─────────────┐     ┌─────────────┐                       │
│   │ Paperless   │     │ Firefly III │  (bidirectional)      │
│   │ (receipts)  │     │ (bank txns) │                       │
│   └──────┬──────┘     └──────┬──────┘                       │
│          │                   │                              │
│          ▼                   ▼                              │
│   ┌─────────────────────────────────────┐                   │
│   │      Unified Context Layer          │                   │
│   │  (Documents + Existing Transactions)│                   │
│   └──────────────────┬──────────────────┘                   │
│                      │                                      │
│                      ▼                                      │
│   ┌─────────────────────────────────────┐                   │
│   │         Matching Engine             │                   │
│   │   (Hash + Fuzzy + Time Window)      │                   │
│   └──────────────────┬──────────────────┘                   │
│                      │                                      │
│                      ▼                                      │
│   ┌─────────────────────────────────────┐                   │
│   │         Review Interface            │                   │
│   │    (Receipt + Reconciliation)       │                   │
│   └──────────────────┬──────────────────┘                   │
│                      │                                      │
│                      ▼                                      │
│              Firefly III (SSOT)                             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Reusable Components

### 2.1 Schema Layer (100% Reusable)

**Files:** [finance_extraction.py](src/paperless_firefly/schemas/finance_extraction.py), [firefly_payload.py](src/paperless_firefly/schemas/firefly_payload.py), [dedupe.py](src/paperless_firefly/schemas/dedupe.py)

| Component | Spark Applicability | Notes |
|-----------|---------------------|-------|
| `FinanceExtraction` | ✅ Direct | Canonical SSOT for extracted data |
| `TransactionProposal` | ✅ Direct | Maps to Firefly transaction structure |
| `LineItem` | ✅ Direct | Supports line item expansion (Spark §9) |
| `ConfidenceScores` | ✅ Direct | Traffic light system (Spark §6) |
| `ReviewState` (AUTO/REVIEW/MANUAL) | ✅ Direct | Core routing logic |
| `generate_external_id()` | ✅ Direct | Hash-based dedup (Spark §4) |
| `FireflyTransactionStore` | ✅ Direct | API payload builder |

**Recommendation:** Keep as-is. These form Spark's schema foundation.

### 2.2 Extractor Architecture (95% Reusable)

**Files:** [base.py](src/paperless_firefly/extractors/base.py), [router.py](src/paperless_firefly/extractors/router.py), [einvoice_extractor.py](src/paperless_firefly/extractors/einvoice_extractor.py), [ocr_extractor.py](src/paperless_firefly/extractors/ocr_extractor.py)

| Component | Spark Applicability | Notes |
|-----------|---------------------|-------|
| `BaseExtractor` interface | ✅ Direct | Abstract base with priority system |
| `ExtractorRouter` | ✅ Direct | Strategy pattern for extractor selection |
| `EInvoiceExtractor` | ✅ Direct | ZUGFeRD/UBL/PEPPOL support |
| `OCRTextExtractor` | ✅ Direct | Fallback heuristics |
| `ExtractionResult` dataclass | ✅ Direct | Intermediate extraction format |

**Extension Needed:** Add `BankStatementExtractor` for CSV/MT940/CAMT parsing.

### 2.3 Firefly Client (85% Reusable)

**File:** [firefly_client/client.py](src/paperless_firefly/firefly_client/client.py)

| Method | Spark Applicability | Notes |
|--------|---------------------|-------|
| `create_transaction()` | ✅ Direct | Core write operation |
| `update_transaction()` | ✅ Direct | Recently added for reimport |
| `find_by_external_id()` | ✅ Direct | Dedup check |
| `list_accounts()` | ✅ Direct | Account selection |
| `get_transaction()` | ✅ Direct | Single transaction fetch |

**Extension Needed:**
- `list_transactions()` - For Firefly introspection (Spark §3)
- `search_transactions()` - Date range + amount matching
- `get_unreconciled_transactions()` - Cash-only flow support

### 2.4 State Store (80% Reusable)

**File:** [state_store/sqlite_store.py](src/paperless_firefly/state_store/sqlite_store.py)

| Table/Method | Spark Applicability | Notes |
|--------------|---------------------|-------|
| `paperless_documents` | ✅ Direct | Document tracking |
| `extractions` | ✅ Direct | Extraction storage |
| `imports` | ✅ Direct | Import tracking |
| `vendor_mappings` | ✅ Direct | Learning from edits |
| `bank_matches` | ⚠️ Placeholder | Needs full implementation |

**Extension Needed:**
- `firefly_transactions` table - Cache of Firefly state
- `reconciliation_queue` table - Bank↔Receipt matching
- Match scoring persistence

### 2.5 Confidence Scoring (90% Reusable)

**File:** [confidence/scorer.py](src/paperless_firefly/confidence/scorer.py)

| Component | Spark Applicability | Notes |
|-----------|---------------------|-------|
| `ConfidenceThresholds` | ✅ Direct | Configurable thresholds |
| `ConfidenceScorer` | ✅ Direct | Strategy-based scoring |
| `STRATEGY_BASE_CONFIDENCE` | ⚠️ Expand | Add bank_csv, reconciliation strategies |

### 2.6 Review Web UI (70% Reusable)

**Files:** [review/web/views.py](src/paperless_firefly/review/web/views.py), templates

| View | Spark Applicability | Notes |
|------|---------------------|-------|
| `review_list` | ✅ Direct | Queue listing |
| `review_detail` | ⚠️ Adapt | Needs reconciliation mode |
| `extraction_archive` | ✅ Direct | Archive/reset functionality |
| `user_settings` | ✅ Direct | Per-user API tokens |
| Background jobs | ✅ Direct | Extraction/import runners |

---

## 3. Refactor Targets

### 3.1 Firefly Client → Bidirectional

**Current:** Write-only (Paperless → Firefly)  
**Target:** Read+Write (Firefly introspection for matching)

**Changes Required:**

```python
# firefly_client/client.py - NEW METHODS

def list_transactions(
    self,
    start_date: str,
    end_date: str,
    account_id: Optional[int] = None,
    type_filter: Optional[str] = None,  # withdrawal, deposit, transfer
) -> Iterator[FireflyTransaction]:
    """
    List transactions in date range.
    
    Spark §3: Dual context sources - Firefly as context
    """
    page = 1
    while True:
        response = self._request(
            "GET",
            "/api/v1/transactions",
            params={
                "start": start_date,
                "end": end_date,
                "type": type_filter,
                "page": page,
            },
        )
        # ... pagination handling

def get_unlinked_transactions(
    self,
    start_date: str,
    end_date: str,
) -> list[FireflyTransaction]:
    """
    Get transactions without external_id (bank imports waiting for receipts).
    
    Spark §7: "Transactions Without Receipts"
    """
    # Filter: transactions where external_id is null/empty
```

**Effort:** Medium (2-3 days)

### 3.2 State Store → Reconciliation Tables

**Current:** Document-centric (paperless_document_id as primary key)  
**Target:** Transaction-centric (support bank-first flow)

**Changes Required:**

```sql
-- NEW: Track Firefly transactions (cache)
CREATE TABLE firefly_cache (
    firefly_id INTEGER PRIMARY KEY,
    external_id TEXT,
    type TEXT NOT NULL,
    date TEXT NOT NULL,
    amount TEXT NOT NULL,
    description TEXT,
    source_account TEXT,
    destination_account TEXT,
    synced_at TEXT NOT NULL,
    -- Match state
    match_status TEXT DEFAULT 'UNMATCHED',  -- UNMATCHED, PROPOSED, CONFIRMED
    matched_document_id INTEGER,
    match_confidence REAL,
    FOREIGN KEY (matched_document_id) REFERENCES paperless_documents(document_id)
);

-- NEW: Proposed matches for review
CREATE TABLE match_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firefly_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    match_score REAL NOT NULL,
    match_reasons TEXT,  -- JSON: ["amount_exact", "date_within_3_days", ...]
    status TEXT DEFAULT 'PENDING',  -- PENDING, ACCEPTED, REJECTED
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY (firefly_id) REFERENCES firefly_cache(firefly_id),
    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
);
```

**Effort:** Medium (2-3 days)

### 3.3 Review UI → Unified Form

**Current:** Receipt-only review (approve/edit/reject extraction)  
**Target:** Dual-mode (extraction review + reconciliation matching)

**Changes Required:**

1. **New Template:** `review/reconcile.html`
   - Side-by-side: Bank transaction ↔ Receipt candidates
   - Match confidence indicators
   - Manual match/split controls

2. **New Views:**
   - `reconciliation_queue` - List unmatched bank transactions
   - `reconciliation_detail` - Match interface
   - `confirm_match` - Finalize match

3. **Modified Views:**
   - `review_detail` - Add "Link to Bank Transaction" option

**Effort:** High (4-5 days)

### 3.4 Config → Multi-Source

**Current:** Paperless + Firefly (write) + thresholds  
**Target:** Add bank import sources, reconciliation settings

**Changes Required:**

```yaml
# config.yaml additions

bank_import:
  # Sources for bank transaction import
  sources:
    - type: firefly_importer  # Existing Firefly Data Importer
      name: "Bank CSV Import"
    - type: direct_csv
      path: "/data/bank_exports/"
      format: "mt940"  # or camt052, csv

reconciliation:
  # Time window for fuzzy date matching (Spark §4)
  date_tolerance_days: 7
  # Minimum score to auto-confirm match
  auto_match_threshold: 0.90
  # Minimum score to show in proposals
  proposal_threshold: 0.60
```

**Effort:** Low (1 day)

---

## 4. Deprecated Paths

### 4.1 Hardcoded Document-First Assumption

**Location:** [runner/main.py](src/paperless_firefly/runner/main.py) `cmd_pipeline()`

**Issue:** Assumes pipeline always starts with Paperless document scan.

**Deprecation Plan:**
1. Keep for backwards compatibility
2. Add parallel `cmd_reconcile()` entry point
3. Eventually unify under generic `spark run` command

### 4.2 Single-Source Extraction

**Location:** [extractors/router.py](src/paperless_firefly/extractors/router.py)

**Issue:** `ExtractorRouter.extract()` only accepts `PaperlessDocument`.

**Deprecation Plan:**
1. Generalize to accept `ContextDocument` interface
2. `PaperlessDocument` and `FireflyTransaction` both implement interface
3. Router dispatches to appropriate extractors

### 4.3 External ID as Sole Dedup Key

**Location:** [schemas/dedupe.py](src/paperless_firefly/schemas/dedupe.py)

**Issue:** Current format `paperless:{doc_id}:{hash}:{amount}:{date}` assumes Paperless origin.

**Evolution Plan:**
```python
# Current format (keep for Paperless-origin)
paperless:{doc_id}:{hash}:{amount}:{date}

# New format for bank-origin (reconciled)
bank:{firefly_id}:{amount}:{date}

# New format for cash (manual entry)
cash:{uuid}:{amount}:{date}
```

### 4.4 Tag-Based Filtering Only

**Location:** [paperless_client/client.py](src/paperless_firefly/paperless_client/client.py) `list_documents()`

**Issue:** Only supports `tags` filter, not date ranges or document types.

**Enhancement:** Add `date_range`, `correspondent`, `document_type` filters for better Firefly↔Paperless matching.

---

## 5. Gap Analysis

### 5.1 Critical Gaps (Block Spark Core Features)

| Gap | Spark Section | Impact | Effort |
|-----|---------------|--------|--------|
| No Firefly transaction listing | §3, §7 | Cannot query existing transactions for reconciliation | High |
| No "unlinked transaction" detection | §7 | Cannot identify bank imports awaiting receipts | Medium |
| No reconciliation matching | §4 | Core Spark feature missing | High |

### 5.2 Moderate Gaps (Reduce Feature Completeness)

| Gap | Spark Section | Impact | Effort |
|-----|---------------|--------|--------|
| Cash flow incomplete | §2 | Only receipt→expense, no Bank→Cash→Merchant model | Medium |
| Tax not first-class | §8 | Tax reporting less automated | Medium |
| No split transactions | §9 | Cannot expand line items to splits | Medium |
| Review UI single-mode | §5 | No unified reconciliation view | Medium |
| No category fetching from Firefly | §LLM | Must hardcode categories or manual config | Low |

### 5.3 Low Gaps (Nice-to-Have)

| Gap | Spark Section | Impact | Effort |
|-----|---------------|--------|--------|
| No batch operations | - | UX for bulk review | Low |
| Limited search | - | Find specific transactions | Low |
| No LLM-assisted categorization | §LLM | Manual category assignment for multilingual receipts | Medium |

### 5.4 Out of Scope for v1.0

| Item | Rationale |
|------|-----------|
| Direct bank CSV/MT940 parsing | Users should use Firefly Data Importer; Phase 4B post-v1.0 |
| Multi-Firefly instance support | external_id contains Paperless doc_id; future versioned format |
| Automatic cash funding transfers | Requires user-defined cash account mapping |

---

## 6. Local LLM Integration (Ollama)

### 6.1 Concept Summary

Integrate a local Open LLM (via Ollama) as an optional **"assist layer"** in Spark's interpretation pipeline. The LLM proposes categories (and optional splits) from an **existing allowed category set** and is **never the authority** for ledger truth, matching, or transaction creation.

**Purpose:** Reduce manual categorization work, especially for multilingual receipts and booking texts (German/English/French/Turkish/Russian/Chinese/etc.), while preserving strict determinism and auditability.

**Key Constraints:**
- LLM is advisory only—never authoritative
- Outputs must be validated against allowed category set
- Runs asynchronously (background worker) to avoid UI latency
- Full opt-out support (global + per-document)
- Complete audit trail for every interpretation run

### 6.2 Recommended Models (Configurable)

Use a **two-tier CPU-friendly strategy** for target hardware (Acer Aspire V / i5 / 16GB RAM):

| Tier | Model | Use Case |
|------|-------|----------|
| **Fast default** | `qwen2.5:3b-instruct` | Most categorizations |
| **Fallback** | `qwen2.5:7b-instruct` (quantized) | Hard cases, ambiguous inputs |

Model names are **configurable via environment/config** without code changes:

```yaml
# config.yaml
llm:
  enabled: true
  model_fast: "qwen2.5:3b-instruct"
  model_fallback: "qwen2.5:7b-instruct"
  ollama_url: "http://localhost:11434"
  timeout_seconds: 30
  max_retries: 2
```

### 6.3 Architecture Placement

Add a dedicated internal module: **`spark_ai/`**

```
src/spark/
├── spark_ai/
│   ├── __init__.py
│   ├── ollama_client.py      # HTTP transport to Ollama API
│   ├── categorizer.py        # Prompting + JSON parsing + validation
│   ├── orchestrator.py       # Fallback routing + caching
│   ├── redaction.py          # PII/secret redaction utilities
│   └── schemas.py            # LLM request/response dataclasses
└── tests/
    ├── test_ollama_client.py
    ├── test_categorizer.py
    └── fixtures/
        └── multilingual_samples/
```

**Integration Point:** After deterministic rules in `ExtractorRouter`, before final `FinanceExtraction` proposal.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Interpretation Pipeline                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   Document/Transaction ──► Deterministic Rules                   │
│                                   │                              │
│                          (high confidence?)                      │
│                            /            \                        │
│                          YES            NO                       │
│                           │              │                       │
│                           │     [LLM enabled + not opted-out?]   │
│                           │              │                       │
│                           │         ┌────┴────┐                  │
│                           │        YES        NO                 │
│                           │         │          │                 │
│                           │   Enqueue LLM Job  │                 │
│                           │         │          │                 │
│                           │   ┌─────▼─────┐    │                 │
│                           │   │ spark_ai/ │    │                 │
│                           │   │ Categorizer│    │                 │
│                           │   └─────┬─────┘    │                 │
│                           │         │          │                 │
│                           ▼         ▼          ▼                 │
│                      FinanceExtraction (with category proposal)  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Critical:** Do **not** call the LLM in the request path. Use Spark's existing worker/queue mechanism for asynchronous inference.

### 6.4 Strict Output Contract (JSON-Only)

The LLM must output **strict JSON only** selecting from the existing category set:

```json
{
  "category_id": "groceries",
  "confidence": 0.85,
  "alternatives": [
    {"category_id": "household", "confidence": 0.12},
    {"category_id": "restaurant", "confidence": 0.03}
  ],
  "reasons": ["Receipt mentions REWE supermarket", "Items are food products"],
  "language_detected": "de"
}
```

**Validation Rules:**

| Field | Constraint |
|-------|------------|
| `category_id` | Must exist in `allowed_categories` set |
| `confidence` | Float in range `[0.0, 1.0]` |
| `alternatives` | Max 3 entries, all valid category IDs |
| `reasons` | Max 3 short strings (for audit trail) |
| `language_detected` | Optional ISO 639-1 code |

**Error Handling:**
1. Strip markdown code fences defensively (`json...`)
2. If JSON parse fails → try fallback model (once)
3. If still invalid → mark low confidence, require human review
4. Never trust raw text outside JSON structure

### 6.5 Input Minimization & Privacy

**Provide to LLM (minimized context):**

| Input | Example | Notes |
|-------|---------|-------|
| `amount_total` | `"35.70"` | Currency-normalized |
| `currency` | `"EUR"` | ISO code |
| `date` | `"2024-11-18"` | ISO format |
| `booking_text` | `"REWE SAGT DANKE"` | Bank statement description |
| `merchant_guess` | `"REWE"` | From OCR/rules |
| `ocr_keywords` | `["Milch", "Brot", "Bio"]` | Short line item strings, not full OCR dump |
| `historical_hints` | `["groceries", "household"]` | Top categories for similar merchants |
| `allowed_categories` | `[{"id": "groceries", "name": "Lebensmittel", "synonyms": [...]}]` | Full category taxonomy |

**Redaction Before Sending:**

```python
# spark_ai/redaction.py

def redact_sensitive(text: str) -> str:
    """Redact IBANs, account numbers, keeping last 4 digits."""
    # DE89 3704 0044 0532 0130 00 → DE89 **** **** **** **30 00
    # Never include API tokens/secrets
    # Optionally redact names based on policy
```

### 6.6 Confidence & UX Integration

LLM output maps to existing Spark traffic light system:

| LLM Confidence | Review State | UI Behavior |
|----------------|--------------|-------------|
| ≥ 0.80 | 🟢 GREEN | Auto-fill category (still reviewable) |
| 0.60 – 0.79 | 🟡 YELLOW | Pre-fill, review recommended |
| < 0.60 or invalid | 🔴 RED | Review required, show alternatives |

**Deterministic Rules Precedence:**
- If deterministic rule has high certainty → prefer it over LLM
- If LLM conflicts with strong deterministic rule → mark for review, record conflict in audit trail
- LLM suggestions shown as "AI suggestion" badge in UI

### 6.7 Opt-Out Support

#### 6.7.1 Global Opt-Out

```bash
# Environment variable
SPARK_LLM_ENABLED=false
```

**When disabled:**
- Pipeline runs deterministic rules + non-LLM heuristics only
- No LLM jobs are scheduled
- UI clearly indicates "AI suggestions disabled"

#### 6.7.2 Per-Document Opt-Out

Each review object (Paperless document or unmatched Firefly transaction) supports independent opt-out:

```python
# state_store/sqlite_store.py - NEW COLUMN

# In extractions table:
llm_opt_out BOOLEAN DEFAULT FALSE
```

**UI Toggle:** "Use AI suggestions" checkbox (default ON if globally enabled)

**Behavior when opted-out:**
- Do not run LLM for this object
- Object remains eligible for deterministic rules + manual review
- Useful for sensitive documents or "LLM keeps getting this wrong" cases

### 6.8 Rescheduling / Re-Running Interpretation

Spark must allow each document/transaction to be rescheduled for interpretation at any time:

**UI Action:** "Re-run interpretation" button

**Optional Reasons (recorded):**
- Bank import arrived
- OCR text updated
- Rules changed
- Category list updated
- User requested

**Behavior:**
- Rescheduling is idempotent and trackable
- Previous runs remain recorded (history preserved)
- New run creates a new `InterpretationRun` record

**Automatic Triggers (optional):**
- Paperless document updated (tags/custom fields changed)
- OCR text modified
- Matching transaction appears in Firefly after CSV import
- Category taxonomy changes

### 6.9 Per-Document Audit Trail (InterpretationRun)

For each document/transaction, maintain a human-readable report:

```sql
-- NEW TABLE: interpretation_runs
CREATE TABLE interpretation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Target object
    document_id INTEGER,
    firefly_id INTEGER,
    external_id TEXT,
    
    -- Run metadata
    run_timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    pipeline_version TEXT NOT NULL,  -- e.g., "spark-1.2.0"
    algorithm_version TEXT,          -- e.g., "rules-v3+llm-v1"
    
    -- Input summary (JSON)
    inputs_summary TEXT NOT NULL,
    -- {
    --   "sources": ["paperless", "firefly"],
    --   "has_ocr": true,
    --   "match_candidates": [{"firefly_id": 123, "score": 0.85}]
    -- }
    
    -- Rules applied (JSON)
    rules_applied TEXT,
    -- [
    --   {"rule_id": "vendor_rewe", "output": "groceries", "confidence": 0.9}
    -- ]
    
    -- LLM involvement (JSON, nullable)
    llm_result TEXT,
    -- {
    --   "model": "qwen2.5:3b-instruct",
    --   "prompt_version": "cat-v2",
    --   "response": {"category_id": "groceries", "confidence": 0.85, ...},
    --   "fallback_used": false
    -- }
    
    -- Final decision
    final_state TEXT NOT NULL,        -- GREEN, YELLOW, RED
    suggested_category TEXT,
    suggested_splits TEXT,            -- JSON array if applicable
    auto_applied BOOLEAN DEFAULT FALSE,
    
    -- Operational clarity fields (see §9.8)
    decision_source TEXT,             -- RULES, LLM, HYBRID, USER
    firefly_write_action TEXT,        -- NONE, CREATE_NEW, UPDATE_EXISTING
    firefly_target_id INTEGER,        -- Firefly transaction ID (if UPDATE_EXISTING)
    linkage_marker_written TEXT,      -- JSON: {"external_id": "...", "notes_appended": true}
    taxonomy_version TEXT,            -- Category taxonomy hash at run time
    
    -- Foreign keys
    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
);

CREATE INDEX idx_interpretation_runs_document ON interpretation_runs(document_id);
CREATE INDEX idx_interpretation_runs_firefly ON interpretation_runs(firefly_id);
```

**UI Presentation:**
- Compact "Run summary" on object detail page
- Expandable "Details" view for debugging
- Timeline of runs (especially useful after re-runs)

**Purpose:**
- **Trust:** User sees why something was categorized
- **Debugging:** Trace misclassifications
- **Improvement:** Data for rules/model tuning

### 6.10 Module Implementation Details

#### `OllamaClient`

```python
# spark_ai/ollama_client.py

@dataclass
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    timeout: int = 30
    max_retries: int = 2

class OllamaClient:
    """HTTP client for Ollama API."""
    
    def __init__(self, config: OllamaConfig):
        self.config = config
        self.session = requests.Session()
    
    def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        format: str = "json",
    ) -> str:
        """Call Ollama /api/generate endpoint."""
        response = self.session.post(
            f"{self.config.base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "system": system,
                "format": format,
                "stream": False,
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return response.json()["response"]
    
    def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            resp = self.session.get(f"{self.config.base_url}/api/tags", timeout=5)
            return resp.ok
        except Exception:
            return False
```

#### `LLMCategorizer`

```python
# spark_ai/categorizer.py

@dataclass
class CategorySuggestion:
    category_id: str
    confidence: float
    alternatives: list[dict]
    reasons: list[str]
    language_detected: Optional[str] = None

class LLMCategorizer:
    """Build prompts, parse responses, validate against category set."""
    
    PROMPT_VERSION = "cat-v1"
    
    def __init__(
        self,
        client: OllamaClient,
        allowed_categories: list[dict],
        model_fast: str = "qwen2.5:3b-instruct",
        model_fallback: str = "qwen2.5:7b-instruct",
    ):
        self.client = client
        self.allowed_categories = allowed_categories
        self.category_ids = {c["id"] for c in allowed_categories}
        self.model_fast = model_fast
        self.model_fallback = model_fallback
    
    def categorize(self, context: dict) -> CategorySuggestion:
        """Get category suggestion for transaction context."""
        prompt = self._build_prompt(context)
        
        # Try fast model first
        try:
            response = self.client.generate(self.model_fast, prompt)
            return self._parse_and_validate(response)
        except (ValidationError, json.JSONDecodeError):
            pass
        
        # Fallback to larger model
        response = self.client.generate(self.model_fallback, prompt)
        return self._parse_and_validate(response)
    
    def _build_prompt(self, context: dict) -> str:
        """Build categorization prompt."""
        categories_str = "\n".join(
            f"- {c['id']}: {c['name']}" for c in self.allowed_categories
        )
        return f"""Categorize this transaction. Respond with JSON only.

Transaction:
- Amount: {context['amount']} {context['currency']}
- Date: {context['date']}
- Description: {context.get('booking_text', 'N/A')}
- Merchant: {context.get('merchant', 'Unknown')}
- Keywords: {', '.join(context.get('keywords', []))}

Allowed categories:
{categories_str}

Respond with: {{"category_id": "...", "confidence": 0.0-1.0, "alternatives": [...], "reasons": [...]}}"""
    
    def _parse_and_validate(self, response: str) -> CategorySuggestion:
        """Parse JSON response and validate against category set."""
        # Strip code fences if present
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        response = response.strip("`").strip()
        
        data = json.loads(response)
        
        # Validate category_id
        if data["category_id"] not in self.category_ids:
            raise ValidationError(f"Invalid category: {data['category_id']}")
        
        # Validate confidence
        if not 0.0 <= data["confidence"] <= 1.0:
            raise ValidationError(f"Invalid confidence: {data['confidence']}")
        
        return CategorySuggestion(
            category_id=data["category_id"],
            confidence=data["confidence"],
            alternatives=data.get("alternatives", [])[:3],
            reasons=data.get("reasons", [])[:3],
            language_detected=data.get("language_detected"),
        )
```

### 6.11 Feasibility Assessment (Target Hardware)

**Target:** Acer Aspire V / Intel i5 / 16GB RAM (CPU-only inference)

| Constraint | Mitigation |
|------------|------------|
| No GPU | Use quantized models (Q4_K_M) |
| Limited RAM | 3B model uses ~3GB, 7B uses ~5GB |
| CPU inference speed | Background worker, 1 concurrent job |
| UI latency | Async processing, never block request path |

**Expected Performance:**
- 3B model: ~2-5 seconds per categorization
- 7B model: ~5-15 seconds per categorization
- With caching: repeated similar transactions instant

**Caching Strategy (see §9.6 for full rationale):**
```python
# Cache key includes taxonomy + prompt version to prevent stale suggestions
cache_key_inputs = {
    "amount_bucket": round(amount, 0),  # Rounded to nearest integer
    "currency": currency,
    "booking_text_hash": sha256(normalized_booking_text)[:8],
    "merchant_normalized": normalize(merchant),
    "keywords_hash": sha256(sorted(keywords))[:8],
    "taxonomy_version": taxonomy_version,  # Hash of category list from Firefly
    "prompt_version": "cat-v1",
    "model": model_name,
}
cache_key = sha256(json.dumps(cache_key_inputs, sort_keys=True))[:24]
```

**Cache Invalidation Triggers:**
- Category list changes in Firefly → taxonomy_version changes
- Prompt template update → prompt_version bump
- Model change → different model name
- TTL expiry (default 30 days)

---

## 7. Phased Roadmap

### Phase 1: Foundation (Weeks 1-2)
**Goal:** Enable Firefly introspection + category taxonomy

| Task | Files | Effort |
|------|-------|--------|
| Add `list_transactions()` to Firefly client | firefly_client/client.py | 1 day |
| Add `list_categories()` to Firefly client | firefly_client/client.py | 0.5 day |
| Add `get_unlinked_transactions()` (no Spark marker) | firefly_client/client.py | 0.5 day |
| Add `firefly_cache` table to state store | state_store/sqlite_store.py | 1 day |
| Create `FireflySyncService` to populate cache | NEW: services/firefly_sync.py | 2 days |
| Add `spark sync` CLI command | runner/main.py | 1 day |
| Tests for new methods | tests/test_firefly_sync.py | 1 day |

**Deliverable:** Can query Firefly transactions and categories, detect unlinked transactions.

### Phase 2: Matching Engine (Weeks 3-4)
**Goal:** Implement hash+fuzzy matching

| Task | Files | Effort |
|------|-------|--------|
| Create `MatchingEngine` class | NEW: matching/engine.py | 2 days |
| Implement hash-exact matching | matching/engine.py | 1 day |
| Implement fuzzy matching (amount/date tolerance) | matching/engine.py | 2 days |
| Add `match_proposals` table | state_store/sqlite_store.py | 0.5 day |
| Create scoring system for matches | NEW: matching/scorer.py | 1 day |
| Tests for matching | tests/test_matching.py | 1 day |

**Deliverable:** Can propose Receipt↔Transaction matches with confidence.

### Phase 3: Reconciliation UI (Weeks 5-6)
**Goal:** Review interface for matches

| Task | Files | Effort |
|------|-------|--------|
| Create reconciliation queue view | review/web/views.py | 1 day |
| Create reconciliation detail template | templates/review/reconcile.html | 2 days |
| Implement match confirmation flow | review/web/views.py | 1 day |
| Add reconciliation to landing page | templates/review/landing.html | 0.5 day |
| Wire up auto-match for high confidence | services/auto_matcher.py | 1 day |
| E2E tests | tests/test_reconciliation_flow.py | 1 day |

**Deliverable:** Users can review and confirm proposed matches via web UI.

### Phase 4A: Bank Reconciliation via Firefly Importer (Weeks 7-8)
**Goal:** Standard path - reconcile bank transactions imported via Firefly Data Importer

| Task | Files | Effort |
|------|-------|--------|
| Add `list_transactions()` to Firefly client | firefly_client/client.py | 1 day |
| Query unlinked transactions (no Spark marker) | firefly_client/client.py | 1 day |
| Create reconciliation matching logic | NEW: matching/reconciler.py | 2 days |
| Link receipt → existing bank transaction | state_store/sqlite_store.py | 1 day |
| Update existing Firefly transaction (add external_id + notes) | firefly_client/client.py | 1 day |
| Tests | tests/test_reconciliation.py | 1 day |

**Deliverable:** Can match Paperless receipts to bank-imported transactions in Firefly.

### Phase 4B: Direct Bank Parsing (Optional, Post-v1.0)
**Goal:** Optional path - parse bank CSV/MT940 directly (for users without Firefly Data Importer)

| Task | Files | Effort |
|------|-------|--------|
| Create `BankStatementExtractor` | NEW: extractors/bank_extractor.py | 3 days |
| Support MT940 format | extractors/bank_extractor.py | 1 day |
| Support generic CSV | extractors/bank_extractor.py | 1 day |
| Create bank import CLI command | runner/main.py | 1 day |
| Tests | tests/test_bank_extractor.py | 1 day |

**Note:** Phase 4B is **out of scope for Spark v1.0**. Users should use Firefly Data Importer for bank imports.

### Phase 5: Tax Enhancement (Week 9)
**Goal:** First-class tax handling

| Task | Files | Effort |
|------|-------|--------|
| Add `tax_relevant` boolean to schemas | schemas/finance_extraction.py | 0.5 day |
| Enhance UI with tax indicators | templates/review/detail.html | 1 day |
| Add tax filtering to archive | review/web/views.py | 0.5 day |
| Tax summary report view | NEW: views.py `tax_report()` | 2 days |

**Deliverable:** Tax-relevant transactions clearly marked and reportable.

### Phase 6: LLM Integration - Foundation (Weeks 10-11)
**Goal:** Add `spark_ai` module with Ollama integration

| Task | Files | Effort |
|------|-------|--------|
| Add LLM config flags to config.py | config.py | 0.5 day |
| Create `OllamaClient` HTTP transport | NEW: spark_ai/ollama_client.py | 1 day |
| Create `LLMCategorizer` with prompt builder | NEW: spark_ai/categorizer.py | 2 days |
| Implement JSON parsing + validation | spark_ai/categorizer.py | 1 day |
| Add redaction utilities | NEW: spark_ai/redaction.py | 0.5 day |
| Implement caching (hash-based) | NEW: spark_ai/cache.py | 1 day |
| Unit tests with mocked Ollama | tests/test_spark_ai.py | 1 day |

**Deliverable:** Standalone LLM categorization module, testable independently.

### Phase 7: LLM Integration - Pipeline (Weeks 12-13)
**Goal:** Integrate LLM into interpretation pipeline

| Task | Files | Effort |
|------|-------|--------|
| Add `llm_opt_out` column to extractions | state_store/sqlite_store.py | 0.5 day |
| Create `LLMOrchestrator` (fallback routing) | NEW: spark_ai/orchestrator.py | 1 day |
| Integrate with `ExtractorRouter` (post-rules) | extractors/router.py | 1 day |
| Add background worker for LLM jobs | runner/workers.py | 2 days |
| Global opt-out handling | config.py, runner/main.py | 0.5 day |
| Per-document opt-out UI toggle | review/web/views.py, templates | 1 day |
| Integration tests | tests/test_llm_integration.py | 1 day |

**Deliverable:** LLM suggestions appear in review UI for low-confidence extractions.

### Phase 8: Interpretation Audit Trail (Week 14)
**Goal:** Full audit trail for interpretation runs

| Task | Files | Effort |
|------|-------|--------|
| Create `interpretation_runs` table | state_store/sqlite_store.py | 1 day |
| Record runs in pipeline | extractors/router.py, spark_ai/ | 1 day |
| Add "Re-run interpretation" action | review/web/views.py | 0.5 day |
| Create audit trail UI (timeline view) | templates/review/audit.html | 1.5 days |
| Automatic re-run triggers | runner/workers.py | 1 day |

**Deliverable:** Every interpretation decision is traceable with full history.

### Phase 9: Rebrand & Polish (Weeks 15-16)
**Goal:** LedgerBridge → Spark transition

| Task | Files | Effort |
|------|-------|--------|
| Rename package `paperless_firefly` → `spark` | All | 1 day |
| Update README and documentation | README.md, docs/ | 1 day |
| Update CLI commands | runner/main.py | 0.5 day |
| Update Docker configuration | Dockerfile, docker-compose.yml | 0.5 day |
| Multilingual LLM golden tests | tests/fixtures/multilingual/ | 1 day |
| Final testing and QA | All | 2 days |

**Deliverable:** Spark v1.0 release with optional LLM assist.

---

## 8. Risk Assessment

### 8.1 Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Firefly API rate limits | Medium | High | Implement caching, batch requests |
| MT940/CAMT parsing complexity | Medium | Medium | Deferred to Phase 4B (post-v1.0) |
| Matching false positives | High | Medium | Require human confirmation for < 0.95 confidence |
| Database migration issues | Low | High | Implement versioned migrations, backup procedures |
| LLM hallucinations (invalid categories) | High | Low | Strict validation, fallback to manual review |
| LLM latency on CPU | Medium | Medium | Background workers, caching, concurrency=1 |
| Ollama server unavailable | Medium | Low | Graceful degradation, LLM marked as optional |
| **LLM "wrong greens"** | Medium | Medium | See §8.4 for detailed mitigations |

### 8.2 Scope Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Feature creep | High | Medium | Strict phase gates, MVP-first approach |
| Backwards compatibility breaks | Medium | High | Deprecate, don't delete; maintain migration scripts |
| LLM scope expansion ("let it do more") | Medium | Medium | Strict contract: category-only, JSON-only |

### 8.3 External Dependencies

| Dependency | Risk | Mitigation |
|------------|------|------------|
| Firefly III API stability | Low | Pin to API v1, test against multiple versions |
| Paperless-ngx API changes | Low | Same as above |
| Python package updates | Low | Pin versions in pyproject.toml |
| Ollama API stability | Low | Simple HTTP API, minimal surface area |
| LLM model availability | Low | Models are local, configurable, replaceable |

### 8.4 LLM Quality Risk: "Wrong Greens"

**Problem:** LLM may output high confidence (≥0.80) for **incorrect** category suggestions, leading to silent errors auto-applied without review.

**Mitigations (see §9.10 for full rationale):**

| Mitigation | Implementation |
|------------|----------------|
| Conservative initial threshold | First 100 LLM suggestions force YELLOW regardless of confidence |
| User feedback loop | "This was wrong" button → `llm_feedback` table |
| Sampling audit | Monthly review of 20 random green decisions |
| Conflict detection | LLM vs deterministic rule disagreement → force YELLOW |
| Threshold tuning | Start at 0.85; adjust based on false-positive rate |

**Acceptance Criteria:**
- False-positive rate < 5% after calibration period
- User can always override/correct any suggestion
- All LLM decisions are logged and auditable

---

## Appendix A: File Impact Matrix

| File | Impact Level | Phase |
|------|--------------|-------|
| `schemas/finance_extraction.py` | Low (add fields) | 5 |
| `schemas/firefly_payload.py` | None | - |
| `schemas/dedupe.py` | Low (new formats) | 4 |
| `firefly_client/client.py` | Medium (new methods + list_categories) | 1 |
| `paperless_client/client.py` | Low (add filters) | 3 |
| `extractors/base.py` | None | - |
| `extractors/router.py` | Medium (LLM integration point) | 4, 7 |
| `extractors/einvoice_extractor.py` | None | - |
| `extractors/ocr_extractor.py` | None | - |
| `state_store/sqlite_store.py` | High (new tables + LLM columns) | 1, 2, 7, 8 |
| `confidence/scorer.py` | Low (new strategies) | 2 |
| `review/workflow.py` | Medium (reconciliation flow) | 3 |
| `review/web/views.py` | High (new views + LLM UI) | 3, 7, 8 |
| `runner/main.py` | Medium (new commands) | 1, 4 |
| `runner/workers.py` | NEW (background jobs) | 7 |
| `config.py` | Medium (new sections + LLM) | 1, 6 |
| `spark_ai/` | NEW (entire module) | 6, 7 |
| `state_store/llm_feedback` | NEW (table) | 7 |

---

## Appendix B: Test Coverage Plan

| New Module | Test File | Priority | Notes |
|------------|-----------|----------|-------|
| Firefly sync service | `test_firefly_sync.py` | High | list_transactions, list_categories |
| Unlinked transaction detection | `test_unlinked_detection.py` | High | Query logic for Spark linkage markers |
| Matching engine | `test_matching.py` | High | Hash + fuzzy matching |
| Bank reconciliation | `test_reconciliation.py` | High | Phase 4A: match receipt → bank txn |
| Bank statement extractor | `test_bank_extractor.py` | Low | Phase 4B: out of scope for v1.0 |
| Tax reporting | `test_tax_reports.py` | Low | |
| Ollama client | `test_ollama_client.py` | High | HTTP transport, error handling |
| LLM categorizer | `test_categorizer.py` | High | Prompt building, JSON validation |
| LLM orchestrator | `test_orchestrator.py` | Medium | Fallback routing, caching |
| LLM feedback loop | `test_llm_feedback.py` | Medium | "Wrong green" tracking |
| Redaction utilities | `test_redaction.py` | Medium | IBAN/PII redaction |
| Interpretation audit trail | `test_interpretation_runs.py` | Medium | Audit fields, rescheduling |
| Multilingual golden tests | `test_multilingual_samples.py` | High | DE/EN/FR/TR/RU/ZH samples |
| Cache invalidation | `test_llm_cache.py` | Medium | Taxonomy version changes |

---

## Conclusion

LedgerBridge provides a solid foundation for Spark. The existing schema layer, extractor architecture, and confidence scoring system are directly reusable. The primary engineering effort centers on:

1. **Firefly introspection** (read existing transactions + categories)
2. **Matching engine** (hash + fuzzy matching)
3. **Reconciliation UI** (unified review form)
4. **Bank reconciliation** (via Firefly Data Importer, Phase 4A)
5. **Local LLM integration** (optional category assist via Ollama)
6. **Interpretation audit trail** (full traceability)

The **16-week roadmap** transforms LedgerBridge into Spark while maintaining backwards compatibility and enabling incremental deployment. The LLM integration (Phases 6-8) is designed as a fully optional assist layer that:

- Reduces manual categorization for multilingual receipts
- Preserves strict determinism and auditability
- Supports full opt-out (global + per-document)
- Runs on target hardware (CPU-only, i5/16GB) via background workers
- Never compromises ledger integrity (advisory only, human-reviewable)

**Spark v1.0 Scope Summary:**
- ✅ Firefly introspection (list transactions, detect unlinked)
- ✅ Receipt → Bank transaction reconciliation (Phase 4A)
- ✅ LLM-assisted categorization (optional)
- ✅ Full audit trail (InterpretationRun)
- ❌ Direct bank CSV/MT940 parsing (Phase 4B, post-v1.0)
- ❌ Multi-Firefly instance support (future external_id format)

---

## 9. Decisions & Rationale

This section documents reconciliation decisions made after verifying the report against the actual LedgerBridge codebase.

### 9.1 Metadata Correctness

**Decision:** Update report date to 2026-01-07, increment version to 1.1.

**Rationale:**
- Original date (2025-01-27) was the initial draft date
- Report now reflects reconciliation with live codebase
- Version increment signals substantive changes

**Migration Impact:** None

---

### 9.2 Cash Flow Model Clarification

**Decision:** Correct "Cash+Receipt Flow" status from "✅ works" to "⚠️ Partial".

**Rationale:**
- **Current implementation:** `ExtractorRouter._determine_transaction_type()` creates `WITHDRAWAL` transactions (Asset → Expense) for receipts
- **This is receipt-to-expense**, not the full cash model described in Spark concept
- **Missing:** The two-step cash model:
  1. `Bank → Cash` (funding transfer)
  2. `Cash → Merchant` (cash expense)
- Current code defaults source to `default_source_account` (typically "Checking Account"), not a Cash asset account

**Code Reference:** [extractors/router.py#L83-L95](src/paperless_firefly/extractors/router.py#L83-L95)

**Migration Impact:** Low - Existing behavior remains valid for card payments; cash handling is an extension.

---

### 9.3 Firefly Introspection & Linkage Semantics

**Decision:** Define "unlinked" as transactions **without Spark's external_id prefix or notes marker**.

**Rationale:**
- Current system uses `external_id` format: `paperless:{doc_id}:{hash[:16]}:{amount}:{date}`
- Also writes `internal_reference: PAPERLESS:{doc_id}` and notes with `Paperless doc_id=X`
- **"Unlinked" in Spark context means:** No `paperless:` prefix in external_id AND no `PAPERLESS:` in internal_reference
- This allows Spark to identify bank-imported transactions (from Firefly Data Importer) that lack receipt linkage
- Checking `external_id IS NULL OR external_id = ''` is **insufficient** because Firefly Importer may set its own external_ids

**Code References:**
- [schemas/dedupe.py#L45-L93](src/paperless_firefly/schemas/dedupe.py#L45-L93) - external_id generation
- [schemas/firefly_payload.py#L250-L255](src/paperless_firefly/schemas/firefly_payload.py#L250-L255) - internal_reference set
- [firefly_client/client.py#L307-L338](src/paperless_firefly/firefly_client/client.py#L307-L338) - find_by_external_id search

**Chosen Linkage Query Logic:**
```python
def get_unlinked_transactions(start_date, end_date):
    """Find transactions not linked to Spark."""
    # Transactions where:
    # - external_id does not start with 'paperless:' AND
    # - internal_reference does not contain 'PAPERLESS:' AND
    # - notes do not contain 'Paperless doc_id='
```

**Migration Impact:** Low - Read-only introspection, no changes to existing data.

---

### 9.4 External ID Format: Stability & Evolution

**Decision:** Keep current `paperless:{doc_id}:{hash[:16]}:{amount}:{date}` format for Spark v1.0. Plan versioned namespace for v2.

**Rationale:**
- **Current format analysis:**
  - Embeds Paperless `doc_id` → tied to Paperless instance (not portable)
  - Uses file hash prefix → stable across reimports if file unchanged
  - Includes amount/date → changes if user edits these fields (regenerated correctly)
  - No explicit version marker
- **Collision risk:** Low for single Paperless instance; problematic if merging multiple instances
- **Firefly ID in external_id:** **Rejected** - Firefly IDs are local to the instance

**Future Format (v2, not in scope):**
```
spark:v1:paperless:{doc_id}:{content_hash}:{amount}:{date}
spark:v1:bank:{import_hash}:{amount}:{date}
spark:v1:cash:{uuid}
```

**Version Bump Strategy:**
- New prefix `spark:v1:` signals format version
- Old `paperless:` prefixes remain valid (backwards compatible)
- Migration tool can upgrade old IDs if needed

**Code Reference:** [schemas/dedupe.py](src/paperless_firefly/schemas/dedupe.py)

**Migration Impact:** None for v1.0 - existing format preserved.

---

### 9.5 Bank Import Scope for v1.0

**Decision:** Split Phase 4 into:
- **Phase 4A (In Scope):** Bank reconciliation via Firefly Data Importer
- **Phase 4B (Out of Scope):** Direct bank CSV/MT940 parsing

**Rationale:**
- User preference: Firefly Data Importer is the standard bank import path
- Direct parsing adds significant complexity (MT940/CAMT formats vary by bank)
- Spark v1.0 focus: Receipt→Bank matching, not bank parsing
- Phase 4B can be added post-v1.0 for users without Firefly Importer access

**What's In Scope for v1.0:**
1. Query Firefly for unlinked transactions (bank-imported)
2. Match Paperless receipts to those transactions
3. Update Firefly transaction with receipt link (external_id, notes, attachment)

**What's Out of Scope for v1.0:**
1. Parsing MT940/CAMT files directly
2. Creating Firefly transactions from bank files
3. Handling SEPA fields directly

**Migration Impact:** None - scope clarification only.

---

### 9.6 LLM Cache Key Composition

**Decision:** Cache key must include taxonomy version and prompt version to prevent stale suggestions.

**Final Cache Key Inputs:**
```python
cache_key_inputs = {
    "amount_bucket": round(amount, 0),  # Rounded to nearest integer
    "currency": currency,
    "booking_text_hash": sha256(normalized_booking_text)[:8],
    "merchant_normalized": normalize(merchant),
    "keywords_hash": sha256(sorted(keywords))[:8],
    "taxonomy_version": taxonomy_version,  # Hash of category list
    "prompt_version": "cat-v1",
    "model": model_name,
}
cache_key = sha256(json.dumps(cache_key_inputs, sort_keys=True))[:24]
```

**Cache Invalidation Triggers:**
1. Category list changes → `taxonomy_version` changes → cache miss
2. Prompt template changes → `prompt_version` bump → cache miss
3. Model change → different model name → cache miss
4. TTL expiry (default 30 days)

**Rationale:**
- Amount bucketing prevents cache fragmentation (35.70 vs 35.71)
- Taxonomy version ensures stale categories aren't suggested
- Short hashes (8 chars) are sufficient for collision resistance in this context

**Migration Impact:** Low - New feature, no existing cache to migrate.

---

### 9.7 Category Taxonomy Source & Versioning

**Decision:** Categories fetched from Firefly III at runtime. Taxonomy version is SHA256 hash of sorted category IDs.

**Rationale:**
- Firefly is the SSOT for categories (user-defined)
- No duplicate category definitions in Spark
- Automatic taxonomy version: `sha256(json.dumps(sorted(category_ids)))[:12]`
- Stored in `InterpretationRun.taxonomy_version` for audit

**Implementation:**
```python
# firefly_client/client.py - NEW METHOD
def list_categories(self) -> list[dict]:
    """List all categories from Firefly."""
    # GET /api/v1/categories
    ...

def get_taxonomy_version(categories: list[dict]) -> str:
    """Compute taxonomy version hash."""
    ids = sorted(c["id"] for c in categories)
    return hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:12]
```

**Rescheduling Trigger:**
- If `current_taxonomy_version != stored_taxonomy_version` → queue reinterpretation

**Code Reference:** Firefly client to be extended; currently only `list_accounts()` exists.

**Migration Impact:** Low - New method, no breaking changes.

---

### 9.8 InterpretationRun Audit Fields

**Decision:** Add operational clarity fields to `interpretation_runs` table.

**Fields Added:**

| Field | Type | Purpose |
|-------|------|---------|
| `decision_source` | TEXT | `RULES` / `LLM` / `HYBRID` / `USER` |
| `firefly_write_action` | TEXT | `NONE` / `CREATE_NEW` / `UPDATE_EXISTING` |
| `firefly_target_id` | INTEGER | Firefly transaction ID (if UPDATE_EXISTING) |
| `linkage_marker_written` | TEXT | JSON: `{"external_id": "...", "notes_appended": true}` |
| `taxonomy_version` | TEXT | Category taxonomy hash at run time |

**Fields Omitted (keep lean):**
- Full prompt text (use `prompt_version` reference instead)
- Raw OCR dump (stored in FinanceExtraction already)
- Full Firefly response (only store success/failure + ID)

**Rationale:**
- `decision_source` enables debugging "why did it choose this?"
- `firefly_write_action` clarifies whether transaction was created vs linked
- `linkage_marker_written` provides audit trail for what was written to Firefly
- Keeping it lean avoids bloating the audit table

**Migration Impact:** Medium - New table columns, but no migration of existing data needed.

---

### 9.9 UI Unification Plan

**Decision:** Temporary dual modes (Receipt Review + Reconciliation), unified in Phase 5.

**Current UI Structure:**
- `review/list.html` → Receipt extraction queue
- `review/detail.html` → Single receipt review form
- `review/archive.html` → Processed items

**Interim Structure (Phases 3-4):**
- Keep existing receipt review
- Add `review/reconcile_list.html` → Bank transactions awaiting receipts
- Add `review/reconcile_detail.html` → Match receipt to bank transaction

**Unification Milestone:** Phase 5 (Tax Enhancement) or earlier if feasible.

**Unified Form Requirements:**
1. Single entry point regardless of origin (Paperless-first or Firefly-first)
2. Show context from both sources when available
3. Support all flows: receipt-only, bank-only (mark as "no receipt"), matched pair
4. Tax relevance toggle visible on all modes

**Acceptance Criteria for Unification:**
- [ ] User can start from either receipt or bank transaction
- [ ] Matching UI shows both contexts side-by-side
- [ ] Single "Approve" action handles creation, update, or link
- [ ] Archive shows unified history

**Migration Impact:** Low - UI changes, no data migration.

---

### 9.10 Risk Mitigation: "Wrong Greens"

**Decision:** Implement conservative threshold + feedback loop for LLM high-confidence errors.

**Problem:** LLM may output high confidence (≥0.80) for **wrong** category suggestions, leading to silent errors in green state.

**Mitigations:**

1. **Conservative Initial Threshold:**
   - LLM green threshold: 0.85 (same as deterministic rules)
   - First 100 LLM suggestions: force YELLOW (review recommended) regardless of confidence
   - After calibration period: allow green

2. **User Feedback Loop:**
   - UI button: "This suggestion was wrong"
   - Records: `llm_feedback` table with `{run_id, suggested, actual, timestamp}`
   - Periodic review of feedback to tune prompts/thresholds

3. **Sampling Audit:**
   - Monthly: Random sample of 20 green decisions
   - Manual review to estimate false-positive rate
   - If rate > 5%: tighten threshold or improve prompts

4. **Conflict Detection:**
   - If LLM and deterministic rules disagree → force YELLOW
   - Log conflict for analysis

**Where Mitigations Live:**

| Mitigation | Location |
|------------|----------|
| Threshold config | `config.py` |
| Calibration counter | `state_store` (counter table) |
| Feedback recording | `state_store.llm_feedback` table |
| Feedback UI | `review/detail.html` button |
| Conflict detection | `spark_ai/orchestrator.py` |

**Migration Impact:** Low - New tables and UI elements.

---

### 9.11 Summary Table

| Item | Decision | Migration Impact |
|------|----------|------------------|
| 1.1 Report date | Update to 2026-01-07, version 1.1 | None |
| 2.1 Cash flow wording | Correct to "⚠️ Partial" | None |
| 3.1 Unlinked definition | No `paperless:` prefix + no `PAPERLESS:` reference | Low |
| 4.1 external_id format | Keep current for v1.0, plan `spark:v1:` for v2 | None |
| 5.1 Bank import scope | Phase 4A in-scope (reconciliation), 4B out-of-scope (parsing) | None |
| 6.1 LLM cache key | Include taxonomy_version + prompt_version | Low |
| 7.1 Category source | Firefly is SSOT, taxonomy_version = hash of IDs | Low |
| 8.1 Audit fields | Add decision_source, firefly_write_action, etc. | Medium |
| 9.1 UI unification | Dual modes interim, unify by Phase 5 | Low |
| 10.1 Wrong greens | Conservative threshold + feedback loop | Low |

---

*Report generated from LedgerBridge codebase analysis.*  
*Target: Spark Architecture Concept v1.0*  
*Reconciled: 2026-01-07*
