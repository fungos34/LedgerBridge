# Firefly Sync Assistant Roadmap

**Status**: Implementation Roadmap  
**Created**: January 16, 2026  
**Last Updated**: January 16, 2026

---

## 1. Context Snapshot

### 1.1 Authentication Stack
- **Framework**: Django 4.x with built-in authentication
- **User Model**: `django.contrib.auth.models.User` with custom `UserProfile` extension
- **Auth Endpoints**:
  - `/login/` - Django LoginView
  - `/logout/` - Django LogoutView
  - `/register/` - Custom registration view
- **Protection**: `@login_required` decorator on all protected views
- **Files Inspected**:
  - [urls.py](../src/paperless_firefly/review/web/urls.py)
  - [views.py](../src/paperless_firefly/review/web/views.py)
  - [models.py](../src/paperless_firefly/review/web/models.py)

### 1.2 Firefly Integration Approach
- **API Client**: `FireflyClient` class in [client.py](../src/paperless_firefly/firefly_client/client.py)
- **Token Storage**: Per-user in `UserProfile.firefly_token` (encrypted field)
- **Token Retrieval**: Each user stores their own Firefly token; no shared global token
- **Existing Methods**:
  - `list_categories()` - Lists all Firefly categories
  - `list_accounts()` - Lists accounts by type (asset, expense, revenue, liability, cash)
  - `list_transactions()` - Lists transactions in date range
  - `create_transaction()`, `update_transaction()` - Create/update transactions
  - `find_or_create_account()` - Account management
- **Rate Limits**: Retry strategy with exponential backoff (429, 500, 502, 503, 504)
- **Pagination**: Handled internally with `max_pages` limit

### 1.3 UI Framework/Structure
- **Template Engine**: Django templates with base template pattern
- **Navigation**: Top nav bar with user dropdown menu in [base.html](../src/paperless_firefly/review/web/templates/review/base.html)
- **User Menu Items**: Settings, Change Password, Syncthing (if configured), Bank Importer (if configured), Logout
- **Card Pattern**: Uses cards with headers, consistent styling (see unified_review_list.html)
- **AJAX Pattern**: Fetch API with CSRF token, JSON responses
- **CSS Variables**: Design system with `--gray-*`, `--green-*`, etc.

### 1.4 DB/Migrations Approach
- **Dual Database**: 
  1. Django default DB for User, UserProfile models
  2. SQLite state.db for operational data (documents, extractions, imports)
- **State Store**: [sqlite_store.py](../src/paperless_firefly/state_store/sqlite_store.py) with manual migrations
- **Migration Pattern**: Numbered Python scripts in `state_store/migrations/` (001, 002, etc.)
- **Runner**: `migrations/runner.py` handles sequential application

---

## 2. Requirements (Interpreted)

### 2.1 MUST Requirements (Non-negotiable)

1. **MUST** add "Firefly Sync Assistant" entry to user dropdown menu
2. **MUST** require authentication for all endpoints (no exceptions except login/register/forgot-password)
3. **MUST** allow each user to fetch data from their own Firefly account into a local "pool"
4. **MUST** allow users to share pool records with specific other Sparklink users
5. **MUST** display owned pool records separately from shared-with-me records
6. **MUST** display original owner identity on shared records
7. **MUST** allow importing selected pool records into user's own Firefly
8. **MUST** use stable fingerprints (NOT Firefly IDs) for deduplication
9. **MUST** skip duplicates by default (create missing, skip existing)
10. **MUST** record audit trail of imports (who, what, outcome)
11. **MUST** enforce authorization:
    - Only owner can grant/revoke shares
    - Users can only view owned OR shared-with-me records
    - Users can only import owned OR shared-with-me records

### 2.2 SHOULD Requirements (Strong Preference)

1. **SHOULD** support initial entity types: Categories, Tags, Accounts
2. **SHOULD** design for extensibility to add more entity types later
3. **SHOULD** provide filter controls per entity type card
4. **SHOULD** support select all / per-record selection
5. **SHOULD** show preview hints (create/skip/update) before import
6. **SHOULD** handle token expiration gracefully with user-friendly error

### 2.3 MAY Requirements (Optional Enhancements)

