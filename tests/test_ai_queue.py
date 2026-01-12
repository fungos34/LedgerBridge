"""
Tests for AI Job Queue functionality.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from paperless_firefly.state_store.sqlite_store import StateStore


class TestAIJobQueueOperations:
    """Test AI job queue database operations."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> StateStore:
        """Create a fresh state store for each test."""
        db_path = tmp_path / "test_state.db"
        return StateStore(db_path, run_migrations=True)

    def test_schedule_ai_job(self, store: StateStore):
        """Test scheduling a new AI job."""
        job_id = store.schedule_ai_job(
            document_id=123,
            extraction_id=456,
            external_id="ext-123",
            priority=5,
            created_by="TEST",
        )

        assert job_id is not None
        assert job_id > 0

        # Verify job was created
        job = store.get_ai_job(job_id)
        assert job is not None
        assert job["document_id"] == 123
        assert job["extraction_id"] == 456
        assert job["external_id"] == "ext-123"
        assert job["priority"] == 5
        assert job["status"] == "PENDING"
        assert job["created_by"] == "TEST"
        assert job["retry_count"] == 0

    def test_schedule_duplicate_job_returns_none(self, store: StateStore):
        """Test that scheduling a duplicate job returns None."""
        # Schedule first job
        job_id1 = store.schedule_ai_job(document_id=123, created_by="TEST")
        assert job_id1 is not None

        # Try to schedule another job for same document
        job_id2 = store.schedule_ai_job(document_id=123, created_by="TEST")
        assert job_id2 is None

    def test_schedule_allows_after_completed(self, store: StateStore):
        """Test that new job can be scheduled after previous one completes."""
        # Schedule and complete first job
        job_id1 = store.schedule_ai_job(document_id=123, created_by="TEST")
        store.start_ai_job(job_id1)
        store.complete_ai_job(job_id1, '{"test": true}')

        # Now we should be able to schedule another
        job_id2 = store.schedule_ai_job(document_id=123, created_by="TEST")
        assert job_id2 is not None
        assert job_id2 != job_id1

    def test_get_next_ai_jobs(self, store: StateStore):
        """Test getting next jobs to process."""
        # Schedule some jobs
        store.schedule_ai_job(document_id=1, priority=1, created_by="TEST")
        store.schedule_ai_job(document_id=2, priority=5, created_by="TEST")
        store.schedule_ai_job(document_id=3, priority=3, created_by="TEST")

        # Get next jobs (should be ordered by priority desc)
        jobs = store.get_next_ai_jobs(limit=3)
        assert len(jobs) == 3
        assert jobs[0]["document_id"] == 2  # Highest priority
        assert jobs[1]["document_id"] == 3
        assert jobs[2]["document_id"] == 1  # Lowest priority

    def test_get_next_ai_jobs_respects_schedule(self, store: StateStore):
        """Test that jobs respect scheduled_for time."""
        now = datetime.now(timezone.utc)
        future = (now + timedelta(hours=1)).isoformat()
        past = (now - timedelta(hours=1)).isoformat()

        # Schedule jobs with different times
        store.schedule_ai_job(document_id=1, scheduled_for=future, created_by="TEST")
        store.schedule_ai_job(document_id=2, scheduled_for=past, created_by="TEST")
        store.schedule_ai_job(document_id=3, scheduled_for=None, created_by="TEST")

        # Only past and null scheduled jobs should be returned
        jobs = store.get_next_ai_jobs(limit=10, check_schedule=True)
        doc_ids = [j["document_id"] for j in jobs]
        assert 1 not in doc_ids  # Future job excluded
        assert 2 in doc_ids
        assert 3 in doc_ids

    def test_start_ai_job(self, store: StateStore):
        """Test starting an AI job."""
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")

        result = store.start_ai_job(job_id)
        assert result is True

        job = store.get_ai_job(job_id)
        assert job["status"] == "PROCESSING"
        assert job["started_at"] is not None

    def test_start_ai_job_only_pending(self, store: StateStore):
        """Test that only pending jobs can be started."""
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")
        store.start_ai_job(job_id)

        # Try to start again - should fail
        result = store.start_ai_job(job_id)
        assert result is False

    def test_complete_ai_job(self, store: StateStore):
        """Test completing an AI job with results."""
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")
        store.start_ai_job(job_id)

        suggestions = {"category": {"value": "Food", "confidence": 0.9}}
        result = store.complete_ai_job(job_id, json.dumps(suggestions))
        assert result is True

        job = store.get_ai_job(job_id)
        assert job["status"] == "COMPLETED"
        assert job["completed_at"] is not None
        assert job["suggestions_json"] is not None
        parsed = json.loads(job["suggestions_json"])
        assert parsed["category"]["value"] == "Food"

    def test_fail_ai_job_with_retry(self, store: StateStore):
        """Test failing a job that can be retried."""
        job_id = store.schedule_ai_job(document_id=123, max_retries=3, created_by="TEST")
        store.start_ai_job(job_id)

        result = store.fail_ai_job(job_id, "Test error", can_retry=True)
        assert result is True

        job = store.get_ai_job(job_id)
        # Should be reset to PENDING for retry
        assert job["status"] == "PENDING"
        assert job["retry_count"] == 1
        assert job["error_message"] == "Test error"

    def test_fail_ai_job_max_retries_exceeded(self, store: StateStore):
        """Test failing a job that has exceeded max retries."""
        # With max_retries=1, the job gets ONE retry after the first failure
        job_id = store.schedule_ai_job(document_id=123, max_retries=1, created_by="TEST")

        # First attempt (retry_count=0, will be incremented to 1, requeued)
        store.start_ai_job(job_id)
        store.fail_ai_job(job_id, "Error 1", can_retry=True)

        job = store.get_ai_job(job_id)
        assert job["status"] == "PENDING"  # Requeued for retry
        assert job["retry_count"] == 1

        # Second attempt (retry_count=1 >= max_retries=1, final failure)
        store.start_ai_job(job_id)
        store.fail_ai_job(job_id, "Error 2", can_retry=True)

        # Should now be FAILED since retry_count >= max_retries
        job = store.get_ai_job(job_id)
        assert job["status"] == "FAILED"
        assert job["retry_count"] == 1  # Doesn't increment on final failure

    def test_cancel_ai_job(self, store: StateStore):
        """Test cancelling an AI job."""
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")

        result = store.cancel_ai_job(job_id)
        assert result is True

        job = store.get_ai_job(job_id)
        assert job["status"] == "CANCELLED"
        assert job["completed_at"] is not None

    def test_get_ai_queue_stats(self, store: StateStore):
        """Test getting queue statistics."""
        # Create jobs with different statuses
        store.schedule_ai_job(document_id=1, created_by="TEST")
        store.schedule_ai_job(document_id=2, created_by="TEST")

        job_id3 = store.schedule_ai_job(document_id=3, created_by="TEST")
        store.start_ai_job(job_id3)

        job_id4 = store.schedule_ai_job(document_id=4, created_by="TEST")
        store.start_ai_job(job_id4)
        store.complete_ai_job(job_id4, "{}")

        job_id5 = store.schedule_ai_job(document_id=5, max_retries=0, created_by="TEST")
        store.start_ai_job(job_id5)
        store.fail_ai_job(job_id5, "Error", can_retry=False)

        stats = store.get_ai_queue_stats()
        assert stats["pending"] == 2
        assert stats["processing"] == 1
        assert stats["completed"] == 1
        assert stats["failed"] == 1
        assert stats["total"] == 5

    def test_get_ai_jobs_list(self, store: StateStore):
        """Test getting job list for display."""
        # Create a document first
        store.upsert_document(
            document_id=123,
            source_hash="hash123",
            title="Test Document",
        )

        store.schedule_ai_job(document_id=123, created_by="TEST")

        jobs = store.get_ai_jobs_list(limit=10)
        assert len(jobs) == 1
        assert jobs[0]["document_id"] == 123
        assert jobs[0]["doc_title"] == "Test Document"

    def test_cleanup_old_ai_jobs(self, store: StateStore):
        """Test cleaning up old completed jobs."""
        # Create and complete a job
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")
        store.start_ai_job(job_id)
        store.complete_ai_job(job_id, "{}")

        # Manually set completed_at to old date
        with store._transaction() as conn:
            conn.execute(
                "UPDATE ai_job_queue SET completed_at = ? WHERE id = ?",
                ("2020-01-01T00:00:00+00:00", job_id),
            )

        # Cleanup old jobs
        deleted = store.cleanup_old_ai_jobs(days=30)
        assert deleted == 1

        # Verify job is gone
        job = store.get_ai_job(job_id)
        assert job is None

    def test_get_ai_job_by_document(self, store: StateStore):
        """Test getting AI job by document ID."""
        store.schedule_ai_job(document_id=123, created_by="TEST")

        job = store.get_ai_job_by_document(123, active_only=True)
        assert job is not None
        assert job["document_id"] == 123

        # Complete the job
        store.start_ai_job(job["id"])
        store.complete_ai_job(job["id"], "{}")

        # Active only should return None
        job = store.get_ai_job_by_document(123, active_only=True)
        assert job is None

        # Non-active should return the completed job
        job = store.get_ai_job_by_document(123, active_only=False)
        assert job is not None
        assert job["status"] == "COMPLETED"

    def test_complete_ai_job_with_split_transactions(self, store: StateStore):
        """Test completing an AI job with comprehensive suggestions including splits."""
        job_id = store.schedule_ai_job(document_id=123, created_by="TEST")
        store.start_ai_job(job_id)

        # Comprehensive suggestions matching what suggest_for_review produces
        suggestions = {
            "suggestions": {
                "category": {"value": "Food & Dining", "confidence": 0.85, "reason": "Restaurant receipt"},
                "transaction_type": {"value": "withdrawal", "confidence": 0.95, "reason": "This is a purchase"},
                "destination_account": {"value": "Restaurant XYZ", "confidence": 0.90, "reason": "From header"},
                "description": {"value": "Dinner at Restaurant XYZ", "confidence": 0.80, "reason": "Occasion"}
            },
            "split_transactions": [
                {"amount": 15.99, "description": "Main course - Pasta", "category": "Food & Dining"},
                {"amount": 4.50, "description": "Beverage - Soda", "category": "Food & Dining"},
                {"amount": 3.00, "description": "Tip", "category": "Food & Dining"}
            ],
            "overall_confidence": 0.85,
            "analysis_notes": "Receipt from Restaurant XYZ with 3 line items"
        }
        
        result = store.complete_ai_job(job_id, json.dumps(suggestions))
        assert result is True

        job = store.get_ai_job(job_id)
        assert job["status"] == "COMPLETED"
        assert job["suggestions_json"] is not None
        
        # Verify all fields are retrievable
        parsed = json.loads(job["suggestions_json"])
        assert parsed["suggestions"]["category"]["value"] == "Food & Dining"
        assert parsed["suggestions"]["destination_account"]["value"] == "Restaurant XYZ"
        assert parsed["suggestions"]["description"]["value"] == "Dinner at Restaurant XYZ"
        assert len(parsed["split_transactions"]) == 3
        assert abs(sum(s["amount"] for s in parsed["split_transactions"]) - 23.49) < 0.01
        assert parsed["overall_confidence"] == 0.85


