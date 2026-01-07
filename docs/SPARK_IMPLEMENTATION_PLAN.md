# Spark v1.0 Implementation Plan

**Version:** 1.0  
**Date:** 2026-01-07  
**Based on:** SPARK_EVALUATION_REPORT.md v1.1

---

## 1. Implementation Order

Following the evaluation report's phased roadmap, but executing with strict proof loops.

### Phase 0: Tooling Setup (Day 1)
**Goal:** Ensure code quality tooling is in place and passing.

| Task | Action |
|------|--------|
| Add ruff linter | Update pyproject.toml |
| Configure isort | Add isort config to pyproject.toml |
| Run black | Format all Python files |
| Run mypy | Fix type errors (existing) |
| Baseline tests | Ensure all existing tests pass |

### Phase 1: Firefly Introspection (Days 2-4)
**Goal:** Enable reading from Firefly III (transactions, categories).

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| Add `list_transactions()` | firefly_client/client.py | Paginated, date-filtered |
| Add `list_categories()` | firefly_client/client.py | For LLM category validation |
| Add `get_unlinked_transactions()` | firefly_client/client.py | Filter by linkage markers |
| `FireflyTransaction` dataclass expansion | firefly_client/client.py | Add fields: internal_reference, notes |
| Migration: `firefly_cache` table | state_store/migrations/001_firefly_cache.py | Cache Firefly state |
| `FireflySyncService` | NEW: services/firefly_sync.py | Populate cache |
| Config: `reconciliation` section | config.py | date_tolerance_days, thresholds |
| Tests | tests/test_firefly_sync.py | Mocked HTTP |

### Phase 2: Matching Engine (Days 5-7)
**Goal:** Implement hash + fuzzy matching between receipts and transactions.

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| `MatchingEngine` class | NEW: matching/engine.py | Core matching logic |
| Hash-exact matching | matching/engine.py | external_id prefix check |
| Fuzzy matching (amount/date) | matching/engine.py | Configurable tolerance |
| `MatchScorer` | NEW: matching/scorer.py | Weighted scoring |
| Migration: `match_proposals` table | state_store/migrations/002_match_proposals.py | Store proposals |
| Tests | tests/test_matching.py | Various match scenarios |

### Phase 3: Reconciliation UI (Days 8-10)
**Goal:** Web interface for reviewing matches.

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| `reconcile_list` view | review/web/views.py | Queue of unmatched |
| `reconcile_detail` view | review/web/views.py | Match interface |
| `confirm_match` action | review/web/views.py | Finalize match |
| Template: `reconcile_list.html` | templates/review/ | List template |
| Template: `reconcile_detail.html` | templates/review/ | Detail template |
| Auto-match service | NEW: services/auto_matcher.py | High confidence auto |
| Tests | tests/test_reconciliation_flow.py | E2E flow |

### Phase 4A: Bank Reconciliation (Days 11-12)
**Goal:** Update existing Firefly transactions with linkage markers.

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| `update_transaction_linkage()` | firefly_client/client.py | Add markers to existing |
| Linkage marker constants | schemas/linkage.py | SSOT for marker formats |
| Reconciler service | NEW: matching/reconciler.py | Orchestrate reconciliation |
| Tests | tests/test_reconciliation.py | Marker verification |

### Phase 6-7: LLM Integration (Days 13-18)
**Goal:** Optional local LLM assist via Ollama.

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| `OllamaClient` | NEW: spark_ai/ollama_client.py | HTTP transport |
| `LLMCategorizer` | NEW: spark_ai/categorizer.py | Prompt + validation |
| `LLMOrchestrator` | NEW: spark_ai/orchestrator.py | Fallback routing |
| Redaction utilities | NEW: spark_ai/redaction.py | PII protection |
| LLM cache | NEW: spark_ai/cache.py | Hash-based caching |
| Config: `llm` section | config.py | Model names, opt-out |
| Migration: `llm_opt_out` column | state_store/migrations/003_llm_columns.py | Per-doc opt-out |
| Background worker | NEW: runner/workers.py | Async LLM jobs |
| UI: LLM toggle | templates/review/detail.html | Opt-out checkbox |
| UI: AI suggestion badge | templates/review/detail.html | Show LLM result |
| Tests | tests/test_spark_ai.py | Mocked Ollama |