1. **MAY** support Groups (Firefly groups/budgets) if API supports it
2. **MAY** support Piggy Banks if API supports it
3. **MAY** support Bookings/Transactions (explicitly deferred pending feasibility analysis)
4. **MAY** implement "allow updates" policy as opt-in advanced feature
5. **MAY** implement pagination for large pool record lists

### 2.4 Ambiguity Resolutions

| Ambiguity | Chosen Interpretation |
|-----------|----------------------|
| "tenant/workspace boundaries" | Sparklink has no multi-tenant model; use user_id ownership pattern already established |
| "avoid global user list leaks" | Only show users who have active Firefly tokens when selecting share recipients |
| "Groups" support | Firefly III has "transaction groups" (split transactions) not "groups" entity; defer groups concept |
| "Piggy banks" | Firefly III supports piggy banks via API; include in initial implementation |
| "Bookings/transactions" | Explicitly defer; risk of duplicate transactions, complex semantics |

---

## 3. Firefly Capability Evaluation: API vs CLI

### 3.1 Available CLI Tools
This deployment uses the **Firefly III API exclusively**. There are no CLI tools deployed:
- Firefly III does not ship with command-line management tools
- All interaction is via REST API (Personal Access Token authentication)
- Firefly Data Importer is a separate web service, not CLI

### 3.2 API Capabilities

| Entity | List | Create | Update | Delete | Notes |
|--------|------|--------|--------|--------|-------|
| Categories | ✅ `/api/v1/categories` | ✅ | ✅ | ✅ | Name + notes |
| Tags | ✅ `/api/v1/tags` | ✅ | ✅ | ✅ | Name + tag/description |
| Accounts | ✅ `/api/v1/accounts` | ✅ | ✅ | ✅ | Multiple types supported |
| Piggy Banks | ✅ `/api/v1/piggy_banks` | ✅ | ✅ | ✅ | Linked to accounts |
| Transactions | ✅ `/api/v1/transactions` | ✅ | ✅ | ✅ | Complex, deferred |

### 3.3 Auth Model Constraints
- Bearer token authentication via `Authorization: Bearer {token}`
- Personal Access Tokens scoped to single Firefly instance
- No cross-instance sharing at Firefly level
- Token expiration: depends on Firefly configuration (typically long-lived)

### 3.4 Rate Limits
- Firefly III has no explicit rate limits
- Retry strategy already implemented in client for error codes

### 3.5 Missing Endpoints
None for the target entities. All CRUD operations available.

### 3.6 Final Choice: **API Only**
**Justification**: Firefly III is API-first with comprehensive REST endpoints. No CLI exists in this deployment. The existing `FireflyClient` class provides a solid foundation that we will extend.

---

## 4. Proposed Architecture

### 4.1 Data Model

#### 4.1.1 Django Models (default database)

```python
# New model in models.py
class SyncPoolRecord(models.Model):
    """Pool record fetched from a user's Firefly instance."""
    
    # Ownership
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sync_pool_records')
    
    # Entity identification
    entity_type = models.CharField(max_length=50)  # 'category', 'tag', 'account', 'piggy_bank'
    firefly_id = models.IntegerField()  # ID in owner's Firefly
    
    # Fingerprint for cross-instance deduplication (NOT Firefly ID)
    fingerprint = models.CharField(max_length=64, db_index=True)  # SHA256 of normalized data
    
    # Cached entity data (JSON)
    data_json = models.TextField()  # Full entity data for display/import
    name = models.CharField(max_length=255)  # Denormalized for display
    
    # Metadata
    fetched_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['owner', 'entity_type', 'fingerprint']
        indexes = [
            models.Index(fields=['entity_type', 'fingerprint']),
        ]


class SyncPoolShare(models.Model):
    """Share permission for a pool record."""
    
    record = models.ForeignKey(SyncPoolRecord, on_delete=models.CASCADE, related_name='shares')
    shared_with = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_shares')
    
    # Metadata
    shared_at = models.DateTimeField(auto_now_add=True)
    shared_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='granted_shares')
    
    class Meta:
        unique_together = ['record', 'shared_with']


class SyncImportLog(models.Model):
    """Audit log of import operations."""
    
    # Who imported
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sync_imports')
    
    # What was imported
    pool_record = models.ForeignKey(SyncPoolRecord, on_delete=models.SET_NULL, null=True)
    entity_type = models.CharField(max_length=50)
    fingerprint = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    
    # Outcome
    status = models.CharField(max_length=20)  # 'created', 'skipped', 'error'
    target_firefly_id = models.IntegerField(null=True)  # ID in importer's Firefly
    error_message = models.TextField(blank=True, default='')
    
    # Metadata
    imported_at = models.DateTimeField(auto_now_add=True)
    source_owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
```

