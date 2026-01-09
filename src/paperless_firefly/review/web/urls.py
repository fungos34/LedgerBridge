"""
URL configuration for the review web interface.
"""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

# Note: No app_name since this is the ROOT_URLCONF

urlpatterns = [
    # Django admin
    path("admin/", admin.site.urls),
    # Landing page / Home
    path("", views.landing_page, name="home"),
    # Authentication
    path("login/", auth_views.LoginView.as_view(template_name="review/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("register/", views.register_user, name="register"),
    path("settings/", views.user_settings, name="settings"),
    path("change-password/", views.change_password, name="change_password"),
    # Main review queue
    path("review/", views.review_list, name="list"),
    # Archive/History (processed documents)
    path("archive/", views.extraction_archive, name="archive"),
    # Import queue
    path("import-queue/", views.import_queue, name="import_queue"),
    path("import-queue/import/", views.run_import, name="run_import"),
    path(
        "import-queue/dismiss/<str:external_id>/",
        views.dismiss_failed_import,
        name="dismiss_import",
    ),
    # Document browser (List/Unlist)
    path("documents/", views.document_browser, name="document_browser"),
    path(
        "documents/<int:document_id>/toggle/", views.toggle_document_listing, name="toggle_listing"
    ),
    # Extraction from Paperless
    path("extract/", views.run_extract, name="run_extract"),
    # Single extraction review
    path("extraction/<int:extraction_id>/", views.review_detail, name="detail"),
    # Actions
    path("extraction/<int:extraction_id>/accept/", views.accept_extraction, name="accept"),
    path("extraction/<int:extraction_id>/reject/", views.reject_extraction, name="reject"),
    path("extraction/<int:extraction_id>/skip/", views.skip_extraction, name="skip"),
    path("extraction/<int:extraction_id>/save/", views.save_extraction, name="save"),
    path("extraction/<int:extraction_id>/reset/", views.reset_extraction, name="reset_extraction"),
    # Document proxy (to serve original document from Paperless)
    path("document/<int:document_id>/", views.document_proxy, name="document"),
    path("document/<int:document_id>/thumbnail/", views.document_thumbnail, name="thumbnail"),
    path(
        "api/document/<int:document_id>/status/",
        views.document_preview_status,
        name="document_status",
    ),
    # API endpoints for AJAX
    path("api/extraction/<int:extraction_id>/", views.api_extraction_detail, name="api_detail"),
    path("api/stats/", views.api_stats, name="api_stats"),
    path("api/accounts/", views.api_firefly_accounts, name="api_accounts"),
    path("api/extract/status/", views.api_extract_status, name="api_extract_status"),
    # ============================================================================
    # Reconciliation Routes (Phase 3)
    # ============================================================================
    path("reconciliation/", views.reconciliation_list, name="reconciliation_list"),
    path(
        "reconciliation/<int:proposal_id>/",
        views.reconciliation_detail,
        name="reconciliation_detail",
    ),
    path(
        "reconciliation/<int:proposal_id>/accept/",
        views.accept_proposal,
        name="accept_proposal",
    ),
    path(
        "reconciliation/<int:proposal_id>/reject/",
        views.reject_proposal,
        name="reject_proposal",
    ),
    path("reconciliation/manual-link/", views.manual_link, name="manual_link"),
    path("reconciliation/unlinked/", views.unlinked_transactions, name="unlinked_transactions"),
    path(
        "reconciliation/link-document/",
        views.link_document_to_transaction,
        name="link_document_to_transaction",
    ),
    # ============================================================================
    # LLM Control Routes (Phase 6-7 - SPARK_EVALUATION_REPORT.md 6.7/6.8)
    # ============================================================================
    path(
        "extraction/<int:extraction_id>/llm-opt-out/",
        views.toggle_llm_opt_out,
        name="toggle_llm_opt_out",
    ),
    path(
        "extraction/<int:extraction_id>/rerun/",
        views.rerun_interpretation,
        name="rerun_interpretation",
    ),
    # ============================================================================
    # Audit Trail Routes (Phase 8)
    # ============================================================================
    path("audit-trail/", views.audit_trail_list, name="audit_trail_list"),
    path("audit-trail/<int:run_id>/", views.audit_trail_detail, name="audit_trail_detail"),
]