### Phase 8: Interpretation Audit Trail (Days 19-20)
**Goal:** Full audit trail for every interpretation run.

| Task | New/Modified File | Notes |
|------|-------------------|-------|
| Migration: `interpretation_runs` table | state_store/migrations/004_interpretation_runs.py | Audit records |
| Migration: `llm_feedback` table | state_store/migrations/005_llm_feedback.py | Wrong-green tracking |
| `InterpretationRun` dataclass | schemas/interpretation.py | Run metadata |
| Record runs in pipeline | extractors/router.py | Log every run |
| Re-run interpretation action | review/web/views.py | Reschedule button |
| Audit UI (timeline view) | templates/review/audit.html | Show run history |
| Tests | tests/test_interpretation_runs.py | Audit verification |

---

## 2. New File Structure

```
src/paperless_firefly/
├── config.py                    # MODIFIED: Add llm, reconciliation sections
├── matching/                    # NEW MODULE
│   ├── __init__.py
│   ├── engine.py               # MatchingEngine class
│   ├── scorer.py               # Match scoring
│   └── reconciler.py           # Reconciliation orchestration
├── services/                    # NEW MODULE
│   ├── __init__.py
│   ├── firefly_sync.py         # Sync Firefly state
│   └── auto_matcher.py         # Auto-confirm high confidence
├── spark_ai/                    # NEW MODULE
│   ├── __init__.py
│   ├── ollama_client.py        # Ollama HTTP client
│   ├── categorizer.py          # LLM categorization
│   ├── orchestrator.py         # Fallback routing
│   ├── redaction.py            # PII redaction
│   ├── cache.py                # LLM response caching
│   └── schemas.py              # LLM request/response types
├── schemas/
│   ├── linkage.py              # NEW: Linkage marker SSOT
│   └── interpretation.py       # NEW: InterpretationRun
├── state_store/
│   ├── migrations/             # NEW: Versioned migrations
│   │   ├── __init__.py
│   │   ├── 001_firefly_cache.py
│   │   ├── 002_match_proposals.py
│   │   ├── 003_llm_columns.py
│   │   ├── 004_interpretation_runs.py
│   │   └── 005_llm_feedback.py
│   └── sqlite_store.py         # MODIFIED: Add migration runner
├── runner/
│   ├── main.py                 # MODIFIED: Add spark sync command
│   └── workers.py              # NEW: Background job workers
└── review/web/
    ├── views.py                # MODIFIED: Add reconciliation views
    └── templates/review/
        ├── reconcile_list.html # NEW
        ├── reconcile_detail.html # NEW
        └── audit.html          # NEW

tests/
├── test_firefly_sync.py        # NEW
├── test_matching.py            # NEW
├── test_reconciliation.py      # NEW
├── test_reconciliation_flow.py # NEW
├── test_spark_ai.py            # NEW
├── test_interpretation_runs.py # NEW
├── test_llm_cache.py           # NEW
├── test_llm_feedback.py        # NEW
└── fixtures/
    └── multilingual_samples/   # NEW: DE/EN/FR/TR samples
```

---

## 3. Database Migrations

### Migration 001: firefly_cache
```sql
CREATE TABLE firefly_cache (
    firefly_id INTEGER PRIMARY KEY,
    external_id TEXT,
    internal_reference TEXT,
    type TEXT NOT NULL,
    date TEXT NOT NULL,
    amount TEXT NOT NULL,
    description TEXT,
    source_account TEXT,
    destination_account TEXT,
    notes TEXT,
    synced_at TEXT NOT NULL,
    match_status TEXT DEFAULT 'UNMATCHED',
    matched_document_id INTEGER,
    match_confidence REAL,
    FOREIGN KEY (matched_document_id) REFERENCES paperless_documents(document_id)
);
CREATE INDEX idx_firefly_cache_match ON firefly_cache(match_status);
CREATE INDEX idx_firefly_cache_date ON firefly_cache(date);
```

