"""
Views for the review web interface.
"""

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
import requests

from ...state_store import StateStore, ExtractionRecord
from ...schemas.finance_extraction import FinanceExtraction, ReviewState
from ...schemas.dedupe import generate_external_id
from ..workflow import ReviewDecision

logger = logging.getLogger(__name__)


def _get_store() -> StateStore:
    """Get the state store instance."""
    return StateStore(settings.STATE_DB_PATH)


def _get_paperless_session() -> requests.Session:
    """Get a session configured for Paperless API."""
    session = requests.Session()
    session.headers["Authorization"] = f"Token {settings.PAPERLESS_TOKEN}"
    return session


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
            extractions.append({
                "id": record.id,
                "document_id": record.document_id,
                "external_id": record.external_id,
                "title": extraction.paperless_title,
                "amount": extraction.proposal.amount,
                "currency": extraction.proposal.currency,
                "date": extraction.proposal.date,
                "vendor": extraction.proposal.destination_account,
                "confidence": record.overall_confidence * 100,  # Convert to percentage
                "review_state": record.review_state,
                "created_at": record.created_at,
            })
        except Exception as e:
            logger.error(f"Error parsing extraction {record.id}: {e}")
    
    context = {
        "extractions": extractions,
        "stats": stats,
        "paperless_url": settings.PAPERLESS_BASE_URL,
        "firefly_url": settings.FIREFLY_BASE_URL,
    }
    return render(request, "review/list.html", context)


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
        return render(request, "review/not_found.html", {"extraction_id": extraction_id}, status=404)
    
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
    
    context = {
        "record": record,
        "extraction": extraction,
        "proposal": extraction.proposal,
        "confidence": confidence_pct,
        "provenance": extraction.provenance,
        "document_id": extraction.paperless_document_id,
        "paperless_url": settings.PAPERLESS_BASE_URL,
        "prev_id": prev_id,
        "next_id": next_id,
        "pending_count": len(pending_ids),
        "current_position": current_idx + 1 if current_idx >= 0 else 0,
        "already_reviewed": record.review_decision is not None,
        # Editable fields config
        "editable_fields": [
            {"name": "amount", "label": "Amount", "type": "number", "step": "0.01", "required": True},
            {"name": "currency", "label": "Currency", "type": "text", "maxlength": 3, "required": True},
            {"name": "date", "label": "Date", "type": "date", "required": True},
            {"name": "description", "label": "Description", "type": "text", "required": True},
            {"name": "destination_account", "label": "Vendor/Destination", "type": "text", "required": False},
            {"name": "source_account", "label": "Source Account", "type": "text", "required": False},
            {"name": "category", "label": "Category", "type": "text", "required": False},
            {"name": "invoice_number", "label": "Invoice Number", "type": "text", "required": False},
            {"name": "transaction_type", "label": "Type", "type": "select", "options": ["withdrawal", "deposit", "transfer"], "required": True},
        ],
    }
    return render(request, "review/detail.html", context)


@require_http_methods(["POST"])
def accept_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Accept extraction as-is."""
    store = _get_store()
    store.update_extraction_review(extraction_id, ReviewDecision.ACCEPTED.value)
    
    # Redirect to next pending or back to list
    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


@require_http_methods(["POST"])
def reject_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Reject extraction (won't be imported)."""
    store = _get_store()
    store.update_extraction_review(extraction_id, ReviewDecision.REJECTED.value)
    
    # Redirect to next pending or back to list
    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


@require_http_methods(["POST"])
def skip_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Skip extraction for now."""
    store = _get_store()
    
    # Get next pending
    pending = store.get_extractions_for_review()
    pending_ids = [r.id for r in pending]
    
    if extraction_id in pending_ids:
        current_idx = pending_ids.index(extraction_id)
        if current_idx < len(pending_ids) - 1:
            return redirect("detail", extraction_id=pending_ids[current_idx + 1])
    
    return redirect("list")


@require_http_methods(["POST"])
def save_extraction(request: HttpRequest, extraction_id: int) -> HttpResponse:
    """Save edited extraction and accept it."""
    store = _get_store()
    
    # Get current extraction
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM extractions WHERE id = ?", (extraction_id,)
        ).fetchone()
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
    
    # Determine decision based on changes
    decision = ReviewDecision.EDITED if changes else ReviewDecision.ACCEPTED
    
    # Save
    updated_json = json.dumps(extraction.to_dict())
    store.update_extraction_review(extraction_id, decision.value, updated_json)
    
    # Redirect to next pending or back to list
    pending = store.get_extractions_for_review()
    if pending:
        return redirect("detail", extraction_id=pending[0].id)
    return redirect("list")


def document_proxy(request: HttpRequest, document_id: int) -> HttpResponse:
    """Proxy the original document from Paperless for viewing."""
    session = _get_paperless_session()
    
    # Check if download is requested via query param
    force_download = request.GET.get('download', '').lower() in ('1', 'true', 'yes')
    
    try:
        # Get document preview URL (not download, to get inline viewing)
        url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/preview/"
        response = session.get(url, stream=True)
        
        # Fallback to download endpoint if preview fails
        if response.status_code == 404:
            url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/download/"
            response = session.get(url, stream=True)
        
        response.raise_for_status()
        
        # Get content type from response
        content_type = response.headers.get("Content-Type", "application/pdf")
        
        # Stream the response
        django_response = HttpResponse(
            response.iter_content(chunk_size=8192),
            content_type=content_type
        )
        
        # Force inline display for iframe viewing (not download)
        # Only use attachment if explicitly requested via ?download=1
        if force_download:
            django_response["Content-Disposition"] = f"attachment; filename=document_{document_id}.pdf"
        else:
            django_response["Content-Disposition"] = f"inline; filename=document_{document_id}.pdf"
        
        return django_response
        
    except requests.RequestException as e:
        logger.error(f"Error fetching document {document_id}: {e}")
        return HttpResponse(f"Error fetching document: {e}", status=502)


def document_thumbnail(request: HttpRequest, document_id: int) -> HttpResponse:
    """Proxy the document thumbnail from Paperless."""
    session = _get_paperless_session()
    
    try:
        url = f"{settings.PAPERLESS_BASE_URL}/api/documents/{document_id}/thumb/"
        response = session.get(url)
        response.raise_for_status()
        
        return HttpResponse(
            response.content,
            content_type=response.headers.get("Content-Type", "image/webp")
        )
        
    except requests.RequestException as e:
        logger.error(f"Error fetching thumbnail {document_id}: {e}")
        return HttpResponse(status=404)


def api_extraction_detail(request: HttpRequest, extraction_id: int) -> JsonResponse:
    """API endpoint to get extraction details as JSON."""
    store = _get_store()
    
    conn = store._get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM extractions WHERE id = ?", (extraction_id,)
        ).fetchone()
    finally:
        conn.close()
    
    if not row:
        return JsonResponse({"error": "Not found"}, status=404)
    
    try:
        extraction_data = json.loads(row["extraction_json"])
    except:
        return JsonResponse({"error": "Invalid extraction data"}, status=500)
    
    return JsonResponse({
        "id": row["id"],
        "document_id": row["document_id"],
        "external_id": row["external_id"],
        "extraction": extraction_data,
        "overall_confidence": row["overall_confidence"],
        "review_state": row["review_state"],
        "review_decision": row["review_decision"],
        "created_at": row["created_at"],
    })


def api_stats(request: HttpRequest) -> JsonResponse:
    """API endpoint to get pipeline statistics."""
    store = _get_store()
    stats = store.get_stats()
    return JsonResponse(stats)