class TestAIJobQueueService:
    """Test AI Job Queue Service."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> StateStore:
        """Create a fresh state store for each test."""
        db_path = tmp_path / "test_state.db"
        return StateStore(db_path, run_migrations=True)

    def test_schedule_job_with_service(self, store: StateStore):
        """Test scheduling a job through the service."""
        from paperless_firefly.services.ai_queue import AIJobQueueService

        service = AIJobQueueService(store, config=None)

        job_id = service.schedule_job(
            document_id=123,
            extraction_id=456,
            created_by="TEST",
        )

        assert job_id is not None
        job = store.get_ai_job(job_id)
        assert job["document_id"] == 123

    def test_get_queue_stats(self, store: StateStore):
        """Test getting queue stats through service."""
        from paperless_firefly.services.ai_queue import AIJobQueueService

        service = AIJobQueueService(store, config=None)

        # Schedule some jobs
        service.schedule_job(document_id=1, created_by="TEST")
        service.schedule_job(document_id=2, created_by="TEST")

        stats = service.get_queue_stats()
        assert stats["pending"] == 2
        assert stats["total"] == 2

    def test_get_job_suggestions_from_service(self, store: StateStore):
        """Test retrieving completed job suggestions through service."""
        from paperless_firefly.services.ai_queue import AIJobQueueService

        service = AIJobQueueService(store, config=None)

        # Schedule and complete a job with comprehensive suggestions
        job_id = service.schedule_job(document_id=123, created_by="TEST")
        assert job_id is not None
        
        # Simulate processing and completion
        store.start_ai_job(job_id)
        suggestions = {
            "suggestions": {
                "category": {"value": "Groceries", "confidence": 0.85, "reason": "Food items"},
                "description": {"value": "Weekly shopping", "confidence": 0.80, "reason": "Regular purchase"}
            },
            "split_transactions": [
                {"amount": 10.00, "description": "Bread, milk", "category": "Groceries"},
                {"amount": 5.00, "description": "Snacks", "category": "Groceries"}
            ],
            "overall_confidence": 0.85,
            "analysis_notes": "Receipt with line items"
        }
        store.complete_ai_job(job_id, json.dumps(suggestions))
        
        # Retrieve suggestions via service
        result = service.get_job_suggestions(document_id=123)
        assert result is not None
        assert result["suggestions"]["category"]["value"] == "Groceries"
        assert len(result["split_transactions"]) == 2
        assert result["overall_confidence"] == 0.85