### Migration 002: match_proposals
```sql
CREATE TABLE match_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firefly_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    match_score REAL NOT NULL,
    match_reasons TEXT,
    status TEXT DEFAULT 'PENDING',
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    FOREIGN KEY (firefly_id) REFERENCES firefly_cache(firefly_id),
    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
);
CREATE INDEX idx_match_proposals_status ON match_proposals(status);
```

### Migration 003: llm_columns
```sql
ALTER TABLE extractions ADD COLUMN llm_opt_out BOOLEAN DEFAULT FALSE;
```

### Migration 004: interpretation_runs
```sql
CREATE TABLE interpretation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER,
    firefly_id INTEGER,
    external_id TEXT,
    run_timestamp TEXT NOT NULL,
    duration_ms INTEGER,
    pipeline_version TEXT NOT NULL,
    algorithm_version TEXT,
    inputs_summary TEXT NOT NULL,
    rules_applied TEXT,
    llm_result TEXT,
    final_state TEXT NOT NULL,
    suggested_category TEXT,
    suggested_splits TEXT,
    auto_applied BOOLEAN DEFAULT FALSE,
    decision_source TEXT,
    firefly_write_action TEXT,
    firefly_target_id INTEGER,
    linkage_marker_written TEXT,
    taxonomy_version TEXT,
    FOREIGN KEY (document_id) REFERENCES paperless_documents(document_id)
);
CREATE INDEX idx_interpretation_runs_document ON interpretation_runs(document_id);
CREATE INDEX idx_interpretation_runs_firefly ON interpretation_runs(firefly_id);
```

### Migration 005: llm_feedback
```sql
CREATE TABLE llm_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    suggested_category TEXT NOT NULL,
    actual_category TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES interpretation_runs(id)
);
CREATE INDEX idx_llm_feedback_run ON llm_feedback(run_id);
```

---

## 4. Acceptance Criteria

### Phase 1
- [ ] `list_transactions()` returns paginated Firefly transactions
- [ ] `list_categories()` returns all Firefly categories
- [ ] `get_unlinked_transactions()` correctly identifies transactions without Spark markers
- [ ] `firefly_cache` table populated via sync
- [ ] Tests pass with mocked HTTP

### Phase 2
- [ ] Exact hash match (same external_id) detected
- [ ] Fuzzy match (amount ±0.01, date ±7 days) scored
- [ ] Match proposals stored with reasons
- [ ] Tests cover edge cases (no match, multiple candidates)

### Phase 3
- [ ] Reconciliation queue shows unmatched transactions
- [ ] Detail view shows candidate receipts
- [ ] Confirm match updates Firefly transaction
- [ ] Auto-match runs for confidence ≥ 0.90

### Phase 4A
- [ ] Firefly transaction updated with external_id
- [ ] internal_reference set to `PAPERLESS:{doc_id}`
- [ ] notes appended with `Paperless doc_id=X`
- [ ] Tests verify all three linkage markers

### Phase 6-7
- [ ] OllamaClient connects to local Ollama
- [ ] LLM returns valid JSON with category_id
- [ ] Invalid category rejected, falls back to review
- [ ] Global opt-out disables all LLM calls
- [ ] Per-document opt-out respected
- [ ] LLM runs async (not in request path)
- [ ] Cache hit returns instantly
- [ ] Cache invalidated on taxonomy change

### Phase 8
- [ ] InterpretationRun created for every extraction
- [ ] Re-run interpretation creates new run
- [ ] Audit UI shows run timeline
- [ ] "Wrong green" feedback recorded

---

## 5. Non-Functional Requirements

- **black** formatted, **isort** sorted
- **ruff** linting clean
- **mypy** type checks pass
- All tests pass: `pytest tests/ -v`
- Migrations versioned and reversible (or documented no-downgrade)
- No secrets in logs
- LLM never creates transactions autonomously

---

## 6. Commands Reference

```bash
# Run formatter
black src/ tests/

# Run import sorter
isort src/ tests/

# Run linter
ruff check src/ tests/

# Run type checker
mypy src/

# Run tests
pytest tests/ -v --tb=short

# Run all checks
black src/ tests/ && isort src/ tests/ && ruff check src/ tests/ && mypy src/ && pytest tests/ -v
```

---

*Implementation plan generated from SPARK_EVALUATION_REPORT.md v1.1*
