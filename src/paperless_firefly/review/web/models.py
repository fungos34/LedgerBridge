"""
Django models for user settings, API token storage, and state store access.

This module provides:
1. UserProfile - User-specific settings and API tokens
2. State Store Models - Django ORM models backed by the SQLite state store database
   These use a custom database router to read/write from the state.db file.
"""

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver

# =============================================================================
# User Profile Model (Django default database)
# =============================================================================


class UserProfile(models.Model):
    """
    Extended user profile with API tokens for Paperless and Firefly.
    Each user can have their own tokens for independent access.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    # Paperless-ngx connection
    paperless_token = models.CharField(max_length=255, blank=True, default="")
    paperless_url = models.URLField(
        blank=True, default="", help_text="Override default Paperless URL"
    )

    # Paperless filter settings
    paperless_filter_tags = models.CharField(
        max_length=500,
        blank=True,
        default="finance/inbox",
        help_text="Comma-separated tags to filter documents during extraction (e.g., 'finance/inbox,receipts')",
    )

    # Firefly III connection
    firefly_token = models.CharField(max_length=255, blank=True, default="")
    firefly_url = models.URLField(blank=True, default="", help_text="Override default Firefly URL")

    # Default source account for this user
    default_source_account = models.CharField(
        max_length=255, blank=True, default="Checking Account"
    )

    # Preferences
    auto_import_threshold = models.FloatField(
        default=0.85, help_text="Auto-import confidence threshold"
    )
    review_threshold = models.FloatField(default=0.60, help_text="Review confidence threshold")

    # External links for UI quick access (optional)
    syncthing_url = models.URLField(
        blank=True, default="", help_text="URL to Syncthing web UI for document syncing"
    )
    importer_url = models.URLField(
        blank=True, default="", help_text="URL to Firefly Importer for bank statement imports"
    )

    # LLM/Ollama settings
    llm_enabled = models.BooleanField(
        default=False, help_text="Enable LLM-assisted categorization"
    )
    ollama_url = models.URLField(
        blank=True, default="", help_text="Ollama server URL (e.g., http://localhost:11434)"
    )
    ollama_model = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Fast model name (e.g., qwen2.5:3b-instruct-q4_K_M)"
    )
    ollama_model_fallback = models.CharField(
        max_length=100, blank=True, default="",
        help_text="Fallback model for complex cases (e.g., qwen2.5:7b-instruct-q4_K_M)"
    )
    ollama_timeout = models.IntegerField(
        default=30, help_text="Request timeout in seconds"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "web"

    def __str__(self):
        return f"Profile for {self.user.username}"

    @property
    def has_paperless_token(self) -> bool:
        return bool(self.paperless_token)

    @property
    def has_firefly_token(self) -> bool:
        return bool(self.firefly_token)

    @property
    def is_configured(self) -> bool:
        """Check if user has configured both tokens."""
        return self.has_paperless_token and self.has_firefly_token


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create a UserProfile when a new User is created."""
    if created:
        UserProfile.objects.create(user=instance)


