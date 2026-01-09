"""
Views for the review web interface.
"""

import json
import logging
import threading
import traceback
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ...firefly_client import FireflyClient
from ...schemas.dedupe import generate_external_id
from ...schemas.finance_extraction import FinanceExtraction
from ...state_store import ExtractionRecord, StateStore
from ..workflow import ReviewDecision

logger = logging.getLogger(__name__)

# Global state for background jobs
_extraction_status = {
    "running": False,
    "progress": "",
    "result": None,
    "error": None,
    "traceback": None,  # Full traceback for debug mode
}

_import_status = {
    "running": False,
    "progress": "",
    "result": None,
    "error": None,
    "traceback": None,  # Full traceback for debug mode
}


def _is_debug_mode() -> bool:
    """Check if LedgerBridge debug mode is enabled."""
    return getattr(settings, "LEDGERBRIDGE_DEBUG", False)


def _get_store() -> StateStore:
    """Get the state store instance."""
    return StateStore(settings.STATE_DB_PATH)


def _get_paperless_session(request: HttpRequest | None = None) -> requests.Session:
    """Get a session configured for Paperless API."""
    session = requests.Session()

    # Try to get user-specific token if available
    token = settings.PAPERLESS_TOKEN
    if request and request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.paperless_token:
                token = profile.paperless_token
        except Exception:
            pass

    session.headers["Authorization"] = f"Token {token}"
    return session


def _get_firefly_client(request: HttpRequest | None = None) -> FireflyClient:
    """Get Firefly client, optionally using user-specific credentials."""
    base_url = settings.FIREFLY_BASE_URL
    token = settings.FIREFLY_TOKEN

    if request and request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.firefly_token:
                token = profile.firefly_token
            if profile.firefly_url:
                base_url = profile.firefly_url
        except Exception:
            pass

    return FireflyClient(base_url=base_url, token=token)


def _get_external_urls(user=None):
    """Get external URLs for browser links.

    Args:
        user: Optional Django user to get profile-specific URLs.

    Returns:
        Dict with external URLs (profile URLs override settings defaults).
    """
    syncthing_url = getattr(settings, "SYNCTHING_URL", "")
    importer_url = getattr(settings, "FIREFLY_IMPORTER_URL", "")

    # Check user profile for overrides
    if user and user.is_authenticated:
        try:
            profile = user.profile
            if profile.syncthing_url:
                syncthing_url = profile.syncthing_url
            if profile.importer_url:
                importer_url = profile.importer_url
        except Exception:
            pass  # User may not have profile

    return {
        "paperless_url": getattr(settings, "PAPERLESS_EXTERNAL_URL", settings.PAPERLESS_BASE_URL),
        "firefly_url": getattr(settings, "FIREFLY_EXTERNAL_URL", settings.FIREFLY_BASE_URL),
        "syncthing_url": syncthing_url,
        "firefly_importer_url": importer_url,
    }


# ============================================================================
# Landing Page & Authentication
# ============================================================================


def landing_page(request: HttpRequest) -> HttpResponse:
    """Landing page with links to all services."""
    if not request.user.is_authenticated:
        return redirect("login")

    store = _get_store()
    stats = store.get_stats()

    # Get pending extractions count
    pending = store.get_extractions_for_review()
    pending_count = len(pending)

    # Get ready-to-import count
    ready_to_import = _get_ready_to_import_count(store)

    context = {
        **_get_external_urls(request.user if hasattr(request, "user") else None),
        "stats": stats,
        "pending_count": pending_count,
        "ready_to_import": ready_to_import,
    }
    return render(request, "review/landing.html", context)


def register_user(request: HttpRequest) -> HttpResponse:
    """User registration page."""
    from django.contrib.auth.models import User

    from .models import UserProfile

    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        password_confirm = request.POST.get("password_confirm", "")

        errors = []

        # Validation
        if not username:
            errors.append("Username is required")
        elif User.objects.filter(username=username).exists():
            errors.append("Username already taken")

        if not email:
            errors.append("Email is required")
        elif User.objects.filter(email=email).exists():
            errors.append("Email already registered")

        if not password:
            errors.append("Password is required")
        elif len(password) < 8:
            errors.append("Password must be at least 8 characters")
        elif password != password_confirm:
            errors.append("Passwords do not match")

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(
                request,
                "review/register.html",
                {"username": username, "email": email},
            )

        # Create user
        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            # Profile is created automatically via signal
            UserProfile.objects.get_or_create(user=user)

            # Log in the new user
            login(request, user)
            messages.success(
                request,
                f"Welcome {username}! Please configure your API tokens in Settings.",
            )
            return redirect("settings")
        except Exception as e:
            messages.error(request, f"Registration failed: {e}")
            return render(
                request,
                "review/register.html",
                {"username": username, "email": email},
            )

    return render(request, "review/register.html")


@login_required
def change_password(request: HttpRequest) -> HttpResponse:
    """Change password page."""
    from django.contrib.auth import update_session_auth_hash

    if request.method == "POST":
        current_password = request.POST.get("current_password", "")
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        errors = []

        # Validate current password
        if not request.user.check_password(current_password):
            errors.append("Current password is incorrect")

        # Validate new password
        if not new_password:
            errors.append("New password is required")
        elif len(new_password) < 8:
            errors.append("New password must be at least 8 characters")
        elif new_password != confirm_password:
            errors.append("New passwords do not match")

        if errors:
            for error in errors:
                messages.error(request, error)
            return render(request, "review/change_password.html")

        # Update password
        request.user.set_password(new_password)
        request.user.save()

        # Keep user logged in after password change
        update_session_auth_hash(request, request.user)

        messages.success(request, "Password changed successfully!")
        return redirect("home")

    return render(request, "review/change_password.html")


