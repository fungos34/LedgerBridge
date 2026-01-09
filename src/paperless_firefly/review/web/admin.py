"""
Django admin configuration for LedgerBridge models.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import UserProfile


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
