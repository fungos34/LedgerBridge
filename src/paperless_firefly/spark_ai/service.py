"""Spark AI service for LLM-assisted transaction categorization.

This service implements the LLM integration specified in Spark v1.0 Phase 6/7.
Features:
- Ollama integration with configurable models
- Cascading model fallback (fast -> slow)
- Response caching with taxonomy version tracking
- Calibration period before auto-apply
- Opt-out support for individual extractions
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from paperless_firefly.spark_ai.prompts import PROMPT_VERSION, CategoryPrompt, SplitPrompt

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


class SparkAIService:
    """LLM-assisted categorization service.

    This service provides AI-powered category suggestions using Ollama.
    It implements:
    - Caching with taxonomy/prompt version tracking
    - Cascading model fallback
    - Calibration period before auto-apply
    - Green threshold for high-confidence suggestions
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

        self._client = httpx.Client(timeout=60.0)
        self._category_prompt = CategoryPrompt()
        self._split_prompt = SplitPrompt()

    def _compute_taxonomy_version(self) -> str:
        """Compute a hash of the category taxonomy for cache invalidation."""
        if not self.categories:
            return "empty"
        cats_str = "|".join(sorted(self.categories))
        return hashlib.sha256(cats_str.encode()).hexdigest()[:12]

    @property
    def is_enabled(self) -> bool:
        """Check if LLM service is enabled."""
        return self.llm_config.enabled

    @property
    def is_calibrating(self) -> bool:
        """Check if service is still in calibration period.

        During calibration, suggestions are shown but not auto-applied.
        """
        if not self.is_enabled:
            return False
        suggestion_count = self.store.get_llm_suggestion_count()
        return suggestion_count < self.llm_config.calibration_count

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
        use_cache: bool = True,
    ) -> CategorySuggestion | None:
        """Suggest a category for a transaction.

        Args:
            amount: Transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            use_cache: Whether to use cached responses.

        Returns:
            CategorySuggestion or None if LLM is disabled or fails.
        """
        if not self.is_enabled:
            logger.debug("LLM service disabled, skipping suggestion")
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
        use_cache: bool = True,
    ) -> SplitSuggestion | None:
        """Suggest transaction splits.

        Args:
            amount: Total transaction amount.
            date: Transaction date.
            vendor: Vendor or payee name.
            description: Transaction description.
            content: Additional document content.
            use_cache: Whether to use cached responses.

        Returns:
            SplitSuggestion or None if LLM is disabled or fails.
        """
        if not self.is_enabled:
            return None

        if not self.categories:
            return None

        # Build cache key including content hash
        content_hash = hashlib.sha256((content or "").encode()).hexdigest()[:8]
        cache_key = self._build_cache_key("split", amount, date, vendor, description, content_hash)

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

        # Build prompt
        user_message = self._split_prompt.format_user_message(
            amount=amount,
            date=date,
            vendor=vendor,
            description=description,
            content=content,
            categories=self.categories,
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
            suggestion = SplitSuggestion(
                should_split=data.get("should_split", False),
                splits=data.get("splits", []),
                confidence=float(data.get("confidence", 0.0)),
                reason=data.get("reason", ""),
                model=result["model"],
            )

            # Validate split categories
            for split in suggestion.splits:
                if split.get("category") not in self.categories:
                    logger.warning(
                        "LLM suggested invalid split category '%s'",
                        split.get("category"),
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
            logger.warning("Failed to parse LLM split response: %s", e)
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
    ) -> dict | None:
        """Call Ollama API for completion.

        Args:
            model: Model name (e.g., "qwen2.5:7b").
            system_prompt: System message.
            user_message: User message.

        Returns:
            Dict with "content" and "model" keys, or None on failure.
        """
        try:
            url = f"{self.llm_config.ollama_url}/api/chat"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "format": "json",
            }

            response = self._client.post(url, json=payload)
            response.raise_for_status()

            data = response.json()
            content = data.get("message", {}).get("content", "")

            return {"content": content, "model": model}

        except httpx.HTTPStatusError as e:
            logger.error("Ollama API error: %s", e.response.status_code)
            return None
        except httpx.RequestError as e:
            logger.error("Ollama request failed: %s", e)
            return None
        except Exception as e:
            logger.exception("Unexpected error calling Ollama: %s", e)
            return None

    def _parse_json_response(self, content: str) -> dict:
        """Parse JSON from LLM response, handling markdown code blocks.

        Args:
            content: Raw LLM response content.

        Returns:
            Parsed JSON dict.

        Raises:
            json.JSONDecodeError: If content is not valid JSON.
        """
        # Strip markdown code blocks if present
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        return json.loads(content.strip())

    def close(self) -> None:
        """Close HTTP client."""
        self._client.close()

    def __enter__(self) -> SparkAIService:
        """Enter context manager."""
        return self

    def __exit__(self, *args) -> None:
        """Exit context manager."""
        self.close()
