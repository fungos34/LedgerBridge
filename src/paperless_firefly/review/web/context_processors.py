"""Context processors for the review web application.

Provides global context variables available to all templates.
"""

from pathlib import Path

from django.conf import settings


def llm_settings(request):
    """Provide LLM settings to all templates.

    Returns timeout and other LLM configuration needed by JavaScript.
    """
    from ...config import load_config
    from .models import UserProfile

    # Default timeout
    llm_timeout = 60  # seconds

    try:
        # Load config to get default timeout
        config_path = (
            Path(getattr(settings, "STATE_DB_PATH", "/app/data/state.db")).parent / "config.yaml"
        )
        if not config_path.exists():
            config_path = Path("/app/config/config.yaml")

        if config_path.exists():
            config = load_config(config_path)
            llm_timeout = config.llm.timeout_seconds

            # Check user profile for override
            if hasattr(request, "user") and request.user.is_authenticated:
                try:
                    profile = request.user.profile
                    if profile.ollama_timeout:
                        llm_timeout = profile.ollama_timeout
                except UserProfile.DoesNotExist:
                    pass
    except Exception:
        pass  # Use default

    return {
        "llm_timeout_seconds": llm_timeout,
    }


def page_context(request):
    """Provide current page context for the chatbot.

    Returns information about the current page that can be passed to the AI.
    """
    # Build page context from request
    page_info = {
        "path": request.path,
        "page_name": _get_page_name(request.path),
        "description": _get_page_description(request.path),
    }

    return {
        "page_context_json": _safe_json(page_info),
    }


def _get_page_name(path: str) -> str:
    """Get human-readable page name from URL path."""
    path_mappings = {
        "/": "Dashboard",
        "/list/": "Review Queue",
        "/archive/": "Archive",
        "/reconciliation/": "Reconciliation",
        "/reconciliation-v2/": "Unified Review",
        "/settings/": "Settings",
        "/import-queue/": "Import Queue",
        "/failed-imports/": "Failed Imports",
        "/audit-trail/": "Audit Trail",
    }

    # Check exact matches
    if path in path_mappings:
        return path_mappings[path]

    # Check prefixes
    if path.startswith("/unified-review/"):
        return "Document Review"
    if path.startswith("/extraction/"):
        # Legacy - kept for backward compatibility
        return "Document Review"
    if path.startswith("/review/"):
        return "Transaction Review"
    if path.startswith("/audit-trail/"):
        return "Audit Trail Detail"
    if path.startswith("/reconciliation"):
        return "Reconciliation"

    return "SparkLink"


def _get_page_description(path: str) -> str:
    """Get description of what the current page does."""
    descriptions = {
        "/": "Main dashboard showing statistics and quick actions",
        "/list/": "Queue of documents waiting to be reviewed and categorized",
        "/archive/": "Completed transactions that have been processed",
        "/reconciliation/": "Match Paperless documents with Firefly transactions",
        "/reconciliation-v2/": "Unified view for reviewing both document types",
        "/settings/": "Configure Paperless, Firefly, and AI settings",
        "/import-queue/": "Documents ready to be imported into Firefly",
        "/failed-imports/": "Documents that failed to import",
        "/audit-trail/": "History of AI interpretations and decisions",
    }

    if path in descriptions:
        return descriptions[path]

    if path.startswith("/unified-review/"):
        return (
            "Review and edit extracted data from a Paperless document. "
            "Set the amount, date, category, and vendor before importing to Firefly."
        )
    if path.startswith("/extraction/"):
        # Legacy - kept for backward compatibility
        return (
            "Review and edit extracted data from a Paperless document. "
            "Set the amount, date, category, and vendor before importing to Firefly."
        )
    if path.startswith("/review/"):
        return "Review a transaction and link it to its matching document or mark as orphan."

    return "SparkLink - Document to Transaction Matching"


def _safe_json(obj: dict) -> str:
    """Safely serialize object to JSON for template embedding."""
    import json

    return json.dumps(obj)
