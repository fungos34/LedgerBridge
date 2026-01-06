"""
Django models for user settings and API token storage.
"""

from django.contrib.auth.models import User
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver


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