@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save the UserProfile when the User is saved."""
    if hasattr(instance, "profile"):
        instance.profile.save()


# =============================================================================
# State Store Models (state.db database via custom router)
# =============================================================================


class StateStoreModel(models.Model):
    """Abstract base class for all state store models."""

    class Meta:
        abstract = True
        managed = False  # Django won't create/modify these tables
        app_label = "web"


class PaperlessDocument(StateStoreModel):
    """
    Processed Paperless documents.
    Maps to: paperless_documents table in state.db
    """

    document_id = models.IntegerField(primary_key=True)
    source_hash = models.TextField()
    title = models.TextField(blank=True, null=True)
    document_type = models.TextField(blank=True, null=True)
    correspondent = models.TextField(blank=True, null=True)
    tags = models.TextField(blank=True, null=True, help_text="JSON array of tags")
    first_seen = models.TextField()
    last_seen = models.TextField()

    class Meta(StateStoreModel.Meta):
        db_table = "paperless_documents"
        verbose_name = "Paperless Document"
        verbose_name_plural = "Paperless Documents"

    def __str__(self):
        return f"Doc #{self.document_id}: {self.title or 'Untitled'}"


class Extraction(StateStoreModel):
    """
    Finance extractions from documents.
    Maps to: extractions table in state.db
    """

    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField()
    external_id = models.TextField(unique=True)
    extraction_json = models.TextField(help_text="JSON extraction data")
    overall_confidence = models.FloatField()
    review_state = models.TextField()
    created_at = models.TextField()
    reviewed_at = models.TextField(blank=True, null=True)
    review_decision = models.TextField(blank=True, null=True)
    llm_opt_out = models.BooleanField(default=False)

    class Meta(StateStoreModel.Meta):
        db_table = "extractions"
        verbose_name = "Extraction"
        verbose_name_plural = "Extractions"

    def __str__(self):
        return f"Extraction {self.external_id} (doc #{self.document_id})"


class Import(StateStoreModel):
    """
    Firefly III import records.
    Maps to: imports table in state.db
    """

    id = models.AutoField(primary_key=True)
    external_id = models.TextField(unique=True)
    document_id = models.IntegerField()
    firefly_id = models.IntegerField(blank=True, null=True)
    status = models.TextField()
    error_message = models.TextField(blank=True, null=True)
    payload_json = models.TextField()
    created_at = models.TextField()
    imported_at = models.TextField(blank=True, null=True)

    class Meta(StateStoreModel.Meta):
        db_table = "imports"
        verbose_name = "Import"
        verbose_name_plural = "Imports"

    def __str__(self):
        return f"Import {self.external_id} - {self.status}"


class FireflyCache(StateStoreModel):
    """
    Cached Firefly transactions for reconciliation.
    Maps to: firefly_cache table in state.db
    """

    firefly_id = models.IntegerField(primary_key=True)
    external_id = models.TextField(blank=True, null=True)
    internal_reference = models.TextField(blank=True, null=True)
    type = models.TextField()
    date = models.TextField()
    amount = models.TextField()
    description = models.TextField(blank=True, null=True)
    source_account = models.TextField(blank=True, null=True)
    destination_account = models.TextField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    category_name = models.TextField(blank=True, null=True)
    tags = models.TextField(blank=True, null=True, help_text="JSON array of tags")
    synced_at = models.TextField()
    match_status = models.TextField(default="UNMATCHED")
    matched_document_id = models.IntegerField(blank=True, null=True)
    match_confidence = models.FloatField(blank=True, null=True)
    deleted_at = models.TextField(blank=True, null=True)

    class Meta(StateStoreModel.Meta):
        db_table = "firefly_cache"
        verbose_name = "Firefly Transaction Cache"
        verbose_name_plural = "Firefly Transaction Cache"

    def __str__(self):
        return f"FF#{self.firefly_id}: {self.description or 'No description'} ({self.amount})"


class MatchProposal(StateStoreModel):
    """
    Match proposals between documents and Firefly transactions.
    Maps to: match_proposals table in state.db
    """

    id = models.AutoField(primary_key=True)
    firefly_id = models.IntegerField()
    document_id = models.IntegerField()
    match_score = models.FloatField()
    match_reasons = models.TextField(blank=True, null=True, help_text="JSON array of reasons")
    status = models.TextField(default="PENDING")
    created_at = models.TextField()
    reviewed_at = models.TextField(blank=True, null=True)

    class Meta(StateStoreModel.Meta):
        db_table = "match_proposals"
        verbose_name = "Match Proposal"
        verbose_name_plural = "Match Proposals"

    def __str__(self):
        return f"Proposal #{self.id}: Doc {self.document_id} ↔ FF {self.firefly_id} ({self.status})"


class VendorMapping(StateStoreModel):
    """
    Learned vendor mappings for auto-fill.
    Maps to: vendor_mappings table in state.db
    """

    id = models.AutoField(primary_key=True)
    vendor_pattern = models.TextField(unique=True)
    destination_account = models.TextField(blank=True, null=True)
    category = models.TextField(blank=True, null=True)
    tags = models.TextField(blank=True, null=True, help_text="JSON array of tags")
    created_at = models.TextField()
    updated_at = models.TextField()
    use_count = models.IntegerField(default=1)

    class Meta(StateStoreModel.Meta):
        db_table = "vendor_mappings"
        verbose_name = "Vendor Mapping"
        verbose_name_plural = "Vendor Mappings"

    def __str__(self):
        return f"{self.vendor_pattern} → {self.destination_account or self.category or 'Uncategorized'}"


class InterpretationRun(StateStoreModel):
    """
    Audit trail for interpretation runs.
    Maps to: interpretation_runs table in state.db
    """

    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(blank=True, null=True)
    firefly_id = models.IntegerField(blank=True, null=True)
    external_id = models.TextField(blank=True, null=True)
    run_timestamp = models.TextField()
    duration_ms = models.IntegerField(blank=True, null=True)
    pipeline_version = models.TextField()
    algorithm_version = models.TextField(blank=True, null=True)
    inputs_summary = models.TextField(help_text="JSON summary of inputs")
    rules_applied = models.TextField(blank=True, null=True, help_text="JSON rules applied")
    llm_result = models.TextField(blank=True, null=True, help_text="JSON LLM result if used")
    final_state = models.TextField()
    suggested_category = models.TextField(blank=True, null=True)
    suggested_splits = models.TextField(
        blank=True, null=True, help_text="JSON splits if applicable"
    )
    auto_applied = models.BooleanField(default=False)
    decision_source = models.TextField(blank=True, null=True)
    firefly_write_action = models.TextField(blank=True, null=True)
    firefly_target_id = models.IntegerField(blank=True, null=True)
    linkage_marker_written = models.TextField(blank=True, null=True)
    taxonomy_version = models.TextField(blank=True, null=True)

    class Meta(StateStoreModel.Meta):
        db_table = "interpretation_runs"
        verbose_name = "Interpretation Run"
        verbose_name_plural = "Interpretation Runs"

    def __str__(self):
        return f"Run #{self.id}: {self.final_state} ({self.decision_source or 'unknown'})"


class BankMatch(StateStoreModel):
    """
    Bank transaction matches.
    Maps to: bank_matches table in state.db
    """

    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(blank=True, null=True)
    bank_reference = models.TextField()
    bank_date = models.TextField()
    bank_amount = models.TextField()
    matched_at = models.TextField()

    class Meta(StateStoreModel.Meta):
        db_table = "bank_matches"
        verbose_name = "Bank Match"
        verbose_name_plural = "Bank Matches"

    def __str__(self):
        return f"Bank Match: {self.bank_reference} ({self.bank_amount})"


class Linkage(StateStoreModel):
    """
    Links between Paperless documents and Firefly transactions.
    Maps to: linkage table in state.db

    This is the SSOT for determining import eligibility:
    - PENDING: Not yet linked, cannot be imported
    - LINKED/AUTO_LINKED: Matched to existing Firefly transaction
    - ORPHAN: No matching transaction (e.g., cash payment)

    Only LINKED, AUTO_LINKED, and ORPHAN statuses can be imported.
    """

    id = models.AutoField(primary_key=True)
    extraction_id = models.IntegerField(unique=True)
    document_id = models.IntegerField()
    firefly_id = models.IntegerField(blank=True, null=True, help_text="NULL for orphans")
    link_type = models.TextField(
        default="PENDING",
        help_text="PENDING, LINKED, ORPHAN, or AUTO_LINKED",
    )
    confidence = models.FloatField(blank=True, null=True, help_text="Match confidence 0.0-1.0")
    match_reasons = models.TextField(blank=True, null=True, help_text="JSON array of match reasons")
    linked_at = models.TextField()
    linked_by = models.TextField(blank=True, null=True, help_text="AUTO, USER, etc.")
    notes = models.TextField(blank=True, null=True)

    class Meta(StateStoreModel.Meta):
        db_table = "linkage"
        verbose_name = "Linkage"
        verbose_name_plural = "Linkages"

    def __str__(self):
        link_target = f"FF#{self.firefly_id}" if self.firefly_id else "ORPHAN"
        return f"Link: Doc#{self.document_id} → {link_target} ({self.link_type})"
