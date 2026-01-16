# Firefly Sync Assistant: "Everything" Expansion Roadmap

**Version:** 1.1  
**Date:** January 16, 2026  
**Status:** ✅ Implementation Complete

---

## Implementation Status

### ✅ Completed Components

| Component | Status | Details |
|-----------|--------|---------|
| **Model Extension** | ✅ Complete | Added 7 new entity types to `SyncPoolRecord.ENTITY_TYPE_CHOICES` |
| **Fingerprint Functions** | ✅ Complete | Added fingerprint computation for all new entity types |
| **FireflyClient Extension** | ✅ Complete | Added ~500 lines of new list/create/find methods |
| **Reference Mapper** | ✅ Complete | New `ReferenceMapper` service for entity name → ID resolution |
| **api_sync_fetch** | ✅ Complete | Extended to fetch all 11 entity types with transaction filters |
| **api_sync_pool** | ✅ Complete | Extended validation for all entity types |
| **api_sync_import** | ✅ Complete | Extended to import all entity types with proper dependencies |
| **api_sync_preview** | ✅ Complete | Extended to preview all entity types |
| **UI Template** | ✅ Complete | Added grouped entity cards with transaction date filters |
| **Tests** | ✅ Complete | Added 11 new tests for fingerprints and entity names (38 total sync tests) |
| **Database Migration** | ✅ Complete | Migration 0006 created for extended entity types |

### Entity Types Now Supported

| Type | Layer | Notes |
|------|-------|-------|
| `category` | 0 | Already implemented |
| `tag` | 0 | Already implemented |
| `budget` | 0 | ✅ NEW - Full CRUD |
| `currency` | 0 | ✅ NEW - Enable only (no create) |
| `account` | 1 | Already implemented |
| `piggy_bank` | 1 | Already implemented |
| `bill` | 1 | ✅ NEW - Full CRUD |
| `rule_group` | 0 | ✅ NEW - Full CRUD |
| `rule` | 2 | ✅ NEW - Full CRUD with rule_group resolution |
| `recurrence` | 2 | ✅ NEW - Full CRUD with account resolution |
| `transaction` | 3 | ✅ NEW - Full CRUD with external_id deduplication |

### Files Modified

| File | Changes |
|------|---------|
| `src/paperless_firefly/review/web/models.py` | Extended `ENTITY_TYPE_CHOICES`, added `IMPORT_LAYERS` |
| `src/paperless_firefly/services/sync_fingerprints.py` | Added 7 new fingerprint functions |
| `src/paperless_firefly/firefly_client/client.py` | Added ~500 lines of new methods |
| `src/paperless_firefly/services/reference_mapper.py` | ✅ NEW - Reference resolution service |
| `src/paperless_firefly/review/web/views.py` | Extended all sync API endpoints |
| `src/paperless_firefly/review/web/templates/review/sync_assistant.html` | Grouped entity cards, transaction filters |
| `tests/test_sync_assistant.py` | Added 11 new tests for new entity types |

---

## 1. Context Snapshot (What Exists Today)

### 1.1 Current Sync Assistant Implementation

The Firefly Sync Assistant is a feature enabling users to fetch entities from their Firefly III instance into a local pool, share those entities with other Sparklink users, and import shared entities into their own Firefly instance.

#### Currently Supported Entity Types
- **Categories** (`category`) - Firefly expense/income categories
- **Tags** (`tag`) - Firefly transaction tags  
- **Accounts** (`account`) - All account types (asset, expense, revenue, liability, cash)
- **Piggy Banks** (`piggy_bank`) - Savings goals linked to asset accounts

#### Data Model (Tables)

**`SyncPoolRecord`** - Pool records fetched from users' Firefly instances:
- `id`, `owner` (FK → User), `entity_type`, `firefly_id`
- `fingerprint` (SHA256 for cross-instance deduplication)
- `data_json` (full entity data), `name` (denormalized)
- `fetched_at`, `updated_at`
- Unique constraint: `(owner, entity_type, fingerprint)`

**`SyncPoolShare`** - Share permissions:
- `id`, `record` (FK → SyncPoolRecord), `shared_with` (FK → User)
- `shared_at`, `shared_by` (FK → User)
- Unique constraint: `(record, shared_with)`

