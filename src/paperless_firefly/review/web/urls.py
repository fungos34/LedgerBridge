"""
URL configuration for the review web interface.
"""

from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

# Note: No app_name since this is the ROOT_URLCONF

urlpatterns = [
    # Landing page / Home
    path("", views.landing_page, name="home"),
    
    # Authentication
    path("login/", auth_views.LoginView.as_view(template_name="review/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("settings/", views.user_settings, name="settings"),
    
    # Main review queue
    path("review/", views.review_list, name="list"),
    
    # Import queue
    path("import-queue/", views.import_queue, name="import_queue"),
    path("import-queue/import/", views.run_import, name="run_import"),
    
    # Extraction from Paperless
    path("extract/", views.run_extract, name="run_extract"),
    
    # Single extraction review
    path("extraction/<int:extraction_id>/", views.review_detail, name="detail"),
    
    # Actions
    path("extraction/<int:extraction_id>/accept/", views.accept_extraction, name="accept"),
    path("extraction/<int:extraction_id>/reject/", views.reject_extraction, name="reject"),
    path("extraction/<int:extraction_id>/skip/", views.skip_extraction, name="skip"),
    path("extraction/<int:extraction_id>/save/", views.save_extraction, name="save"),
    
    # Document proxy (to serve original document from Paperless)
    path("document/<int:document_id>/", views.document_proxy, name="document"),
    path("document/<int:document_id>/thumbnail/", views.document_thumbnail, name="thumbnail"),
    
    # API endpoints for AJAX
    path("api/extraction/<int:extraction_id>/", views.api_extraction_detail, name="api_detail"),
    path("api/stats/", views.api_stats, name="api_stats"),
    path("api/accounts/", views.api_firefly_accounts, name="api_accounts"),
    path("api/extract/status/", views.api_extract_status, name="api_extract_status"),
]
