"""
Process AI Job Queue management command.

Processes pending AI interpretation jobs from the queue.
Can be run as a one-shot command or in daemon mode.

Usage:
    # Process next batch of jobs
    python manage.py process_ai_queue

    # Run in daemon mode (continuous processing)
    python manage.py process_ai_queue --daemon

    # Process specific number of jobs
    python manage.py process_ai_queue --batch-size 5

    # Run with custom interval
    python manage.py process_ai_queue --daemon --interval 30
"""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Process AI interpretation jobs from the queue."""

    help = "Process pending AI interpretation jobs from the queue"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown_requested = False

    def add_arguments(self, parser):
        """Add command arguments."""
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="Run in daemon mode (continuous processing)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Processing interval in minutes (overrides user setting)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=None,
            help="Number of jobs to process per run (overrides user setting)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be processed without actually processing",
        )
        parser.add_argument(
            "--config",
            type=str,
            default=None,
            help="Path to config file",
        )

    def handle(self, *args, **options):
        """Execute the command."""
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

        # Load configuration
        try:
            config = self._load_config(options.get("config"))
            state_store = self._get_state_store(config)
            ai_service = self._get_ai_service(config)
            paperless_client = self._get_paperless_client(config)
        except Exception as e:
            raise CommandError(f"Failed to initialize: {e}")

        # Get user settings
        user_settings = self._get_user_settings()

        # Determine processing parameters
        interval_minutes = options["interval"] or user_settings.get(
            "ai_schedule_interval_minutes", 60
        )
        batch_size = options["batch_size"] or user_settings.get(
            "ai_schedule_batch_size", 1
        )
        start_hour = user_settings.get("ai_schedule_start_hour", 0)
        end_hour = user_settings.get("ai_schedule_end_hour", 24)

        daemon_mode = options["daemon"]
        dry_run = options["dry_run"]

        self.stdout.write(
            self.style.SUCCESS(
                f"AI Queue Processor Started\n"
                f"  Mode: {'Daemon' if daemon_mode else 'One-shot'}\n"
                f"  Interval: {interval_minutes} minutes\n"
                f"  Batch size: {batch_size}\n"
                f"  Active hours: {start_hour}:00 - {end_hour}:00\n"
                f"  Dry run: {dry_run}"
            )
        )

        if daemon_mode:
            self._run_daemon(
                state_store=state_store,
                ai_service=ai_service,
                paperless_client=paperless_client,
                interval_minutes=interval_minutes,
                batch_size=batch_size,
                start_hour=start_hour,
                end_hour=end_hour,
                dry_run=dry_run,
            )
        else:
            self._process_batch(
                state_store=state_store,
                ai_service=ai_service,
                paperless_client=paperless_client,
                batch_size=batch_size,
                start_hour=start_hour,
                end_hour=end_hour,
                dry_run=dry_run,
            )

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self._shutdown_requested = True
        self.stdout.write(
            self.style.WARNING("\nShutdown requested, finishing current job...")
        )

    def _run_daemon(
        self,
        state_store,
        ai_service,
        paperless_client,
        interval_minutes: int,
        batch_size: int,
        start_hour: int,
        end_hour: int,
        dry_run: bool,
    ):
        """Run in daemon mode with continuous processing."""
        interval_seconds = interval_minutes * 60

        while not self._shutdown_requested:
            try:
                processed = self._process_batch(
                    state_store=state_store,
                    ai_service=ai_service,
                    paperless_client=paperless_client,
                    batch_size=batch_size,
                    start_hour=start_hour,
                    end_hour=end_hour,
                    dry_run=dry_run,
                )

                if processed == 0:
                    self.stdout.write(
                        self.style.NOTICE(
                            f"No jobs to process. Sleeping for {interval_minutes} minutes..."
                        )
                    )

                # Sleep until next interval (interruptible)
                sleep_until = time.time() + interval_seconds
                while time.time() < sleep_until and not self._shutdown_requested:
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Error in daemon loop: {e}")
                self.stderr.write(self.style.ERROR(f"Error: {e}"))
                # Sleep a bit on error before retrying
                time.sleep(30)

        self.stdout.write(self.style.SUCCESS("Daemon shutdown complete"))

    def _process_batch(
        self,
        state_store,
        ai_service,
        paperless_client,
        batch_size: int,
        start_hour: int,
        end_hour: int,
        dry_run: bool,
    ) -> int:
        """Process a batch of jobs."""
        # Check if within active hours
        current_hour = datetime.now().hour
        if not (start_hour <= current_hour < end_hour):
            self.stdout.write(
                self.style.WARNING(
                    f"Outside active hours ({start_hour}:00-{end_hour}:00). "
                    f"Current: {current_hour}:00"
                )
            )
            return 0

        # Get next jobs
        jobs = state_store.get_next_ai_jobs(limit=batch_size, check_schedule=True)

        if not jobs:
            return 0

        self.stdout.write(f"Found {len(jobs)} job(s) to process")

        processed = 0
        for job in jobs:
            if self._shutdown_requested:
                break

            job_id = job["id"]
            document_id = job["document_id"]

            self.stdout.write(f"Processing job #{job_id} (document {document_id})...")

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"  [DRY RUN] Would process job #{job_id}")
                )
                processed += 1
                continue

            try:
                success = self._process_single_job(
                    job=job,
                    state_store=state_store,
                    ai_service=ai_service,
                    paperless_client=paperless_client,
                )

                if success:
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✓ Job #{job_id} completed")
                    )
                    processed += 1
                else:
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ Job #{job_id} failed")
                    )

            except Exception as e:
                logger.exception(f"Error processing job #{job_id}")
                self.stderr.write(
                    self.style.ERROR(f"  ✗ Job #{job_id} error: {e}")
                )
                # Mark as failed
                state_store.fail_ai_job(job_id, str(e), can_retry=True)

        self.stdout.write(f"Processed {processed}/{len(jobs)} job(s)")
        return processed

    def _process_single_job(
        self,
        job: dict,
        state_store,
        ai_service,
        paperless_client,
    ) -> bool:
        """Process a single AI job."""
        from paperless_firefly.services.ai_queue import AIJobQueueService

        # Create queue service
        # Note: We need a dummy config since we already have the components
        queue_service = AIJobQueueService(
            state_store=state_store,
            config=None,  # Not used in process_job
        )

        return queue_service.process_job(
            job=job,
            ai_service=ai_service,
            paperless_client=paperless_client,
        )

    def _load_config(self, config_path: str | None):
        """Load application configuration."""
        from paperless_firefly.config import Config, load_config

        if config_path:
            return load_config(Path(config_path))

        # Try default locations
        for path in [Path("config.yaml"), Path("/app/config/config.yaml")]:
            if path.exists():
                return load_config(path)

        # Fall back to environment-based config
        return Config.from_env()

    def _get_state_store(self, config):
        """Get state store instance."""
        from paperless_firefly.state_store.sqlite_store import StateStore

        db_path = getattr(config, "state_db_path", "data/state.db")
        return StateStore(db_path)

    def _get_ai_service(self, config):
        """Get AI service instance."""
        from paperless_firefly.spark_ai.service import SparkAIService

        if not hasattr(config, "llm") or not config.llm:
            raise CommandError("LLM configuration not found")

        return SparkAIService(config.llm)

    def _get_paperless_client(self, config):
        """Get Paperless client instance."""
        from paperless_firefly.paperless_client.client import PaperlessClient

        return PaperlessClient(
            base_url=config.paperless.base_url,
            token=config.paperless.token,
        )

    def _get_user_settings(self) -> dict:
        """Get user settings from database."""
        try:
            from paperless_firefly.review.web.models import UserProfile

            # Get first admin user's settings or defaults
            profile = UserProfile.objects.first()
            if profile:
                return {
                    "ai_schedule_enabled": profile.ai_schedule_enabled,
                    "ai_schedule_interval_minutes": profile.ai_schedule_interval_minutes,
                    "ai_schedule_batch_size": profile.ai_schedule_batch_size,
                    "ai_schedule_max_retries": profile.ai_schedule_max_retries,
                    "ai_schedule_start_hour": profile.ai_schedule_start_hour,
                    "ai_schedule_end_hour": profile.ai_schedule_end_hour,
                }
        except Exception as e:
            logger.warning(f"Could not load user settings: {e}")

        # Return defaults
        return {
            "ai_schedule_enabled": True,
            "ai_schedule_interval_minutes": 60,
            "ai_schedule_batch_size": 1,
            "ai_schedule_max_retries": 3,
            "ai_schedule_start_hour": 0,
            "ai_schedule_end_hour": 24,
        }
