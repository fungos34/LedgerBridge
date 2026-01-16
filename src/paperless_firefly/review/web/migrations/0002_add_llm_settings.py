# Migration to add LLM settings fields to existing UserProfile table

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("web", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="llm_enabled",
            field=models.BooleanField(
                default=False, help_text="Enable LLM-assisted categorization"
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ollama_url",
            field=models.URLField(
                blank=True, default="", help_text="Ollama server URL (e.g., http://localhost:11434)"
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ollama_model",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Fast model name (e.g., qwen2.5:3b-instruct-q4_K_M)",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ollama_model_fallback",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Fallback model for complex cases (e.g., qwen2.5:7b-instruct-q4_K_M)",
                max_length=100,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="ollama_timeout",
            field=models.IntegerField(default=30, help_text="Request timeout in seconds"),
        ),
    ]
