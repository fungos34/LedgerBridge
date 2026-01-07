"""
Tests for the interpretation trace module.

Tests cover:
- Privacy enforcement (SSOT for sensitive data detection)
- Trace building and recording
- Safe logging functionality
"""

from decimal import Decimal

import pytest

from paperless_firefly.schemas.interpretation_trace import (
    InterpretationTrace,
    LLMUsageRecord,
    SourceReference,
    TraceBuilder,
    TraceEvent,
    TraceMethod,
    TraceSource,
    TraceStage,
    contains_sensitive_data,
    safe_outcome_description,
    sanitize_string,
)


class TestContainsSensitiveData:
    """Tests for the contains_sensitive_data function (privacy enforcement)."""

    def test_iban_detected(self) -> None:
        """Test IBAN patterns are detected as sensitive."""
        assert contains_sensitive_data("DE89370400440532013000") is True
        assert contains_sensitive_data("Payment from DE89370400440532013000") is True

    def test_card_number_detected(self) -> None:
        """Test credit card numbers are detected as sensitive."""
        assert contains_sensitive_data("1234567890123456") is True
        assert contains_sensitive_data("Card: 4111111111111111") is True

    def test_phone_number_detected(self) -> None:
        """Test phone numbers are detected as sensitive."""
        assert contains_sensitive_data("+49 123 456 7890") is True

    def test_email_detected(self) -> None:
        """Test email addresses are detected as sensitive."""
        assert contains_sensitive_data("user@example.com") is True
        assert contains_sensitive_data("Contact: john.doe@company.org") is True

    def test_safe_strings_not_flagged(self) -> None:
        """Test normal strings are not flagged as sensitive."""
        assert contains_sensitive_data("Groceries") is False
        assert contains_sensitive_data("REWE supermarket") is False

    def test_empty_string_safe(self) -> None:
        """Test empty string is not flagged."""
        assert contains_sensitive_data("") is False


class TestSanitizeString:
    """Tests for the sanitize_string function."""

    def test_iban_redacted(self) -> None:
        """Test IBAN is redacted."""
        result = sanitize_string("Transfer from DE89370400440532013000")
        assert "DE89370400440532013000" not in result
        assert "[REDACTED]" in result

    def test_card_number_redacted(self) -> None:
        """Test card numbers are redacted."""
        result = sanitize_string("Card 4111111111111111")
        assert "4111111111111111" not in result
        assert "[REDACTED]" in result

    def test_email_redacted(self) -> None:
        """Test email is redacted."""
        result = sanitize_string("Email: test@example.com")
        assert "test@example.com" not in result
        assert "[REDACTED]" in result

    def test_safe_string_unchanged(self) -> None:
        """Test safe strings pass through unchanged."""
        original = "Groceries at REWE"
        result = sanitize_string(original)
        assert result == original

    def test_truncation_applied(self) -> None:
        """Test long strings are truncated."""
        long_string = "A" * 1000
        result = sanitize_string(long_string, max_length=100)
        assert len(result) <= 100
        assert result.endswith("...")


class TestTraceEnums:
    """Tests for trace enumeration types."""

    def test_trace_method_values(self) -> None:
        """Test TraceMethod has expected values."""
        assert TraceMethod.RULE.value == "RULE"
        assert TraceMethod.LLM.value == "LLM"
        assert TraceMethod.DEFAULT.value == "DEFAULT"
        assert TraceMethod.USER_OVERRIDE.value == "USER_OVERRIDE"

    def test_trace_source_values(self) -> None:
        """Test TraceSource has expected values."""
        assert TraceSource.PAPERLESS.value == "paperless"
        assert TraceSource.FIREFLY.value == "firefly"
        assert TraceSource.LLM.value == "llm"

    def test_trace_stage_values(self) -> None:
        """Test TraceStage has expected values."""
        assert TraceStage.EXTRACTION.value == "extraction"
        assert TraceStage.MATCHING.value == "matching"
        assert TraceStage.DECISION.value == "decision"


class TestSourceReference:
    """Tests for SourceReference dataclass."""

    def test_to_dict(self) -> None:
        """Test to_dict produces expected structure."""
        ref = SourceReference(
            system=TraceSource.PAPERLESS,
            field_name="total_gross",
            identifier="123",
        )
        data = ref.to_dict()
        assert data["system"] == "paperless"
        assert data["field"] == "total_gross"
        assert data["id"] == "123"


