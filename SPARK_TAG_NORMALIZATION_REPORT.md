# Spark Tag Normalization Implementation Report

**Date:** January 7, 2026  
**Issue:** Firefly tag payload variations causing transaction parsing errors  
**Status:** ✅ Fixed and verified

---

## Problem Statement

The Spark reconciliation pipeline was failing when parsing Firefly transactions because the `tags` field in the Firefly API response can have multiple formats:

- `list[str]`: `["groceries", "rent"]`
- `list[dict]`: `[{"tag": "groceries"}, {"tag": "rent"}]`
- Mixed: `[{"tag": "groceries"}, "rent", null]`
- `None`: absence of tags

The original code assumed only `list[dict]` format:
```python
tags=[t.get("tag") for t in tx.get("tags", []) if t.get("tag")]
```

This failed with `AttributeError: 'str' object has no attribute 'get'` when Firefly returned `list[str]`.

---

## Solution

### 1. SSOT Normalizer Function

Added `_normalize_tags()` function in [src/paperless_firefly/firefly_client/client.py](src/paperless_firefly/firefly_client/client.py) as the single source of truth for tag normalization:

```python
def _normalize_tags(raw: object) -> list[str] | None:
    """Normalize Firefly tags payload to list[str] or None (SSOT)."""
```

**Behavior:**
| Input | Output |
|-------|--------|
| `None` | `None` |
| `[]` | `None` (empty treated as absent) |
| `["groceries", "rent"]` | `["groceries", "rent"]` |
| `[{"tag": "groceries"}]` | `["groceries"]` |
| `[{"name": "groceries"}]` | `["groceries"]` (alternate key) |
| Mixed list | Extracts strings, skips unknowns |
| `dict`, `int`, `str` | Raises `FireflyAPIError` |

**Non-silent behavior:** Unknown dict items are logged once at DEBUG level. Completely invalid types (dict, int, bare string) raise `FireflyAPIError` with actionable message.

### 2. Integration Point

Updated `list_transactions()` to use the normalizer:
```python
tags=_normalize_tags(tx.get("tags")),
```

---

## Test Coverage

Added 11 new tests in [tests/test_clients.py](tests/test_clients.py) class `TestNormalizeTags`:

| Test Case | Description |
|-----------|-------------|
| `test_case_a_list_of_strings` | `["groceries", "rent"]` → same list |
| `test_case_b_list_of_dicts_with_tag_key` | `[{"tag": "groceries"}]` → `["groceries"]` |
| `test_case_c_mixed_list` | Mixed formats → extracts strings safely |
| `test_case_d_none_returns_none` | `None` → `None` |
| `test_empty_list_returns_none` | `[]` → `None` |
| `test_list_with_only_none_returns_none` | `[None, None]` → `None` |
| `test_dict_with_name_key` | Alternate "name" key works |
| `test_dict_with_tag_preferred_over_name` | "tag" takes precedence |
| `test_empty_strings_filtered` | Empty strings removed |
| `test_unexpected_type_raises_error` | Invalid types raise error |
| `test_complex_mixed_scenario` | All variations combined |

---

## Quality Gates

All gates passing:

```
✅ black --check: 65 files unchanged
✅ isort --check: No issues
✅ ruff check: All checks passed
✅ pytest: 357 passed
```

---

## Documentation Updates

Updated [DOCKER_QUICK_START.md](DOCKER_QUICK_START.md):
- Added "Service Architecture" section explaining one-shot vs continuous services
- Added warning about using `docker compose run --rm` for worker commands
- Clarified that workers (reconcile, extract, import) exit after completing

---

## Files Changed

| File | Change |
|------|--------|
| `src/paperless_firefly/firefly_client/client.py` | Added `_normalize_tags()` SSOT function, updated `list_transactions()` |
| `tests/test_clients.py` | Added `TestNormalizeTags` class with 11 tests |
| `DOCKER_QUICK_START.md` | Added service architecture documentation |

---

## Verification

To verify the fix end-to-end:

```bash
# Run reconciliation
docker compose run --rm paperless-firefly reconcile

# Expected: No tag parsing errors, transactions cached successfully
```

---

## SSOT/DRY Compliance

- **SSOT:** `_normalize_tags()` is the single parsing point for all Firefly tag data
- **DRY:** Tag normalization logic defined once, used everywhere
- **Deterministic:** Same input always produces same output (no randomness)
- **Fail-loud:** Invalid types raise explicit errors, not silent fallbacks
