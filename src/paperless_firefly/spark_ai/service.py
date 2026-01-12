"""Spark AI service for LLM-assisted transaction categorization.

This service implements the LLM integration specified in Spark v1.0 Phase 6/7.
Features:
- Ollama integration with configurable models (localhost, LAN, or remote)
- Cascading model fallback (fast -> slow)
- Response caching with taxonomy version tracking
- Calibration period before auto-apply
- Opt-out support for individual extractions
- Concurrency limiting via semaphore
- Chatbot for documentation questions

Privacy Constraints (non-negotiable):
- Never log prompts or raw document content at INFO level
- Sensitive data must be redacted before logging
- Remote Ollama: auth header support, no PII in logs
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from paperless_firefly.spark_ai.prompts import (
    PROMPT_VERSION,
    CategoryPrompt,
    ChatPrompt,
    SplitPrompt,
    TransactionReviewPrompt,
)

if TYPE_CHECKING:
    from paperless_firefly.config import Config, LLMConfig
    from paperless_firefly.state_store import StateStore

logger = logging.getLogger(__name__)


@dataclass
class CategorySuggestion:
    """Result of LLM category suggestion."""

    category: str
    confidence: float
    reason: str
    model: str
    from_cache: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "category": self.category,
            "confidence": self.confidence,
            "reason": self.reason,
            "model": self.model,
            "from_cache": self.from_cache,
        }


@dataclass
class SplitSuggestion:
    """Result of LLM split suggestion."""

    should_split: bool
    splits: list[dict]  # [{"category": str, "amount": float, "description": str}]
    confidence: float
    reason: str
    model: str
    from_cache: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "should_split": self.should_split,
            "splits": self.splits,
            "confidence": self.confidence,
            "reason": self.reason,
            "model": self.model,
            "from_cache": self.from_cache,
        }


@dataclass
class FieldSuggestion:
    """Suggestion for a single form field."""
    
    value: str
    confidence: float
    reason: str
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "value": self.value,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass
class TransactionReviewSuggestion:
    """Result of comprehensive transaction review suggestion."""
    
    suggestions: dict[str, FieldSuggestion]  # field_name -> FieldSuggestion
    overall_confidence: float
    analysis_notes: str
    model: str
    from_cache: bool = False
    split_transactions: list[dict] | None = None  # [{"amount": float, "description": str, "category": str}]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "suggestions": {
                k: v.to_dict() for k, v in self.suggestions.items()
            },
            "overall_confidence": self.overall_confidence,
            "analysis_notes": self.analysis_notes,
            "model": self.model,
            "from_cache": self.from_cache,
        }
        if self.split_transactions:
            result["split_transactions"] = self.split_transactions
        return result


class LLMConcurrencyLimiter:
    """Semaphore-based concurrency limiter for LLM requests.

    Prevents overwhelming the Ollama server with too many concurrent requests.
    Thread-safe for synchronous usage.
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self._semaphore = threading.Semaphore(max_concurrent)
        self._active_count = 0
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Acquire a slot for an LLM request.

        Args:
            timeout: Maximum time to wait (None = blocking)

        Returns:
            True if acquired, False if timeout
        """
        acquired = self._semaphore.acquire(blocking=True, timeout=timeout)
        if acquired:
            with self._lock:
                self._active_count += 1
        return acquired

    def release(self) -> None:
        """Release a slot after request completes."""
        with self._lock:
            self._active_count -= 1
        self._semaphore.release()

    @property
    def active_requests(self) -> int:
        """Current number of active LLM requests."""
        with self._lock:
            return self._active_count


class SparkAIService:
    """LLM-assisted categorization service.

    This service provides AI-powered category suggestions using Ollama.
    It implements:
    - Caching with taxonomy/prompt version tracking
    - Cascading model fallback
    - Calibration period before auto-apply
    - Green threshold for high-confidence suggestions
    - Concurrency limiting for remote/shared servers
    - Optional auth header for proxied deployments

    LLM opt-in control (SSOT - single enforcement point):
    - Global: config.llm.enabled (master switch)
    - Per-document: llm_opt_out column in extractions table
    - This service is the ONLY place that checks these flags
    """

    def __init__(
        self,
        state_store: StateStore,
        config: Config,
        categories: list[str] | None = None,
    ) -> None:
        """Initialize the AI service.

        Args:
            state_store: State store for caching and feedback.
            config: Application configuration.
            categories: List of available category names.
        """
        self.store = state_store
        self.config = config
        self.llm_config: LLMConfig = config.llm
        self.categories = categories or []
        self._taxonomy_version = self._compute_taxonomy_version()

        # Configure HTTP client with auth header support
        headers = {}
        if self.llm_config.auth_header:
            # Support formats: "Bearer token" or "Custom-Header: value"
            if ":" in self.llm_config.auth_header:
                key, value = self.llm_config.auth_header.split(":", 1)
                headers[key.strip()] = value.strip()
            else:
                headers["Authorization"] = self.llm_config.auth_header

        # Use explicit timeout configuration:
        # - connect: 10 seconds for initial connection
        # - read: full timeout for waiting for LLM response
        # - write: 30 seconds for sending request
        # - pool: 10 seconds for getting connection from pool
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,
                read=float(self.llm_config.timeout_seconds),
                write=30.0,
                pool=10.0,
            ),
            headers=headers,
        )
        self._category_prompt = CategoryPrompt()
        self._split_prompt = SplitPrompt()
        self._review_prompt = TransactionReviewPrompt()

        # Concurrency limiter (SSOT for queue management)
        self._limiter = LLMConcurrencyLimiter(max_concurrent=self.llm_config.max_concurrent)

    def _compute_taxonomy_version(self) -> str:
        """Compute a hash of the category taxonomy for cache invalidation."""
        if not self.categories:
            return "empty"
        cats_str = "|".join(sorted(self.categories))
        return hashlib.sha256(cats_str.encode()).hexdigest()[:12]

    @property
    def is_enabled(self) -> bool:
        """Check if LLM service is enabled (SSOT)."""
        return self.llm_config.enabled

    @property
    def is_remote(self) -> bool:
        """Check if Ollama is configured for remote access."""
        return self.llm_config.is_remote()

    @property
    def endpoint_class(self) -> str:
        """Get endpoint classification for trace logging."""
        if not self.is_enabled:
            return "disabled"
        return "remote" if self.is_remote else "local"

    @property
    def active_requests(self) -> int:
        """Current number of active LLM requests."""
        return self._limiter.active_requests

    @property
    def is_calibrating(self) -> bool:
        """Check if service is still in calibration period.

        During calibration, suggestions are shown but not auto-applied.
        """
        if not self.is_enabled:
            return False
        suggestion_count = self.store.get_llm_suggestion_count()
        return suggestion_count < self.llm_config.calibration_count

    def check_opt_out(self, document_id: int) -> tuple[bool, str]:
        """Check if LLM is opted-out for a specific document.

        This is the SINGLE enforcement point for per-document opt-out.

        Args:
            document_id: Paperless document ID

        Returns:
            Tuple of (is_opted_out, reason)
        """
        if not self.is_enabled:
            return True, "LLM globally disabled"

        try:
            extraction = self.store.get_extraction_by_document(document_id)
            if extraction and getattr(extraction, "llm_opt_out", False):
                return True, "Per-document opt-out"
        except Exception as e:
            logger.debug("Could not check opt-out for doc %d: %s", document_id, e)

        return False, "LLM enabled"

    def set_categories(self, categories: list[str]) -> None:
        """Update available categories and recalculate taxonomy version.

        Args:
            categories: New list of category names.
        """
        self.categories = categories
        self._taxonomy_version = self._compute_taxonomy_version()

    def suggest_category(
        self,
        amount: str,
        date: str,
        vendor: str | None = None,
        description: str | None = None,
        document_id: int | None = None,
        use_cache: bool = True,
    ) -> CategorySuggestion | None:
        """Suggest a category for a transaction.

        Args:
            amount: Transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            document_id: Optional document ID for per-doc opt-out check.
            use_cache: Whether to use cached responses.

        Returns:
            CategorySuggestion or None if LLM is disabled or fails.
        """
        if not self.is_enabled:
            logger.debug("LLM service disabled, skipping suggestion")
            return None

        # Per-document opt-out check
        if document_id:
            opted_out, reason = self.check_opt_out(document_id)
            if opted_out:
                logger.debug("LLM opted out for doc %d: %s", document_id, reason)
                return None

        if not self.categories:
            logger.warning("No categories configured for LLM suggestions")
            return None

        # Build cache key
        cache_key = self._build_cache_key("category", amount, date, vendor, description)

        # Check cache
        if use_cache:
            cached = self.store.get_llm_cache(cache_key)
            if cached:
                try:
                    data = json.loads(cached["response_json"])
                    return CategorySuggestion(
                        category=data["category"],
                        confidence=data["confidence"],
                        reason=data["reason"],
                        model=cached["model"],
                        from_cache=True,
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Invalid cached response: %s", e)

        # Build prompt
        user_message = self._category_prompt.format_user_message(
            amount=amount,
            date=date,
            vendor=vendor,
            description=description,
            categories=self.categories,
        )

        # Try fast model first
        result = self._call_ollama(
            model=self.llm_config.model_fast,
            system_prompt=self._category_prompt.system_prompt,
            user_message=user_message,
        )

        # Fallback to slow model if needed
        if result is None and self.llm_config.model_fallback:
            logger.info("Fast model failed, falling back to %s", self.llm_config.model_fallback)
            result = self._call_ollama(
                model=self.llm_config.model_fallback,
                system_prompt=self._category_prompt.system_prompt,
                user_message=user_message,
            )

        if result is None:
            return None

        try:
            data = self._parse_json_response(result["content"])
            suggestion = CategorySuggestion(
                category=data.get("category", ""),
                confidence=float(data.get("confidence", 0.0)),
                reason=data.get("reason", ""),
                model=result["model"],
            )

            # Validate category
            if suggestion.category not in self.categories:
                logger.warning(
                    "LLM suggested invalid category '%s', not in taxonomy",
                    suggestion.category,
                )
                return None

            # Cache the result
            self.store.set_llm_cache(
                cache_key=cache_key,
                model=result["model"],
                prompt_version=PROMPT_VERSION,
                taxonomy_version=self._taxonomy_version,
                response_json=json.dumps(data),
            )

            return suggestion

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Failed to parse LLM response: %s", e)
            return None

    def suggest_splits(
        self,
        amount: str,
        date: str,
        vendor: str | None = None,
        description: str | None = None,
        content: str | None = None,
        bank_data: dict | None = None,
        use_cache: bool = True,
    ) -> SplitSuggestion | None:
        """Suggest transaction splits from OCR content.

        Enhanced to extract line items with prices from OCR text and
        assign categories based on item descriptions.

        Args:
            amount: Total transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            content: OCR/document content with potential line items.
            bank_data: Optional linked bank transaction data for context.
            use_cache: Whether to use cached responses.

        Returns:
            SplitSuggestion or None if LLM is disabled or fails.
        """
        if not self.is_enabled:
            return None

        if not self.categories:
            return None

        # Build cache key including content hash and bank data
        content_hash = hashlib.sha256((content or "").encode()).hexdigest()[:8]
        bank_hash = hashlib.sha256(json.dumps(bank_data or {}, sort_keys=True).encode()).hexdigest()[:8]
        cache_key = self._build_cache_key("split", amount, date, vendor, description, content_hash, bank_hash)

        # Check cache
        if use_cache:
            cached = self.store.get_llm_cache(cache_key)
            if cached:
                try:
                    data = json.loads(cached["response_json"])
                    return SplitSuggestion(
                        should_split=data["should_split"],
                        splits=data.get("splits", []),
                        confidence=data["confidence"],
                        reason=data["reason"],
                        model=cached["model"],
                        from_cache=True,
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Invalid cached split response: %s", e)

        # Build prompt with bank data context
        user_message = self._split_prompt.format_user_message(
            amount=amount,
            date=date,
            vendor=vendor,
            description=description,
            content=content,
            categories=self.categories,
            bank_data=bank_data,
        )

        # Use slow model for splits (more complex reasoning)
        model = self.llm_config.model_fallback or self.llm_config.model_fast
        result = self._call_ollama(
            model=model,
            system_prompt=self._split_prompt.system_prompt,
            user_message=user_message,
        )

        if result is None:
            return None

        try:
            data = self._parse_json_response(result["content"])

            # Normalize splits data
            raw_splits = data.get("splits", [])
            normalized_splits = []

            for split in raw_splits:
                # Normalize amount - handle string/float/int
                raw_amount = split.get("amount", 0)
                if isinstance(raw_amount, str):
                    # Handle European format (comma as decimal)
                    raw_amount = raw_amount.replace(",", ".").strip()
                    # Remove currency symbols
                    raw_amount = re.sub(r"[€$£]", "", raw_amount).strip()
                try:
                    amount = float(raw_amount)
                except (ValueError, TypeError):
                    amount = 0.0

                # Normalize category - fuzzy match to available categories
                raw_category = str(split.get("category", "")).strip()
                matched_category = self._match_category(raw_category)

                if matched_category and amount > 0:
                    normalized_splits.append({
                        "category": matched_category,
                        "amount": round(amount, 2),
                        "description": str(split.get("description", "")).strip(),
                    })

            suggestion = SplitSuggestion(
                should_split=data.get("should_split", False) and len(normalized_splits) > 0,
                splits=normalized_splits,
                confidence=float(data.get("confidence", 0.0)),
                reason=data.get("reason", ""),
                model=result["model"],
            )

            # Cache the result
            if suggestion.splits:
                self.store.set_llm_cache(
                    cache_key=cache_key,
                    model=result["model"],
                    prompt_version=PROMPT_VERSION,
                    taxonomy_version=self._taxonomy_version,
                    response_json=json.dumps({
                        "should_split": suggestion.should_split,
                        "splits": suggestion.splits,
                        "confidence": suggestion.confidence,
                        "reason": suggestion.reason,
                    }),
                )

            return suggestion

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Failed to parse LLM split response: %s", e)
            return None

    def _match_category(self, raw_category: str) -> str | None:
        """Fuzzy match a category name to available categories.

        Args:
            raw_category: Raw category name from LLM.

        Returns:
            Matched category name or None if no match.
        """
        if not raw_category:
            return None

        raw_lower = raw_category.lower().strip()

        # Exact match first
        for cat in self.categories:
            if cat.lower() == raw_lower:
                return cat

        # Substring match
        for cat in self.categories:
            if raw_lower in cat.lower() or cat.lower() in raw_lower:
                return cat

        # Word overlap match
        raw_words = set(raw_lower.split())
        best_match = None
        best_overlap = 0
        for cat in self.categories:
            cat_words = set(cat.lower().split())
            overlap = len(raw_words & cat_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = cat

        if best_overlap > 0:
            return best_match

        logger.debug("Could not match category '%s' to available categories", raw_category)
        return None

    def should_auto_apply(self, confidence: float) -> bool:
        """Check if a suggestion should be auto-applied.

        Auto-apply requires:
        1. LLM is enabled
        2. Calibration period is complete
        3. Confidence exceeds green threshold

        Args:
            confidence: Suggestion confidence score (0-1).

        Returns:
            True if suggestion can be auto-applied.
        """
        if not self.is_enabled:
            return False
        if self.is_calibrating:
            return False
        return confidence >= self.llm_config.green_threshold

    def record_feedback(
        self,
        run_id: int,
        suggested_category: str,
        actual_category: str,
        notes: str | None = None,
    ) -> None:
        """Record feedback on a suggestion for calibration.

        Args:
            run_id: Interpretation run ID.
            suggested_category: LLM-suggested category.
            actual_category: Category actually used.
            notes: Optional notes about the correction.
        """
        feedback_type = "CORRECT" if suggested_category == actual_category else "WRONG"
        self.store.record_llm_feedback(
            run_id=run_id,
            suggested_category=suggested_category,
            actual_category=actual_category,
            feedback_type=feedback_type,
            notes=notes,
        )
        logger.debug(
            "Recorded %s feedback for run %d: suggested=%s, actual=%s",
            feedback_type,
            run_id,
            suggested_category,
            actual_category,
        )

    def get_calibration_stats(self) -> dict:
        """Get calibration statistics.

        Returns:
            Dict with calibration progress and accuracy.
        """
        suggestion_count = self.store.get_llm_suggestion_count()
        feedback_stats = self.store.get_llm_feedback_stats()

        return {
            "enabled": self.is_enabled,
            "endpoint_class": self.endpoint_class,
            "active_requests": self.active_requests,
            "calibrating": self.is_calibrating,
            "suggestion_count": suggestion_count,
            "calibration_target": self.llm_config.calibration_count,
            "calibration_progress": (
                min(1.0, suggestion_count / self.llm_config.calibration_count)
                if self.llm_config.calibration_count > 0
                else 1.0
            ),
            "feedback": feedback_stats,
        }

    def get_llm_status(self) -> dict:
        """Get LLM service status for UI display.

        Returns:
            Dict with status information for the interpretation trace panel.
        """
        return {
            "enabled": self.is_enabled,
            "endpoint_class": self.endpoint_class,
            "is_remote": self.is_remote,
            "ollama_url": self.llm_config.ollama_url if self.is_enabled else None,
            "model_fast": self.llm_config.model_fast if self.is_enabled else None,
            "model_fallback": self.llm_config.model_fallback if self.is_enabled else None,
            "active_requests": self.active_requests,
            "max_concurrent": self.llm_config.max_concurrent,
            "calibrating": self.is_calibrating,
            "reason_disabled": None if self.is_enabled else "LLM globally disabled in config",
        }

    def _build_cache_key(self, prefix: str, *args: str | None) -> str:
        """Build a cache key from components.

        Args:
            prefix: Key prefix (e.g., "category", "split").
            *args: Key components.

        Returns:
            SHA256 hash-based cache key.
        """
        components = [
            prefix,
            PROMPT_VERSION,
            self._taxonomy_version,
            *[str(a) for a in args if a],
        ]
        key_str = "|".join(components)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def _call_ollama(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        no_timeout: bool = False,
        cancel_check: callable = None,
    ) -> dict | None:
        """Call Ollama API for completion with concurrency limiting.

        Uses semaphore to limit concurrent requests to the Ollama server.
        Never logs prompts or raw content at INFO level (privacy constraint).

        Args:
            model: Model name (e.g., "qwen2.5:7b").
            system_prompt: System message.
            user_message: User message.
            no_timeout: If True, wait indefinitely for response (for scheduled jobs).
                       LLM inference can take minutes or even hours for complex documents.
            cancel_check: Optional callable that returns True if the job should be cancelled.
                         Used for streaming responses to check for cancellation between chunks.

        Returns:
            Dict with "content" and "model" keys, or None on failure/cancellation.
        """
        # Acquire concurrency slot - wait indefinitely if no_timeout, otherwise use configured timeout
        wait_timeout = None if no_timeout else self.llm_config.timeout_seconds
        if not self._limiter.acquire(timeout=wait_timeout):
            logger.warning(
                "LLM request timed out waiting for concurrency slot (max=%d, active=%d)",
                self.llm_config.max_concurrent,
                self._limiter.active_requests,
            )
            return None

        try:
            url = f"{self.llm_config.ollama_url}/api/chat"
            
            # Use streaming if we have a cancel_check function
            use_streaming = cancel_check is not None and no_timeout
            
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": use_streaming,
                "format": "json",
            }

            # Debug logging only (never at INFO)
            if no_timeout:
                logger.info("Calling Ollama model %s (no timeout - will wait indefinitely%s)", 
                           model, ", with cancel check" if cancel_check else "")
            else:
                logger.debug("Calling Ollama model %s at %s", model, self.llm_config.ollama_url)

            # Use explicit Timeout - None for scheduled jobs that must wait indefinitely
            # LLM inference can take minutes or even hours for complex documents
            if no_timeout:
                # No timeout at all - wait as long as needed
                request_timeout = None
            else:
                request_timeout = httpx.Timeout(
                    connect=10.0,
                    read=float(self.llm_config.timeout_seconds),
                    write=30.0,
                    pool=10.0,
                )
            
            if use_streaming:
                # Stream response and check for cancellation between chunks
                content_parts = []
                with self._client.stream("POST", url, json=payload, timeout=request_timeout) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        # Check for cancellation between chunks
                        if cancel_check and cancel_check():
                            logger.info("Ollama request cancelled by user")
                            return None
                        
                        if line:
                            try:
                                chunk = json.loads(line)
                                if "message" in chunk and "content" in chunk["message"]:
                                    content_parts.append(chunk["message"]["content"])
                                # Check if streaming is done
                                if chunk.get("done", False):
                                    break
                            except json.JSONDecodeError:
                                continue
                
                # Final cancellation check
                if cancel_check and cancel_check():
                    logger.info("Ollama request cancelled by user after completion")
                    return None
                    
                content = "".join(content_parts)
            else:
                # Non-streaming request
                response = self._client.post(url, json=payload, timeout=request_timeout)
                response.raise_for_status()
                data = response.json()
                content = data.get("message", {}).get("content", "")

            logger.debug("Ollama %s returned %d chars", model, len(content))
            return {"content": content, "model": model}

        except httpx.TimeoutException:
            logger.warning("Ollama request timed out after %ds", self.llm_config.timeout_seconds)
            return None
        except httpx.HTTPStatusError as e:
            logger.error(
                "Ollama API error %s for model '%s' at %s",
                e.response.status_code,
                model,
                self.llm_config.ollama_url,
            )
            return None
        except httpx.RequestError as e:
            logger.error("Ollama request failed: %s (URL: %s)", e, self.llm_config.ollama_url)
            return None
        except Exception as e:
            logger.exception("Unexpected error calling Ollama: %s", e)
            return None
        finally:
            # Always release the concurrency slot
            self._limiter.release()

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from LLM response with robust handling of malformed responses.

        Handles:
        - Markdown code blocks (```json ... ```)
        - Leading/trailing whitespace
        - Newlines within JSON
        - Minor formatting issues
        - Extracts JSON from mixed text responses

        Args:
            content: Raw LLM response content.

        Returns:
            Parsed JSON dict.

        Raises:
            json.JSONDecodeError: If content cannot be parsed as valid JSON.
        """
        if not content:
            raise json.JSONDecodeError("Empty response", "", 0)

        # Strip leading/trailing whitespace
        content = content.strip()

        # Remove markdown code blocks
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        # Try direct parsing first
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # Try to find JSON array pattern for splits FIRST (handles nested objects)
        # Check for array pattern before object pattern to avoid extracting first object
        if "[" in content:
            array_match = re.search(r"\[[\s\S]*\]", content)
            if array_match:
                try:
                    # Wrap in object with splits key
                    splits_data = json.loads(array_match.group())
                    if isinstance(splits_data, list) and len(splits_data) > 0:
                        return {
                            "should_split": True,
                            "splits": splits_data,
                            "confidence": 0.5,
                            "reason": "Extracted from malformed response",
                        }
                except json.JSONDecodeError:
                    pass

        # Try to extract JSON object from response using regex
        # Match the outermost { ... } block (only if not inside an array)
        json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Clean common issues and retry
        # Remove control characters except newlines and tabs
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", content)
        # Fix common JSON issues: trailing commas before } or ]
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        # Fix unquoted keys (simple cases)
        cleaned = re.sub(r"(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1"\2":', cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Last resort: try to extract key-value pairs manually
        result = {}

        # Look for should_split
        split_match = re.search(r'"?should_split"?\s*:\s*(true|false)', content, re.IGNORECASE)
        if split_match:
            result["should_split"] = split_match.group(1).lower() == "true"

        # Look for confidence
        conf_match = re.search(r'"?confidence"?\s*:\s*([0-9.]+)', content)
        if conf_match:
            try:
                result["confidence"] = float(conf_match.group(1))
            except ValueError:
                result["confidence"] = 0.5

        # Look for category
        cat_match = re.search(r'"?category"?\s*:\s*"([^"]+)"', content)
        if cat_match:
            result["category"] = cat_match.group(1)

        # Look for reason
        reason_match = re.search(r'"?reason"?\s*:\s*"([^"]+)"', content)
        if reason_match:
            result["reason"] = reason_match.group(1)
        else:
            result["reason"] = "Extracted from malformed response"

        if result:
            logger.debug("Extracted partial data from malformed LLM response: %s", result)
            return result

        # Give up
        raise json.JSONDecodeError(
            f"Could not parse JSON from response: {content[:200]}...", content, 0
        )

    def suggest_for_review(
        self,
        amount: str,
        date: str,
        vendor: str | None = None,
        description: str | None = None,
        current_category: str | None = None,
        current_type: str | None = None,
        invoice_number: str | None = None,
        ocr_confidence: float = 0.0,
        document_content: str | None = None,
        bank_transaction: dict | None = None,
        previous_decisions: list[dict] | None = None,
        document_id: int | None = None,
        use_cache: bool = True,
        no_timeout: bool = False,
        cancel_check: callable = None,
        source_accounts: list[str] | None = None,
        current_source_account: str | None = None,
    ) -> TransactionReviewSuggestion | None:
        """Suggest values for all editable transaction fields during review.
        
        This is the comprehensive AI suggestion method used by the review UI.
        It analyzes the document content, linked bank transactions, and previous
        decisions to suggest values for category, transaction type, vendor, etc.
        
        Args:
            amount: Transaction amount.
            date: Transaction date.
            vendor: Current vendor/payee name.
            description: Current transaction description.
            current_category: Currently assigned category.
            current_type: Currently assigned transaction type.
            invoice_number: Invoice/receipt number if extracted.
            ocr_confidence: Overall OCR confidence (0.0-1.0).
            document_content: Raw OCR text or structured invoice content.
            bank_transaction: Linked bank transaction data if available.
            previous_decisions: List of previous interpretation decisions.
            document_id: Optional document ID for per-doc opt-out check.
            use_cache: Whether to use cached responses.
            no_timeout: If True, wait indefinitely for LLM response (for scheduled jobs).
                       LLM inference can take minutes or even hours.
            cancel_check: Optional callable that returns True if the job should be cancelled.
                         Allows cancellation of long-running LLM requests.
            source_accounts: List of available source account names for suggestions.
            current_source_account: Currently selected source account.
            
        Returns:
            TransactionReviewSuggestion with per-field suggestions, or None if LLM disabled/cancelled.
        """
        if not self.is_enabled:
            logger.debug("LLM service disabled, skipping review suggestions")
            return None
            
        # Per-document opt-out check
        if document_id:
            opted_out, reason = self.check_opt_out(document_id)
            if opted_out:
                logger.debug("LLM opted out for doc %d: %s", document_id, reason)
                return None
                
        if not self.categories:
            logger.warning("No categories configured for LLM suggestions")
            return None
            
        # Build cache key including all context
        context_hash = hashlib.sha256(
            json.dumps({
                "amount": amount,
                "date": date,
                "vendor": vendor,
                "description": description,
                "current_category": current_category,
                "current_type": current_type,
                "bank_amount": bank_transaction.get("amount") if bank_transaction else None,
                "content_hash": hashlib.sha256((document_content or "")[:500].encode()).hexdigest()[:8],
            }, sort_keys=True).encode()
        ).hexdigest()[:16]
        cache_key = f"review:{context_hash}:{self._taxonomy_version}"
        
        # Check cache
        if use_cache:
            cached = self.store.get_llm_cache(cache_key)
            if cached:
                try:
                    data = json.loads(cached["response_json"])
                    suggestions = {}
                    for field, field_data in data.get("suggestions", {}).items():
                        suggestions[field] = FieldSuggestion(
                            value=field_data["value"],
                            confidence=field_data["confidence"],
                            reason=field_data.get("reason", ""),
                        )
                    return TransactionReviewSuggestion(
                        suggestions=suggestions,
                        overall_confidence=data.get("overall_confidence", 0.0),
                        analysis_notes=data.get("analysis_notes", ""),
                        model=cached["model"],
                        from_cache=True,
                    )
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("Invalid cached review response: %s", e)
                    
        # Build prompt with full context
        user_message = self._review_prompt.format_user_message(
            amount=amount,
            date=date,
            vendor=vendor,
            description=description,
            current_category=current_category,
            current_type=current_type,
            invoice_number=invoice_number,
            ocr_confidence=ocr_confidence,
            document_content=document_content,
            bank_transaction=bank_transaction,
            previous_decisions=previous_decisions,
            categories=self.categories,
            source_accounts=source_accounts,
            current_source_account=current_source_account,
        )
        
        # Try fast model first
        result = self._call_ollama(
            model=self.llm_config.model_fast,
            system_prompt=self._review_prompt.system_prompt,
            user_message=user_message,
            no_timeout=no_timeout,
            cancel_check=cancel_check,
        )
        
        # Fallback to slow model if needed
        if result is None and self.llm_config.model_fallback:
            # Check for cancellation before fallback
            if cancel_check and cancel_check():
                logger.info("Job cancelled, skipping fallback model")
                return None
            logger.info("Fast model failed, falling back to %s", self.llm_config.model_fallback)
            result = self._call_ollama(
                model=self.llm_config.model_fallback,
                system_prompt=self._review_prompt.system_prompt,
                user_message=user_message,
                no_timeout=no_timeout,
                cancel_check=cancel_check,
            )
            
        if result is None:
            return None
            
        try:
            data = self._parse_json_response(result["content"])
            
            # Parse suggestions from response
            suggestions = {}
            for field, field_data in data.get("suggestions", {}).items():
                if isinstance(field_data, dict) and "value" in field_data:
                    # Validate category suggestions
                    if field == "category" and field_data["value"] not in self.categories:
                        logger.warning(
                            "LLM suggested invalid category '%s', skipping",
                            field_data["value"],
                        )
                        continue
                    # Validate transaction_type suggestions
                    if field == "transaction_type" and field_data["value"] not in ["withdrawal", "deposit", "transfer"]:
                        logger.warning(
                            "LLM suggested invalid transaction_type '%s', skipping",
                            field_data["value"],
                        )
                        continue
                        
                    suggestions[field] = FieldSuggestion(
                        value=str(field_data["value"]),
                        confidence=float(field_data.get("confidence", 0.5)),
                        reason=str(field_data.get("reason", "")),
                    )
            
            # Parse split transactions if present
            split_transactions = None
            raw_splits = data.get("split_transactions")
            if raw_splits and isinstance(raw_splits, list) and len(raw_splits) > 0:
                # Validate split categories against available categories
                valid_splits = []
                for split in raw_splits:
                    if isinstance(split, dict):
                        split_cat = split.get("category")
                        if split_cat and split_cat not in self.categories:
                            logger.warning(
                                "LLM suggested invalid split category '%s', skipping",
                                split_cat,
                            )
                            split["category"] = None  # Clear invalid category
                        valid_splits.append({
                            "amount": float(split.get("amount", 0)),
                            "description": str(split.get("description", "")),
                            "category": split.get("category"),
                        })
                if valid_splits:
                    split_transactions = valid_splits
                    
            review_suggestion = TransactionReviewSuggestion(
                suggestions=suggestions,
                overall_confidence=float(data.get("overall_confidence", 0.0)),
                analysis_notes=str(data.get("analysis_notes", "")),
                model=result["model"],
                split_transactions=split_transactions,
            )
            
            # Cache the result
            self.store.set_llm_cache(
                cache_key=cache_key,
                model=result["model"],
                prompt_version=PROMPT_VERSION,
                taxonomy_version=self._taxonomy_version,
                response_json=json.dumps(review_suggestion.to_dict()),
            )
            
            return review_suggestion
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Failed to parse LLM review response: %s", e)
            return None

    def chat(
        self,
        question: str,
        documentation: str | None = None,
        page_context: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> str | None:
        """Chat with the LLM using documentation context.

        Args:
            question: User's question.
            documentation: Optional documentation content for context.
            page_context: Optional context about the current page the user is viewing.
            conversation_history: Optional list of recent messages for context.

        Returns:
            LLM response string or None if failed.
        """
        if not self.is_enabled:
            return None

        chat_prompt = ChatPrompt()
        user_message = chat_prompt.format_user_message(
            question=question,
            documentation=documentation or "",
            page_context=page_context or "",
            conversation_history=conversation_history,
        )

        # Use fast model for chat (without JSON format requirement)
        result = self._call_ollama_text(
            model=self.llm_config.model_fast,
            system_prompt=chat_prompt.system_prompt,
            user_message=user_message,
        )

        if result:
            return result["content"]
        return None

    def _call_ollama_text(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
    ) -> dict | None:
        """Call Ollama API for text completion (no JSON format).

        Similar to _call_ollama but returns natural text instead of forcing JSON.
        Used for chatbot responses.

        Args:
            model: Model name (e.g., "qwen2.5:7b").
            system_prompt: System message.
            user_message: User message.

        Returns:
            Dict with "content" and "model" keys, or None on failure.
        """
        # Acquire concurrency slot with timeout
        wait_timeout = self.llm_config.timeout_seconds
        if not self._limiter.acquire(timeout=wait_timeout):
            logger.warning(
                "LLM request timed out waiting for concurrency slot (max=%d, active=%d)",
                self.llm_config.max_concurrent,
                self._limiter.active_requests,
            )
            return None

        try:
            url = f"{self.llm_config.ollama_url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                # No "format": "json" - we want natural text
            }

            logger.debug("Calling Ollama chat model %s at %s", model, url)

            # Use explicit Timeout with long read timeout for LLM inference
            request_timeout = httpx.Timeout(
                connect=10.0,
                read=float(self.llm_config.timeout_seconds),
                write=30.0,
                pool=10.0,
            )
            response = self._client.post(url, json=payload, timeout=request_timeout)
            response.raise_for_status()

            data = response.json()
            content = data.get("message", {}).get("content", "")

            logger.debug("Ollama %s returned %d chars", model, len(content))
            return {"content": content, "model": model}

        except httpx.TimeoutException:
            logger.warning("Ollama request timed out after %ds", self.llm_config.timeout_seconds)
            return None
        except httpx.HTTPStatusError as e:
            logger.error(
                "Ollama API error %s for model '%s' at %s",
                e.response.status_code,
                model,
                self.llm_config.ollama_url,
            )
            return None
        except httpx.RequestError as e:
            logger.error("Ollama request failed: %s (URL: %s)", e, self.llm_config.ollama_url)
            return None
        except Exception as e:
            logger.exception("Unexpected error calling Ollama: %s", e)
            return None
        finally:
            self._limiter.release()

    def close(self) -> None:
        """Close HTTP client."""
        self._client.close()

    def __enter__(self) -> SparkAIService:
        """Enter context manager."""
        return self

    def __exit__(self, *args) -> None:
        """Exit context manager."""
        self.close()
