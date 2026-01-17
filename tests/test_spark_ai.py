"""Tests for Spark AI service and prompts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paperless_firefly.spark_ai.prompts import (
    PROMPT_VERSION,
    CategoryPrompt,
    ChatPrompt,
    SplitPrompt,
)
from paperless_firefly.spark_ai.service import (
    CategorySuggestion,
    SparkAIService,
    SplitSuggestion,
    TransactionReviewSuggestion,
)
from paperless_firefly.state_store import StateStore


class TestCategoryPrompt:
    """Tests for CategoryPrompt."""

    def test_prompt_version_set(self) -> None:
        """Test prompt has version set."""
        prompt = CategoryPrompt()
        assert prompt.version == PROMPT_VERSION

    def test_format_user_message(self) -> None:
        """Test formatting user message with all fields."""
        prompt = CategoryPrompt()
        message = prompt.format_user_message(
            amount="99.99",
            date="2025-01-15",
            vendor="Amazon",
            description="Electronics purchase",
            categories=["Shopping", "Electronics", "Groceries"],
        )

        assert "99.99" in message
        assert "2025-01-15" in message
        assert "Amazon" in message
        assert "Electronics purchase" in message
        assert "- Shopping" in message
        assert "- Electronics" in message
        assert "- Groceries" in message

    def test_format_user_message_missing_optional(self) -> None:
        """Test formatting with missing optional fields."""
        prompt = CategoryPrompt()
        message = prompt.format_user_message(
            amount="50.00",
            date="2025-01-15",
            vendor=None,
            description=None,
            categories=["Shopping"],
        )

        assert "50.00" in message
        assert "Unknown" in message  # Default for vendor
        assert "No description" in message  # Default for description


class TestSplitPrompt:
    """Tests for SplitPrompt."""

    def test_format_user_message(self) -> None:
        """Test formatting split prompt with content."""
        prompt = SplitPrompt()
        message = prompt.format_user_message(
            amount="150.00",
            date="2025-01-15",
            vendor="Target",
            description="Multiple items",
            content="Item 1: $100, Item 2: $50",
            categories=["Groceries", "Household"],
        )

        assert "150.00" in message
        assert "Target" in message
        assert "Item 1: $100" in message
        # Categories are now formatted with bullet points (•)
        assert "Groceries" in message
        assert "Household" in message

    def test_format_user_message_with_bank_data(self) -> None:
        """Test formatting split prompt with bank data context."""
        prompt = SplitPrompt()
        message = prompt.format_user_message(
            amount="150.00",
            date="2025-01-15",
            vendor="Target",
            description="Multiple items",
            content="Item 1: $100",
            categories=["Groceries"],
            bank_data={
                "amount": "150.00",
                "date": "2025-01-15",
                "description": "TARGET STORE",
                "category_name": "Shopping",
            },
        )

        assert "Bank Amount: 150.00" in message
        assert "Bank Description: TARGET STORE" in message
        assert "Bank Category: Shopping" in message


class TestChatPrompt:
    """Tests for ChatPrompt."""

    def test_prompt_version_set(self) -> None:
        """Test chat prompt has version set."""
        prompt = ChatPrompt()
        assert prompt.version == PROMPT_VERSION

    def test_format_user_message(self) -> None:
        """Test formatting chat user message."""
        prompt = ChatPrompt()
        message = prompt.format_user_message(
            question="How do I configure Paperless connection?",
            documentation="# Configuration\nSet PAPERLESS_URL in config.yaml",
        )

        assert "How do I configure Paperless connection?" in message
        assert "Set PAPERLESS_URL" in message

    def test_format_user_message_no_docs(self) -> None:
        """Test formatting chat message without documentation."""
        prompt = ChatPrompt()
        message = prompt.format_user_message(
            question="What is SparkLink?",
            documentation="",
        )

        assert "What is SparkLink?" in message
        assert "No additional documentation" in message

    def test_format_user_message_with_history(self) -> None:
        """Test formatting chat message with conversation history."""
        prompt = ChatPrompt()
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
        ]
        message = prompt.format_user_message(
            question="What buttons are on this page?",
            documentation="Test docs",
            conversation_history=history,
        )

        assert "RECENT CONVERSATION" in message
        assert "USER: Hello" in message
        assert "ASSISTANT: Hi! How can I help?" in message
        assert "What buttons are on this page?" in message

    def test_format_user_message_with_page_context(self) -> None:
        """Test formatting chat message with page context."""
        prompt = ChatPrompt()
        page_context = "Current page: Document Review\nYou can edit amount and date."
        message = prompt.format_user_message(
            question="What can I do here?",
            documentation="Test docs",
            page_context=page_context,
        )

        assert "CURRENT PAGE CONTEXT" in message
        assert "Document Review" in message
        assert "edit amount and date" in message

    def test_system_prompt_content(self) -> None:
        """Test system prompt contains key information."""
        prompt = ChatPrompt()

        assert "SparkLink" in prompt.system_prompt
        assert "Paperless-ngx" in prompt.system_prompt
        assert "Firefly III" in prompt.system_prompt


class TestCategorySuggestion:
    """Tests for CategorySuggestion dataclass."""

    def test_to_dict(self) -> None:
        """Test to_dict serialization."""
        suggestion = CategorySuggestion(
            category="Shopping",
            confidence=0.85,
            reason="Vendor is Amazon",
            model="qwen2.5:7b",
            from_cache=False,
        )
        data = suggestion.to_dict()

        assert data["category"] == "Shopping"
        assert data["confidence"] == 0.85
        assert data["reason"] == "Vendor is Amazon"
        assert data["model"] == "qwen2.5:7b"
        assert data["from_cache"] is False


class TestSplitSuggestion:
    """Tests for SplitSuggestion dataclass."""

    def test_to_dict_with_splits(self) -> None:
        """Test to_dict with splits."""
        suggestion = SplitSuggestion(
            should_split=True,
            splits=[
                {"category": "Groceries", "amount": 100.0, "description": "Food"},
                {"category": "Household", "amount": 50.0, "description": "Supplies"},
            ],
            confidence=0.75,
            reason="Itemized receipt",
            model="qwen2.5:14b",
        )
        data = suggestion.to_dict()

        assert data["should_split"] is True
        assert len(data["splits"]) == 2
        assert data["confidence"] == 0.75


class TestSparkAIService:
    """Tests for SparkAIService."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore for each test."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    @pytest.fixture
    def mock_config_enabled(self) -> MagicMock:
        """Create mock config with LLM enabled."""
        config = MagicMock()
        config.llm.enabled = True
        config.llm.ollama_url = "http://localhost:11434"
        config.llm.model_fast = "qwen2.5:7b"
        config.llm.model_fallback = "qwen2.5:14b"
        config.llm.green_threshold = 0.90
        config.llm.calibration_count = 50
        config.llm.timeout_seconds = 30
        config.llm.max_concurrent = 2
        config.llm.auth_header = None  # No auth for local Ollama
        return config

    @pytest.fixture
    def mock_config_disabled(self) -> MagicMock:
        """Create mock config with LLM disabled."""
        config = MagicMock()
        config.llm.enabled = False
        config.llm.ollama_url = "http://localhost:11434"
        config.llm.timeout_seconds = 30
        config.llm.max_concurrent = 2
        config.llm.auth_header = None
        return config

    @pytest.fixture
    def categories(self) -> list[str]:
        """Test categories."""
        return ["Shopping", "Groceries", "Dining", "Transportation", "Bills"]

    def test_is_enabled_true(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test is_enabled returns True when enabled."""
        service = SparkAIService(store, mock_config_enabled, categories)
        assert service.is_enabled is True

    def test_is_enabled_false(
        self, store: StateStore, mock_config_disabled: MagicMock, categories: list[str]
    ) -> None:
        """Test is_enabled returns False when disabled."""
        service = SparkAIService(store, mock_config_disabled, categories)
        assert service.is_enabled is False

    def test_is_calibrating_initial(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test is_calibrating returns True initially."""
        service = SparkAIService(store, mock_config_enabled, categories)
        assert service.is_calibrating is True

    def test_taxonomy_version_changes_with_categories(
        self, store: StateStore, mock_config_enabled: MagicMock
    ) -> None:
        """Test taxonomy version changes when categories change."""
        service = SparkAIService(store, mock_config_enabled, ["Cat1", "Cat2"])
        version1 = service._taxonomy_version

        service.set_categories(["Cat1", "Cat2", "Cat3"])
        version2 = service._taxonomy_version

        assert version1 != version2

    def test_taxonomy_version_same_for_same_categories(
        self, store: StateStore, mock_config_enabled: MagicMock
    ) -> None:
        """Test taxonomy version is stable for same categories."""
        service1 = SparkAIService(store, mock_config_enabled, ["Cat1", "Cat2"])
        service2 = SparkAIService(store, mock_config_enabled, ["Cat2", "Cat1"])  # Different order

        # Same categories in different order should give same version
        assert service1._taxonomy_version == service2._taxonomy_version

    def test_suggest_category_disabled(
        self, store: StateStore, mock_config_disabled: MagicMock, categories: list[str]
    ) -> None:
        """Test suggest_category returns None when disabled."""
        service = SparkAIService(store, mock_config_disabled, categories)
        result = service.suggest_category(amount="99.99", date="2025-01-15", vendor="Amazon")
        assert result is None

    def test_suggest_category_no_categories(
        self, store: StateStore, mock_config_enabled: MagicMock
    ) -> None:
        """Test suggest_category returns None without categories."""
        service = SparkAIService(store, mock_config_enabled, categories=[])
        result = service.suggest_category(amount="99.99", date="2025-01-15", vendor="Amazon")
        assert result is None

    @patch("paperless_firefly.spark_ai.service.httpx.Client")
    def test_suggest_category_caches_result(
        self,
        mock_client_class: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test successful suggestion is cached."""
        # Mock Ollama response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {"category": "Shopping", "confidence": 0.85, "reason": "Amazon"}
                )
            }
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        service = SparkAIService(store, mock_config_enabled, categories)

        # First call - hits Ollama
        result1 = service.suggest_category(amount="99.99", date="2025-01-15", vendor="Amazon")

        assert result1 is not None
        assert result1.category == "Shopping"
        assert result1.from_cache is False

        # Second call - should use cache
        result2 = service.suggest_category(amount="99.99", date="2025-01-15", vendor="Amazon")

        assert result2 is not None
        assert result2.category == "Shopping"
        assert result2.from_cache is True

        # Ollama should only be called once
        assert mock_client.post.call_count == 1

    @patch("paperless_firefly.spark_ai.service.httpx.Client")
    def test_suggest_category_invalid_category_rejected(
        self,
        mock_client_class: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test invalid category from LLM is rejected."""
        # Mock Ollama response with invalid category
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {"category": "InvalidCategory", "confidence": 0.9, "reason": "Test"}
                )
            }
        }
        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_class.return_value = mock_client

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_category(amount="99.99", date="2025-01-15", vendor="Amazon")

        assert result is None  # Invalid category should be rejected

    def test_should_auto_apply_disabled(
        self, store: StateStore, mock_config_disabled: MagicMock, categories: list[str]
    ) -> None:
        """Test should_auto_apply returns False when disabled."""
        service = SparkAIService(store, mock_config_disabled, categories)
        assert service.should_auto_apply(0.95) is False

    def test_should_auto_apply_calibrating(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test should_auto_apply returns False during calibration."""
        service = SparkAIService(store, mock_config_enabled, categories)
        # Should be calibrating since no suggestions yet
        assert service.should_auto_apply(0.95) is False

    def test_should_auto_apply_below_threshold(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test should_auto_apply returns False below threshold."""
        mock_config_enabled.llm.calibration_count = 0  # Skip calibration
        service = SparkAIService(store, mock_config_enabled, categories)
        assert service.should_auto_apply(0.80) is False  # Below 0.90 threshold

    def test_should_auto_apply_above_threshold(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test should_auto_apply returns True above threshold."""
        mock_config_enabled.llm.calibration_count = 0  # Skip calibration
        service = SparkAIService(store, mock_config_enabled, categories)
        assert service.should_auto_apply(0.95) is True  # Above 0.90 threshold

    def test_record_feedback_correct(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test recording correct feedback."""
        # Create an interpretation run first
        store.upsert_document(document_id=1, source_hash="hash", title="Test")
        run_id = store.create_interpretation_run(
            document_id=1,
            firefly_id=None,
            external_id="ext-1",
            pipeline_version="1.0",
            inputs_summary={},
            final_state="AWAITING_REVIEW",
            llm_result={"category": "Shopping"},
        )

        service = SparkAIService(store, mock_config_enabled, categories)
        service.record_feedback(
            run_id=run_id,
            suggested_category="Shopping",
            actual_category="Shopping",
        )

        stats = store.get_llm_feedback_stats()
        assert stats["correct"] == 1
        assert stats["wrong"] == 0

    def test_record_feedback_wrong(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test recording wrong feedback."""
        store.upsert_document(document_id=1, source_hash="hash", title="Test")
        run_id = store.create_interpretation_run(
            document_id=1,
            firefly_id=None,
            external_id="ext-1",
            pipeline_version="1.0",
            inputs_summary={},
            final_state="AWAITING_REVIEW",
            llm_result={"category": "Shopping"},
        )

        service = SparkAIService(store, mock_config_enabled, categories)
        service.record_feedback(
            run_id=run_id,
            suggested_category="Shopping",
            actual_category="Groceries",
        )

        stats = store.get_llm_feedback_stats()
        assert stats["correct"] == 0
        assert stats["wrong"] == 1

    def test_get_calibration_stats(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test getting calibration stats."""
        service = SparkAIService(store, mock_config_enabled, categories)
        stats = service.get_calibration_stats()

        assert stats["enabled"] is True
        assert stats["calibrating"] is True
        assert stats["suggestion_count"] == 0
        assert stats["calibration_target"] == 50
        assert stats["calibration_progress"] == 0.0

    def test_parse_json_response_plain(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test parsing plain JSON response."""
        service = SparkAIService(store, mock_config_enabled, categories)
        result = service._parse_json_response('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_json_response_markdown_code_block(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test parsing JSON from markdown code block."""
        service = SparkAIService(store, mock_config_enabled, categories)
        result = service._parse_json_response('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_context_manager(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test service can be used as context manager."""
        with SparkAIService(store, mock_config_enabled, categories) as service:
            assert service.is_enabled is True

    def test_build_cache_key_deterministic(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test cache key is deterministic."""
        service = SparkAIService(store, mock_config_enabled, categories)

        key1 = service._build_cache_key("category", "100", "2025-01-15", "Amazon")
        key2 = service._build_cache_key("category", "100", "2025-01-15", "Amazon")

        assert key1 == key2

    def test_build_cache_key_differs_by_content(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test cache key differs by content."""
        service = SparkAIService(store, mock_config_enabled, categories)

        key1 = service._build_cache_key("category", "100", "2025-01-15", "Amazon")
        key2 = service._build_cache_key("category", "200", "2025-01-15", "Amazon")

        assert key1 != key2

    def test_parse_json_response_malformed_extracts_object(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test parsing JSON from malformed response with extra text."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Response with extra text before and after JSON
        malformed = (
            'Here is my response:\n{"category": "Shopping", "confidence": 0.8}\nHope this helps!'
        )
        result = service._parse_json_response(malformed)

        assert result["category"] == "Shopping"
        assert result["confidence"] == 0.8

    def test_parse_json_response_trailing_comma(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test parsing JSON with trailing commas."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Common LLM mistake: trailing comma
        malformed = '{"category": "Shopping", "confidence": 0.8,}'
        result = service._parse_json_response(malformed)

        assert result["category"] == "Shopping"
        assert result["confidence"] == 0.8

    def test_parse_json_response_unquoted_keys(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test parsing JSON with unquoted keys."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Another common LLM mistake
        malformed = '{category: "Shopping", confidence: 0.8}'
        result = service._parse_json_response(malformed)

        assert result["category"] == "Shopping"
        assert result["confidence"] == 0.8

    def test_parse_json_response_extracts_key_values(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test extracting key-values from very malformed response."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Badly formatted response
        malformed = """
        I think this should be categorized as follows:
        should_split: true
        confidence: 0.75
        reason: "Multiple items detected"
        """
        result = service._parse_json_response(malformed)

        assert result.get("should_split") is True
        assert result.get("confidence") == 0.75

    def test_parse_json_response_extracts_array(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test extracting JSON array from split response."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Response with just array (using proper JSON double quotes)
        malformed = """Here are the splits:
        [{"category": "Groceries", "amount": 50.0}, {"category": "Household", "amount": 25.0}]
        """
        result = service._parse_json_response(malformed)

        assert result["should_split"] is True
        assert len(result["splits"]) == 2

    def test_match_category_exact(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test exact category matching."""
        service = SparkAIService(store, mock_config_enabled, categories)

        result = service._match_category("Shopping")
        assert result == "Shopping"

        # Case insensitive
        result = service._match_category("shopping")
        assert result == "Shopping"

    def test_match_category_substring(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test substring category matching."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # LLM might say "Food & Groceries" but category is "Groceries"
        result = service._match_category("Food & Groceries")
        assert result == "Groceries"

    def test_match_category_word_overlap(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test word overlap category matching."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Test with partial word match
        result = service._match_category("Public Transportation")
        assert result == "Transportation"

    def test_match_category_no_match(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test category matching with no match."""
        service = SparkAIService(store, mock_config_enabled, categories)

        result = service._match_category("Healthcare")
        assert result is None

    def test_chat_disabled_returns_none(
        self, store: StateStore, mock_config_disabled: MagicMock, categories: list[str]
    ) -> None:
        """Test chat returns None when LLM is disabled."""
        service = SparkAIService(store, mock_config_disabled, categories)

        result = service.chat("What is SparkLink?")
        assert result is None

    @patch.object(SparkAIService, "_call_ollama_text")
    def test_chat_returns_response(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test chat returns response from LLM."""
        mock_call.return_value = {
            "content": "SparkLink is a financial document processing application.",
            "model": "qwen2.5:7b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.chat("What is SparkLink?", documentation="Test docs")

        assert result == "SparkLink is a financial document processing application."
        mock_call.assert_called_once()

    @patch.object(SparkAIService, "_call_ollama_text")
    def test_chat_with_history_and_context(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test chat passes conversation history and page context to LLM."""
        mock_call.return_value = {
            "content": "The Confirm button saves and marks the document as reviewed.",
            "model": "qwen2.5:7b",
        }

        conversation_history = [
            {"role": "user", "content": "What can I do on this page?"},
            {"role": "assistant", "content": "You can review and edit document details."},
        ]
        page_context = "Current page: Document Review\nThe user can edit amount, date, etc."

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.chat(
            "What does the Confirm button do?",
            documentation="Test docs",
            page_context=page_context,
            conversation_history=conversation_history,
        )

        assert result == "The Confirm button saves and marks the document as reviewed."
        mock_call.assert_called_once()

        # Verify history and context were included in the user message
        call_args = mock_call.call_args
        user_message = call_args.kwargs["user_message"]
        assert "Document Review" in user_message
        assert "RECENT CONVERSATION" in user_message
        assert "edit amount" in user_message

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_splits_with_bank_data(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_splits includes bank data in prompt."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "should_split": True,
                    "splits": [{"category": "Groceries", "amount": 50.0, "description": "Food"}],
                    "confidence": 0.8,
                    "reason": "Single item receipt",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_splits(
            amount="50.00",
            date="2025-01-15",
            vendor="Supermarket",
            description="Purchase",
            content="Milk 2.99\nBread 3.50",
            bank_data={"amount": "50.00", "description": "SUPERMARKET PURCHASE"},
        )

        assert result is not None
        assert result.should_split is True
        assert len(result.splits) == 1
        # Verify bank_data was passed to the prompt
        call_args = mock_call.call_args
        assert "SUPERMARKET PURCHASE" in call_args.kwargs["user_message"]

    def test_suggest_splits_normalizes_european_amounts(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test split suggestion normalizes European number format."""
        service = SparkAIService(store, mock_config_enabled, categories)

        # Test the amount normalization in _parse_json_response context
        # by checking that European format (comma decimal) is handled
        response_with_european = json.dumps(
            {
                "should_split": True,
                "splits": [
                    {"category": "Groceries", "amount": "12,50", "description": "Food"},
                ],
                "confidence": 0.8,
                "reason": "Test",
            }
        )

        # Mock the Ollama call to return European format amounts
        with patch.object(service, "_call_ollama") as mock_call:
            mock_call.return_value = {"content": response_with_european, "model": "qwen2.5:14b"}

            result = service.suggest_splits(amount="12.50", date="2025-01-15", use_cache=False)

            # The amount should be normalized to float
            if result and result.splits:
                assert result.splits[0]["amount"] == 12.50


class TestSuggestForReview:
    """Tests for the comprehensive suggest_for_review method."""

    @pytest.fixture
    def store(self, tmp_path) -> StateStore:
        """Create a fresh StateStore for each test."""
        db_path = tmp_path / "test.db"
        return StateStore(str(db_path), run_migrations=True)

    @pytest.fixture
    def mock_config_enabled(self) -> MagicMock:
        """Create mock config with LLM enabled."""
        config = MagicMock()
        config.llm.enabled = True
        config.llm.ollama_url = "http://localhost:11434"
        config.llm.model_fast = "qwen2.5:7b"
        config.llm.model_fallback = "qwen2.5:14b"
        config.llm.green_threshold = 0.90
        config.llm.calibration_count = 50
        config.llm.timeout_seconds = 30
        config.llm.max_concurrent = 2
        config.llm.auth_header = None
        return config

    @pytest.fixture
    def mock_config_disabled(self) -> MagicMock:
        """Create mock config with LLM disabled."""
        config = MagicMock()
        config.llm.enabled = False
        config.llm.ollama_url = "http://localhost:11434"
        config.llm.timeout_seconds = 30
        config.llm.max_concurrent = 2
        config.llm.auth_header = None
        return config

    @pytest.fixture
    def categories(self) -> list[str]:
        """Test categories including Electronics for split tests."""
        return ["Shopping", "Groceries", "Dining", "Transportation", "Bills", "Electronics"]

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_returns_all_fields(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review returns suggestions for all requested fields."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {
                            "value": "Groceries",
                            "confidence": 0.95,
                            "reason": "Food items detected",
                        },
                        "description": {
                            "value": "Grocery shopping at Lidl",
                            "confidence": 0.85,
                            "reason": "Based on receipt header",
                        },
                        "destination_account": {
                            "value": "Lidl Store",
                            "confidence": 0.90,
                            "reason": "Vendor name from header",
                        },
                        "transaction_type": {
                            "value": "withdrawal",
                            "confidence": 0.99,
                            "reason": "This is a purchase",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Clear receipt with itemized list",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="45.99",
            date="2025-01-15",
            vendor="Lidl",
            description="Purchase",
            document_content="LIDL\nMilk 2.99\nBread 1.50\nTotal 45.99",
            use_cache=False,
        )

        assert result is not None
        assert isinstance(result, TransactionReviewSuggestion)

        # Verify all field suggestions are present
        assert "category" in result.suggestions
        assert result.suggestions["category"].value == "Groceries"
        assert result.suggestions["category"].confidence == 0.95

        assert "description" in result.suggestions
        assert result.suggestions["description"].value == "Grocery shopping at Lidl"

        assert "destination_account" in result.suggestions
        assert result.suggestions["destination_account"].value == "Lidl Store"

        assert "transaction_type" in result.suggestions
        assert result.suggestions["transaction_type"].value == "withdrawal"

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_with_split_transactions(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review handles split transaction suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {
                            "value": "Shopping",
                            "confidence": 0.80,
                            "reason": "Mixed categories",
                        },
                        "description": {
                            "value": "Multi-category shopping",
                            "confidence": 0.85,
                            "reason": "Multiple item types",
                        },
                        "destination_account": {
                            "value": "Supermarket",
                            "confidence": 0.90,
                            "reason": "Store name",
                        },
                        "transaction_type": {
                            "value": "withdrawal",
                            "confidence": 0.99,
                            "reason": "Purchase",
                        },
                    },
                    "split_transactions": [
                        {
                            "amount": 25.50,
                            "description": "Food items (milk, bread, eggs)",
                            "category": "Groceries",
                        },
                        {
                            "amount": 15.00,
                            "description": "Cleaning supplies",
                            "category": "Shopping",
                        },
                        {"amount": 9.49, "description": "Electronics", "category": "Electronics"},
                    ],
                    "overall_confidence": 0.85,
                    "analysis_notes": "Receipt with multiple categories detected",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="49.99",
            date="2025-01-15",
            vendor="Supermarket",
            document_content="Supermarket\nMilk 2.99\nCleaning spray 15.00\nUSB Cable 9.49\nTotal 49.99",
            use_cache=False,
        )

        assert result is not None
        assert result.split_transactions is not None
        assert len(result.split_transactions) == 3

        # Verify split details
        assert result.split_transactions[0]["amount"] == 25.50
        assert result.split_transactions[0]["category"] == "Groceries"
        assert result.split_transactions[1]["amount"] == 15.00
        assert result.split_transactions[1]["category"] == "Shopping"
        assert result.split_transactions[2]["amount"] == 9.49

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_invalid_category_rejected(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review rejects invalid category suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {
                            "value": "InvalidCategory",
                            "confidence": 0.95,
                            "reason": "Test",
                        },
                        "description": {
                            "value": "Valid description",
                            "confidence": 0.85,
                            "reason": "Test",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(amount="10.00", date="2025-01-15", use_cache=False)

        assert result is not None
        # Category should be rejected as invalid
        assert "category" not in result.suggestions
        # But other valid fields should still be present
        assert "description" in result.suggestions
        assert result.suggestions["description"].value == "Valid description"

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_invalid_transaction_type_rejected(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review rejects invalid transaction_type suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"},
                        "transaction_type": {
                            "value": "payment",
                            "confidence": 0.80,
                            "reason": "Invalid type",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(amount="10.00", date="2025-01-15", use_cache=False)

        assert result is not None
        # Invalid transaction_type should be rejected
        assert "transaction_type" not in result.suggestions
        # Valid category should still be present
        assert "category" in result.suggestions

    def test_suggest_for_review_disabled_returns_none(
        self, store: StateStore, mock_config_disabled: MagicMock, categories: list[str]
    ) -> None:
        """Test suggest_for_review returns None when LLM is disabled."""
        service = SparkAIService(store, mock_config_disabled, categories)

        result = service.suggest_for_review(amount="10.00", date="2025-01-15")

        assert result is None

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_caches_result(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review uses cache for repeated calls."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"}
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)

        # First call - should hit LLM
        result1 = service.suggest_for_review(
            amount="10.00", date="2025-01-15", vendor="Test", use_cache=True
        )

        # Second call with same params - should use cache
        result2 = service.suggest_for_review(
            amount="10.00", date="2025-01-15", vendor="Test", use_cache=True
        )

        # LLM should only be called once
        assert mock_call.call_count == 1

        # Both results should be valid
        assert result1 is not None
        assert result2 is not None
        assert result2.from_cache is True

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_to_dict_structure(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review to_dict produces correct structure for UI."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {
                            "value": "Groceries",
                            "confidence": 0.95,
                            "reason": "Food items",
                        },
                        "description": {
                            "value": "Test purchase",
                            "confidence": 0.85,
                            "reason": "Based on content",
                        },
                    },
                    "split_transactions": [
                        {"amount": 10.00, "description": "Item 1", "category": "Groceries"}
                    ],
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test analysis",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(amount="10.00", date="2025-01-15", use_cache=False)

        assert result is not None

        # Convert to dict for UI consumption
        data = result.to_dict()

        # Verify structure matches what UI expects
        assert "suggestions" in data
        assert "category" in data["suggestions"]
        assert "value" in data["suggestions"]["category"]
        assert "confidence" in data["suggestions"]["category"]
        assert "reason" in data["suggestions"]["category"]

        assert "split_transactions" in data
        assert len(data["split_transactions"]) == 1
        assert data["split_transactions"][0]["amount"] == 10.00

        assert "overall_confidence" in data
        assert "analysis_notes" in data

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_invalid_currency_rejected(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review rejects invalid currency suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"},
                        "currency": {
                            "value": "XXX",  # Invalid currency
                            "confidence": 0.80,
                            "reason": "Invalid currency",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            currencies=["EUR", "USD", "GBP"],  # XXX not in list
        )

        assert result is not None
        # Invalid currency should be rejected
        assert "currency" not in result.suggestions
        # Valid category should still be present
        assert "category" in result.suggestions

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_valid_currency_accepted(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review accepts valid currency suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"},
                        "currency": {
                            "value": "EUR",
                            "confidence": 0.90,
                            "reason": "Standard currency",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            currencies=["EUR", "USD", "GBP"],
        )

        assert result is not None
        assert "currency" in result.suggestions
        assert result.suggestions["currency"].value == "EUR"

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_invalid_source_account_rejected(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review rejects invalid source_account suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"},
                        "source_account": {
                            "value": "NonExistentAccount",
                            "confidence": 0.80,
                            "reason": "Invalid",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            source_accounts_detailed=[
                {"name": "Checking", "iban": "DE89370400440532013000"},
                {"name": "Savings", "iban": "DE89370400440532013001"},
            ],
        )

        assert result is not None
        # Invalid source_account should be rejected
        assert "source_account" not in result.suggestions

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_valid_source_account_accepted(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review accepts valid source_account from detailed list."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "source_account": {
                            "value": "Checking",
                            "confidence": 0.90,
                            "reason": "Matched by IBAN",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            source_accounts_detailed=[
                {"name": "Checking", "iban": "DE89370400440532013000"},
                {"name": "Savings", "iban": "DE89370400440532013001"},
            ],
        )

        assert result is not None
        assert "source_account" in result.suggestions
        assert result.suggestions["source_account"].value == "Checking"

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_invalid_existing_transaction_rejected(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review rejects invalid existing_transaction suggestions."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "category": {"value": "Groceries", "confidence": 0.95, "reason": "Test"},
                        "existing_transaction": {
                            "value": "999999",  # Invalid ID
                            "confidence": 0.80,
                            "reason": "Invalid",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            existing_transactions=[
                {"id": "123", "amount": "10.00", "date": "2025-01-15"},
                {"id": "456", "amount": "20.00", "date": "2025-01-16"},
            ],
        )

        assert result is not None
        # Invalid existing_transaction should be rejected
        assert "existing_transaction" not in result.suggestions

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_valid_existing_transaction_accepted(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review accepts valid existing_transaction from candidates."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "existing_transaction": {
                            "value": "123",
                            "confidence": 0.90,
                            "reason": "High match score",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            existing_transactions=[
                {"id": "123", "amount": "10.00", "date": "2025-01-15"},
                {"id": "456", "amount": "20.00", "date": "2025-01-16"},
            ],
        )

        assert result is not None
        assert "existing_transaction" in result.suggestions
        assert result.suggestions["existing_transaction"].value == "123"

    @patch.object(SparkAIService, "_call_ollama")
    def test_suggest_for_review_create_new_always_valid(
        self,
        mock_call: MagicMock,
        store: StateStore,
        mock_config_enabled: MagicMock,
        categories: list[str],
    ) -> None:
        """Test suggest_for_review accepts 'create_new' for existing_transaction."""
        mock_call.return_value = {
            "content": json.dumps(
                {
                    "suggestions": {
                        "existing_transaction": {
                            "value": "create_new",
                            "confidence": 0.95,
                            "reason": "No matching transaction found",
                        },
                    },
                    "overall_confidence": 0.90,
                    "analysis_notes": "Test",
                }
            ),
            "model": "qwen2.5:14b",
        }

        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_for_review(
            amount="10.00",
            date="2025-01-15",
            use_cache=False,
            existing_transactions=[
                {"id": "123", "amount": "50.00", "date": "2025-01-10"},
            ],
        )

        assert result is not None
        assert "existing_transaction" in result.suggestions
        assert result.suggestions["existing_transaction"].value == "create_new"