### 4.2 Security Model

1. **Authentication**: `@login_required` on all views
2. **Authorization**:
   - `SyncPoolRecord` ownership via `owner` FK
   - `SyncPoolShare` grants read access to specific users
   - All queries filter by `owner=user OR shares__shared_with=user`
3. **Token Isolation**: Each user's Firefly token only used for their own operations
4. **Share Recipient List**: Only show users with `has_firefly_token=True` (no exposure of all users)

### 4.3 Data Flow

#### 4.3.1 Fetch (My Firefly → My Pool)
1. User clicks "Fetch from my Firefly" on entity card
2. View retrieves user's Firefly token from `UserProfile`
3. `FireflyClient` calls appropriate list endpoint (paginated)
4. For each entity:
   - Compute fingerprint from normalized data
   - Upsert into `SyncPoolRecord` (update if fingerprint exists, create if not)
5. Return success count to UI

#### 4.3.2 Share Management
1. User selects pool records they own
2. User selects recipients from eligible users list
3. POST creates `SyncPoolShare` entries
4. Revoke: DELETE removes `SyncPoolShare` entries
5. Authorization: Only `record.owner == request.user` can modify shares

#### 4.3.3 Import (Pool → My Firefly)
1. User selects records (owned or shared-with-me)
2. POST triggers import for each record:
   - Load data from `SyncPoolRecord.data_json`
   - Check if entity exists in user's Firefly (by fingerprint or name)
   - If exists: skip (log as 'skipped')
   - If not exists: create via API (log as 'created')
   - On error: log as 'error' with message
3. Return summary to UI, persist to `SyncImportLog`

### 4.4 Fingerprint Strategy

Fingerprints must be stable across Firefly instances:

| Entity | Fingerprint Components |
|--------|----------------------|
| Category | `sha256(f"category:{name.lower().strip()}")` |
| Tag | `sha256(f"tag:{name.lower().strip()}")` |
| Account | `sha256(f"account:{type}:{name.lower().strip()}:{currency_code}")` |
| Piggy Bank | `sha256(f"piggy:{name.lower().strip()}:{target_amount}")` |

### 4.5 Idempotency Strategy

1. **Fetch**: Upsert by `(owner, entity_type, fingerprint)` - same entity updates, not duplicates
2. **Share**: Unique constraint on `(record, shared_with)` - no duplicate shares
3. **Import**: Check existence by fingerprint in target Firefly before create; skip if exists

### 4.6 Extensibility Strategy

- `entity_type` field allows adding new types without schema changes
- `data_json` stores entity-specific data flexibly
- Fingerprint functions registered in a registry pattern:

```python
FINGERPRINT_REGISTRY = {
    'category': compute_category_fingerprint,
    'tag': compute_tag_fingerprint,
    'account': compute_account_fingerprint,
    'piggy_bank': compute_piggy_bank_fingerprint,
}
```

---

## 5. UI/UX Plan

### 5.1 Entry Point
Add to user dropdown menu in `base.html`:
```html
<a href="{% url 'sync_assistant' %}">🔄 Sync Assistant</a>
```
Placed after "Change Password", before external links section.

### 5.2 Route
- `GET /sync-assistant/` → Main page (sync_assistant view)
- `POST /api/sync/fetch/<entity_type>/` → Fetch from Firefly
- `GET /api/sync/pool/<entity_type>/` → List pool records
- `POST /api/sync/share/` → Grant shares
- `DELETE /api/sync/share/<share_id>/` → Revoke share
- `POST /api/sync/import/` → Import selected records
- `GET /api/sync/eligible-users/` → List users eligible for sharing

