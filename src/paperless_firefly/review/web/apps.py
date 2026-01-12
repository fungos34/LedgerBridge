"""
Django application configuration with background AI queue processing.

This module configures the Django app and starts a background thread
for automatic AI job queue processing when the app is ready.
"""

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Global flag to track if the background worker is running
_ai_worker_started = False
_ai_worker_thread = None
_ai_worker_shutdown = threading.Event()


class WebConfig(AppConfig):
    """Django app configuration for the review web application."""

    name = "paperless_firefly.review.web"
    verbose_name = "Paperless-Firefly Review Web"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        """Called when Django is ready - start background worker if enabled."""
        # Only start in main process, not in Django management commands or reloader
        # RUN_MAIN is set to "true" when running with auto-reload (the child process)
        # When not using auto-reload, neither RUN_MAIN nor DJANGO_AUTORELOAD is set
        run_main = os.environ.get("RUN_MAIN")
        autoreload = os.environ.get("DJANGO_AUTORELOAD")

        # Start worker if:
        # 1. RUN_MAIN is "true" (child process in auto-reload mode), OR
        # 2. Neither RUN_MAIN nor DJANGO_AUTORELOAD is set (normal run without reload)
        should_start = (run_main == "true") or (run_main is None and autoreload is None)

        if should_start:
            self._start_ai_queue_worker()

    def _start_ai_queue_worker(self):
        """Start the background AI queue worker thread."""
        global _ai_worker_started, _ai_worker_thread

        if _ai_worker_started:
            return

        # Don't start during Django setup commands (migrate, collectstatic, etc.)
        import sys

        if len(sys.argv) > 1 and sys.argv[1] in (
            "migrate",
            "makemigrations",
            "collectstatic",
            "createsuperuser",
            "shell",
            "dbshell",
            "check",
            "test",
            "help",
        ):
            logger.info("Skipping AI worker start for management command: %s", sys.argv[1])
            return

        _ai_worker_started = True
        _ai_worker_thread = threading.Thread(
            target=_ai_queue_worker_loop,
            name="ai-queue-worker",
            daemon=True,  # Thread will be killed when main process exits
        )
        _ai_worker_thread.start()
        logger.info("Started background AI queue worker thread")


def _ai_queue_worker_loop():
    """Background worker loop that processes AI jobs at intervals."""
    # Wait a bit for Django to fully initialize
    time.sleep(5)

    logger.info("AI queue worker loop starting - will check for pending jobs")

    while not _ai_worker_shutdown.is_set():
        try:
            _process_ai_queue_batch()
        except Exception as e:
            logger.error(f"Error in AI queue worker: {e}", exc_info=True)

        # Get user settings for interval
        interval_seconds = _get_processing_interval()
        logger.info(f"AI worker sleeping for {interval_seconds} seconds before next check")

        # Sleep in small increments so we can respond to shutdown quickly
        sleep_until = time.time() + interval_seconds
        while time.time() < sleep_until and not _ai_worker_shutdown.is_set():
            time.sleep(1)

    logger.info("AI queue worker stopped")


def _get_processing_interval() -> int:
    """Get the processing interval in seconds from user settings."""
    try:
        from paperless_firefly.review.web.models import UserProfile

        profile = UserProfile.objects.first()
        if profile:
            # Always use the profile's configured interval
            interval = profile.ai_schedule_interval_minutes * 60
            logger.info(
                f"AI worker interval from profile: {profile.ai_schedule_interval_minutes} minutes "
                f"({interval} seconds), enabled={profile.ai_schedule_enabled}"
            )
            return interval
        else:
            logger.info("No UserProfile found, using default interval")
    except Exception as e:
        logger.warning(f"Could not get interval from profile: {e}")

    # Default: 5 minutes (reduced from 60 for more responsive processing)
    default_interval = 5 * 60
    logger.info(f"Using default AI worker interval: {default_interval} seconds")
    return default_interval


def _is_within_active_hours() -> bool:
    """Check if current time is within scheduled processing hours."""
    try:
        from paperless_firefly.review.web.models import UserProfile

        profile = UserProfile.objects.first()
        if profile:
            current_hour = datetime.now().hour
            within = profile.ai_schedule_start_hour <= current_hour < profile.ai_schedule_end_hour
            if not within:
                logger.info(
                    f"Outside active hours: current={current_hour}, "
                    f"active={profile.ai_schedule_start_hour}-{profile.ai_schedule_end_hour}"
                )
            return within
    except Exception as e:
        logger.debug(f"Could not check active hours: {e}")

    return True  # Default: always active


