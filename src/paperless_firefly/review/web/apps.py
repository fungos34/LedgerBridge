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


# Track per-user last job completion times for interval enforcement
_user_last_job_time: dict[int | None, float] = {}
_user_last_job_lock = threading.Lock()


def _ai_queue_worker_loop():
    """Background worker loop that processes AI jobs at intervals.

    The worker processes jobs for ALL users, respecting each user's individual settings:
    - Each user has their own ai_schedule_enabled, interval, and active hours
    - Jobs are processed based on the owner's settings
    - If a job has no owner (legacy), default settings apply
    
    Interval Logic:
    - If batch_size == 1: Wait for user's interval between each job
    - If batch_size > 1: Process batch_size jobs sequentially without interval delays,
      then wait for interval before next batch
    - AI can only handle one job at a time (sequential processing)
    """
    # Wait a bit for Django to fully initialize
    time.sleep(5)

    logger.info("AI queue worker loop starting - will check for pending jobs")

    # Check every 30 seconds for new jobs (responsive polling)
    BASE_CHECK_INTERVAL = 30

    while not _ai_worker_shutdown.is_set():
        try:
            _process_ai_queue_with_intervals()
        except Exception as e:
            logger.error(f"Error in AI queue worker: {e}", exc_info=True)

        # Sleep for base interval
        logger.debug(f"AI worker sleeping for {BASE_CHECK_INTERVAL} seconds before next check")

        # Sleep in small increments so we can respond to shutdown quickly
        sleep_until = time.time() + BASE_CHECK_INTERVAL
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


def _is_user_schedule_enabled(user_id: int | None) -> bool:
    """Check if AI scheduling is enabled for a specific user.

    Args:
        user_id: The user ID to check. If None, uses global default settings.

    Returns:
        True if the user has AI scheduling enabled, False otherwise.
    """
    if user_id is None:
        # Legacy job with no owner - check global settings
        return _is_ai_schedule_enabled()

    try:
        from django.contrib.auth.models import User


        user = User.objects.get(id=user_id)
        profile = user.profile
        return profile.ai_schedule_enabled
    except Exception:
        # User not found or no profile - default to enabled
        return True


def _is_within_user_active_hours(user_id: int | None) -> bool:
    """Check if current time is within a specific user's active hours.

    Args:
        user_id: The user ID to check. If None, uses global default settings.

    Returns:
        True if within the user's active hours, False otherwise.
    """
    if user_id is None:
        # Legacy job with no owner - check global settings
        return _is_within_active_hours()

    try:
        from django.contrib.auth.models import User


        user = User.objects.get(id=user_id)
        profile = user.profile
        current_hour = datetime.now().hour
        return profile.ai_schedule_start_hour <= current_hour < profile.ai_schedule_end_hour
    except Exception:
        # User not found or no profile - default to always active
        return True


def _get_user_interval_seconds(user_id: int | None) -> int:
    """Get the AI processing interval in seconds for a user."""
    if user_id is None:
        return 60 * 60  # Default: 1 hour for legacy jobs

    try:
        from django.contrib.auth.models import User

        user = User.objects.get(id=user_id)
        profile = user.profile
        return profile.ai_schedule_interval_minutes * 60
    except Exception:
        return 60 * 60  # Default: 1 hour


def _get_user_batch_size(user_id: int | None) -> int:
    """Get the AI batch size for a user."""
    if user_id is None:
        return 1  # Default: 1 job at a time

    try:
        from django.contrib.auth.models import User

        user = User.objects.get(id=user_id)
        profile = user.profile
        return max(1, profile.ai_schedule_batch_size)
    except Exception:
        return 1


def _can_process_job_for_user(user_id: int | None) -> bool:
    """Check if enough time has passed since last job for this user.
    
    Returns True if:
    - No previous job for this user, or
    - The user's interval has elapsed since their last job
    """
    with _user_last_job_lock:
        last_time = _user_last_job_time.get(user_id)
    
    if last_time is None:
        return True
    
    interval = _get_user_interval_seconds(user_id)
    elapsed = time.time() - last_time
    
    if elapsed >= interval:
        return True
    
    logger.debug(
        f"User {user_id}: {elapsed:.0f}s elapsed, need {interval}s - waiting"
    )
    return False