**`SyncImportLog`** - Audit log of import operations:
- `id`, `user` (FK), `pool_record` (FK, nullable)
- `entity_type`, `fingerprint`, `name` (preserved even if record deleted)
- `status` (created/skipped/error), `target_firefly_id`, `error_message`
- `imported_at`, `source_owner` (FK)

#### Files Inspected

| File | Purpose |
|------|---------|
| [src/paperless_firefly/review/web/models.py](../src/paperless_firefly/review/web/models.py) | Django ORM models including SyncPoolRecord, SyncPoolShare, SyncImportLog |
| [src/paperless_firefly/review/web/views.py](../src/paperless_firefly/review/web/views.py) | View functions: sync_assistant, api_sync_* (lines 6975-7600) |
| [src/paperless_firefly/review/web/urls.py](../src/paperless_firefly/review/web/urls.py) | URL routing for /sync-assistant/ and /api/sync/* |
| [src/paperless_firefly/firefly_client/client.py](../src/paperless_firefly/firefly_client/client.py) | FireflyClient with list/create methods for categories, tags, accounts, piggy banks |
| [src/paperless_firefly/services/sync_fingerprints.py](../src/paperless_firefly/services/sync_fingerprints.py) | Fingerprint computation (SHA256-based) for deduplication |
| [src/paperless_firefly/review/web/templates/review/sync_assistant.html](../src/paperless_firefly/review/web/templates/review/sync_assistant.html) | UI template with card-based layout (1056 lines) |

#### Current API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/sync-assistant/` | GET | Main page view |
| `/api/sync/fetch/<entity_type>/` | POST | Fetch from Firefly → Pool |
| `/api/sync/pool/<entity_type>/` | GET | List owned + shared records |
| `/api/sync/share/` | POST | Share records with users |
| `/api/sync/share/<share_id>/` | DELETE | Revoke share |
| `/api/sync/import/` | POST | Import records → user's Firefly |
| `/api/sync/eligible-users/` | GET | List shareable users |
| `/api/sync/preview/<entity_type>/` | GET | Preview import status |

#### Authentication & Authorization Pattern
- All endpoints decorated with `@login_required`
- Ownership verification: `SyncPoolRecord.objects.filter(owner=request.user)`
- Access check: owned records OR records in SyncPoolShare where `shared_with=request.user`
- No unauthenticated access to any sync endpoint

### 1.2 Firefly Client Capabilities

The existing `FireflyClient` class provides:

**Existing Methods:**
- `list_categories()` → `list[FireflyCategory]`
- `list_tags()` → `list[dict]`
- `list_accounts(account_type)` → `list[dict]`
- `list_piggy_banks()` → `list[dict]`
- `create_category(name, notes)` → `int`
- `create_tag(tag, description)` → `int`
- `find_or_create_account(name, type, currency)` → `int`
- `create_piggy_bank(name, account_id, target_amount, notes)` → `int`
- `list_transactions(start_date, end_date, type_filter, limit)` → `list[FireflyTransaction]`
- `create_transaction(payload)` → `int|None`
- `find_by_external_id(external_id)` → `FireflyTransaction|None`
- `set_external_id(transaction_id, external_id)` → `bool`

---

## 2. Goal: "Everything"

### 2.1 Definition

"Everything" means supporting **all feasible Firefly III entities** that can be:
1. Retrieved via API
2. Created via API (for import)
3. Meaningfully shared between users

### 2.2 Firefly III Entity Inventory

Based on Firefly III API v6.4.14:

| Entity | API Support | Include | Rationale |
|--------|-------------|---------|-----------|
| **Categories** | Full CRUD | ✅ Already implemented | Core entity |
| **Tags** | Full CRUD | ✅ Already implemented | Core entity |
| **Accounts** | Full CRUD | ✅ Already implemented | Core entity |
| **Piggy Banks** | Full CRUD | ✅ Already implemented | Core entity |
| **Budgets** | Full CRUD | ✅ **NEW** | Important for financial planning |
| **Bills** | Full CRUD | ✅ **NEW** | Recurring expense tracking |
| **Rules** | Full CRUD | ✅ **NEW** | Automation, very shareable |
| **Rule Groups** | Full CRUD | ✅ **NEW** | Container for rules |
| **Recurrences** | Full CRUD | ✅ **NEW** | Recurring transactions |
| **Currencies** | Read + Enable | ✅ **NEW** | Currency support |
| **Object Groups** | Read + Update | ⚠️ Partial | Auto-created, limited utility |
| **Transactions** | Full CRUD | ✅ **NEW** | Core financial data |
| **Attachments** | Full CRUD | ❌ Skip | Binary data, complex handling |
| **Webhooks** | Full CRUD | ❌ Skip | Instance-specific configuration |
| **Users** | Admin only | ❌ Skip | Requires owner role |
| **Preferences** | Per-user | ❌ Skip | Personal settings, not shareable |
| **Link Types** | Full CRUD | ❌ Skip | System-level, rarely customized |

### 2.3 Transaction Sync Definition

"Transaction sync" means:

1. **Fetch**: Retrieve transactions from user's Firefly (with date range, type filters)
2. **Pool**: Store transactions with fingerprint for cross-instance deduplication
3. **Share**: Allow sharing selected transactions with other users
4. **Import**: Create transactions in target user's Firefly, with:
   - Deterministic deduplication via `external_id`
   - Reference mapping (account names → target Firefly IDs)
   - Split transaction support
   - Transfer handling (requires both source + destination accounts)

### 2.4 New Entity Types to Implement

| Entity Type | Key Fields for Fingerprint | Import Dependencies |
|-------------|---------------------------|---------------------|
| `budget` | name | None |
| `bill` | name, amount_min, amount_max, date | None |
| `rule` | title, trigger, actions | rule_group, categories, tags, accounts |
| `rule_group` | title | None |
| `recurrence` | title, first_date, repeat_freq | accounts, categories, tags, budgets |
| `currency` | code | None (enable only) |
| `transaction` | date, amount, description, source, destination | accounts, categories, tags |

---

## 3. Firefly Capability Evaluation: API vs CLI vs Hybrid

### 3.1 API Coverage Assessment

| Endpoint | Method | List | Create | Read | Update | Delete |
|----------|--------|------|--------|------|--------|--------|
| `/v1/budgets` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/v1/bills` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/v1/rules` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/v1/rule-groups` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/v1/recurrences` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/v1/currencies` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `/v1/transactions` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### 3.2 CLI Evaluation

Firefly III does not provide a dedicated CLI tool for data management. The `php artisan` commands are for server administration only (migrations, cache, etc.), not for entity CRUD operations.

**Conclusion: CLI is not applicable for this use case.**

### 3.3 Rate Limits and Pagination

- **Pagination**: All list endpoints support `?page=N` parameter
- **Rate Limits**: None enforced by default Firefly III installation
- **Bulk Operations**: Not natively supported; must iterate with individual requests
- **Search**: `/v1/search/transactions` supports query-based filtering

### 3.4 Final Decision

| Entity Class | Method | Justification |
|--------------|--------|---------------|
| All entities | **API Only** | Full CRUD support, no CLI alternative, reliable pagination |

---

## 4. Entity Inventory + Dependency Graph

### 4.1 Entity Dependency Matrix

```
                    DEPENDS ON →
                ┌─────────────────────────────────────────────────────────┐
                │ currency  category  tag  account  budget  rule_group   │
    ┌───────────┼─────────────────────────────────────────────────────────┤
    │ category  │                                                        │
    │ tag       │                                                        │
    │ account   │    ✓                                                   │
    │ budget    │                                                        │
    │ piggy_bank│             ✓                 ✓                        │
    │ bill      │                               ✓                        │
    │ rule_group│                                                        │
    │ rule      │             ✓        ✓        ✓                  ✓     │
    │ recurrence│    ✓        ✓        ✓        ✓        ✓               │
    │transaction│    ✓        ✓        ✓        ✓        ✓               │
    └───────────┴─────────────────────────────────────────────────────────┘
```

### 4.2 Import Order (Topological Sort)

To ensure dependencies exist before dependents:

1. **Layer 0** (No dependencies):
   - `currency`
   - `category`
   - `tag`
   - `budget`
   - `rule_group`

2. **Layer 1** (Depends on Layer 0):
   - `account` → needs currency
   - `bill` → needs account (optional)

3. **Layer 2** (Depends on Layer 0-1):
   - `piggy_bank` → needs account
   - `rule` → needs rule_group, category, tag, account
   - `recurrence` → needs account, category, tag, budget

4. **Layer 3** (Depends on all above):
   - `transaction` → needs account, category, tag (budget optional)

### 4.3 Full Entity Table

| Entity | Type Key | Import Layer | Fingerprint Components | Notes |
|--------|----------|--------------|------------------------|-------|
| Currency | `currency` | 0 | code | Enable only, don't create |
| Category | `category` | 0 | name | Already implemented |
| Tag | `tag` | 0 | tag (name) | Already implemented |
| Budget | `budget` | 0 | name | New |
| Rule Group | `rule_group` | 0 | title | New |
| Account | `account` | 1 | type, name, currency_code | Already implemented |
| Bill | `bill` | 1 | name, amount_min, amount_max | New |
| Piggy Bank | `piggy_bank` | 2 | name, target_amount | Already implemented |
| Rule | `rule` | 2 | title, triggers_hash, actions_hash | New, complex |
| Recurrence | `recurrence` | 2 | title, first_date, repeat_freq | New |
| Transaction | `transaction` | 3 | date, amount, source, destination, description | New, complex |

---

## 5. Data Model & Idempotency Strategy

### 5.1 Extended SyncPoolRecord Entity Types

Add to `ENTITY_TYPE_CHOICES`:

```python
ENTITY_TYPE_CHOICES = [
    # Existing
    (ENTITY_CATEGORY, "Category"),
    (ENTITY_TAG, "Tag"),
    (ENTITY_ACCOUNT, "Account"),
    (ENTITY_PIGGY_BANK, "Piggy Bank"),
    # New - Layer 0
    (ENTITY_CURRENCY, "Currency"),
    (ENTITY_BUDGET, "Budget"),
    (ENTITY_RULE_GROUP, "Rule Group"),
    # New - Layer 1
    (ENTITY_BILL, "Bill"),
    # New - Layer 2
    (ENTITY_RULE, "Rule"),
    (ENTITY_RECURRENCE, "Recurrence"),
    # New - Layer 3
    (ENTITY_TRANSACTION, "Transaction"),
]
```

### 5.2 Fingerprint Strategy Per Entity

| Entity | Fingerprint Components | Hash Formula |
|--------|----------------------|--------------|
| Currency | code | `SHA256(f"currency:{code.upper()}")` |
| Budget | name | `SHA256(f"budget:{name.lower().strip()}")` |
| Rule Group | title | `SHA256(f"rule_group:{title.lower().strip()}")` |
| Bill | name, amount_min, amount_max | `SHA256(f"bill:{name}:{amount_min:.2f}:{amount_max:.2f}")` |
| Rule | title, triggers_hash, actions_hash | `SHA256(f"rule:{title}:{triggers_hash}:{actions_hash}")` |
| Recurrence | title, first_date, repeat_freq | `SHA256(f"recurrence:{title}:{first_date}:{repeat_freq}")` |
| Transaction | date, amount, source, destination, description | Use existing `generate_external_id_v2()` |

### 5.3 Import Strategy Per Entity

| Entity | Create | Skip | Update | Notes |
|--------|--------|------|--------|-------|
| Currency | ❌ | ✅ (if exists) | ❌ | Enable only via API |
| Category | ✅ | ✅ (same name) | ❌ | Simple name match |
| Tag | ✅ | ✅ (same name) | ❌ | Simple name match |
| Budget | ✅ | ✅ (same name) | ❌ | Simple name match |
| Rule Group | ✅ | ✅ (same title) | ❌ | Simple title match |
| Account | ✅ | ✅ (same name+type) | ❌ | Use find_or_create |
| Piggy Bank | ✅ | ✅ (same name) | ❌ | Needs account mapping |
| Bill | ✅ | ✅ (same name) | ❌ | Amount-based fingerprint |
| Rule | ✅ | ✅ (same title) | ❌ | Complex reference mapping |
| Recurrence | ✅ | ✅ (same title) | ❌ | Complex reference mapping |
| Transaction | ✅ | ✅ (same external_id) | ❌ | Dedupe via external_id |

### 5.4 Reference Mapping System

When importing entities with dependencies, we need to map source Firefly IDs to target Firefly entities by name:

```python
class ReferenceMapper:
    """Maps source entity references to target Firefly IDs."""
    
    def __init__(self, client: FireflyClient):
        self.client = client
        self._cache = {}
    
    def resolve_account(self, source_name: str, account_type: str) -> int:
        """Find or create account by name and type."""
        key = f"account:{account_type}:{source_name.lower()}"
        if key not in self._cache:
            accounts = self.client.list_accounts(account_type)
            for acc in accounts:
                if acc["name"].lower() == source_name.lower():
                    self._cache[key] = int(acc["id"])
                    break
            else:
                # Create if not found
                self._cache[key] = self.client.find_or_create_account(
                    source_name, account_type
                )
        return self._cache[key]
    
    def resolve_category(self, source_name: str) -> int | None:
        """Find category by name, return None if not found."""
        ...
```

### 5.5 Transaction Deduplication Strategy

**Chosen Strategy: Firefly `external_id` field**

1. **On Fetch**: Compute `external_id` using `generate_external_id_v2(amount, date, source, destination, description)`
2. **On Import**: 
   - Search for existing transaction with same `external_id` using `/v1/search/transactions?query=external_id:{id}`
   - If found → skip (already imported)
   - If not found → create with `external_id` set

**Transaction Fingerprint for Pool Storage:**
```python
def compute_transaction_fingerprint(data: dict) -> str:
    """Compute fingerprint for transaction deduplication."""
    return generate_external_id_v2(
        amount=data["amount"],
        date=data["date"][:10],  # YYYY-MM-DD
        source=data.get("source_name", ""),
        destination=data.get("destination_name", ""),
        description=data.get("description", ""),
    )
```

### 5.6 Split Transaction Handling

- **Firefly Model**: Transactions can have multiple "splits" (journal entries)
- **Pool Storage**: Store full split array in `data_json`
- **Import**: Create transaction with all splits intact
- **Fingerprint**: Based on first split (primary) + total amount

### 5.7 Transfer Handling

Transfers require:
- Source account (asset type)
- Destination account (asset type)
- Both must exist in target Firefly

**Import Logic:**
1. Resolve source account by name
2. Resolve destination account by name
3. If either fails → skip with error message
4. Create transfer with resolved IDs

---

## 6. UI/UX Expansion Plan

### 6.1 Entity Card Pattern (Existing)

Each entity type has a card with three collapsible sections:
1. **My Firefly → My Pool** (Fetch): Button to pull from Firefly
2. **My Pool (Owned)**: List of owned records with share capability
3. **Shared with Me**: List of records shared by others with import capability

### 6.2 New Entity Cards

Add cards for:
- 💰 **Budgets**
- 📋 **Bills**
- ⚙️ **Rule Groups**
- 🔧 **Rules**
- 🔄 **Recurrences**
- 💱 **Currencies**
- 💳 **Transactions** (special handling)

### 6.3 Transaction Card Special Features

The transaction card needs additional UI elements:

```html
<!-- Transaction Filters -->
<div class="transaction-filters">
    <input type="date" name="start_date" placeholder="Start Date">
    <input type="date" name="end_date" placeholder="End Date">
    <select name="type_filter">
        <option value="">All Types</option>
        <option value="withdrawal">Withdrawals</option>
        <option value="deposit">Deposits</option>
        <option value="transfer">Transfers</option>
    </select>
    <input type="number" name="limit" placeholder="Max results" value="100">
</div>
```

### 6.4 Card Layout Grid

Organize cards in logical groups:

```
┌─────────────────────────────────────────────────────────────┐
│                    CORE ENTITIES                            │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────┐│
│  │ Categories  │ │    Tags     │ │  Accounts   │ │ Budgets ││
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────┘│
├─────────────────────────────────────────────────────────────┤
│                    PLANNING                                 │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────┐│
│  │ Piggy Banks │ │    Bills    │ │  Currencies │ │         ││
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────┘│
├─────────────────────────────────────────────────────────────┤
│                    AUTOMATION                               │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│  │ Rule Groups │ │    Rules    │ │ Recurrences │            │
│  └─────────────┘ └─────────────┘ └─────────────┘            │
├─────────────────────────────────────────────────────────────┤
│                    TRANSACTIONS                             │
│  ┌─────────────────────────────────────────────────────────┐│
│  │                    Transactions                          ││
│  │  [Date Range Picker] [Type Filter] [Fetch] [Import All] ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Endpoints Plan

### 7.1 Authentication Enforcement

**Global Policy**: All endpoints require authentication except:
- `/login/`
- `/logout/`
- `/register/`
- `/admin/` (has its own auth)

**Implementation**: All view functions use `@login_required` decorator.

### 7.2 New/Modified Endpoints

| Endpoint | Method | Purpose | Auth |
|----------|--------|---------|------|
| `/api/sync/fetch/<entity_type>/` | POST | Fetch entities (extended for new types) | ✅ Required |
| `/api/sync/pool/<entity_type>/` | GET | List pool records (extended) | ✅ Required |
| `/api/sync/share/` | POST | Share records (unchanged) | ✅ Required |
| `/api/sync/share/<share_id>/` | DELETE | Revoke share (unchanged) | ✅ Required |
| `/api/sync/import/` | POST | Import records (extended) | ✅ Required |
| `/api/sync/preview/<entity_type>/` | GET | Preview import (extended) | ✅ Required |

### 7.3 Extended Entity Types in API

The `entity_type` path parameter will accept:
```python
VALID_ENTITY_TYPES = [
    "category", "tag", "account", "piggy_bank",  # Existing
    "currency", "budget", "rule_group", "bill",  # New Layer 0-1
    "rule", "recurrence",                        # New Layer 2
    "transaction",                               # New Layer 3
]
```

### 7.4 Authorization Rules

| Action | Rule |
|--------|------|
| Fetch | User must have Firefly token configured |
| View Pool | User sees only records where `owner=user` OR `shared_with=user` |
| Share | User must be `owner` of record |
| Unshare | User must be `owner` of record |
| Import | User must own record OR have share permission |

### 7.5 Security Test Requirements

1. **Unauthenticated Access**:
   ```python
   def test_sync_endpoints_require_auth(self):
       """All sync endpoints must return 302→login for anonymous users."""
       endpoints = [
           "/sync-assistant/",
           "/api/sync/fetch/category/",
           "/api/sync/pool/category/",
           "/api/sync/share/",
           "/api/sync/import/",
       ]
       for url in endpoints:
           response = self.client.get(url)
           self.assertIn(response.status_code, [302, 403])
   ```

2. **Cross-User Access**:
   ```python
   def test_cannot_access_other_users_pool_records(self):
       """User A cannot see User B's unshared pool records."""
       ...
   ```

3. **Cross-User Modification**:
   ```python
   def test_cannot_share_other_users_records(self):
       """User A cannot share User B's records."""
       ...
   ```

---

## 8. Roadmap (Step-by-Step Implementation)

### Phase 1: Database Schema Extension (Migration 0006)

1. Update `SyncPoolRecord.ENTITY_TYPE_CHOICES` with new entity types
2. No structural changes needed (existing schema handles all entity types)
3. Create migration

### Phase 2: Fingerprint Functions

1. Add to `sync_fingerprints.py`:
   - `compute_currency_fingerprint()`
   - `compute_budget_fingerprint()`
   - `compute_rule_group_fingerprint()`
   - `compute_bill_fingerprint()`
   - `compute_rule_fingerprint()`
   - `compute_recurrence_fingerprint()`
   - `compute_transaction_fingerprint()` (uses existing `generate_external_id_v2`)
2. Update `FINGERPRINT_FUNCTIONS` registry
3. Update `normalize_entity_data()` for new types
4. Update `get_entity_name()` for new types

### Phase 3: FireflyClient Extensions

1. Add list methods:
   - `list_budgets()` → `list[dict]`
   - `list_bills()` → `list[dict]`
   - `list_rule_groups()` → `list[dict]`
   - `list_rules()` → `list[dict]`
   - `list_recurrences()` → `list[dict]`
   - `list_currencies()` → `list[dict]`
2. Add create methods:
   - `create_budget(name, auto_budget_amount, auto_budget_period)` → `int`
   - `create_bill(name, amount_min, amount_max, date, repeat_freq, ...)` → `int`
   - `create_rule_group(title, order)` → `int`
   - `create_rule(title, trigger, actions, rule_group_id, ...)` → `int`
   - `create_recurrence(title, first_date, repeat_freq, transactions)` → `int`
   - `enable_currency(code)` → `bool`
3. Add find methods:
   - `find_budget_by_name(name)` → `dict|None`
   - `find_bill_by_name(name)` → `dict|None`
   - `find_rule_group_by_title(title)` → `dict|None`
   - `find_rule_by_title(title)` → `dict|None`
   - `find_recurrence_by_title(title)` → `dict|None`

### Phase 4: Reference Mapper Implementation

1. Create `src/paperless_firefly/services/reference_mapper.py`:
   ```python
   class ReferenceMapper:
       def resolve_account(name, type) -> int
       def resolve_category(name) -> int | None
       def resolve_tag(name) -> int | None
       def resolve_budget(name) -> int | None
       def resolve_rule_group(title) -> int | None
   ```
2. Add caching to minimize API calls

### Phase 5: Backend API Extensions

1. Update `api_sync_fetch()` to handle new entity types
2. Update `api_sync_pool()` to handle new entity types
3. Update `api_sync_import()` with:
   - Layer-aware import ordering
   - Reference resolution using `ReferenceMapper`
   - Transaction-specific dedupe logic

### Phase 6: Transaction-Specific Implementation

1. Fetch with filters:
   - Accept `start_date`, `end_date`, `type_filter`, `limit` parameters
   - Paginate through large result sets
2. Import with deduplication:
   - Compute `external_id` for each transaction
   - Check existing via `/v1/search/transactions`
   - Map account names to target Firefly IDs
   - Handle splits and transfers
3. Error handling:
   - Account resolution failures
   - Currency mismatches
   - Invalid date formats

### Phase 7: UI Template Updates

1. Add new entity cards to `sync_assistant.html`
2. Group cards by category (Core, Planning, Automation, Transactions)
3. Add transaction-specific filters UI
4. Add loading states and error messages

### Phase 8: Testing

1. Unit tests:
   - Fingerprint computation for all new types
   - Reference mapper resolution
   - Transaction fingerprint stability
2. Integration tests:
   - Full fetch-pool-share-import cycle for each entity type
   - Transaction import with splits
   - Transaction import with transfers
3. Security tests:
   - Unauthenticated access blocked
   - Cross-user access blocked
   - Ownership verification

### Phase 9: Documentation

1. Update README.md with new features
2. Add user guide section for Sync Assistant
3. Document API endpoints

---

## 9. Test Plan & Verification

### 9.1 Unit Tests

```python
class TestSyncFingerprints:
    def test_budget_fingerprint_stability(self):
        """Same budget produces same fingerprint."""
    
    def test_rule_fingerprint_includes_triggers_actions(self):
        """Rule fingerprint changes with trigger/action changes."""
    
    def test_transaction_fingerprint_uses_external_id_v2(self):
        """Transaction fingerprint matches generate_external_id_v2."""

class TestReferenceMapper:
    def test_resolve_existing_account(self):
        """Maps to existing account by name."""
    
    def test_resolve_missing_account_creates(self):
        """Creates account if not found."""
    
    def test_caching_reduces_api_calls(self):
        """Second resolve uses cache."""
```

### 9.2 Integration Tests

```python
class TestSyncAssistantIntegration:
    def test_full_budget_sync_cycle(self):
        """Fetch → Pool → Share → Import for budgets."""
    
    def test_transaction_import_deduplication(self):
        """Repeated import skips duplicates."""
    
    def test_transaction_import_resolves_accounts(self):
        """Transaction import maps account names correctly."""
    
    def test_split_transaction_import(self):
        """Split transactions import with all splits."""
    
    def test_transfer_import_requires_both_accounts(self):
        """Transfer fails gracefully if destination missing."""
```

### 9.3 Security Tests

```python
class TestSyncAssistantSecurity:
    def test_all_sync_endpoints_require_auth(self):
        """Anonymous requests return 302 or 403."""
    
    def test_cannot_fetch_other_users_pool(self):
        """User A cannot list User B's pool records."""
    
    def test_cannot_share_unowned_records(self):
        """Sharing records you don't own returns 403."""
    
    def test_cannot_import_unshared_records(self):
        """Importing records not owned/shared returns 403."""
```

### 9.4 Transaction Correctness Tests

```python
class TestTransactionSync:
    def test_repeated_import_no_duplicates(self):
        """Import same transaction twice → second is skipped."""
    
    def test_external_id_matches_source(self):
        """Imported transaction has correct external_id."""
    
    def test_split_amounts_preserved(self):
        """Split transaction totals match original."""
    
    def test_transfer_both_accounts_exist(self):
        """Transfer import creates/resolves both accounts."""
```

---

## 10. Definition of Done

### Checklist

- [ ] **Database**: Migration 0006 applied, new entity types registered
- [ ] **Fingerprints**: All new entity types have fingerprint functions with tests
- [ ] **FireflyClient**: List/create/find methods for all new entity types
- [ ] **Reference Mapper**: Working account/category/tag/budget resolution
- [ ] **API**: All endpoints extended for new entity types
- [ ] **Transactions**: Full fetch/import cycle with deduplication
- [ ] **UI**: Cards for all entity types with consistent pattern
- [ ] **Transactions UI**: Date range and type filters working
- [ ] **Authorization**: All endpoints verified to require auth
- [ ] **Cross-user**: Cannot access unshared records
- [ ] **Audit**: SyncImportLog captures all import operations
- [ ] **Tests**: All tests passing (unit, integration, security)
- [ ] **Documentation**: README updated, API documented

### Success Metrics

1. User can fetch all supported entities from Firefly
2. User can share any entity type with other users
3. User can import shared entities to their own Firefly
4. Transaction import does not create duplicates on re-import
5. No authenticated endpoint accessible without login
6. No cross-user data leakage

---

## Appendix A: Entity Data Schemas

### Budget
```json
{
    "id": 1,
    "name": "Groceries",
    "auto_budget_type": "rollover",
    "auto_budget_amount": "500.00",
    "auto_budget_period": "monthly"
}
```

### Bill
```json
{
    "id": 1,
    "name": "Netflix",
    "amount_min": "15.99",
    "amount_max": "15.99",
    "date": "2026-01-15",
    "repeat_freq": "monthly",
    "skip": 0,
    "active": true
}
```

### Rule Group
```json
{
    "id": 1,
    "title": "Auto-categorization",
    "order": 1,
    "active": true
}
```

### Rule
```json
{
    "id": 1,
    "title": "Tag grocery stores",
    "rule_group_id": 1,
    "order": 1,
    "active": true,
    "strict": false,
    "trigger": "store_id",
    "triggers": [
        {"type": "description_contains", "value": "GROCERY", "active": true}
    ],
    "actions": [
        {"type": "add_tag", "value": "groceries", "active": true}
    ]
}
```

### Recurrence
```json
{
    "id": 1,
    "title": "Monthly rent",
    "first_date": "2026-02-01",
    "repeat_freq": "monthly",
    "repetitions": 0,
    "transactions": [
        {
            "amount": "1500.00",
            "description": "Rent payment",
            "source_id": 1,
            "destination_id": null,
            "destination_name": "Landlord"
        }
    ]
}
```

### Transaction (for pool storage)
```json
{
    "id": 12345,
    "type": "withdrawal",
    "date": "2026-01-15",
    "amount": "45.67",
    "description": "Grocery shopping",
    "source_name": "Checking Account",
    "destination_name": "SPAR",
    "category_name": "Groceries",
    "tags": ["food", "weekly"],
    "external_id": "abc123def456:pl:doc789",
    "splits": []
}
```

---

## Appendix B: Implementation Notes

### Handling Rule Triggers and Actions

Rules have complex trigger/action structures. For fingerprinting:

```python
def compute_rule_fingerprint(data: dict) -> str:
    """Compute fingerprint for a rule."""
    title = str(data.get("title", "")).lower().strip()
    
    # Hash triggers
    triggers = data.get("triggers", [])
    triggers_str = json.dumps(sorted(triggers, key=lambda t: t.get("type", "")), sort_keys=True)
    triggers_hash = hashlib.sha256(triggers_str.encode()).hexdigest()[:8]
    
    # Hash actions
    actions = data.get("actions", [])
    actions_str = json.dumps(sorted(actions, key=lambda a: a.get("type", "")), sort_keys=True)
    actions_hash = hashlib.sha256(actions_str.encode()).hexdigest()[:8]
    
    content = f"rule:{title}:{triggers_hash}:{actions_hash}"
    return hashlib.sha256(content.encode()).hexdigest()
```

### Currency Handling

Currencies cannot be created via API (only enabled/disabled):

```python
def import_currency(self, code: str) -> dict:
    """Enable a currency in target Firefly."""
    # Check if already enabled
    currencies = self.client.list_currencies()
    for curr in currencies:
        if curr["code"].upper() == code.upper():
            if curr["enabled"]:
                return {"status": "skipped", "reason": "already enabled"}
            else:
                self.client.enable_currency(code)
                return {"status": "enabled"}
    return {"status": "error", "reason": "currency not available"}
```

---

*End of Roadmap Document*