def _is_ai_schedule_enabled() -> bool:
    """Check if AI scheduling is enabled in user settings."""
    try:
        from paperless_firefly.review.web.models import UserProfile

        profile = UserProfile.objects.first()
        if profile:
            enabled = profile.ai_schedule_enabled
            if not enabled:
                logger.info("AI schedule is disabled in user settings")
            return enabled
        else:
            logger.info("No UserProfile found - AI schedule defaults to enabled")
    except Exception as e:
        logger.warning(f"Could not check AI schedule setting: {e}")

    return True  # Default: enabled


def _process_ai_queue_batch():
    """Process a batch of pending AI jobs."""
    logger.info("AI worker checking for pending jobs...")

    # Check if scheduling is enabled
    if not _is_ai_schedule_enabled():
        return

    # Check if within active hours
    if not _is_within_active_hours():
        return

    try:
        # Get dependencies
        from paperless_firefly.paperless_client.client import PaperlessClient
        from paperless_firefly.review.web.models import UserProfile
        from paperless_firefly.services.ai_queue import AIJobQueueService
        from paperless_firefly.spark_ai.service import SparkAIService
        from paperless_firefly.state_store.sqlite_store import StateStore

        # Load config
        config = _load_config()
        if not config:
            logger.warning("No config available for AI queue processing - check config.yaml")
            return

        # Check if LLM is configured
        if not hasattr(config, "llm") or not config.llm or not config.llm.enabled:
            logger.info("LLM not enabled in config - skipping AI queue processing")
            return

        # Get user settings
        batch_size = 1
        try:
            profile = UserProfile.objects.first()
            if profile:
                batch_size = profile.ai_schedule_batch_size
                logger.debug(f"Using batch size from profile: {batch_size}")
        except Exception:
            pass

        # Initialize services
        state_store = StateStore(config.state_db_path)
        ai_service = SparkAIService(state_store=state_store, config=config)
        paperless_client = PaperlessClient(
            base_url=config.paperless.base_url,
            token=config.paperless.token,
        )
        queue_service = AIJobQueueService(
            state_store=state_store,
            config=config,
        )

        # Get next jobs - process ALL pending jobs, regardless of scheduled time
        # The scheduled_for field is for user-facing scheduling hints, but the background
        # worker should process the entire queue sorted by priority
        jobs = state_store.get_next_ai_jobs(limit=batch_size, check_schedule=False)

        if not jobs:
            logger.info("No pending AI jobs in queue")
            return

        logger.info(f"Processing {len(jobs)} AI job(s) from queue")

        for job in jobs:
            if _ai_worker_shutdown.is_set():
                break

            job_id = job["id"]
            document_id = job["document_id"]

            try:
                logger.info(f"Processing AI job #{job_id} for document {document_id}")
                success = queue_service.process_job(
                    job=job,
                    ai_service=ai_service,
                    paperless_client=paperless_client,
                )

                if success:
                    logger.info(f"AI job #{job_id} completed successfully")
                else:
                    logger.warning(f"AI job #{job_id} failed")

            except Exception as e:
                logger.error(f"Error processing AI job #{job_id}: {e}", exc_info=True)
                try:
                    state_store.fail_ai_job(job_id, str(e), can_retry=True)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"Error in AI queue batch processing: {e}", exc_info=True)


def _load_config():
    """Load application configuration."""
    try:
        from paperless_firefly.config import Config, load_config

        # Try default locations
        for path in [Path("config.yaml"), Path("/app/config/config.yaml")]:
            if path.exists():
                logger.debug(f"Loading config from {path}")
                return load_config(path)

        # Fall back to environment-based config
        logger.debug("Attempting to load config from environment")
        return Config.from_env()

    except Exception as e:
        logger.warning(f"Could not load config: {e}")
        return None


def stop_ai_worker():
    """Signal the AI worker thread to stop."""
    global _ai_worker_started
    _ai_worker_shutdown.set()
    _ai_worker_started = False

    if _ai_worker_thread and _ai_worker_thread.is_alive():
        _ai_worker_thread.join(timeout=5)