def _record_job_completion(user_id: int | None):
    """Record that a job was completed for this user."""
    with _user_last_job_lock:
        _user_last_job_time[user_id] = time.time()


def _process_ai_queue_with_intervals():
    """Process AI jobs respecting per-user intervals.
    
    Logic:
    - For each user with pending jobs, check if their interval has elapsed
    - If batch_size > 1, process that many jobs sequentially without interval delays
    - Only one job at a time (AI handles one job at a time)
    - After processing batch, record completion time for interval tracking
    """
    logger.info("AI worker checking for pending jobs...")

    try:
        # Get dependencies
        from django.contrib.auth.models import User

        from paperless_firefly.paperless_client.client import PaperlessClient
        from paperless_firefly.services.ai_queue import AIJobQueueService
        from paperless_firefly.spark_ai.service import SparkAIService
        from paperless_firefly.state_store.sqlite_store import StateStore

        # Load config
        config = _load_config()
        if not config:
            logger.warning("No config available for AI queue processing")
            return

        if not hasattr(config, "llm") or not config.llm or not config.llm.enabled:
            logger.info("LLM not enabled in config")
            return

        # Initialize services
        state_store = StateStore(config.state_db_path)
        paperless_client = PaperlessClient(
            base_url=config.paperless.base_url,
            token=config.paperless.token,
        )

        # Fetch categories from Firefly
        from paperless_firefly.firefly_client.client import FireflyClient

        categories = []
        try:
            firefly_client = FireflyClient(
                base_url=config.firefly.base_url,
                token=config.firefly.token,
            )
            firefly_categories = firefly_client.list_categories()
            categories = [cat.name for cat in firefly_categories]
            logger.info(f"Loaded {len(categories)} categories from Firefly")
        except Exception as e:
            logger.warning(f"Could not fetch Firefly categories: {e}")

        ai_service = SparkAIService(
            state_store=state_store, config=config, categories=categories
        )
        queue_service = AIJobQueueService(state_store=state_store, config=config)

        # Get all pending jobs
        all_jobs = state_store.get_next_ai_jobs(limit=50, check_schedule=True)
        if not all_jobs:
            logger.info("No pending AI jobs in queue")
            return

        # Group jobs by user_id
        jobs_by_user: dict[int | None, list] = {}
        for job in all_jobs:
            user_id = job.get("user_id")
            if user_id not in jobs_by_user:
                jobs_by_user[user_id] = []
            jobs_by_user[user_id].append(job)

        logger.info(f"Found jobs for {len(jobs_by_user)} user(s)")

        # Process jobs for each user that's ready
        for user_id, user_jobs in jobs_by_user.items():
            if _ai_worker_shutdown.is_set():
                break

            # Check user settings
            if not _is_user_schedule_enabled(user_id):
                logger.debug(f"User {user_id}: schedule disabled, skipping {len(user_jobs)} jobs")
                continue

            if not _is_within_user_active_hours(user_id):
                logger.debug(f"User {user_id}: outside active hours, skipping")
                continue

            # Check if interval has elapsed for this user
            if not _can_process_job_for_user(user_id):
                continue

            # Get user's batch size
            batch_size = _get_user_batch_size(user_id)
            jobs_to_process = user_jobs[:batch_size]

            logger.info(
                f"User {user_id}: processing batch of {len(jobs_to_process)} job(s) "
                f"(batch_size={batch_size})"
            )

            # Process jobs sequentially (AI handles one at a time)
            for job in jobs_to_process:
                if _ai_worker_shutdown.is_set():
                    break

                job_id = job["id"]
                document_id = job["document_id"]

                try:
                    logger.info(f"Processing AI job #{job_id} for document {document_id}")

                    # Get user-specific Paperless client
                    user_paperless_client = paperless_client
                    if user_id:
                        try:
                            user = User.objects.get(id=user_id)
                            profile = user.profile
                            if profile.paperless_token:
                                user_paperless_client = PaperlessClient(
                                    base_url=profile.paperless_url or config.paperless.base_url,
                                    token=profile.paperless_token,
                                )
                        except Exception:
                            pass

                    success = queue_service.process_job(
                        job=job,
                        ai_service=ai_service,
                        paperless_client=user_paperless_client,
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

            # Record completion time after processing batch
            _record_job_completion(user_id)
            logger.info(f"User {user_id}: batch complete, next job in {_get_user_interval_seconds(user_id)}s")

    except Exception as e:
        logger.error(f"Error in AI queue processing: {e}", exc_info=True)


def _process_ai_queue_batch():
    """Process a batch of pending AI jobs (legacy wrapper).

    This function respects per-user settings:
    - Checks if each job's owner has AI scheduling enabled
    - Checks if within the owner's active hours
    - Uses the owner's batch size preference
    """
    logger.info("AI worker checking for pending jobs...")

    try:
        # Get dependencies
        from django.contrib.auth.models import User

        from paperless_firefly.paperless_client.client import PaperlessClient
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

        # Initialize services
        state_store = StateStore(config.state_db_path)
        paperless_client = PaperlessClient(
            base_url=config.paperless.base_url,
            token=config.paperless.token,
        )

        # Fetch categories from Firefly for AI suggestions
        from paperless_firefly.firefly_client.client import FireflyClient

        categories = []
        try:
            firefly_client = FireflyClient(
                base_url=config.firefly.base_url,
                token=config.firefly.token,
            )
            firefly_categories = firefly_client.list_categories()
            categories = [cat.name for cat in firefly_categories]
            logger.info(f"Loaded {len(categories)} categories from Firefly for AI service")
        except Exception as e:
            logger.warning(f"Could not fetch Firefly categories for AI service: {e}")

        ai_service = SparkAIService(state_store=state_store, config=config, categories=categories)
        queue_service = AIJobQueueService(
            state_store=state_store,
            config=config,
        )

        # Get next pending jobs (check_schedule=True respects scheduled_for time)
        jobs = state_store.get_next_ai_jobs(limit=10, check_schedule=True)

        if not jobs:
            logger.info("No pending AI jobs in queue")
            return

        logger.info(f"Found {len(jobs)} pending AI job(s), checking user settings...")

        processed_count = 0
        skipped_count = 0

        for job in jobs:
            if _ai_worker_shutdown.is_set():
                break

            job_id = job["id"]
            document_id = job["document_id"]
            job_user_id = job.get("user_id")

            # Check per-user settings
            if not _is_user_schedule_enabled(job_user_id):
                logger.debug(f"Skipping job #{job_id} - user schedule disabled")
                skipped_count += 1
                continue

            if not _is_within_user_active_hours(job_user_id):
                logger.debug(f"Skipping job #{job_id} - outside user's active hours")
                skipped_count += 1
                continue

            try:
                logger.info(f"Processing AI job #{job_id} for document {document_id}")

                # Get user-specific Paperless client if available
                user_paperless_client = paperless_client
                if job_user_id:
                    try:
                        user = User.objects.get(id=job_user_id)
                        profile = user.profile
                        if profile.paperless_token:
                            user_paperless_client = PaperlessClient(
                                base_url=profile.paperless_url or config.paperless.base_url,
                                token=profile.paperless_token,
                            )
                    except Exception:
                        pass  # Use default client

                success = queue_service.process_job(
                    job=job,
                    ai_service=ai_service,
                    paperless_client=user_paperless_client,
                )

                if success:
                    logger.info(f"AI job #{job_id} completed successfully")
                    processed_count += 1
                else:
                    logger.warning(f"AI job #{job_id} failed")

            except Exception as e:
                logger.error(f"Error processing AI job #{job_id}: {e}", exc_info=True)
                try:
                    state_store.fail_ai_job(job_id, str(e), can_retry=True)
                except Exception:
                    pass

        if processed_count > 0 or skipped_count > 0:
            logger.info(
                f"AI batch complete: {processed_count} processed, {skipped_count} skipped (settings)"
            )

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