### 5.3 Page Layout

```
┌─────────────────────────────────────────────────────────────────┐
│ 🔄 Firefly Sync Assistant                                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 📁 Categories                                               │ │
│ ├─────────────────────────────────────────────────────────────┤ │
│ │ ▸ My Firefly → My Pool                                      │ │
│ │   [Filter: ________] [🔄 Fetch from my Firefly]             │ │
│ ├─────────────────────────────────────────────────────────────┤ │
│ │ ▸ My Pool (Owned)                                           │ │
│ │   [☐ Select All]                                            │ │
│ │   ┌────┬──────────────────┬───────────────────────────────┐ │ │
│ │   │ ☐ │ Groceries        │ [Share ▼] [Shared with: 2]     │ │ │
│ │   │ ☐ │ Transportation   │ [Share ▼] [Shared with: 0]     │ │ │
│ │   │ ☐ │ Entertainment    │ [Share ▼] [Shared with: 1]     │ │ │
│ │   └────┴──────────────────┴───────────────────────────────┘ │ │
│ ├─────────────────────────────────────────────────────────────┤ │
│ │ ▸ Shared With Me                                            │ │
│ │   [☐ Select All] [📥 Import Selected to My Firefly]         │ │
│ │   ┌────┬──────────────────┬──────────────┬────────────────┐ │ │
│ │   │ ☐ │ Utilities        │ From: alice  │ [Will Create]  │ │ │
│ │   │ ☐ │ Insurance        │ From: bob    │ [Will Skip]    │ │ │
│ │   └────┴──────────────────┴──────────────┴────────────────┘ │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 🏷️ Tags                                                     │ │
│ │ (same structure)                                            │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 🏦 Accounts                                                 │ │
│ │ (same structure + type filter: asset/expense/revenue/etc)  │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ 🐷 Piggy Banks                                              │ │
│ │ (same structure)                                            │ │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.4 Selection Mechanics
- Checkbox per row
- "Select All" toggles all visible in section
- Selection state maintained in JavaScript (not persisted)
- Batch actions operate on selected items

### 5.5 Preview Behavior
Before import, each shared record shows hint:
- 🟢 "Will Create" - fingerprint not found in user's Firefly
- ⚪ "Will Skip" - fingerprint already exists in user's Firefly
- 🔴 "Error" - if pre-check fails

### 5.6 Error Messages
- Token missing: "Please configure your Firefly token in Settings"
- Token expired: "Firefly token expired. Please update in Settings"
- Connection failed: "Could not connect to Firefly. Check URL in Settings"
- Partial failure: "Imported 5 of 8 items. 3 errors (see details)"

---

## 6. Endpoint & Authorization Plan

### 6.1 Page Views

| Method | Path | Auth | Authorization | Description |
|--------|------|------|---------------|-------------|
| GET | `/sync-assistant/` | ✅ Required | Any authenticated user | Main page |

### 6.2 API Endpoints

| Method | Path | Auth | Authorization | I/O |
|--------|------|------|---------------|-----|
| POST | `/api/sync/fetch/<entity_type>/` | ✅ | User must have Firefly token | In: `{filter: str}`, Out: `{success: bool, count: int}` |
| GET | `/api/sync/pool/<entity_type>/` | ✅ | Returns owned + shared-with-me | Out: `{owned: [...], shared: [...]}` |
| POST | `/api/sync/share/` | ✅ | Must own all specified records | In: `{record_ids: [...], user_ids: [...]}` |
| DELETE | `/api/sync/share/<share_id>/` | ✅ | Must own the record | Out: `{success: bool}` |
| POST | `/api/sync/import/` | ✅ | Must own or be shared on records | In: `{record_ids: [...]}`, Out: `{results: [...]}` |
| GET | `/api/sync/eligible-users/` | ✅ | Returns users with Firefly tokens (not self) | Out: `{users: [{id, username}, ...]}` |
| GET | `/api/sync/preview/<entity_type>/` | ✅ | Shared-with-me records only | Out: `{previews: [{id, status: 'create'|'skip'}, ...]}` |

### 6.3 Security Invariants (enforced in views)

1. All endpoints check `request.user.is_authenticated`
2. Fetch: `request.user.profile.has_firefly_token` must be True
3. Share: `SyncPoolRecord.objects.filter(id__in=record_ids, owner=request.user).count() == len(record_ids)`
4. Import: All records must be `owner=user OR shares__shared_with=user`
5. Eligible users: Filter `UserProfile.objects.filter(firefly_token__gt='').exclude(user=request.user)`

---

## 7. Roadmap (Implementation Steps)

### Phase 1: Foundation

| Step | What | Files | Verification |
|------|------|-------|--------------|
| 1.1 | Create Django models | `models.py` | `makemigrations`, `migrate` |
| 1.2 | Create fingerprint utilities | `sync_assistant/fingerprints.py` | Unit tests |
| 1.3 | Extend FireflyClient for tags, piggy banks | `firefly_client/client.py` | Unit tests |

### Phase 2: Backend API

| Step | What | Files | Verification |
|------|------|-------|--------------|
| 2.1 | Add sync assistant routes | `urls.py` | Route resolution |
| 2.2 | Implement fetch endpoint | `views.py` | Manual test with Firefly |
| 2.3 | Implement pool list endpoint | `views.py` | Returns owned/shared correctly |
| 2.4 | Implement share/unshare endpoints | `views.py` | Authorization tests |
| 2.5 | Implement import endpoint | `views.py` | Creates in Firefly, logs audit |
| 2.6 | Implement preview endpoint | `views.py` | Returns create/skip hints |
| 2.7 | Implement eligible-users endpoint | `views.py` | Only returns valid users |

### Phase 3: Frontend UI

| Step | What | Files | Verification |
|------|------|-------|--------------|
| 3.1 | Add menu entry | `base.html` | Link visible in dropdown |
| 3.2 | Create sync_assistant.html template | `templates/review/sync_assistant.html` | Page loads |
| 3.3 | Implement fetch UI per card | Template + JS | Fetches and displays |
| 3.4 | Implement pool display | Template + JS | Shows owned/shared sections |
| 3.5 | Implement selection mechanics | JS | Select all works |
| 3.6 | Implement share dialog | Template + JS | Can share with users |
| 3.7 | Implement import flow | Template + JS | Imports with feedback |

### Phase 4: Polish & Testing

| Step | What | Files | Verification |
|------|------|-------|--------------|
| 4.1 | Add loading states | Template + CSS | Spinners during fetch/import |
| 4.2 | Add error handling UI | Template + JS | Errors displayed nicely |
| 4.3 | Write integration tests | `tests/test_sync_assistant.py` | All tests pass |
| 4.4 | Security tests | `tests/test_sync_assistant.py` | Unauth/unauthorized rejected |

---

## 8. Test Plan

### 8.1 Unit Tests

- **Fingerprint Functions**:
  - `test_category_fingerprint_stable` - same input → same output
  - `test_category_fingerprint_normalized` - "Groceries" == "groceries " 
  - `test_account_fingerprint_includes_type` - same name, different type → different fingerprint
  
- **Deduplication Logic**:
  - `test_upsert_creates_new_record`
  - `test_upsert_updates_existing_record`
  - `test_import_skips_existing_fingerprint`

### 8.2 Integration Tests

- **Fetch Endpoint**:
  - `test_fetch_requires_auth` - 302 redirect without login
  - `test_fetch_requires_firefly_token` - 400 if no token
  - `test_fetch_creates_pool_records`
  
- **Pool List Endpoint**:
  - `test_pool_returns_owned_records`
  - `test_pool_returns_shared_records`
  - `test_pool_hides_others_private_records`
  
- **Share Endpoint**:
  - `test_share_requires_ownership` - 403 if not owner
  - `test_share_creates_share_entry`
  - `test_unshare_removes_share_entry`
  
- **Import Endpoint**:
  - `test_import_creates_in_firefly`
  - `test_import_skips_existing`
  - `test_import_logs_audit_trail`
  - `test_import_requires_access` - 403 for unshared records

### 8.3 Security Tests

- `test_all_endpoints_require_auth`
- `test_cannot_share_others_records`
- `test_cannot_import_unshared_records`
- `test_eligible_users_excludes_self`
- `test_eligible_users_only_shows_token_users`

### 8.4 UI Tests (Manual)

1. Navigate to Sync Assistant from dropdown
2. Fetch categories from Firefly
3. Share a category with another user
4. Log in as other user, see shared category
5. Import shared category into second Firefly
6. Verify category exists in second Firefly

---

## 9. Risks & Mitigations

### 9.1 Data Leakage
| Risk | Mitigation |
|------|------------|
| User sees others' private records | All queries explicitly filter by ownership or shares |
| User list exposes all users | Only show users with Firefly tokens configured |
| Shared data includes sensitive info | Pool records only store entity metadata, not financial data |

### 9.2 Cross-Tenant Access
| Risk | Mitigation |
|------|------------|
| User A accesses User B's Firefly | Each user uses their own token; no token sharing |
| Bulk import uses wrong token | Import endpoint uses `request.user.profile.firefly_token` explicitly |

### 9.3 Token Misuse
| Risk | Mitigation |
|------|------------|
| Token stored insecurely | Django model field, database-level encryption available |
| Token logged accidentally | FireflyClient uses redacted logging |
| Token shared between users | Each user has their own token in UserProfile |

### 9.4 Duplicate Creation
| Risk | Mitigation |
|------|------------|
| Same entity imported twice | Fingerprint check before create |
| Race condition on import | Unique constraint on fingerprint; catch IntegrityError |
| Stale fingerprint match | Preview endpoint checks immediately before import button enabled |

### 9.5 Stale Pool Data
| Risk | Mitigation |
|------|------------|
| Pool shows deleted entities | `updated_at` timestamp; UI shows staleness indicator after 24h |
| Manual refresh confusion | Clear "Last fetched: X" timestamp per entity type |

### 9.6 Performance
| Risk | Mitigation |
|------|------------|
| Large Firefly instance | Pagination on fetch; streaming JSON response |
| Many shared records | Indexed queries on `entity_type`, `fingerprint` |
| Slow import | Background task with progress indicator (future enhancement) |

---

## 10. Definition of Done

- [ ] Django migrations created and applied
- [ ] All API endpoints implemented with authentication
- [ ] Authorization enforced on all endpoints (ownership/sharing checks)
- [ ] FireflyClient extended with `list_tags()` and `list_piggy_banks()`
- [ ] Fingerprint functions implemented and tested
- [ ] UI page created with all four entity cards
- [ ] Fetch, share, import flows working end-to-end
- [ ] Audit trail logging on imports
- [ ] Unit tests for fingerprint logic
- [ ] Integration tests for all endpoints
- [ ] Security tests for auth/authz
- [ ] Manual UI verification completed
- [ ] Menu entry visible in user dropdown
- [ ] Error handling for missing tokens, connection failures
- [ ] Documentation updated (if any user-facing docs exist)

---

## Appendix A: Firefly API Reference

### Categories
```
GET /api/v1/categories?page=N
Response: { data: [{ id, attributes: { name, notes } }], meta: { pagination } }

POST /api/v1/categories
Body: { name: str, notes?: str }
Response: { data: { id, attributes: {...} } }
```

### Tags
```
GET /api/v1/tags?page=N
Response: { data: [{ id, attributes: { tag, description } }], meta: { pagination } }

POST /api/v1/tags
Body: { tag: str, description?: str }
Response: { data: { id, attributes: {...} } }
```

### Accounts
```
GET /api/v1/accounts?type=X&page=N
Response: { data: [{ id, attributes: { name, type, currency_code, ... } }], meta: { pagination } }

POST /api/v1/accounts
Body: { name: str, type: str, currency_code: str, ... }
Response: { data: { id, attributes: {...} } }
```

### Piggy Banks
```
GET /api/v1/piggy_banks?page=N
Response: { data: [{ id, attributes: { name, target_amount, account_id, ... } }], meta: { pagination } }

POST /api/v1/piggy_banks
Body: { name: str, account_id: int, target_amount?: str, ... }
Response: { data: { id, attributes: {...} } }
```
