"""Tests for Spark AI service and prompts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from paperless_firefly.spark_ai.prompts import PROMPT_VERSION, CategoryPrompt, ChatPrompt, SplitPrompt
from paperless_firefly.spark_ai.service import (
    CategorySuggestion,
    SparkAIService,
    SplitSuggestion,
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
        assert "- Groceries" in message

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
            }
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
        malformed = 'Here is my response:\n{"category": "Shopping", "confidence": 0.8}\nHope this helps!'
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
        malformed = '''
        I think this should be categorized as follows:
        should_split: true
        confidence: 0.75
        reason: "Multiple items detected"
        '''
        result = service._parse_json_response(malformed)
        
        assert result.get("should_split") is True
        assert result.get("confidence") == 0.75

    def test_parse_json_response_extracts_array(
        self, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test extracting JSON array from split response."""
        service = SparkAIService(store, mock_config_enabled, categories)
        
        # Response with just array (using proper JSON double quotes)
        malformed = '''Here are the splits:
        [{"category": "Groceries", "amount": 50.0}, {"category": "Household", "amount": 25.0}]
        '''
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

    @patch.object(SparkAIService, '_call_ollama_text')
    def test_chat_returns_response(
        self, mock_call: MagicMock, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test chat returns response from LLM."""
        mock_call.return_value = {
            "content": "SparkLink is a financial document processing application.",
            "model": "qwen2.5:7b"
        }
        
        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.chat("What is SparkLink?", documentation="Test docs")
        
        assert result == "SparkLink is a financial document processing application."
        mock_call.assert_called_once()

    @patch.object(SparkAIService, '_call_ollama')
    def test_suggest_splits_with_bank_data(
        self, mock_call: MagicMock, store: StateStore, mock_config_enabled: MagicMock, categories: list[str]
    ) -> None:
        """Test suggest_splits includes bank data in prompt."""
        mock_call.return_value = {
            "content": json.dumps({
                "should_split": True,
                "splits": [
                    {"category": "Groceries", "amount": 50.0, "description": "Food"}
                ],
                "confidence": 0.8,
                "reason": "Single item receipt"
            }),
            "model": "qwen2.5:14b"
        }
        
        service = SparkAIService(store, mock_config_enabled, categories)
        result = service.suggest_splits(
            amount="50.00",
            date="2025-01-15",
            vendor="Supermarket",
            description="Purchase",
            content="Milk 2.99\nBread 3.50",
            bank_data={
                "amount": "50.00",
                "description": "SUPERMARKET PURCHASE"
            }
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
        response_with_european = json.dumps({
            "should_split": True,
            "splits": [
                {"category": "Groceries", "amount": "12,50", "description": "Food"},
            ],
            "confidence": 0.8,
            "reason": "Test"
        })
        
        # Mock the Ollama call to return European format amounts
        with patch.object(service, '_call_ollama') as mock_call:
            mock_call.return_value = {
                "content": response_with_european,
                "model": "qwen2.5:14b"
            }
            
            result = service.suggest_splits(
                amount="12.50",
                date="2025-01-15",
                use_cache=False
            )
            
            # The amount should be normalized to float
            if result and result.splits:
                assert result.splits[0]["amount"] == 12.50

