"""
AI Job Queue Service.

Provides scheduling and processing of AI interpretation jobs.

Features:
- Schedule jobs when documents are extracted
- Process jobs on configurable intervals
- Fresh document metadata at processing time
- Store results for display in review UI
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from paperless_firefly.config import Config
from paperless_firefly.state_store.sqlite_store import StateStore

logger = logging.getLogger(__name__)


class AIJobQueueService:
    """
    Service for managing AI job queue.

    Handles scheduling, processing, and result storage for
    AI interpretation jobs.
    """

    def __init__(
        self,
        state_store: StateStore,
        config: Config,
    ):
        """
        Initialize the AI job queue service.

        Args:
            state_store: State store for job persistence
            config: Application configuration
        """
        self.store = state_store
        self.config = config

    def schedule_job(
        self,
        document_id: int,
        extraction_id: int | None = None,
        external_id: str | None = None,
        priority: int = 0,
        created_by: str = "AUTO",
        notes: str | None = None,
        interval_minutes: int | None = None,
    ) -> int | None:
        """
        Schedule an AI interpretation job for a document.

        The job will be processed based on the configured schedule.
        Only one active job is allowed per document.

        Args:
            document_id: Paperless document ID
            extraction_id: Optional extraction ID to update
            external_id: Optional external reference
            priority: Job priority (higher = processed first)
            created_by: Who scheduled (AUTO, USER, SYSTEM)
            notes: Optional notes
            interval_minutes: Override default interval

        Returns:
            Job ID if scheduled, None if job already exists
        """
        # Calculate scheduled_for based on interval
        scheduled_for = None
        if interval_minutes:
            scheduled_for = (
                datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)
            ).isoformat()

        job_id = self.store.schedule_ai_job(
            document_id=document_id,
            extraction_id=extraction_id,
            external_id=external_id,
            priority=priority,
            scheduled_for=scheduled_for,
            created_by=created_by,
            max_retries=self.config.llm.max_retries if hasattr(self.config, "llm") else 3,
            notes=notes,
        )

        if job_id:
            logger.info(
                f"Scheduled AI job #{job_id} for document {document_id} "
                f"(created_by={created_by})"
            )
        else:
            logger.debug(f"AI job already exists for document {document_id}")

        return job_id

    def schedule_for_extraction(
        self,
        extraction_id: int,
        document_id: int,
        external_id: str | None = None,
        created_by: str = "AUTO",
    ) -> int | None:
        """
        Schedule AI job for a newly created extraction.

        Called during the extraction workflow when AI is enabled.

        Args:
            extraction_id: Extraction record ID
            document_id: Paperless document ID
            external_id: External reference
            created_by: Who scheduled

        Returns:
            Job ID if scheduled, None if job already exists
        """
        return self.schedule_job(
            document_id=document_id,
            extraction_id=extraction_id,
            external_id=external_id,
            priority=0,  # Normal priority for auto-scheduled
            created_by=created_by,
        )

    def get_next_jobs(self, batch_size: int = 1) -> list[dict[str, Any]]:
        """
        Get the next jobs to process.

        Args:
            batch_size: Maximum number of jobs to return

        Returns:
            List of job dicts
        """
        return self.store.get_next_ai_jobs(
            limit=batch_size,
            check_schedule=True,
        )

    def process_job(
        self,
        job: dict[str, Any],
        ai_service: Any,  # SparkAIService
        paperless_client: Any,  # PaperlessClient
        firefly_client: Any = None,  # FireflyClient
    ) -> bool:
        """
        Process a single AI job.

        Fetches fresh document metadata and runs AI interpretation.
        Checks if AI is enabled for this document before processing.

        Args:
            job: Job dict from queue
            ai_service: AI service for interpretation
            paperless_client: Paperless client for fetching document
            firefly_client: Firefly client for fetching accounts/currencies

        Returns:
            True if successful, False otherwise
        """
        job_id = job["id"]
        document_id = job["document_id"]
        # extraction_id available in job dict if needed: job.get("extraction_id")

        logger.info(f"Processing AI job #{job_id} for document {document_id}")

        # Check if AI is opted-out for this document BEFORE processing
        try:
            extraction = self.store.get_extraction_by_document(document_id)
            if extraction and extraction.llm_opt_out:
                logger.info(f"Skipping AI job #{job_id} - document {document_id} has AI opted out")
                # Mark as completed with skip reason
                self.store.start_ai_job(job_id)
                self.store.complete_ai_job(
                    job_id,
                    json.dumps({"skipped": True, "reason": "AI opted out for this document"}),
                )
                return True  # Successfully handled (by skipping)
        except Exception as e:
            logger.warning(f"Could not check opt-out status for job #{job_id}: {e}")

        # Mark job as processing
        if not self.store.start_ai_job(job_id):
            logger.warning(f"Could not start job #{job_id} - may already be processing")
            return False

        try:
            # Fetch fresh document metadata from Paperless
            document = paperless_client.get_document(document_id)
            if not document:
                raise ValueError(f"Document {document_id} not found in Paperless")

            # Get extraction data if available
            extraction_data = None
            extraction = self.store.get_extraction_by_document(document_id)
            if extraction:
                extraction_data = json.loads(extraction.extraction_json)

            # Build document content for AI
            content_parts = []
            if getattr(document, "title", None):
                content_parts.append(f"Title: {document.title}")
            if getattr(document, "correspondent", None):
                content_parts.append(f"Correspondent: {document.correspondent}")
            if getattr(document, "content", None):
                content_parts.append(document.content)
            document_content = "\n\n".join(content_parts)

            # Extract current values from extraction data
            amount = str(extraction_data.get("total_amount", "0")) if extraction_data else "0"
            date = extraction_data.get("invoice_date", "") if extraction_data else ""
            vendor = extraction_data.get("vendor_name") if extraction_data else None
            description = extraction_data.get("description") if extraction_data else None
            current_category = (
                extraction_data.get("suggested_category") if extraction_data else None
            )

            # Fetch Firefly data for enhanced AI suggestions
            source_accounts_detailed = None
            currencies = None
            existing_transactions = None
            if firefly_client:
                try:
                    # Get accounts with identifiers for IBAN matching
                    raw_accounts = firefly_client.list_accounts(
                        account_type="asset", include_identifiers=True
                    )
                    source_accounts_detailed = [
                        {
                            "name": acc.get("name", ""),
                            "iban": acc.get("iban"),
                            "account_number": acc.get("account_number"),
                            "bic": acc.get("bic"),
                        }
                        for acc in raw_accounts
                    ]

                    # Get enabled currencies
                    raw_currencies = firefly_client.list_currencies(enabled_only=True)
                    currencies = [c.get("code") for c in raw_currencies if c.get("code")]

                    # Get existing transaction candidates from matching engine
                    from ..matching.engine import MatchingEngine
                    from ..config import load_config
                    from pathlib import Path

                    config_path = Path(self.config._config_path) if hasattr(self.config, "_config_path") else None
                    if config_path and config_path.exists():
                        match_config = load_config(config_path)
                        engine = MatchingEngine(self.store, match_config)
                        extraction_dict = {
                            "amount": extraction_data.get("proposal", {}).get("amount") if extraction_data else None,
                            "date": extraction_data.get("proposal", {}).get("date") if extraction_data else None,
                            "vendor": extraction_data.get("proposal", {}).get("destination_account") if extraction_data else None,
                            "description": extraction_data.get("proposal", {}).get("description") if extraction_data else None,
                        }
                        matches = engine.find_matches(
                            document_id=document_id,
                            extraction=extraction_dict,
                            max_results=5,
                        )
                        existing_transactions = [
                            {
                                "id": str(m.firefly_id),
                                "date": "",  # Would need cache lookup
                                "amount": "",  # Would need cache lookup
                                "description": "",  # Would need cache lookup
                                "score": round(m.total_score * 100, 1),
                            }
                            for m in matches
                        ]
                except Exception as e:
                    logger.warning(f"Could not fetch Firefly data for AI job #{job_id}: {e}")

            # Run AI interpretation with correct parameters
            suggestions = ai_service.suggest_for_review(
                amount=amount,
                date=date,
                vendor=vendor,
                description=description,
                current_category=current_category,
                document_content=document_content,
                document_id=document_id,
                no_timeout=True,  # Background job, wait for LLM
                source_accounts_detailed=source_accounts_detailed,
                currencies=currencies,
                existing_transactions=existing_transactions,
            )

            # Store results - convert to dict if needed
            if suggestions:
                suggestions_dict = (
                    suggestions.to_dict() if hasattr(suggestions, "to_dict") else suggestions
                )
                suggestions_json = json.dumps(suggestions_dict)
            else:
                suggestions_json = None
            self.store.complete_ai_job(job_id, suggestions_json)

            # Count suggestions for logging
            suggestion_count = 0
            if suggestions:
                suggestion_count = len(suggestions.suggestions) if hasattr(suggestions, 'suggestions') else 0

            logger.info(
                f"AI job #{job_id} completed successfully "
                f"with {suggestion_count} field suggestion(s)"
            )
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error(f"AI job #{job_id} failed: {error_msg}")
            self.store.fail_ai_job(job_id, error_msg, can_retry=True)
            return False

    def get_job_suggestions(self, document_id: int) -> dict[str, Any] | None:
        """
        Get AI suggestions for a document from a completed job.

        Args:
            document_id: Paperless document ID

        Returns:
            Suggestions dict if available, None otherwise
        """
        job = self.store.get_ai_job_by_document(document_id, active_only=False)
        if not job or job["status"] != "COMPLETED":
            return None

        if job.get("suggestions_json"):
            try:
                return json.loads(job["suggestions_json"])
            except json.JSONDecodeError:
                logger.warning(f"Invalid suggestions JSON for job {job['id']}")

        return None

    def get_queue_stats(self) -> dict[str, int]:
        """Get queue statistics."""
        return self.store.get_ai_queue_stats()

    def get_jobs_list(
        self,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get list of jobs for display."""
        return self.store.get_ai_jobs_list(
            status=status,
            limit=limit,
            offset=offset,
        )

    def cancel_job(self, job_id: int) -> bool:
        """Cancel a job."""
        return self.store.cancel_ai_job(job_id)

    def retry_job(self, job_id: int) -> bool:
        """Retry a failed job."""
        job = self.store.get_ai_job(job_id)
        if not job or job["status"] != "FAILED":
            return False

        # Reset the job to pending
        with self.store._transaction() as conn:
            conn.execute(
                """
                UPDATE ai_job_queue
                SET status = 'PENDING', error_message = NULL,
                    started_at = NULL, completed_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
        return True

    def cleanup_old_jobs(self, days: int = 30) -> int:
        """Remove old completed/failed jobs."""
        count = self.store.cleanup_old_ai_jobs(days)
        if count:
            logger.info(f"Cleaned up {count} old AI jobs")
        return count
