"""
Migration 0003: Add AI schedule settings to UserProfile.

Adds fields for configuring the AI job queue scheduler.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Add AI schedule settings to UserProfile."""

    dependencies = [
        ("web", "0002_add_llm_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Automatically schedule AI interpretation for new documents",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_interval_minutes",
            field=models.IntegerField(
                default=60,
                help_text="Process one job every N minutes (e.g., 60 = one per hour)",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_batch_size",
            field=models.IntegerField(
                default=1,
                help_text="Number of jobs to process per scheduled run",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_max_retries",
            field=models.IntegerField(
                default=3,
                help_text="Maximum retry attempts for failed jobs",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_start_hour",
            field=models.IntegerField(
                default=0,
                help_text="Start processing jobs from this hour (0-23)",
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ai_schedule_end_hour",
            field=models.IntegerField(
                default=24,
                help_text="Stop processing jobs after this hour (0-24, 24=no limit)",
            ),
        ),
    ]
