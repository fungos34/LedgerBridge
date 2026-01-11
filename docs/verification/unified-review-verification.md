# Unified Review Fix Verification Checklist

**Date**: 2024-12-XX  
**Issue**: 500 error on `/unified-review/paperless/<id>/`, duplicate navigation, missing admin model

## Pre-Flight Checks

- [x] Root cause documented in `docs/debugging/unified-review-500.md`
- [x] Regression tests created in `tests/test_unified_review.py`
- [x] All 10 unified review tests pass
- [x] Formatting passes (black)
- [x] Import sorting passes (isort)
- [x] Linting passes (ruff)

## Core Fix Verification

### Issue 1: 500 Error on Unified Review Detail

**Root Cause**: Template expected `destination_account` key but code provided `destination` or omitted the key entirely.

**Files Changed**:
- `src/paperless_firefly/review/web/views.py`
- `src/paperless_firefly/review/web/templates/review/unified_review_detail.html`

**Verification Steps**:

1. **View Context (views.py)**:
   - [ ] Navigate to `/unified-review/paperless/<id>/` for a Paperless document
   - [ ] Verify page loads without 500 error
   - [ ] Check that "Destination Account" field is populated or shows empty (not crashing)

2. **Template Safe Access (unified_review_detail.html)**:
   - [ ] Verify hidden input for `destination_account` uses `|default:''`
   - [ ] Verify suggestion meta fields use `|default:"—"`

3. **Suggestions Normalization**:
   - [ ] View suggestions for a Paperless record
   - [ ] Confirm each suggestion shows vendor AND destination account info
   - [ ] View suggestions for a Firefly record
   - [ ] Confirm each suggestion shows consistent schema

**Test Coverage**:
```bash
pytest tests/test_unified_review.py -v -k "destination_account or suggestion"
```

### Issue 2: Duplicate Navigation Cards

**Root Cause**: Landing page had separate "Review Queue" and "Reconciliation" cards causing user confusion.

**Files Changed**:
- `src/paperless_firefly/review/web/templates/review/landing.html`

**Verification Steps**:

1. **Landing Page**:
   - [ ] Navigate to `/` (landing page)
   - [ ] Verify only ONE "Review & Link" card exists
   - [ ] Verify NO "Review Queue" service card exists (tooltip references are OK)
   - [ ] Verify NO "Reconciliation" service card exists
   - [ ] Verify "Review & Link" card links to `/unified-review/`

**Test Coverage**:
```bash
pytest tests/test_unified_review.py -v -k "landing"
```

### Issue 3: Missing Linkage Model in Admin

**Root Cause**: The `Linkage` model from migration 008 was not exposed in Django admin.

**Files Changed**:
- `src/paperless_firefly/review/web/models.py` (added `Linkage` model)
- `src/paperless_firefly/review/web/admin.py` (added `LinkageAdmin`)

**Verification Steps**:

1. **Django Admin**:
   - [ ] Navigate to `/admin/`
   - [ ] Verify "Linkage" appears in the model list under "Web" app
   - [ ] Click on "Linkage" to view the list
   - [ ] Verify list_display shows: id, extraction_id, document_id, firefly_id, link_type, confidence, linked_at
   - [ ] Verify colored badges for link_type (MATCHED=green, CONFIRMED=blue, PENDING=yellow, REJECTED=red)
   - [ ] Verify search works on document_id, firefly_id, linked_by
   - [ ] Verify filters work for link_type, linked_by

**Test Coverage**:
```bash
pytest tests/test_unified_review.py -v -k "admin or linkage"
```

## Test Summary

```bash
# Run all unified review tests
pytest tests/test_unified_review.py -v

# Expected: 10 passed
```

## Code Quality Gates

```bash
# Formatting
black src tests --check

# Import sorting
isort src tests --check-only

# Linting
ruff check src tests

# Expected: All pass
```

## Sign-Off

- [ ] All manual verification steps completed
- [ ] All automated tests pass
- [ ] No regressions in existing functionality
- [ ] Documentation updated

**Verified by**: ________________  
**Date**: ________________