class TestTraceEvent:
    """Tests for TraceEvent dataclass."""

    def test_to_dict_with_all_fields(self) -> None:
        """Test to_dict includes all fields."""
        event = TraceEvent(
            timestamp="2024-01-15T10:30:00",
            stage=TraceStage.EXTRACTION,
            target_field="amount",
            sources=[SourceReference(TraceSource.PAPERLESS, "total_gross", "123")],
            method=TraceMethod.RULE,
            outcome="amount=100.50",
            confidence=0.95,
            notes="Pattern matched total amount",
        )
        data = event.to_dict()
        assert data["timestamp"] == "2024-01-15T10:30:00"
        assert data["stage"] == "extraction"
        assert data["target_field"] == "amount"
        assert data["method"] == "RULE"
        assert data["confidence"] == 0.95


class TestLLMUsageRecord:
    """Tests for LLMUsageRecord dataclass."""

    def test_to_dict_when_used(self) -> None:
        """Test to_dict when LLM was used."""
        record = LLMUsageRecord(
            used=True,
            model_name="qwen2.5:7b",
            endpoint_class="local",
        )
        data = record.to_dict()
        assert data["used"] is True
        assert data["model"] == "qwen2.5:7b"
        assert data["endpoint_class"] == "local"

    def test_to_dict_when_not_used(self) -> None:
        """Test to_dict when LLM was not used."""
        record = LLMUsageRecord(
            used=False,
            endpoint_class="disabled",
            reason_not_used="LLM globally disabled",
        )
        data = record.to_dict()
        assert data["used"] is False
        assert data["reason_not_used"] == "LLM globally disabled"


class TestTraceBuilder:
    """Tests for TraceBuilder class."""

    def test_init_creates_trace(self) -> None:
        """Test constructor initializes a trace."""
        builder = TraceBuilder(
            document_id=123,
            external_id="PAPERLESS:123:abc",
        )
        assert builder.trace.document_id == 123
        assert builder.trace.external_id == "PAPERLESS:123:abc"

    def test_record_extraction_adds_event(self) -> None:
        """Test record_extraction adds an event."""
        builder = TraceBuilder(123, "ext_id")
        builder.record_extraction(
            field="amount",
            value=Decimal("50.00"),
            source_field="extraction.total_gross",
            method="RULE",
            confidence=0.9,
        )
        assert len(builder.trace.events) == 1
        event = builder.trace.events[0]
        assert event.target_field == "amount"
        assert event.method == TraceMethod.RULE
        assert event.confidence == 0.9

    def test_set_llm_usage_updates_trace(self) -> None:
        """Test set_llm_usage updates LLM record."""
        builder = TraceBuilder(123, "ext")
        builder.set_llm_usage(
            used=True,
            model_name="qwen2.5:7b",
            endpoint_class="local",
        )
        assert builder.trace.llm_usage.used is True
        assert builder.trace.llm_usage.model_name == "qwen2.5:7b"

    def test_build_returns_trace(self) -> None:
        """Test build() returns completed trace."""
        builder = TraceBuilder(123, "ext_id")
        trace = builder.build()
        assert isinstance(trace, InterpretationTrace)
        assert trace.document_id == 123


class TestInterpretationTrace:
    """Tests for InterpretationTrace dataclass."""

    def test_to_dict_full(self) -> None:
        """Test to_dict produces complete structure."""
        builder = TraceBuilder(123, "PAPERLESS:123:abc")
        builder.record_extraction("amount", "50.00", "total", "RULE", 0.95)
        trace = builder.build()

        data = trace.to_dict()
        assert data["document_id"] == 123
        assert data["external_id"] == "PAPERLESS:123:abc"
        assert "events" in data
        assert len(data["events"]) == 1

    def test_add_event_updates_summaries(self) -> None:
        """Test add_event updates sources_used and methods_used."""
        trace = InterpretationTrace(document_id=123)
        event = TraceEvent(
            timestamp="2024-01-15T10:30:00",
            stage=TraceStage.EXTRACTION,
            target_field="amount",
            sources=[SourceReference(TraceSource.PAPERLESS, "total", "123")],
            method=TraceMethod.RULE,
            outcome="amount=100.00",
            confidence=0.9,
        )
        trace.add_event(event)

        assert "RULE" in trace.methods_used
        assert "paperless.total" in trace.sources_used
        assert trace.field_confidence["amount"] == 0.9


class TestSafeOutcomeDescription:
    """Tests for safe_outcome_description function."""

    def test_numeric_field_shows_value(self) -> None:
        """Test numeric fields show value directly."""
        result = safe_outcome_description("amount", 100.50, TraceMethod.RULE)
        assert "amount=100.5" in result

    def test_date_field_shows_value(self) -> None:
        """Test date fields show value directly."""
        result = safe_outcome_description("date", "2024-01-15", TraceMethod.RULE)
        assert "date=2024-01-15" in result

    def test_text_field_sanitized(self) -> None:
        """Test text fields are sanitized."""
        result = safe_outcome_description("vendor", "Test test@example.com", TraceMethod.RULE)
        assert "test@example.com" not in result
        assert "[REDACTED]" in result
