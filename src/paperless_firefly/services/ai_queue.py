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
            max_retries=self.config.llm.max_retries if hasattr(self.config, 'llm') else 3,
            notes=notes,
        )

        if job_id:
            logger.info(
                f"Scheduled AI job #{job_id} for document {document_id} "
                f"(created_by={created_by})"
            )
        else:
            logger.debug(
                f"AI job already exists for document {document_id}"
            )

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
    ) -> bool:
        """
        Process a single AI job.

        Fetches fresh document metadata and runs AI interpretation.
        Checks if AI is enabled for this document before processing.

        Args:
            job: Job dict from queue
            ai_service: AI service for interpretation
            paperless_client: Paperless client for fetching document

        Returns:
            True if successful, False otherwise
        """
        job_id = job["id"]
        document_id = job["document_id"]
        extraction_id = job.get("extraction_id")

        logger.info(f"Processing AI job #{job_id} for document {document_id}")

        # Check if AI is opted-out for this document BEFORE processing
        try:
            extraction = self.store.get_extraction_by_document(document_id)
            if extraction and extraction.llm_opt_out:
                logger.info(
                    f"Skipping AI job #{job_id} - document {document_id} has AI opted out"
                )
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

            # Build context for AI
            context = self._build_ai_context(document, extraction_data)

            # Run AI interpretation
            suggestions = ai_service.suggest_for_review(
                document_content=context.get("content", ""),
                current_values=context.get("current_values", {}),
                available_categories=context.get("categories", []),
                available_accounts=context.get("accounts", []),
            )

            # Store results
            suggestions_json = json.dumps(suggestions) if suggestions else None
            self.store.complete_ai_job(job_id, suggestions_json)

            logger.info(
                f"AI job #{job_id} completed successfully "
                f"with {len(suggestions) if suggestions else 0} suggestions"
            )
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error(f"AI job #{job_id} failed: {error_msg}")
            self.store.fail_ai_job(job_id, error_msg, can_retry=True)
            return False

    def _build_ai_context(
        self,
        document: dict[str, Any],
        extraction_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Build context dict for AI interpretation.

        Args:
            document: Paperless document data
            extraction_data: Existing extraction data if available

        Returns:
            Context dict with content, current values, etc.
        """
        context = {
            "content": "",
            "current_values": {},
            "categories": [],
            "accounts": [],
        }

        # Extract document content
        content_parts = []
        if document.get("title"):
            content_parts.append(f"Title: {document['title']}")
        if document.get("correspondent"):
            content_parts.append(f"Correspondent: {document['correspondent']}")
        if document.get("content"):
            content_parts.append(document["content"])

        context["content"] = "\n\n".join(content_parts)

        # Current extraction values
        if extraction_data:
            context["current_values"] = {
                "amount": extraction_data.get("total_amount"),
                "date": extraction_data.get("invoice_date"),
                "vendor": extraction_data.get("vendor_name"),
                "description": extraction_data.get("description"),
                "category": extraction_data.get("suggested_category"),
            }

        return context

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