@login_required
def user_settings(request: HttpRequest) -> HttpResponse:
    """User settings page for configuring API tokens."""
    from .models import UserProfile

    # Ensure profile exists
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        # Update profile
        profile.paperless_token = request.POST.get("paperless_token", "")
        profile.paperless_url = request.POST.get("paperless_url", "")
        profile.paperless_filter_tags = request.POST.get("paperless_filter_tags", "finance/inbox")
        profile.firefly_token = request.POST.get("firefly_token", "")
        profile.firefly_url = request.POST.get("firefly_url", "")
        profile.default_source_account = request.POST.get(
            "default_source_account", "Checking Account"
        )

        # External links (optional)
        profile.syncthing_url = request.POST.get("syncthing_url", "")
        profile.importer_url = request.POST.get("importer_url", "")

        try:
            profile.auto_import_threshold = float(request.POST.get("auto_import_threshold", 0.85))
            profile.review_threshold = float(request.POST.get("review_threshold", 0.60))
        except ValueError:
            pass

        profile.save()
        messages.success(request, "Settings saved successfully!")
        return redirect("settings")

    # Test connections if tokens are set
    paperless_status = None
    firefly_status = None
    firefly_accounts = []

    if profile.paperless_token or settings.PAPERLESS_TOKEN:
        try:
            session = _get_paperless_session(request)
            url = settings.PAPERLESS_BASE_URL
            if profile.paperless_url:
                url = profile.paperless_url
            resp = session.get(f"{url}/api/", timeout=5)
            paperless_status = "connected" if resp.ok else "error"
        except Exception as e:
            paperless_status = f"error: {e}"

    if profile.firefly_token or settings.FIREFLY_TOKEN:
        try:
            client = _get_firefly_client(request)
            # Only test connection, don't fetch accounts on page load (too slow)
            if client.test_connection():
                firefly_status = "connected"
                # Fetch accounts with a timeout to prevent hanging
                try:
                    firefly_accounts = client.list_accounts("asset")
                except Exception as accounts_err:
                    logger.warning("Failed to fetch Firefly accounts: %s", accounts_err)
                    # Connection is OK, just couldn't fetch accounts
            else:
                firefly_status = "error"
        except Exception as e:
            firefly_status = f"error: {e}"

    context = {
        "profile": profile,
        "paperless_status": paperless_status,
        "firefly_status": firefly_status,
        "firefly_accounts": firefly_accounts,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/settings.html", context)


# ============================================================================
# Review Queue
# ============================================================================


@login_required
def review_list(request: HttpRequest) -> HttpResponse:
    """List all extractions pending review."""
    store = _get_store()
    pending = store.get_extractions_for_review()

    # Also get stats
    stats = store.get_stats()

    # Parse extractions for display
    extractions = []
    for record in pending:
        try:
            data = json.loads(record.extraction_json)
            extraction = FinanceExtraction.from_dict(data)
            extractions.append(
                {
                    "id": record.id,
                    "document_id": record.document_id,
                    "external_id": record.external_id,
                    "title": extraction.paperless_title,
                    "amount": extraction.proposal.amount,
                    "currency": extraction.proposal.currency,
                    "date": extraction.proposal.date,
                    "vendor": extraction.proposal.destination_account,
                    "confidence": record.overall_confidence * 100,
                    "review_state": record.review_state,
                    "created_at": record.created_at,
                }
            )
        except Exception as e:
            logger.error(f"Error parsing extraction {record.id}: {e}")

    context = {
        "extractions": extractions,
        "stats": stats,
        "extraction_status": _extraction_status,
        "debug_mode": _is_debug_mode(),
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/list.html", context)


@login_required
def extraction_archive(request: HttpRequest) -> HttpResponse:
    """
    Show archive of processed extractions (imported, rejected).

    Allows resetting extractions for re-review or reimport.
    """
    store = _get_store()
    processed = store.get_processed_extractions()

    # Parse extractions for display
    items = []
    for row in processed:
        try:
            data = json.loads(row["extraction_json"])
            extraction = FinanceExtraction.from_dict(data)

            # Determine status for display
            status = "unknown"
            status_class = "secondary"
            if row["import_status"] == "IMPORTED":
                status = "Imported"
                status_class = "success"
            elif row["import_status"] == "FAILED":
                status = "Failed"
                status_class = "danger"
            elif row["review_decision"] == "REJECTED":
                status = "Rejected"
                status_class = "warning"
            elif row["review_decision"] in ("ACCEPTED", "EDITED"):
                status = "Approved (pending import)"
                status_class = "info"

            items.append(
                {
                    "id": row["id"],
                    "document_id": row["document_id"],
                    "external_id": row["external_id"],
                    "title": extraction.paperless_title,
                    "amount": extraction.proposal.amount,
                    "currency": extraction.proposal.currency,
                    "date": extraction.proposal.date,
                    "vendor": extraction.proposal.destination_account,
                    "status": status,
                    "status_class": status_class,
                    "review_decision": row["review_decision"],
                    "import_status": row["import_status"],
                    "firefly_id": row["firefly_id"],
                    "import_error": row["import_error"],
                    "reviewed_at": row["reviewed_at"],
                    "created_at": row["created_at"],
                }
            )
        except Exception as e:
            logger.error(f"Error parsing extraction {row['id']}: {e}")

    context = {
        "items": items,
        "debug_mode": _is_debug_mode(),
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/archive.html", context)


def _get_llm_suggestion_for_document(store: StateStore, document_id: int) -> dict | None:
    """Get the most recent LLM suggestion for a document.

    Per SPARK_EVALUATION_REPORT.md 6.6: LLM suggestions shown as 'AI suggestion' badge.

    Returns:
        Dict with 'category', 'confidence', 'run_id' if LLM was used, else None.
    """
    runs = store.get_interpretation_runs(document_id)
    for run in runs:
        # Find runs with LLM results (most recent first)
        if run.get("llm_result") and run.get("suggested_category"):
            try:
                llm_data = (
                    json.loads(run["llm_result"])
                    if isinstance(run["llm_result"], str)
                    else run["llm_result"]
                )
                return {
                    "category": run["suggested_category"],
                    "confidence": llm_data.get("confidence", 0),
                    "run_id": run["id"],
                    "timestamp": run["run_timestamp"],
                }
            except (json.JSONDecodeError, TypeError):
                continue
    return None


def _is_llm_globally_enabled() -> bool:
    """Check if LLM is globally enabled via config or environment.

    Per SPARK_EVALUATION_REPORT.md 6.7.1: Global opt-out via config.llm.enabled.
    Environment variable SPARK_LLM_ENABLED takes precedence.
    """
    import os
    from pathlib import Path

    from django.conf import settings

    # Check environment variable first (takes precedence)
    env_value = os.environ.get("SPARK_LLM_ENABLED", "").lower()
    if env_value == "true":
        return True
    elif env_value == "false":
        return False

    # Fall back to config file
    try:
        from ...config import load_config

        config_path = (
            Path(getattr(settings, "STATE_DB_PATH", "/app/data/state.db")).parent / "config.yaml"
        )
        if not config_path.exists():
            config_path = Path("/app/config/config.yaml")

        if config_path.exists():
            config = load_config(config_path)
            return config.llm.enabled
    except Exception:
        pass

    return False  # Default to disabled if config unavailable


@login_required
def review_detail(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Show single extraction for review with document preview."""
    store = _get_store()

    # Get the extraction record
    pending = store.get_extractions_for_review()
    record = None
    for r in pending:
        if r.id == extraction_id:
            record = r
            break

    if not record:
        # Check if it exists but was already reviewed
        conn = store._get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM extractions WHERE id = ?", (extraction_id,)
            ).fetchone()
            if row:
                record = ExtractionRecord(
                    id=row["id"],
                    document_id=row["document_id"],
                    external_id=row["external_id"],
                    extraction_json=row["extraction_json"],
                    overall_confidence=row["overall_confidence"],
                    review_state=row["review_state"],
                    review_decision=row["review_decision"],
                    reviewed_at=row["reviewed_at"],
                    created_at=row["created_at"],
                )
        finally:
            conn.close()

    if not record:
        return render(
            request, "review/not_found.html", {"extraction_id": extraction_id}, status=404
        )

    try:
        data = json.loads(record.extraction_json)
        extraction = FinanceExtraction.from_dict(data)
    except Exception as e:
        logger.error(f"Error parsing extraction {extraction_id}: {e}")
        return render(request, "review/error.html", {"error": str(e)}, status=500)

    # Get list of all pending for navigation
    all_pending = store.get_extractions_for_review()
    pending_ids = [r.id for r in all_pending]
    current_idx = pending_ids.index(extraction_id) if extraction_id in pending_ids else -1
    prev_id = pending_ids[current_idx - 1] if current_idx > 0 else None
    next_id = pending_ids[current_idx + 1] if current_idx < len(pending_ids) - 1 else None

    # Convert confidence scores to percentages for display
    confidence_pct = {
        "overall": extraction.confidence.overall * 100,
        "amount": extraction.confidence.amount * 100,
        "date": extraction.confidence.date * 100,
        "currency": extraction.confidence.currency * 100,
        "description": extraction.confidence.description * 100,
        "vendor": extraction.confidence.vendor * 100,
        "invoice_number": extraction.confidence.invoice_number * 100,
    }

    # Get Firefly accounts for dropdown
    firefly_accounts = []
    try:
        client = _get_firefly_client(request)
        firefly_accounts = client.list_accounts("asset")
    except Exception as e:
        logger.warning(f"Could not fetch Firefly accounts: {e}")

    # LLM context (Spark v1.0 - SPARK_EVALUATION_REPORT.md 6.6/6.7)
    llm_suggestion = _get_llm_suggestion_for_document(store, extraction.paperless_document_id)
    llm_globally_enabled = _is_llm_globally_enabled()

    # Get Firefly categories for dropdown
    firefly_categories = []
    firefly_categories_json = "[]"  # JSON serialized for JavaScript
    try:
        categories_raw = client.list_categories() if firefly_accounts else []
        firefly_categories = categories_raw
        # Serialize to JSON for JavaScript use in split transactions
        firefly_categories_json = json.dumps(
            [{"id": cat.id, "name": cat.name} for cat in categories_raw]
        )
    except Exception as e:
        logger.warning(f"Could not fetch Firefly categories: {e}")

    # Find potential matching Firefly transactions for this document
    # Per SPARK_EVALUATION_REPORT.md - attempt to match reviewed document to existing Firefly entries
    matching_transactions = []
    try:
        from ...config import Config
        from ...matching.engine import MatchingEngine

        # Build extraction dict from FinanceExtraction for the matching engine
        extraction_dict = {
            "amount": extraction.proposal.amount,
            "date": extraction.proposal.date.isoformat() if extraction.proposal.date else None,
            "vendor": extraction.proposal.vendor,
            "description": extraction.proposal.description,
            "correspondent": getattr(extraction.proposal, "correspondent", None),
        }

        # Get config for matching engine
        config = Config.load(settings.CONFIG_PATH if hasattr(settings, "CONFIG_PATH") else None)
        engine = MatchingEngine(store, config)

        # Find matches using the matching engine
        matches = engine.find_matches(
            document_id=extraction.paperless_document_id,
            extraction=extraction_dict,
            max_results=5,
        )

        # Enrich matches with transaction details from cache
        for m in matches:
            cached_tx = store.get_firefly_cache_entry(m.firefly_id)
            if cached_tx:
                matching_transactions.append(
                    {
                        "firefly_id": m.firefly_id,
                        "score": round(m.total_score * 100, 1),
                        "amount": cached_tx.get("amount"),
                        "date": cached_tx.get("date"),
                        "description": cached_tx.get("description"),
                        "destination": cached_tx.get("destination_account"),
                        "reasons": m.reasons,
                    }
                )
    except Exception as e:
        logger.warning(f"Could not find matching transactions: {e}")

    # Prepare line items for display (split transactions support)
    line_items_data = []
    for idx, item in enumerate(extraction.line_items):
        line_items_data.append(
            {
                "index": idx,
                "description": item.description,
                "amount": item.total
                or (item.quantity * item.unit_price if item.quantity and item.unit_price else None),
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "category": getattr(item, "category", None),  # Can be set per line item
            }
        )

    # Compute weighted category from line items (SSOT: workflow.compute_weighted_category)
    weighted_category = None
    if line_items_data:
        from ..workflow import compute_weighted_category

        weighted_category = compute_weighted_category(line_items_data)

    context = {
        "record": record,
        "extraction": extraction,
        "proposal": extraction.proposal,
        "confidence": confidence_pct,
        "provenance": extraction.provenance,
        "document_id": extraction.paperless_document_id,
        "prev_id": prev_id,
        "next_id": next_id,
        "pending_count": len(pending_ids),
        "current_position": current_idx + 1 if current_idx >= 0 else 0,
        "already_reviewed": record.review_decision is not None,
        "firefly_accounts": firefly_accounts,
        "firefly_categories": firefly_categories,
        "firefly_categories_json": firefly_categories_json,  # JSON for JS
        # Line items for split transactions
        "line_items": line_items_data,
        "has_line_items": len(line_items_data) > 0,
        "weighted_category": weighted_category,  # Computed from splits
        # Matching transactions
        "matching_transactions": matching_transactions,
        "has_matches": len(matching_transactions) > 0,
        # LLM context
        "llm_suggestion": llm_suggestion,
        "llm_globally_enabled": llm_globally_enabled,
        "llm_opt_out": record.llm_opt_out,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/detail.html", context)


# ============================================================================
# Extraction Actions
# ============================================================================


@login_required
@require_http_methods(["POST"])
def accept_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Accept extraction as-is."""
    store = _get_store()
    store.update_extraction_review(extraction_id, ReviewDecision.ACCEPTED.value)

    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


@login_required
@require_http_methods(["POST"])
def reject_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Reject extraction (won't be imported)."""
    store = _get_store()
    store.update_extraction_review(extraction_id, ReviewDecision.REJECTED.value)

    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


@login_required
@require_http_methods(["POST"])
def reset_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """
    Reset an extraction to allow re-review and re-import.

    This is used when:
    - A document was rejected but should now be imported
    - A document was imported but needs to be updated (reimport)
    - A document was unlisted then relisted
    - A reviewed item in import queue needs to be re-reviewed
    """
    store = _get_store()

    # Reset the extraction review decision
    if store.reset_extraction_for_review(extraction_id):
        # Also clear any failed import so it can be retried
        conn = store._get_connection()
        try:
            row = conn.execute(
                "SELECT external_id FROM extractions WHERE id = ?", (extraction_id,)
            ).fetchone()
            if row:
                external_id = row["external_id"]
                # Delete import record so it can be re-imported
                store.delete_import(external_id)
        finally:
            conn.close()

        messages.success(request, "Extraction has been reset for re-review.")
    else:
        messages.error(request, "Extraction not found.")

    # Redirect back to referring page (import queue or review list)
    next_url = (
        request.POST.get("next") or request.GET.get("next") or request.META.get("HTTP_REFERER")
    )
    if next_url and ("import-queue" in next_url or "archive" in next_url):
        return redirect(next_url)
    return redirect("list")


@login_required
@require_http_methods(["POST"])
def delete_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """
    Permanently delete an extraction from the database.

    Only allowed for rejected extractions that haven't been imported to Firefly.
    The document in Paperless is not affected.
    After deletion, the document can be re-extracted to create a new extraction.
    """
    store = _get_store()

    conn = store._get_connection()
    try:
        # Get extraction details
        row = conn.execute(
            """
            SELECT e.id, e.external_id, e.review_decision, e.document_id,
                   i.firefly_id
            FROM extractions e
            LEFT JOIN imports i ON e.external_id = i.external_id
            WHERE e.id = ?
            """,
            (extraction_id,),
        ).fetchone()

        if not row:
            messages.error(request, "Extraction not found.")
            return redirect("archive")

        # Safety check: only delete rejected extractions that aren't imported
        if row["review_decision"] != "REJECTED":
            messages.error(
                request,
                "Only rejected extractions can be deleted. Reset the extraction first if needed.",
            )
            return redirect("archive")

        if row["firefly_id"]:
            messages.error(
                request,
                "Cannot delete: This extraction has already been imported to Firefly. "
                "Delete the transaction in Firefly first if needed.",
            )
            return redirect("archive")

        # Delete the extraction
        external_id = row["external_id"]
        document_id = row["document_id"]

        conn.execute("DELETE FROM extractions WHERE id = ?", (extraction_id,))

        # Also delete any import record (should not exist for rejected items, but just in case)
        if external_id:
            conn.execute("DELETE FROM imports WHERE external_id = ?", (external_id,))

        conn.commit()

        messages.success(
            request,
            f"Extraction deleted. Document #{document_id} can now be re-extracted.",
        )

    except Exception as e:
        logger.error(f"Failed to delete extraction {extraction_id}: {e}")
        messages.error(request, f"Failed to delete extraction: {e}")
    finally:
        conn.close()

    return redirect("archive")


@login_required
@require_http_methods(["POST"])
def skip_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Skip extraction for now."""
    store = _get_store()

    pending = store.get_extractions_for_review()
    pending_ids = [r.id for r in pending]

    if extraction_id in pending_ids:
        current_idx = pending_ids.index(extraction_id)
        if current_idx < len(pending_ids) - 1:
            return redirect("detail", extraction_id=pending_ids[current_idx + 1])

    return redirect("list")


@login_required
@require_http_methods(["POST"])
def save_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Save edited extraction and accept it."""
    store = _get_store()

    conn = store._get_connection()
    try:
        row = conn.execute("SELECT * FROM extractions WHERE id = ?", (extraction_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return JsonResponse({"error": "Extraction not found"}, status=404)

    try:
        data = json.loads(row["extraction_json"])
        extraction = FinanceExtraction.from_dict(data)
    except Exception as e:
        return JsonResponse({"error": f"Failed to parse extraction: {e}"}, status=500)

    # Apply edits from form
    proposal = extraction.proposal
    changes = []

    # Amount
    new_amount = request.POST.get("amount")
    if new_amount:
        try:
            new_amount_decimal = Decimal(new_amount.replace(",", "."))
            if new_amount_decimal != proposal.amount:
                proposal.amount = new_amount_decimal
                changes.append("amount")
        except InvalidOperation:
            pass

    # Currency
    new_currency = request.POST.get("currency")
    if new_currency and new_currency.upper() != proposal.currency:
        proposal.currency = new_currency.upper()
        changes.append("currency")

    # Date
    new_date = request.POST.get("date")
    if new_date and new_date != proposal.date:
        proposal.date = new_date
        changes.append("date")

    # Description
    new_description = request.POST.get("description")
    if new_description and new_description != proposal.description:
        proposal.description = new_description
        changes.append("description")

    # Destination account (vendor)
    new_dest = request.POST.get("destination_account")
    if new_dest != proposal.destination_account:
        proposal.destination_account = new_dest if new_dest else None
        changes.append("destination_account")

    # Source account
    new_source = request.POST.get("source_account")
    if new_source != proposal.source_account:
        proposal.source_account = new_source if new_source else None
        changes.append("source_account")

    # Category
    new_category = request.POST.get("category")
    if new_category != proposal.category:
        proposal.category = new_category if new_category else None
        changes.append("category")

    # Invoice number
    new_invoice = request.POST.get("invoice_number")
    if new_invoice != proposal.invoice_number:
        proposal.invoice_number = new_invoice if new_invoice else None
        changes.append("invoice_number")

    # Transaction type
    new_type = request.POST.get("transaction_type")
    if new_type:
        from ...schemas.finance_extraction import TransactionType

        try:
            new_type_enum = TransactionType(new_type.upper())
            if new_type_enum != proposal.transaction_type:
                proposal.transaction_type = new_type_enum
                changes.append("transaction_type")
        except ValueError:
            pass

    # Handle split transactions (line items)
    line_items_json = request.POST.get("line_items_json")
    if line_items_json:
        try:
            from ...schemas.finance_extraction import LineItem

            line_items_data = json.loads(line_items_json)
            if line_items_data and isinstance(line_items_data, list):
                # Convert to LineItem objects
                new_line_items = []
                for idx, item in enumerate(line_items_data):
                    line_item = LineItem(
                        description=item.get("description", ""),
                        quantity=Decimal("1"),  # Default for split bookings
                        unit_price=Decimal(str(item.get("amount", 0))),
                        total=Decimal(str(item.get("amount", 0))),
                        position=idx + 1,
                        category=item.get("category"),  # Category for split transaction
                    )
                    new_line_items.append(line_item)

                # Replace extraction line items
                extraction.line_items = new_line_items
                changes.append("line_items")

                # Validate: sum of line items should equal total amount
                line_total = sum(item.total for item in new_line_items)
                if abs(line_total - proposal.amount) > Decimal("0.01"):
                    logger.warning(
                        f"Split transaction total ({line_total}) differs from "
                        f"main amount ({proposal.amount}) by {abs(line_total - proposal.amount)}"
                    )
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse line_items_json: {e}")

    # Regenerate external_id if critical fields changed
    if "amount" in changes or "date" in changes:
        proposal.external_id = generate_external_id(
            document_id=extraction.paperless_document_id,
            source_hash=extraction.source_hash,
            amount=proposal.amount,
            date=proposal.date,
        )
        changes.append("external_id")

    decision = ReviewDecision.EDITED if changes else ReviewDecision.ACCEPTED

    updated_json = json.dumps(extraction.to_dict())
    store.update_extraction_review(extraction_id, decision.value, updated_json)

    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


# ============================================================================
# LLM Control Actions (Phase 6-7 - SPARK_EVALUATION_REPORT.md 6.7/6.8)
# ============================================================================


@login_required
@require_http_methods(["POST"])
def toggle_llm_opt_out(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Toggle LLM opt-out for a specific extraction.

    Per SPARK_EVALUATION_REPORT.md 6.7.2: Per-document opt-out support.
    UI Toggle: "Use AI suggestions" checkbox.

    Accepts JSON body with { "opt_out": true/false } and returns JSON response.
    """
    store = _get_store()

    # Parse the JSON body to get the opt_out value
    try:
        body = json.loads(request.body) if request.body else {}
        new_opt_out = body.get("opt_out", None)
    except json.JSONDecodeError:
        new_opt_out = None

    # Get current state
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT llm_opt_out FROM extractions WHERE id = ?", (extraction_id,)
        ).fetchone()
        if not row:
            return JsonResponse({"success": False, "error": "Extraction not found"}, status=404)

        current_opt_out = bool(row["llm_opt_out"])
    finally:
        conn.close()

    # If opt_out was provided in the body, use it; otherwise toggle
    if new_opt_out is None:
        new_opt_out = not current_opt_out

    if store.update_extraction_llm_opt_out(extraction_id, new_opt_out):
        if new_opt_out:
            message = "AI suggestions disabled for this document"
        else:
            message = "AI suggestions enabled for this document"
        return JsonResponse({"success": True, "opt_out": new_opt_out, "message": message})
    else:
        return JsonResponse(
            {"success": False, "error": "Failed to update LLM settings"}, status=500
        )


@login_required
@require_http_methods(["POST"])
def rerun_interpretation(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Re-run interpretation for a document with SparkAI.

    Per SPARK_EVALUATION_REPORT.md 6.8: Rescheduling / Re-Running Interpretation.
    - Actually invokes SparkAI service to get new category suggestion
    - Creates a new InterpretationRun record with the LLM result
    - Preserves history of all runs

    Returns JSON response for AJAX calls (with X-Requested-With header or Accept: application/json).
    """
    import time
    from pathlib import Path

    store = _get_store()

    # Detect if this is an AJAX request
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in request.headers.get("Accept", "")
        or request.content_type == "application/json"
    )

    # Get extraction details (including the full extraction JSON)
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT id, document_id, external_id, extraction_json FROM extractions WHERE id = ?",
            (extraction_id,),
        ).fetchone()
        if not row:
            if is_ajax:
                return JsonResponse({"success": False, "error": "Extraction not found"}, status=404)
            messages.error(request, "Extraction not found")
            return redirect("list")

        document_id = row["document_id"]
        external_id = row["external_id"]
        extraction_json = row["extraction_json"]
    finally:
        conn.close()

    # Get the reason if provided (from JSON body or form data)
    reason = "User requested"
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body)
            reason = body.get("reason", reason)
        except json.JSONDecodeError:
            pass
    else:
        reason = request.POST.get("reason", reason)

    start_time = time.time()
    llm_result = None
    suggested_category = None
    final_state = "COMPLETED"
    error_message = None

    try:
        # Check if LLM is enabled
        if not _is_llm_globally_enabled():
            final_state = "SKIPPED"
            error_message = "LLM is not enabled. Enable it in config or set SPARK_LLM_ENABLED=true"
        else:
            # Load config and create SparkAI service
            from ...config import load_config
            from ...spark_ai import SparkAIService

            config_path = (
                Path(getattr(settings, "STATE_DB_PATH", "/app/data/state.db")).parent
                / "config.yaml"
            )
            if not config_path.exists():
                config_path = Path("/app/config/config.yaml")

            if not config_path.exists():
                final_state = "ERROR"
                error_message = "Configuration file not found"
            else:
                config = load_config(config_path)

                # Get categories from Firefly
                firefly_client = _get_firefly_client()
                categories = []
                if firefly_client:
                    try:
                        cats = firefly_client.list_categories()
                        categories = [c.name for c in cats]
                    except Exception as e:
                        logger.warning(f"Could not fetch categories: {e}")

                if not categories:
                    final_state = "ERROR"
                    error_message = "No categories available from Firefly"
                else:
                    # Parse the extraction to get transaction details
                    extraction_data = json.loads(extraction_json) if extraction_json else {}
                    proposal = extraction_data.get("proposal", {})

                    amount = str(proposal.get("amount", "0"))
                    date = proposal.get("date", "")
                    vendor = proposal.get("vendor", "")
                    description = proposal.get("description", "")

                    # Create and call SparkAI service
                    ai_service = SparkAIService(store, config, categories)

                    suggestion = ai_service.suggest_category(
                        amount=amount,
                        date=date,
                        vendor=vendor,
                        description=description,
                        document_id=document_id,
                        use_cache=False,  # Force fresh call
                    )

                    if suggestion:
                        llm_result = suggestion.to_dict()
                        suggested_category = suggestion.category
                        final_state = "COMPLETED"
                    else:
                        final_state = "NO_SUGGESTION"
                        error_message = (
                            "LLM did not return a suggestion (may be opted out or unavailable)"
                        )

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Record the interpretation run (audit trail)
        store.create_interpretation_run(
            document_id=document_id,
            external_id=external_id,
            pipeline_version="1.0.0",
            inputs_summary={
                "action": "rerun_interpretation",
                "extraction_id": extraction_id,
                "reason": reason,
                "triggered_by": "user",
            },
            final_state=final_state,
            duration_ms=duration_ms,
            llm_result=llm_result,
            suggested_category=suggested_category,
            decision_source="USER_RERUN",
        )

        if is_ajax:
            response_data = {
                "success": final_state in ["COMPLETED", "NO_SUGGESTION"],
                "state": final_state,
                "message": f"AI interpretation completed. {f'Suggested: {suggested_category}' if suggested_category else error_message or 'No suggestion returned.'}",
            }
            if suggested_category:
                response_data["suggestion"] = {
                    "category": suggested_category,
                    "confidence": llm_result.get("confidence", 0) if llm_result else 0,
                }
            return JsonResponse(response_data)

        if suggested_category:
            messages.success(
                request,
                f"AI suggested category: {suggested_category}",
            )
        else:
            messages.warning(request, error_message or "AI did not return a suggestion")

    except Exception as e:
        logger.error(f"Error running AI interpretation for extraction {extraction_id}: {e}")
        duration_ms = int((time.time() - start_time) * 1000)

        # Record the failed run
        store.create_interpretation_run(
            document_id=document_id,
            external_id=external_id,
            pipeline_version="1.0.0",
            inputs_summary={
                "action": "rerun_interpretation",
                "extraction_id": extraction_id,
                "reason": reason,
                "triggered_by": "user",
                "error": str(e),
            },
            final_state="ERROR",
            duration_ms=duration_ms,
            decision_source="USER_RERUN",
        )

        if is_ajax:
            return JsonResponse({"success": False, "error": str(e)}, status=500)
        messages.error(request, f"AI interpretation failed: {e}")

    return redirect("detail", extraction_id=extraction_id)


# ============================================================================
# Import Queue
# ============================================================================


def _get_ready_to_import_count(store: StateStore) -> int:
    """Get count of extractions ready to import."""
    conn = store._get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt FROM extractions e
            LEFT JOIN imports i ON e.external_id = i.external_id
            WHERE i.id IS NULL
            AND (e.review_state = 'AUTO' OR e.review_decision IN ('ACCEPTED', 'EDITED'))
        """
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()


@login_required
def import_queue(request: HttpRequest) -> HttpResponse:
    """Show transactions ready to import to Firefly."""
    store = _get_store()
    ready_items = []
    failed_items = []
    recent_imports = []

    # Get data from database - keep connection open during access
    conn = store._get_connection()
    try:
        # Get extractions ready for import (no import record exists OR import failed)
        extraction_rows = conn.execute(
            """
            SELECT e.*, i.status as import_status, i.error_message as import_error
            FROM extractions e
            LEFT JOIN imports i ON e.external_id = i.external_id
            WHERE (i.id IS NULL OR i.status = 'FAILED')
            AND (e.review_state = 'AUTO' OR e.review_decision IN ('ACCEPTED', 'EDITED'))
            ORDER BY e.created_at DESC
        """
        ).fetchall()

        # Parse extractions while connection is still open
        for row in extraction_rows:
            try:
                data = json.loads(row["extraction_json"])
                extraction = FinanceExtraction.from_dict(data)
                item = {
                    "id": row["id"],
                    "document_id": row["document_id"],
                    "external_id": row["external_id"],
                    "title": extraction.paperless_title,
                    "amount": extraction.proposal.amount,
                    "currency": extraction.proposal.currency,
                    "date": extraction.proposal.date,
                    "vendor": extraction.proposal.destination_account,
                    "source_account": extraction.proposal.source_account or "Default",
                    "review_state": row["review_state"],
                    "review_decision": row["review_decision"],
                }
                # Separate failed imports from new items
                if row["import_status"] == "FAILED":
                    item["error_message"] = row["import_error"]
                    failed_items.append(item)
                else:
                    ready_items.append(item)
            except Exception as e:
                logger.error(f"Error parsing extraction {row['id']}: {e}")

        # Get recent SUCCESSFUL imports only (for history display)
        import_rows = conn.execute(
            """
            SELECT i.*, e.extraction_json
            FROM imports i
            LEFT JOIN extractions e ON i.external_id = e.external_id
            WHERE i.status = 'IMPORTED'
            ORDER BY i.created_at DESC LIMIT 20
        """
        ).fetchall()

        for irow in import_rows:
            # Try to get title from extraction
            title = None
            if irow["extraction_json"]:
                try:
                    ext_data = json.loads(irow["extraction_json"])
                    title = ext_data.get("paperless_title")
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

            recent_imports.append(
                {
                    "external_id": irow["external_id"],
                    "document_id": irow["document_id"],
                    "title": title,
                    "status": irow["status"],
                    "firefly_id": irow["firefly_id"],
                    "error_message": irow["error_message"],
                    "created_at": irow["created_at"],
                }
            )
    except Exception as e:
        logger.error(f"Error loading import queue: {e}")
    finally:
        conn.close()

    context = {
        "ready_items": ready_items,
        "failed_items": failed_items,
        "recent_imports": recent_imports,
        "import_status": _import_status,
        "debug_mode": _is_debug_mode(),
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/import_queue.html", context)


@login_required
@require_http_methods(["POST"])
def run_import(request: HttpRequest) -> HttpResponse:
    """Trigger import to Firefly III."""
    global _import_status

    if _import_status["running"]:
        messages.warning(request, "Import is already running!")
        return redirect("import_queue")

    # Get selected items or import all
    request.POST.getlist("selected_ids")

    # Capture user's source account preference before starting thread
    user_source_account = None
    try:
        profile = request.user.profile
        if profile.default_source_account:
            user_source_account = profile.default_source_account
    except Exception:
        pass  # User may not have profile

    def do_import():
        global _import_status
        _import_status = {
            "running": True,
            "progress": "Starting import...",
            "result": None,
            "error": None,
            "traceback": None,
        }

        try:
            from pathlib import Path

            from ...config import load_config
            from ...runner.main import cmd_import

            config_path = Path(settings.STATE_DB_PATH).parent / "config.yaml"
            if not config_path.exists():
                config_path = Path("/app/config/config.yaml")

            logger.info(f"Loading config from {config_path}")
            config = load_config(config_path)

            logger.info(f"Starting import with source_account_override={user_source_account}")
            _import_status["progress"] = "Importing to Firefly III..."
            result = cmd_import(
                config,
                auto_only=False,
                dry_run=False,
                source_account_override=user_source_account,
            )

            logger.info(f"Import completed with result={result}")
            if result == 0:
                _import_status["result"] = "Import completed successfully"
            else:
                _import_status["result"] = (
                    f"Import completed with {result} failure(s). Check the Failed Imports section below."
                )
            _import_status["progress"] = "Done"
        except Exception as e:
            _import_status["error"] = str(e)
            _import_status["traceback"] = traceback.format_exc() if _is_debug_mode() else None
            logger.exception("Import failed with exception")
        finally:
            _import_status["running"] = False

    thread = threading.Thread(target=do_import)
    thread.start()

    messages.info(request, "Import started in background. Refresh to see progress.")
    return redirect("import_queue")


@login_required
@require_http_methods(["POST"])
def dismiss_failed_import(request: HttpRequest, external_id: str) -> HttpResponse:
    """Dismiss a failed import by rejecting the extraction (won't be retried)."""
    store = _get_store()

    # Find the extraction by external_id
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM extractions WHERE external_id = ?", (external_id,)
        ).fetchone()

        if row:
            # Mark extraction as rejected so it won't be imported
            store.update_extraction_review(row["id"], ReviewDecision.REJECTED.value)
            # Delete the failed import record
            store.delete_import(external_id)
            messages.success(request, "Failed import dismissed. The extraction has been rejected.")
        else:
            messages.error(request, "Extraction not found.")
    finally:
        conn.close()

    return redirect("import_queue")


# ============================================================================
# Extraction Trigger
# ============================================================================


@login_required
@require_http_methods(["POST"])
def run_extract(request: HttpRequest) -> HttpResponse:
    """Trigger extraction from Paperless."""
    global _extraction_status

    if _extraction_status["running"]:
        messages.warning(request, "Extraction is already running!")
        return redirect("list")

    tag = request.POST.get("tag", "finance/inbox")
    limit = int(request.POST.get("limit", 10))

    def do_extract():
        global _extraction_status
        _extraction_status = {
            "running": True,
            "progress": "Starting extraction...",
            "result": None,
            "error": None,
            "traceback": None,
        }

        try:
            from pathlib import Path

            from ...config import load_config
            from ...runner.main import cmd_extract

            config_path = Path(settings.STATE_DB_PATH).parent / "config.yaml"
            if not config_path.exists():
                config_path = Path("/app/config/config.yaml")

            config = load_config(config_path)

            _extraction_status["progress"] = f"Extracting documents with tag '{tag}'..."
            result = cmd_extract(config, doc_id=None, tag=tag, limit=limit)

            _extraction_status["result"] = f"Extraction completed with exit code {result}"
            _extraction_status["progress"] = "Done"
        except Exception as e:
            _extraction_status["error"] = str(e)
            _extraction_status["traceback"] = traceback.format_exc() if _is_debug_mode() else None
            logger.exception("Extraction failed")
        finally:
            _extraction_status["running"] = False

    thread = threading.Thread(target=do_extract)
    thread.start()

    messages.info(request, "Extraction started in background. Refresh to see progress.")
    return redirect("list")


# ============================================================================
# Document Proxy
# ============================================================================


@login_required
def document_preview_status(request: HttpRequest, document_id: int) -> JsonResponse:
    """
    API endpoint to check document availability in Paperless.

    Returns JSON with document status for the frontend to decide how to render.
    This allows clean separation: JSON API for status, separate endpoint for actual preview.

    Response format:
    - {"status": "ok", "preview_url": "/document/<id>/"}
    - {"status": "missing", "message": "...", "actions": [...]}
    - {"status": "error", "message": "..."}
    """
    session = _get_paperless_session(request)

    try:
        # Check if document exists in Paperless
        url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/"
        response = session.get(url)

        if response.status_code == 404:
            return JsonResponse(
                {
                    "status": "missing",
                    "message": (
                        "This document has been deleted from Paperless. "
                        "The source file is no longer available in your DMS."
                    ),
                    "actions": [
                        {
                            "label": "Restore in Paperless",
                            "description": "Re-upload or restore the document in Paperless-ngx",
                            "type": "info",
                        },
                        {
                            "label": "Skip this document",
                            "description": "Skip reviewing this document - it will remain in queue",
                            "type": "skip",
                        },
                        {
                            "label": "Reject",
                            "description": "Reject this document to remove it from the review queue",
                            "type": "reject",
                        },
                        {
                            "label": "Import anyway",
                            "description": "If you know what this document was, you can still import it",
                            "type": "proceed",
                        },
                    ],
                    "document_id": document_id,
                }
            )

        response.raise_for_status()

        # Document exists, return preview URL
        from django.urls import reverse

        preview_url = reverse("document", kwargs={"document_id": document_id})

        return JsonResponse(
            {"status": "ok", "preview_url": preview_url, "document_id": document_id}
        )

    except requests.RequestException as e:
        logger.error(f"Error checking document {document_id}: {e}")
        return JsonResponse(
            {
                "status": "error",
                "message": f"Could not connect to Paperless: {e}",
                "document_id": document_id,
            },
            status=500,
        )


@login_required
def document_proxy(request: HttpRequest, document_id: int) -> HttpResponse:
    """Proxy the original document from Paperless for viewing."""
    session = _get_paperless_session(request)

    force_download = request.GET.get("download", "").lower() in ("1", "true", "yes")

    try:
        # First check if document exists
        check_url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/"
        check_response = session.get(check_url)

        if check_response.status_code == 404:
            # Return a user-friendly HTML page for deleted documents
            return HttpResponse(
                _render_document_missing_page(document_id),
                content_type="text/html",
                status=200,  # 200 so iframe renders it properly
            )

        url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/preview/"
        response = session.get(url, stream=True)

        if response.status_code == 404:
            url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/download/"
            response = session.get(url, stream=True)

        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "application/pdf")

        django_response = HttpResponse(
            response.iter_content(chunk_size=8192), content_type=content_type
        )

        if force_download:
            django_response["Content-Disposition"] = (
                f"attachment; filename=document_{document_id}.pdf"
            )
        else:
            django_response["Content-Disposition"] = f"inline; filename=document_{document_id}.pdf"

        return django_response

    except requests.RequestException as e:
        logger.error(f"Error fetching document {document_id}: {e}")
        # Return HTML error page that renders nicely in iframe
        return HttpResponse(
            _render_document_error_page(document_id, str(e)),
            content_type="text/html",
            status=200,  # 200 so iframe renders it
        )


def _render_document_missing_page(document_id: int) -> str:
    """Render an HTML page for missing documents (displayed in iframe)."""
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document Not Found</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 2rem;
            background: #fef2f2;
            color: #991b1b;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: calc(100vh - 4rem);
            text-align: center;
        }}
        .icon {{ font-size: 4rem; margin-bottom: 1rem; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        p {{ color: #7f1d1d; max-width: 400px; line-height: 1.6; }}
        .actions {{ margin-top: 1.5rem; background: #fff; padding: 1rem; border-radius: 0.5rem; }}
        .actions h3 {{ font-size: 0.875rem; color: #374151; margin-bottom: 0.5rem; }}
        .actions ul {{ text-align: left; color: #6b7280; font-size: 0.875rem; padding-left: 1.5rem; }}
        .actions li {{ margin-bottom: 0.25rem; }}
    </style>
</head>
<body>
    <div class="icon">📄❌</div>
    <h1>Document Not Found in Paperless</h1>
    <p>
        The source document (ID: {document_id}) has been deleted from Paperless-ngx.
        The file is no longer available for preview.
    </p>
    <div class="actions">
        <h3>What you can do:</h3>
        <ul>
            <li><strong>Restore:</strong> Re-upload or restore the document in Paperless</li>
            <li><strong>Skip:</strong> Move to the next document for now</li>
            <li><strong>Reject:</strong> Remove this from the review queue</li>
            <li><strong>Import anyway:</strong> If you remember the details, proceed with import</li>
        </ul>
    </div>
</body>
</html>
"""


def _render_document_error_page(document_id: int, error: str) -> str:
    """Render an HTML page for document fetch errors (displayed in iframe)."""
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Error Loading Document</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 2rem;
            background: #fef3c7;
            color: #92400e;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: calc(100vh - 4rem);
            text-align: center;
        }}
        .icon {{ font-size: 4rem; margin-bottom: 1rem; }}
        h1 {{ font-size: 1.5rem; margin-bottom: 0.5rem; }}
        p {{ color: #78350f; max-width: 400px; line-height: 1.6; }}
        .error {{ font-family: monospace; font-size: 0.75rem; background: #fff; padding: 0.5rem; border-radius: 0.25rem; margin-top: 1rem; }}
    </style>
</head>
<body>
    <div class="icon">⚠️</div>
    <h1>Could Not Load Document</h1>
    <p>
        There was a problem connecting to Paperless to load document {document_id}.
        Please check your Paperless connection and try again.
    </p>
    <div class="error">{error}</div>
</body>
</html>
"""


@login_required
def document_thumbnail(request: HttpRequest, document_id: int) -> HttpResponse:
    """Proxy the document thumbnail from Paperless."""
    session = _get_paperless_session(request)

    try:
        url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/thumb/"
        response = session.get(url)
        response.raise_for_status()

        return HttpResponse(
            response.content, content_type=response.headers.get("Content-Type", "image/webp")
        )

    except requests.RequestException as e:
        logger.error(f"Error fetching thumbnail {document_id}: {e}")
        return HttpResponse(status=404)


# ============================================================================
# API Endpoints
# ============================================================================


@login_required
def api_extraction_detail(request: HttpRequest, extraction_id: int) -> JsonResponse:
    """API endpoint to get extraction details as JSON."""
    store = _get_store()

    conn = store._get_connection()
    try:
        row = conn.execute("SELECT * FROM extractions WHERE id = ?", (extraction_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return JsonResponse({"error": "Not found"}, status=404)

    try:
        extraction_data = json.loads(row["extraction_json"])
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({"error": "Invalid extraction data"}, status=500)

    return JsonResponse(
        {
            "id": row["id"],
            "document_id": row["document_id"],
            "external_id": row["external_id"],
            "extraction": extraction_data,
            "overall_confidence": row["overall_confidence"],
            "review_state": row["review_state"],
            "review_decision": row["review_decision"],
            "created_at": row["created_at"],
        }
    )


@login_required
def api_stats(request: HttpRequest) -> JsonResponse:
    """API endpoint to get pipeline statistics."""
    store = _get_store()
    stats = store.get_stats()
    return JsonResponse(stats)


@login_required
def api_firefly_accounts(request: HttpRequest) -> JsonResponse:
    """API endpoint to get Firefly accounts."""
    try:
        client = _get_firefly_client(request)
        account_type = request.GET.get("type", "asset")
        accounts = client.list_accounts(account_type)
        return JsonResponse({"accounts": accounts})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def api_extract_status(request: HttpRequest) -> JsonResponse:
    """API endpoint to get extraction status."""
    return JsonResponse(_extraction_status)


# ============================================================================
# Document Browser (List/Unlist)
# ============================================================================


@login_required
def document_browser(request: HttpRequest) -> HttpResponse:
    """Browse Paperless documents and select/deselect for extraction."""
    session = _get_paperless_session(request)
    filter_tag = getattr(settings, "PAPERLESS_FILTER_TAG", "finance/inbox")

    # Get query parameters
    page = int(request.GET.get("page", 1))
    search = request.GET.get("search", "")
    show_listed = request.GET.get("listed", "all")  # all, listed, unlisted

    documents = []
    total_count = 0
    page_size = 25
    filter_tag_id = None

    try:
        # First, get/create the filter tag ID
        tags_resp = session.get(f"{settings.PAPERLESS_BASE_URL}/api/tags/")
        if tags_resp.ok:
            tags_data = tags_resp.json()
            for tag in tags_data.get("results", []):
                if tag.get("name") == filter_tag:
                    filter_tag_id = tag.get("id")
                    break

        # Build query params
        params = {
            "page": page,
            "page_size": page_size,
            "ordering": "-created",
        }

        if search:
            params["query"] = search

        # Filter by tag presence
        if show_listed == "listed" and filter_tag_id:
            params["tags__id__all"] = filter_tag_id
        elif show_listed == "unlisted" and filter_tag_id:
            params["tags__id__none"] = filter_tag_id

        # Fetch documents
        resp = session.get(f"{settings.PAPERLESS_BASE_URL}/api/documents/", params=params)
        if resp.ok:
            data = resp.json()
            total_count = data.get("count", 0)

            for doc in data.get("results", []):
                doc_tags = doc.get("tags", [])
                is_listed = filter_tag_id in doc_tags if filter_tag_id else False

                documents.append(
                    {
                        "id": doc.get("id"),
                        "title": doc.get("title"),
                        "created": doc.get("created"),
                        "added": doc.get("added"),
                        "correspondent": doc.get("correspondent"),
                        "document_type": doc.get("document_type"),
                        "is_listed": is_listed,
                    }
                )
    except Exception as e:
        logger.error(f"Error fetching documents: {e}")
        messages.error(request, f"Could not fetch documents from Paperless: {e}")

    # Pagination
    total_pages = (total_count + page_size - 1) // page_size

    context = {
        "documents": documents,
        "filter_tag": filter_tag,
        "filter_tag_id": filter_tag_id,
        "search": search,
        "show_listed": show_listed,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/document_browser.html", context)


@login_required
@require_http_methods(["POST"])
def toggle_document_listing(request: HttpRequest, document_id: int) -> HttpResponse:
    """Add or remove the filter tag from a document."""
    session = _get_paperless_session(request)
    filter_tag = getattr(settings, "PAPERLESS_FILTER_TAG", "finance/inbox")

    action = request.POST.get("action", "list")  # list or unlist

    try:
        # Get or create the filter tag
        tag_id = None
        tags_resp = session.get(f"{settings.PAPERLESS_BASE_URL}/api/tags/")
        if tags_resp.ok:
            for tag in tags_resp.json().get("results", []):
                if tag.get("name") == filter_tag:
                    tag_id = tag.get("id")
                    break

        # Create tag if it doesn't exist
        if not tag_id and action == "list":
            create_resp = session.post(
                f"{settings.PAPERLESS_BASE_URL}/api/tags/",
                json={"name": filter_tag, "color": "#4CAF50"},
            )
            if create_resp.ok:
                tag_id = create_resp.json().get("id")

        if not tag_id:
            return JsonResponse({"error": "Could not find or create tag"}, status=400)

        # Get current document tags
        doc_resp = session.get(f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/")
        if not doc_resp.ok:
            return JsonResponse({"error": "Document not found"}, status=404)

        doc_data = doc_resp.json()
        current_tags = doc_data.get("tags", [])

        # Update tags
        if action == "list" and tag_id not in current_tags:
            current_tags.append(tag_id)
        elif action == "unlist" and tag_id in current_tags:
            current_tags.remove(tag_id)

        # Save document with updated tags
        update_resp = session.patch(
            f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/",
            json={"tags": current_tags},
        )

        if update_resp.ok:
            return JsonResponse({"success": True, "action": action, "document_id": document_id})
        else:
            return JsonResponse({"error": "Failed to update document"}, status=500)

    except Exception as e:
        logger.error(f"Error toggling document listing: {e}")
        return JsonResponse({"error": str(e)}, status=500)


# ============================================================================
# Reconciliation Views (Phase 3 - Match Proposals UI)
# ============================================================================


@login_required
def reconciliation_list(request: HttpRequest) -> HttpResponse:
    """
    List pending match proposals for review.

    Displays proposals sorted by confidence score, with transaction and
    document details for review.

    Query parameters:
        match_document: Optional document ID to pre-select for matching
                       (from "Link to Bank Transaction" flow per SPARK_EVALUATION_REPORT.md 3.3)
    """
    store = _get_store()

    # Check if we're coming from "Link to Bank Transaction" flow
    match_document_id = request.GET.get("match_document")
    match_document_info = None
    if match_document_id:
        try:
            doc_id = int(match_document_id)
            extraction = store.get_extraction_by_document(doc_id)
            if extraction:
                try:
                    extraction_data = json.loads(extraction.extraction_json)
                    match_document_info = {
                        "document_id": doc_id,
                        "title": extraction_data.get("paperless_title", f"Document #{doc_id}"),
                        "amount": extraction_data.get("proposal", {}).get("amount"),
                        "date": extraction_data.get("proposal", {}).get("date"),
                        "currency": extraction_data.get("proposal", {}).get("currency", "EUR"),
                    }
                except (json.JSONDecodeError, TypeError):
                    pass
        except (ValueError, TypeError):
            pass

    # Get pending proposals from state store
    proposals = store.get_pending_proposals()

    # Parse match reasons for display
    for proposal in proposals:
        if proposal.get("match_reasons"):
            try:
                proposal["reasons_list"] = json.loads(proposal["match_reasons"])
            except (json.JSONDecodeError, TypeError):
                proposal["reasons_list"] = []
        else:
            proposal["reasons_list"] = []

        # Format score as percentage
        proposal["score_pct"] = proposal.get("match_score", 0) * 100

    # Get reconciliation stats
    stats = _get_reconciliation_stats(store)

    context = {
        "proposals": proposals,
        "stats": stats,
        "match_document": match_document_info,
        "debug_mode": _is_debug_mode(),
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/reconciliation_list.html", context)


@login_required
def reconciliation_detail(request: HttpRequest, proposal_id: int) -> HttpResponse:
    """
    Show detailed view of a match proposal for review.

    Displays transaction and document side-by-side with matching details.
    """
    store = _get_store()

    # Get the proposal
    proposal = store.get_proposal_by_id(proposal_id)
    if not proposal:
        return render(
            request,
            "review/not_found.html",
            {"message": f"Match proposal {proposal_id} not found"},
            status=404,
        )

    # Parse match reasons
    reasons_list = []
    if proposal.get("match_reasons"):
        try:
            reasons_list = json.loads(proposal["match_reasons"])
        except (json.JSONDecodeError, TypeError):
            pass

    # Get full transaction details from Firefly cache
    tx_details = store.get_firefly_cache_entry(proposal["firefly_id"])

    # Get document details
    doc_record = store.get_document(proposal["document_id"])

    # Get extraction for the document if available
    extraction_data = None
    extraction = store.get_extraction_by_document(proposal["document_id"])
    if extraction:
        try:
            extraction_data = json.loads(extraction.extraction_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Get pending proposals for navigation
    all_pending = store.get_pending_proposals()
    pending_ids = [p["id"] for p in all_pending]
    current_idx = pending_ids.index(proposal_id) if proposal_id in pending_ids else -1
    prev_id = pending_ids[current_idx - 1] if current_idx > 0 else None
    next_id = pending_ids[current_idx + 1] if current_idx < len(pending_ids) - 1 else None

    # Get interpretation run history for this document
    audit_trail = store.get_interpretation_runs(proposal["document_id"])

    context = {
        "proposal": proposal,
        "proposal_id": proposal_id,
        "reasons_list": reasons_list,
        "score_pct": proposal.get("match_score", 0) * 100,
        "tx_details": tx_details,
        "doc_record": doc_record,
        "extraction_data": extraction_data,
        "prev_id": prev_id,
        "next_id": next_id,
        "pending_count": len(pending_ids),
        "current_position": current_idx + 1 if current_idx >= 0 else 0,
        "audit_trail": audit_trail,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/reconciliation_detail.html", context)


@login_required
@require_http_methods(["POST"])
def accept_proposal(request: HttpRequest, proposal_id: int) -> HttpResponse:
    """
    Accept a match proposal and link the document to the transaction.

    This writes linkage markers to Firefly and records the action in audit trail.
    """
    from ...services.reconciliation import ReconciliationService

    store = _get_store()

    # Get proposal
    proposal = store.get_proposal_by_id(proposal_id)
    if not proposal:
        messages.error(request, "Proposal not found")
        return redirect("reconciliation_list")

    try:
        # Get Firefly client and reconciliation service
        firefly_client = _get_firefly_client(request)

        from ...config import load_config

        config = load_config()

        service = ReconciliationService(
            config=config,
            state_store=store,
            firefly_client=firefly_client,
        )

        # Execute the link (user_decision=True means accept)
        success = service.link_proposal(proposal_id, user_decision=True)

        if success:
            messages.success(
                request,
                f"Successfully linked document {proposal['document_id']} "
                f"to transaction {proposal['firefly_id']}",
            )
        else:
            messages.error(request, "Failed to link document to transaction")

    except Exception as e:
        logger.error(f"Error accepting proposal {proposal_id}: {e}")
        messages.error(request, f"Error accepting proposal: {e}")

    # Navigate to next pending proposal
    pending = store.get_pending_proposals()
    if pending:
        return redirect("reconciliation_detail", proposal_id=pending[0]["id"])
    return redirect("reconciliation_list")


@login_required
@require_http_methods(["POST"])
def reject_proposal(request: HttpRequest, proposal_id: int) -> HttpResponse:
    """
    Reject a match proposal.

    The proposal is marked as rejected and won't be shown again.
    """
    store = _get_store()

    # Get proposal
    proposal = store.get_proposal_by_id(proposal_id)
    if not proposal:
        messages.error(request, "Proposal not found")
        return redirect("reconciliation_list")

    try:
        # Update status to REJECTED
        store.update_proposal_status(proposal_id, "REJECTED")

        # Record in audit trail
        store.create_interpretation_run(
            document_id=proposal["document_id"],
            firefly_id=proposal["firefly_id"],
            external_id=None,
            pipeline_version="1.0.0",
            inputs_summary={
                "action": "reject_proposal",
                "proposal_id": proposal_id,
                "match_score": proposal.get("match_score"),
            },
            final_state="REJECTED",
            decision_source="USER",
            firefly_write_action=None,
        )

        messages.success(request, "Match proposal rejected")

    except Exception as e:
        logger.error(f"Error rejecting proposal {proposal_id}: {e}")
        messages.error(request, f"Error rejecting proposal: {e}")

    # Navigate to next pending proposal
    pending = store.get_pending_proposals()
    if pending:
        return redirect("reconciliation_detail", proposal_id=pending[0]["id"])
    return redirect("reconciliation_list")


@login_required
@require_http_methods(["POST"])
def manual_link(request: HttpRequest) -> HttpResponse:
    """
    Manually link a document to a Firefly transaction.

    This allows linking documents that weren't matched automatically.
    """
    from ...services.reconciliation import ReconciliationService

    document_id = request.POST.get("document_id")
    firefly_id = request.POST.get("firefly_id")

    if not document_id or not firefly_id:
        messages.error(request, "Both document_id and firefly_id are required")
        return redirect("reconciliation_list")

    try:
        document_id_int = int(document_id)
        firefly_id_int = int(firefly_id)
    except ValueError:
        messages.error(request, "Invalid document_id or firefly_id")
        return redirect("reconciliation_list")

    store = _get_store()

    try:
        firefly_client = _get_firefly_client(request)
        from ...config import load_config

        config = load_config()

        service = ReconciliationService(
            config=config,
            state_store=store,
            firefly_client=firefly_client,
        )

        success = service.manual_link(
            document_id=document_id_int,
            firefly_id=firefly_id_int,
        )

        if success:
            messages.success(
                request,
                f"Successfully linked document {document_id_int} to transaction {firefly_id_int}",
            )
        else:
            messages.error(request, "Failed to link document to transaction")

    except Exception as e:
        logger.error(f"Error manual linking doc {document_id} to tx {firefly_id}: {e}")
        messages.error(request, f"Error creating manual link: {e}")

    return redirect("reconciliation_list")


@login_required
def link_document_to_transaction(request: HttpRequest) -> HttpResponse:
    """
    Link a document directly to an existing Firefly transaction.

    This skips creating a new transaction and instead links the document
    to an existing one - useful when the matching engine finds potential matches.
    Supports both GET (confirmation) and POST (action).
    """
    from ...config import load_config
    from ...services.reconciliation import ReconciliationService

    document_id = request.GET.get("document_id") or request.POST.get("document_id")
    firefly_id = request.GET.get("firefly_id") or request.POST.get("firefly_id")

    if not document_id or not firefly_id:
        messages.error(request, "Both document_id and firefly_id are required")
        return redirect("list")

    try:
        document_id_int = int(document_id)
        firefly_id_int = int(firefly_id)
    except ValueError:
        messages.error(request, "Invalid document_id or firefly_id")
        return redirect("list")

    store = _get_store()

    if request.method == "POST":
        # Perform the actual linking
        try:
            firefly_client = _get_firefly_client(request)
            config = load_config()

            service = ReconciliationService(
                config=config,
                state_store=store,
                firefly_client=firefly_client,
            )

            success = service.manual_link(
                document_id=document_id_int,
                firefly_id=firefly_id_int,
            )

            if success:
                # Mark the extraction as LINKED (not ACCEPTED - different semantic)
                record = store.get_extraction_by_document_id(document_id_int)
                if record:
                    store.update_extraction_status(
                        record.id,
                        review_decision="LINKED",
                        review_state="LINKED",
                    )

                messages.success(
                    request,
                    f"Document {document_id_int} linked to Firefly transaction {firefly_id_int}",
                )
                return redirect("list")
            else:
                messages.error(request, "Failed to link document to transaction")
                return redirect("detail", extraction_id=document_id_int)

        except Exception as e:
            logger.error(f"Error linking doc {document_id} to tx {firefly_id}: {e}")
            messages.error(request, f"Error creating link: {e}")
            return redirect("list")

    # GET: Show confirmation page
    # Get document and transaction details for confirmation
    extraction_record = store.get_extraction_by_document_id(document_id_int)
    tx_cache = store.get_firefly_cache_entry(firefly_id_int)

    context = {
        "document_id": document_id_int,
        "firefly_id": firefly_id_int,
        "extraction": extraction_record,
        "transaction": tx_cache,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }

    return render(request, "review/link_confirmation.html", context)


@login_required
def unlinked_transactions(request: HttpRequest) -> HttpResponse:
    """
    Show Firefly transactions that don't have linked documents.

    Useful for finding transactions that need receipts.
    """
    store = _get_store()

    # Get unmatched transactions from cache (UNMATCHED status)
    unmatched = store.get_unmatched_firefly_transactions()

    # Parse and format for display
    transactions = []
    for tx in unmatched[:100]:  # Limit to 100
        transactions.append(
            {
                "firefly_id": tx["firefly_id"],
                "date": tx["date"],
                "amount": tx["amount"],
                "description": tx.get("description"),
                "source_account": tx.get("source_account"),
                "destination_account": tx.get("destination_account"),
                "category": tx.get("category_name"),
                "cached_at": tx.get("synced_at"),
            }
        )

    context = {
        "transactions": transactions,
        "total_count": len(transactions),
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/unlinked_transactions.html", context)


# Global state for Firefly sync background job
_firefly_sync_status = {
    "running": False,
    "progress": "",
    "result": None,
    "error": None,
    "synced_count": 0,
}


@login_required
@require_http_methods(["POST"])
def sync_firefly_transactions(request: HttpRequest) -> HttpResponse:
    """
    Sync transactions from Firefly III into local cache.

    This fetches recent transactions from Firefly and stores them in the
    local cache for matching with Paperless documents.
    """
    global _firefly_sync_status
    from datetime import timedelta
    from pathlib import Path

    if _firefly_sync_status["running"]:
        messages.warning(request, "Firefly sync is already running!")
        return redirect("reconciliation_list")

    # Get sync parameters from form
    days = int(request.POST.get("days", 90))
    type_filter = request.POST.get("type_filter", "")  # Empty = all types

    def do_sync():
        global _firefly_sync_status
        _firefly_sync_status = {
            "running": True,
            "progress": "Connecting to Firefly...",
            "result": None,
            "error": None,
            "synced_count": 0,
        }

        try:
            from datetime import date

            from ...config import load_config

            config_path = (
                Path(getattr(settings, "STATE_DB_PATH", "/app/data/state.db")).parent
                / "config.yaml"
            )
            if not config_path.exists():
                config_path = Path("/app/config/config.yaml")

            config = load_config(config_path)

            firefly = FireflyClient(
                base_url=config.firefly.base_url,
                token=config.firefly.token,
            )

            store = _get_store()

            # Calculate date range
            end_date = date.today()
            start_date = end_date - timedelta(days=days)

            _firefly_sync_status["progress"] = (
                f"Fetching transactions from {start_date} to {end_date}..."
            )

            # Fetch transactions from Firefly
            transactions = firefly.list_transactions(
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                type_filter=type_filter if type_filter else None,
            )

            _firefly_sync_status["progress"] = f"Caching {len(transactions)} transactions..."

            # Store in cache
            synced = 0
            for tx in transactions:
                store.upsert_firefly_cache(
                    firefly_id=tx.id,
                    type_=tx.type,
                    date=tx.date,
                    amount=tx.amount,
                    description=tx.description,
                    external_id=tx.external_id,
                    internal_reference=tx.internal_reference,
                    source_account=tx.source_name,
                    destination_account=tx.destination_name,
                    notes=tx.notes,
                    category_name=tx.category_name,
                    tags=tx.tags,
                )
                synced += 1

            _firefly_sync_status["synced_count"] = synced
            _firefly_sync_status["result"] = f"Successfully synced {synced} transactions"
            _firefly_sync_status["progress"] = "Done"

        except Exception as e:
            _firefly_sync_status["error"] = str(e)
            logger.exception("Firefly sync failed")
        finally:
            _firefly_sync_status["running"] = False

    # Run in background thread
    thread = threading.Thread(target=do_sync)
    thread.start()

    messages.info(request, "Firefly sync started in background. Refresh to see progress.")
    return redirect("reconciliation_list")


@login_required
def api_sync_firefly_status(request: HttpRequest) -> JsonResponse:
    """
    API endpoint to check Firefly sync status.

    Returns JSON with current sync state for AJAX polling.
    """
    global _firefly_sync_status
    return JsonResponse(_firefly_sync_status)


def _get_reconciliation_stats(store: StateStore) -> dict:
    """Get statistics for reconciliation dashboard."""
    conn = store._get_connection()
    try:
        # Pending proposals
        pending = conn.execute(
            "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'PENDING'"
        ).fetchone()
        pending_count = pending["count"] if pending else 0

        # Accepted proposals
        accepted = conn.execute(
            "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'ACCEPTED'"
        ).fetchone()
        accepted_count = accepted["count"] if accepted else 0

        # Rejected proposals
        rejected = conn.execute(
            "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'REJECTED'"
        ).fetchone()
        rejected_count = rejected["count"] if rejected else 0

        # Auto-matched
        auto_matched = conn.execute(
            "SELECT COUNT(*) as count FROM match_proposals WHERE status = 'AUTO_MATCHED'"
        ).fetchone()
        auto_count = auto_matched["count"] if auto_matched else 0

        # Unmatched transactions (in cache with UNMATCHED status)
        unmatched = conn.execute(
            "SELECT COUNT(*) as count FROM firefly_cache WHERE match_status = 'UNMATCHED'"
        ).fetchone()
        unmatched_count = unmatched["count"] if unmatched else 0

        return {
            "pending": pending_count,
            "accepted": accepted_count,
            "rejected": rejected_count,
            "auto_matched": auto_count,
            "unlinked": unmatched_count,  # Keep "unlinked" key for template compatibility
        }
    finally:
        conn.close()


# ============================================================================
# Audit Trail Views (Phase 8)
# ============================================================================


@login_required
def audit_trail_list(request: HttpRequest) -> HttpResponse:
    """
    List all interpretation runs (audit trail).

    Read-only view showing history of all reconciliation decisions.
    """
    store = _get_store()

    # Pagination
    page = int(request.GET.get("page", 1))
    page_size = 50

    # Filter options
    filter_document = request.GET.get("document_id")
    filter_firefly = request.GET.get("firefly_id")
    filter_source = request.GET.get("decision_source")

    conn = store._get_connection()
    try:
        # Build query with filters
        query = "SELECT * FROM interpretation_runs WHERE 1=1"
        params = []

        if filter_document:
            query += " AND document_id = ?"
            params.append(int(filter_document))

        if filter_firefly:
            query += " AND firefly_id = ?"
            params.append(int(filter_firefly))

        if filter_source:
            query += " AND decision_source = ?"
            params.append(filter_source)

        # Count total
        count_query = query.replace("SELECT *", "SELECT COUNT(*)")
        total_count = conn.execute(count_query, params).fetchone()[0]

        # Add ordering and pagination
        query += " ORDER BY run_timestamp DESC LIMIT ? OFFSET ?"
        params.extend([page_size, (page - 1) * page_size])

        rows = conn.execute(query, params).fetchall()
        runs = [dict(row) for row in rows]

        # Parse JSON fields
        for run in runs:
            if run.get("inputs_summary"):
                try:
                    run["inputs_summary"] = json.loads(run["inputs_summary"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if run.get("rules_applied"):
                try:
                    run["rules_applied"] = json.loads(run["rules_applied"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if run.get("llm_result"):
                try:
                    run["llm_result"] = json.loads(run["llm_result"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if run.get("linkage_marker_written"):
                try:
                    run["linkage_marker_written"] = json.loads(run["linkage_marker_written"])
                except (json.JSONDecodeError, TypeError):
                    pass
    finally:
        conn.close()

    # Pagination
    total_pages = (total_count + page_size - 1) // page_size

    context = {
        "runs": runs,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "filter_document": filter_document or "",
        "filter_firefly": filter_firefly or "",
        "filter_source": filter_source or "",
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/audit_trail_list.html", context)


@login_required
def audit_trail_detail(request: HttpRequest, run_id: int) -> HttpResponse:
    """
    Show detailed view of a single interpretation run.
    """
    store = _get_store()

    conn = store._get_connection()
    try:
        row = conn.execute("SELECT * FROM interpretation_runs WHERE id = ?", (run_id,)).fetchone()

        if not row:
            return render(
                request,
                "review/not_found.html",
                {"message": f"Interpretation run {run_id} not found"},
                status=404,
            )

        run = dict(row)

        # Parse JSON fields
        if run.get("inputs_summary"):
            try:
                run["inputs_summary"] = json.loads(run["inputs_summary"])
            except (json.JSONDecodeError, TypeError):
                pass
        if run.get("rules_applied"):
            try:
                run["rules_applied"] = json.loads(run["rules_applied"])
            except (json.JSONDecodeError, TypeError):
                pass
        if run.get("llm_result"):
            try:
                run["llm_result"] = json.loads(run["llm_result"])
            except (json.JSONDecodeError, TypeError):
                pass
        if run.get("suggested_splits"):
            try:
                run["suggested_splits"] = json.loads(run["suggested_splits"])
            except (json.JSONDecodeError, TypeError):
                pass
        if run.get("linkage_marker_written"):
            try:
                run["linkage_marker_written"] = json.loads(run["linkage_marker_written"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Get related document info if available
        doc_record = None
        if run.get("document_id"):
            doc_record = store.get_document(run["document_id"])

        # Get related transaction info if available
        tx_details = None
        if run.get("firefly_id"):
            tx_details = store.get_firefly_cache_entry(run["firefly_id"])

    finally:
        conn.close()

    context = {
        "run": run,
        "run_id": run_id,
        "doc_record": doc_record,
        "tx_details": tx_details,
        **_get_external_urls(request.user if hasattr(request, "user") else None),
    }
    return render(request, "review/audit_trail_detail.html", context)
