"""
URL configuration for the review web interface.
"""

from django.urls import path
from . import views

# Note: No app_name since this is the ROOT_URLCONF

urlpatterns = [
    # Main review queue
    path("", views.review_list, name="list"),
    
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
]
