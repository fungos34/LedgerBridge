# Root Cause Analysis: 500 Error on `/unified-review/paperless/<id>/`

## Summary

The 500 error occurs because of inconsistent key naming in the suggestion dictionaries passed to `unified_review_detail.html`. The view code creates suggestions with `"destination"` as the key, but the template expects `"destination_account"`.

## Error Details

```
django.template.base.VariableDoesNotExist: 
  Failed lookup for key [destination_account] in {...}
```

## Root Cause

### Location 1: `_get_top_match_suggestions()` in `views.py` (line ~3447)

For Firefly suggestions, the code creates:
```python
suggestions.append({
    "id": match.firefly_id,
    "type": "firefly",
    "score": round(match.total_score * 100, 1),
    "amount": cached_tx.get("amount"),
    "date": cached_tx.get("date"),
    "description": cached_tx.get("description"),
    "destination": cached_tx.get("destination_account"),  # ← WRONG KEY
    "reasons": match.reasons,
})
```

### Location 2: Template (`unified_review_detail.html`, lines 545-546)

```django
{% if suggestion.vendor or suggestion.destination_account %}
• {{ suggestion.vendor|default:suggestion.destination_account }}
{% endif %}
```

The template looks for `destination_account` but the dict has `destination`.

### Location 3: Main record data for Paperless (views.py line ~3739)

The `record_data` for Paperless documents uses:
```python
"vendor": proposal.get("destination_account"),
```

But does NOT provide `destination_account` as a separate key, only `vendor`.

For Firefly records, it includes both:
```python
"vendor": tx.get("destination_account") or tx.get("source_account"),
"destination_account": tx.get("destination_account"),
```

## Fix Strategy

### 1. Normalize Suggestion Schema (SSOT)

Create a consistent suggestion structure:
```python
{
    "id": int,
    "type": str,  # "firefly" or "paperless"
    "score": float,
    "amount": str | None,
    "date": str | None,
    "description": str | None,
    "vendor": str | None,           # Primary display field
    "destination_account": str | None,  # Keep for backward compat
    "source_account": str | None,
    "title": str | None,  # For paperless docs
    "reasons": list[str],
}
```

### 2. Normalize Main Record Schema

Ensure `record_data` always includes:
```python
"vendor": str | None,
"source_account": str | None,
"destination_account": str | None,  # Even if None for withdrawals
```

### 3. Template Safety

Use defensive template syntax:
```django
{{ suggestion.vendor|default:suggestion.destination_account|default:"—" }}
```

## Files to Modify

1. `views.py` - `_get_top_match_suggestions()`: Add `destination_account` key
2. `views.py` - `unified_review_detail()`: Ensure paperless record includes `destination_account`
3. `unified_review_detail.html`: Update to use safe defaults everywhere

## Test Cases Required

1. Paperless document with no `destination_account` in proposal → renders without 500
2. Firefly transaction with null `destination_account` → renders with "—"
3. Paperless document with valid extraction data → renders correctly
4. Firefly transaction with valid data → renders correctly

## Related Issues

- Landing page has duplicate cards: "Review Queue" and "Reconciliation" 
- Both redirect to different URLs but should be consolidated

---

## Resolution (Completed)

### Changes Implemented

#### 1. views.py - `_get_top_match_suggestions()` (line ~3447)

Fixed Firefly suggestions to use `destination_account` instead of `destination`:

```python
# Before
"destination": cached_tx.get("destination_account"),

# After
"vendor": cached_tx.get("destination_account") or cached_tx.get("source_account"),
"destination_account": cached_tx.get("destination_account"),
```

Also added `vendor` key for consistent display.

#### 2. views.py - Paperless suggestions (line ~3540)

Added `destination_account` key to Paperless document suggestions:

```python
"destination_account": proposal.get("destination_account"),
```

#### 3. views.py - `unified_review_detail()` (line ~3730)

Added `destination_account` to Paperless record_data:

```python
"destination_account": proposal.get("destination_account"),
```

#### 4. unified_review_detail.html (line ~394, 545-547)

Added safe defaults:

```django
<!-- Hidden input -->
value="{{ record.vendor|default:record.destination_account|default:'' }}"

<!-- Suggestion meta -->
{{ suggestion.vendor|default:suggestion.destination_account|default:"—" }}
```

### Test Coverage

All tests pass in `tests/test_unified_review.py`:

- `TestUnifiedReviewContext::test_record_data_always_has_destination_account_key`
- `TestUnifiedReviewContext::test_suggestions_have_consistent_schema`
- `TestUnifiedReviewContext::test_template_safe_access_pattern`
- `TestUnifiedReviewDetailView::test_paperless_record_without_destination_returns_200`
- `TestUnifiedReviewDetailView::test_firefly_record_context_building`
- `TestSuggestionNormalization::test_firefly_suggestion_has_destination_account_key`
- `TestSuggestionNormalization::test_paperless_suggestion_has_destination_account_key`
- `TestLandingPageDuplication::test_only_one_review_card_exists`
- `TestAdminModelsRegistered::test_linkage_model_exists`
- `TestAdminModelsRegistered::test_linkage_admin_registered`

### Additional Fixes

1. **Duplicate Navigation**: Removed separate "Review Queue" and "Reconciliation" cards from landing.html, replaced with single "Review & Link" card.

2. **Linkage Admin**: Added `Linkage` model to `models.py` and `LinkageAdmin` to `admin.py` with proper list_display, filters, search, and colored status badges.

