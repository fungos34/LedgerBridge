"""
Django admin configuration for SparkLink models.

Provides admin interfaces for:
1. User management (with UserProfile inline)
2. State store models (read/write access to state.db tables)
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from .models import (
    AIJobQueue,
    BankMatch,
    Extraction,
    FireflyCache,
    Import,
    InterpretationRun,
    Linkage,
    MatchProposal,
    PaperlessDocument,
    UserProfile,
    VendorMapping,
)

# =============================================================================
# User Profile Admin (Default Database)
# =============================================================================


class UserProfileInline(admin.StackedInline):
    """Inline admin for UserProfile within User admin."""

    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"


class UserAdmin(BaseUserAdmin):
    """Extended User admin with profile inline."""

    inlines = (UserProfileInline,)
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "get_has_tokens",
    )
    list_select_related = ("profile",)

    def get_has_tokens(self, instance):
        """Check if user has API tokens configured."""
        if hasattr(instance, "profile"):
            return instance.profile.is_configured
        return False

    get_has_tokens.short_description = "Tokens Configured"
    get_has_tokens.boolean = True

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return []
        return super().get_inline_instances(request, obj)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for UserProfile model."""

    list_display = (
        "user",
        "has_paperless_token",
        "has_firefly_token",
        "default_source_account",
        "auto_import_threshold",
        "created_at",
    )
    list_filter = ("auto_import_threshold", "review_threshold")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            "User",
            {
                "fields": ("user",),
            },
        ),
        (
            "Paperless-ngx",
            {
                "fields": ("paperless_token", "paperless_url"),
            },
        ),
        (
            "Firefly III",
            {
                "fields": ("firefly_token", "firefly_url", "default_source_account"),
            },
        ),
        (
            "Thresholds",
            {
                "fields": ("auto_import_threshold", "review_threshold"),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


# Re-register UserAdmin
admin.site.unregister(User)
admin.site.register(User, UserAdmin)

# Customize admin site header
admin.site.site_header = "SparkLink Administration"
admin.site.site_title = "SparkLink Admin"
admin.site.index_title = "Dashboard"


# =============================================================================
# State Store Model Admins (state.db Database)
# =============================================================================


class StateStoreAdmin(admin.ModelAdmin):
    """Base admin class for state store models."""

    using = "state_store"

    def save_model(self, request, obj, form, change):
        obj.save(using=self.using)

    def delete_model(self, request, obj):
        obj.delete(using=self.using)

    def get_queryset(self, request):
        return super().get_queryset(request).using(self.using)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        kwargs["using"] = self.using
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        kwargs["using"] = self.using
        return super().formfield_for_manytomany(db_field, request, **kwargs)


@admin.register(PaperlessDocument)
class PaperlessDocumentAdmin(StateStoreAdmin):
    """Admin for Paperless documents."""

    list_display = (
        "document_id",
        "title",
        "document_type",
        "correspondent",
        "first_seen",
        "last_seen",
    )
    list_filter = ("document_type", "correspondent")
    search_fields = ("document_id", "title", "correspondent")
    ordering = ("-last_seen",)
    readonly_fields = ("first_seen", "last_seen")


@admin.register(Extraction)
class ExtractionAdmin(StateStoreAdmin):
    """Admin for finance extractions."""

    list_display = (
        "id",
        "document_id",
        "external_id_short",
        "overall_confidence_pct",
        "review_state",
        "review_decision",
        "created_at",
    )
    list_filter = ("review_state", "review_decision", "llm_opt_out")
    search_fields = ("external_id", "document_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)

    def external_id_short(self, obj):
        """Truncate external ID for display."""
        return obj.external_id[:20] + "..." if len(obj.external_id) > 20 else obj.external_id

    external_id_short.short_description = "External ID"

    def overall_confidence_pct(self, obj):
        """Display confidence as percentage."""
        return f"{obj.overall_confidence * 100:.0f}%"

    overall_confidence_pct.short_description = "Confidence"


@admin.register(Import)
class ImportAdmin(StateStoreAdmin):
    """Admin for Firefly imports."""

    list_display = (
        "id",
        "external_id_short",
        "document_id",
        "firefly_id",
        "status",
        "created_at",
        "imported_at",
    )
    list_filter = ("status",)
    search_fields = ("external_id", "document_id", "firefly_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "imported_at")

    def external_id_short(self, obj):
        return obj.external_id[:20] + "..." if len(obj.external_id) > 20 else obj.external_id

    external_id_short.short_description = "External ID"


@admin.register(FireflyCache)
class FireflyCacheAdmin(StateStoreAdmin):
    """Admin for cached Firefly transactions."""

    list_display = (
        "firefly_id",
        "type",
        "date",
        "amount",
        "description_short",
        "match_status",
        "is_deleted",
        "synced_at",
    )
    list_filter = ("type", "match_status", "source_account", "destination_account")
    search_fields = ("firefly_id", "description", "external_id", "internal_reference")
    ordering = ("-date",)
    readonly_fields = ("synced_at", "deleted_at")

    def description_short(self, obj):
        """Truncate description for display."""
        if not obj.description:
            return "-"
        return obj.description[:40] + "..." if len(obj.description) > 40 else obj.description

    description_short.short_description = "Description"

    def is_deleted(self, obj):
        """Show soft-delete status."""
        return obj.deleted_at is not None

    is_deleted.boolean = True
    is_deleted.short_description = "Deleted"


@admin.register(MatchProposal)
class MatchProposalAdmin(StateStoreAdmin):
    """Admin for match proposals."""

    list_display = (
        "id",
        "document_id",
        "firefly_id",
        "match_score_pct",
        "status",
        "created_at",
        "reviewed_at",
    )
    list_filter = ("status",)
    search_fields = ("document_id", "firefly_id")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "reviewed_at")

    def match_score_pct(self, obj):
        """Display match score as percentage."""
        return f"{obj.match_score * 100:.0f}%"

    match_score_pct.short_description = "Match Score"


@admin.register(VendorMapping)
class VendorMappingAdmin(StateStoreAdmin):
    """Admin for vendor mappings."""

    list_display = (
        "id",
        "vendor_pattern",
        "destination_account",
        "category",
        "use_count",
        "updated_at",
    )
    search_fields = ("vendor_pattern", "destination_account", "category")
    ordering = ("-use_count",)
    readonly_fields = ("created_at", "updated_at", "use_count")


@admin.register(InterpretationRun)
class InterpretationRunAdmin(StateStoreAdmin):
    """Admin for interpretation run audit trail."""

    list_display = (
        "id",
        "document_id",
        "firefly_id",
        "final_state_colored",
        "decision_source",
        "firefly_write_action",
        "auto_applied",
        "run_timestamp",
    )
    list_filter = ("final_state", "decision_source", "firefly_write_action", "auto_applied")
    search_fields = ("document_id", "firefly_id", "external_id")
    ordering = ("-run_timestamp",)
    readonly_fields = (
        "run_timestamp",
        "duration_ms",
        "pipeline_version",
        "algorithm_version",
        "inputs_summary",
        "rules_applied",
        "llm_result",
        "linkage_marker_written",
        "taxonomy_version",
    )

    def final_state_colored(self, obj):
        """Color-code the final state."""
        colors = {
            "GREEN": "#28a745",
            "YELLOW": "#ffc107",
            "RED": "#dc3545",
        }
        color = colors.get(obj.final_state, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.final_state,
        )

    final_state_colored.short_description = "Final State"


@admin.register(BankMatch)
class BankMatchAdmin(StateStoreAdmin):
    """Admin for bank matches."""

    list_display = (
        "id",
        "document_id",
        "bank_reference",
        "bank_date",
        "bank_amount",
        "matched_at",
    )
    search_fields = ("bank_reference", "document_id")
    ordering = ("-matched_at",)
    readonly_fields = ("matched_at",)


@admin.register(Linkage)
class LinkageAdmin(StateStoreAdmin):
    """Admin for document-transaction linkages.

    The linkage table is the SSOT for import eligibility.
    """

    list_display = (
        "id",
        "document_id",
        "extraction_id",
        "firefly_id",
        "link_type_colored",
        "confidence_pct",
        "linked_by",
        "linked_at",
    )
    list_filter = ("link_type", "linked_by")
    search_fields = ("document_id", "extraction_id", "firefly_id")
    ordering = ("-linked_at",)
    readonly_fields = ("linked_at",)

    def link_type_colored(self, obj):
        """Color-code the link type."""
        colors = {
            "LINKED": "#28a745",
            "AUTO_LINKED": "#17a2b8",
            "ORPHAN": "#ffc107",
            "PENDING": "#6c757d",
        }
        color = colors.get(obj.link_type, "#6c757d")
        text_color = "white" if obj.link_type != "ORPHAN" else "black"
        return format_html(
            '<span style="background-color: {}; color: {}; padding: 2px 8px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            text_color,
            obj.link_type,
        )

    link_type_colored.short_description = "Link Type"

    def confidence_pct(self, obj):
        """Display confidence as percentage."""
        if obj.confidence is None:
            return "—"
        return f"{obj.confidence * 100:.0f}%"

    confidence_pct.short_description = "Confidence"


@admin.register(AIJobQueue)
class AIJobQueueAdmin(StateStoreAdmin):
    """Admin for AI job queue.

    Allows viewing and managing scheduled AI interpretation jobs.
    """

    list_display = (
        "id",
        "document_id",
        "extraction_id",
        "status_colored",
        "priority",
        "retry_count",
        "created_by",
        "scheduled_at",
        "scheduled_for",
        "started_at",
        "completed_at",
    )
    list_filter = ("status", "created_by", "priority")
    search_fields = ("document_id", "extraction_id", "external_id", "error_message")
    ordering = ("-scheduled_at",)
    readonly_fields = ("scheduled_at", "started_at", "completed_at")
    actions = ["cancel_jobs", "retry_jobs", "reset_to_pending"]

    fieldsets = (
        (
            "Job Info",
            {
                "fields": ("document_id", "extraction_id", "external_id", "status", "priority"),
            },
        ),
        (
            "Scheduling",
            {
                "fields": ("scheduled_at", "scheduled_for", "started_at", "completed_at"),
            },
        ),
        (
            "Results",
            {
                "fields": ("suggestions_json", "error_message"),
                "classes": ("collapse",),
            },
        ),
        (
            "Retry Info",
            {
                "fields": ("retry_count", "max_retries"),
            },
        ),
        (
            "Metadata",
            {
                "fields": ("created_by", "notes"),
            },
        ),
    )

    def status_colored(self, obj):
        """Color-code the job status."""
        colors = {
            "PENDING": "#6c757d",
            "PROCESSING": "#17a2b8",
            "COMPLETED": "#28a745",
            "FAILED": "#dc3545",
            "CANCELLED": "#ffc107",
        }
        color = colors.get(obj.status, "#6c757d")
        text_color = "white" if obj.status != "CANCELLED" else "black"
        return format_html(
            '<span style="background-color: {}; color: {}; padding: 2px 8px; '
            'border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            text_color,
            obj.status,
        )

    status_colored.short_description = "Status"

    @admin.action(description="Cancel selected jobs")
    def cancel_jobs(self, request, queryset):
        """Cancel pending/processing jobs."""
        updated = queryset.filter(status__in=["PENDING", "PROCESSING"]).update(
            status="CANCELLED"
        )
        self.message_user(request, f"Cancelled {updated} job(s).")

    @admin.action(description="Retry failed jobs")
    def retry_jobs(self, request, queryset):
        """Reset failed jobs for retry."""
        updated = queryset.filter(status="FAILED").update(
            status="PENDING",
            error_message=None,
        )
        self.message_user(request, f"Reset {updated} job(s) for retry.")

    @admin.action(description="Reset to pending")
    def reset_to_pending(self, request, queryset):
        """Reset any jobs to pending status."""
        updated = queryset.update(
            status="PENDING",
            started_at=None,
            completed_at=None,
            error_message=None,
        )
        self.message_user(request, f"Reset {updated} job(s) to pending.")
