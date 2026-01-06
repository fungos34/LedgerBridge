"""
Views for the review web interface.
"""

import json
import logging
import subprocess
import threading
import traceback
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Optional

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from ...firefly_client import FireflyClient, FireflyError
from ...schemas.dedupe import generate_external_id
from ...schemas.finance_extraction import FinanceExtraction, ReviewState
from ...state_store import ExtractionRecord, ImportStatus, StateStore
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


def _get_paperless_session(request: Optional[HttpRequest] = None) -> requests.Session:
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


def _get_firefly_client(request: Optional[HttpRequest] = None) -> FireflyClient:
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


def _get_external_urls():
    """Get external URLs for browser links."""
    return {
        "paperless_url": getattr(settings, "PAPERLESS_EXTERNAL_URL", settings.PAPERLESS_BASE_URL),
        "firefly_url": getattr(settings, "FIREFLY_EXTERNAL_URL", settings.FIREFLY_BASE_URL),
        "syncthing_url": getattr(settings, "SYNCTHING_URL", ""),
        "firefly_importer_url": getattr(settings, "FIREFLY_IMPORTER_URL", ""),
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
        **_get_external_urls(),
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
def user_settings(request: HttpRequest) -> HttpResponse:
    """User settings page for configuring API tokens."""
    from .models import UserProfile

    # Ensure profile exists
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == "POST":
        # Update profile
        profile.paperless_token = request.POST.get("paperless_token", "")
        profile.paperless_url = request.POST.get("paperless_url", "")
        profile.firefly_token = request.POST.get("firefly_token", "")
        profile.firefly_url = request.POST.get("firefly_url", "")
        profile.default_source_account = request.POST.get(
            "default_source_account", "Checking Account"
        )

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
            if client.test_connection():
                firefly_status = "connected"
                firefly_accounts = client.list_accounts("asset")
            else:
                firefly_status = "error"
        except Exception as e:
            firefly_status = f"error: {e}"

    context = {
        "profile": profile,
        "paperless_status": paperless_status,
        "firefly_status": firefly_status,
        "firefly_accounts": firefly_accounts,
        **_get_external_urls(),
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
        **_get_external_urls(),
    }
    return render(request, "review/list.html", context)


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
        **_get_external_urls(),
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
                except:
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
        **_get_external_urls(),
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
    selected_ids = request.POST.getlist("selected_ids")
    
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

            config = load_config(config_path)

            _import_status["progress"] = "Importing to Firefly III..."
            result = cmd_import(
                config,
                auto_only=False,
                dry_run=False,
                source_account_override=user_source_account,
            )

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
            logger.exception("Import failed")
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
            "SELECT id FROM extractions WHERE external_id = ?",
            (external_id,)
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
def document_proxy(request: HttpRequest, document_id: int) -> HttpResponse:
    """Proxy the original document from Paperless for viewing."""
    session = _get_paperless_session(request)

    force_download = request.GET.get("download", "").lower() in ("1", "true", "yes")

    try:
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
        return HttpResponse(f"Error fetching document: {e}", status=502)


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
    except:
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
        **_get_external_urls(),
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
