"""
Migration to add firefly_importer_token field to UserProfile.

This allows users to store a separate API token for the Firefly Importer,
which may be different from their main Firefly III API token.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add firefly_importer_token field to UserProfile."""

    dependencies = [
        ("web", "0003_add_ai_schedule_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="firefly_importer_token",
            field=models.CharField(
                blank=True,
                default="",
                help_text="API token for Firefly Importer (separate from Firefly III token)",
                max_length=255,
            ),
        ),
    ]
