"""
Interpretation Trace module (SSOT).

Provides privacy-safe, method-level audit trail for all interpretation decisions.
This module implements the trace system specified in the Spark Fix & Completion Plan.

Privacy Invariants (non-negotiable):
- NO raw OCR text
- NO full vendor addresses
- NO IBANs/account numbers verbatim
- NO raw LLM prompts/responses
- NO invoice line content verbatim
- ONLY: meta-level descriptions, identifiers, field names, method labels, confidence scores

All trace events are structured and validated before storage.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ============================================================================
# Constants and SSOT definitions
# ============================================================================

# Patterns that indicate PII/sensitive data (case-insensitive)
SENSITIVE_PATTERNS = [
    # IBAN patterns (various country formats)
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",
    # Credit card numbers (16 digits, possibly separated)
    r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b",
    # Long numeric sequences (potential account numbers)
    r"\b\d{10,}\b",
    # Email addresses
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    # Phone numbers (various formats)
    r"\b[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[(]?[0-9]{1,4}[)]?[-\s\.]?[0-9]{1,4}[-\s\.]?[0-9]{1,9}\b",
]

# Compiled patterns for efficiency
_SENSITIVE_REGEXES = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]

# Maximum allowed length for any string value in trace events
MAX_STRING_LENGTH = 200

# Maximum raw text snippet length (for safety, heavily truncated)
MAX_SNIPPET_LENGTH = 50


class TraceStage(str, Enum):
    """Processing stages in the interpretation pipeline."""

    EXTRACTION = "extraction"
    NORMALIZATION = "normalization"
    MATCHING = "matching"
    SUGGESTION = "suggestion"  # LLM or rules-based
    DECISION = "decision"
    WRITE = "write"


class TraceMethod(str, Enum):
    """Methods used to derive values."""

    RULE = "RULE"
    HEURISTIC = "HEURISTIC"
    TEMPLATE_RECOGNITION = "TEMPLATE_RECOGNITION"
    FUZZY_MATCH = "FUZZY_MATCH"
    EXACT_MATCH = "EXACT_MATCH"
    LLM = "LLM"
    USER_OVERRIDE = "USER_OVERRIDE"
    DEFAULT = "DEFAULT"
    CACHE = "CACHE"


class TraceSource(str, Enum):
    """Systems that provided data."""

    PAPERLESS = "paperless"
    FIREFLY = "firefly"
    LLM = "llm"
    USER = "user"
    CACHE = "cache"
    RULES_ENGINE = "rules"


@dataclass
class SourceReference:
    """Reference to a data source field."""

    system: TraceSource
    field_name: str
    identifier: str | None = None  # doc_id, tx_id, etc.

    def to_dict(self) -> dict:
        d = {"system": self.system.value, "field": self.field_name}
        if self.identifier:
            d["id"] = self.identifier
        return d


@dataclass
class TraceEvent:
    """A single event in the interpretation trace.

    All string values are validated and sanitized before storage.
    """

    timestamp: str  # ISO format
    stage: TraceStage
    target_field: str  # e.g., "amount", "category", "vendor"
    sources: list[SourceReference]
    method: TraceMethod
    outcome: str  # Meta-level description of the selected value (not raw)
    confidence: float | None = None
    notes: str | None = None

    def to_dict(self) -> dict:
        d = {
            "timestamp": self.timestamp,
            "stage": self.stage.value,
            "target_field": self.target_field,
            "sources": [s.to_dict() for s in self.sources],
            "method": self.method.value,
            "outcome": self.outcome,
        }
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 4)
        if self.notes:
            d["notes"] = self.notes
        return d


@dataclass
class LLMUsageRecord:
    """Record of LLM usage for a trace."""

    used: bool
    model_name: str | None = None
    endpoint_class: str = "disabled"  # "local", "remote", "disabled"
    reason_not_used: str | None = None  # If not used, why

    def to_dict(self) -> dict:
        d = {"used": self.used, "endpoint_class": self.endpoint_class}
        if self.model_name:
            d["model"] = self.model_name
        if self.reason_not_used:
            d["reason_not_used"] = self.reason_not_used
        return d


@dataclass
class InterpretationTrace:
    """Complete interpretation trace for a document.

    This is the structured trace model that gets stored per run.
    """

    document_id: int
    external_id: str | None = None
    firefly_id: int | None = None
    run_id: int | None = None  # DB ID when persisted

    # Summary fields
    matching_result: str = "unknown"  # "auto-linked", "proposed", "manual", "no_match"
    known_format_recognized: str | None = None  # Template name if recognized
    llm_usage: LLMUsageRecord = field(default_factory=lambda: LLMUsageRecord(used=False))

    # Events timeline
    events: list[TraceEvent] = field(default_factory=list)

    # Performance
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0

    # Sources used summary
    sources_used: set[str] = field(default_factory=set)
    methods_used: set[str] = field(default_factory=set)

    # Per-field confidence (optional)
    field_confidence: dict[str, float] = field(default_factory=dict)

    def add_event(self, event: TraceEvent) -> None:
        """Add an event to the trace."""
        self.events.append(event)
        # Update summaries
        self.methods_used.add(event.method.value)
        for source in event.sources:
            self.sources_used.add(f"{source.system.value}.{source.field_name}")
        if event.confidence is not None:
            self.field_confidence[event.target_field] = event.confidence

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON storage."""
        return {
            "document_id": self.document_id,
            "external_id": self.external_id,
            "firefly_id": self.firefly_id,
            "run_id": self.run_id,
            "summary": {
                "matching_result": self.matching_result,
                "known_format": self.known_format_recognized,
                "llm": self.llm_usage.to_dict(),
            },
            "events": [e.to_dict() for e in self.events],
            "timing": {
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "duration_ms": self.duration_ms,
            },
            "sources_used": list(self.sources_used),
            "methods_used": list(self.methods_used),
            "field_confidence": self.field_confidence,
        }


# ============================================================================
# Privacy enforcement utilities
# ============================================================================


def contains_sensitive_data(text: str) -> bool:
    """Check if text contains patterns that suggest sensitive data.

    Args:
        text: Text to check

    Returns:
        True if any sensitive pattern is detected
    """
    for regex in _SENSITIVE_REGEXES:
        if regex.search(text):
            return True
    return False


def sanitize_string(value: str, max_length: int = MAX_STRING_LENGTH) -> str:
    """Sanitize a string for safe trace storage.

    - Truncates to max length
    - Replaces detected sensitive patterns with [REDACTED]
    - Removes excessive whitespace

    Args:
        value: String to sanitize
        max_length: Maximum allowed length

    Returns:
        Sanitized string
    """
    if not value:
        return ""

    # Clean whitespace
    result = " ".join(value.split())

    # Redact sensitive patterns
    for regex in _SENSITIVE_REGEXES:
        result = regex.sub("[REDACTED]", result)

    # Truncate
    if len(result) > max_length:
        result = result[: max_length - 3] + "..."

    return result


def safe_outcome_description(
    field_name: str,
    value: Any,
    method: TraceMethod,
) -> str:
    """Generate a privacy-safe outcome description.

    Args:
        field_name: Name of the field being set
        value: The value (will be converted to safe string)
        method: Method used to derive value

    Returns:
        Safe description string
    """
    # Numeric fields can show value directly
    if field_name in ("amount", "confidence", "match_score"):
        return f"{field_name}={value}"

    # Date fields are safe
    if field_name in ("date", "due_date", "invoice_date"):
        return f"{field_name}={value}"

    # For text fields, truncate and sanitize
    if isinstance(value, str):
        safe_val = sanitize_string(value, MAX_SNIPPET_LENGTH)
        return f"{field_name}='{safe_val}'"

    # For other types, just indicate the type and presence
    return f"{field_name}=<{type(value).__name__}>"


# ============================================================================
# Trace Builder (SSOT for creating traces)
# ============================================================================


class TraceBuilder:
    """Builder for creating interpretation traces.

    Use this to construct traces in a safe, validated way.

    Example:
        builder = TraceBuilder(document_id=123)
        builder.record_extraction("amount", Decimal("50.00"), "paperless.total_gross", "RULE", 0.95)
        builder.record_matching_attempt(132, best_score=0.87)
        builder.set_llm_usage(used=False, reason="global disabled")
        trace = builder.build()
    """

    def __init__(self, document_id: int, external_id: str | None = None) -> None:
        self.trace = InterpretationTrace(
            document_id=document_id,
            external_id=external_id,
            started_at=datetime.utcnow().isoformat(),
        )
        self._start_time = time.time()

    def record_extraction(
        self,
        field: str,
        value: Any,
        source_field: str,
        method: TraceMethod | str,
        confidence: float | None = None,
        source_system: TraceSource = TraceSource.PAPERLESS,
        notes: str | None = None,
    ) -> None:
        """Record an extraction event.

        Args:
            field: Target field name (e.g., "amount", "vendor")
            value: Extracted value (will be sanitized)
            source_field: Source field name (e.g., "extraction.total_gross")
            method: Method used
            confidence: Optional confidence score
            source_system: Source system
            notes: Optional safe notes
        """
        if isinstance(method, str):
            method = TraceMethod(method)

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.EXTRACTION,
            target_field=field,
            sources=[
                SourceReference(
                    system=source_system,
                    field_name=source_field,
                    identifier=str(self.trace.document_id),
                )
            ],
            method=method,
            outcome=safe_outcome_description(field, value, method),
            confidence=confidence,
            notes=sanitize_string(notes) if notes else None,
        )
        self.trace.add_event(event)

    def record_normalization(
        self,
        field: str,
        original_value: Any,
        normalized_value: Any,
        method: TraceMethod | str,
        notes: str | None = None,
    ) -> None:
        """Record a normalization event."""
        if isinstance(method, str):
            method = TraceMethod(method)

        outcome = f"Normalized {field}: {safe_outcome_description(field, original_value, method)} → {safe_outcome_description(field, normalized_value, method)}"

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.NORMALIZATION,
            target_field=field,
            sources=[
                SourceReference(
                    system=TraceSource.RULES_ENGINE,
                    field_name=f"normalize_{field}",
                )
            ],
            method=method,
            outcome=outcome,
            notes=sanitize_string(notes) if notes else None,
        )
        self.trace.add_event(event)

    def record_matching_attempt(
        self,
        candidates_checked: int,
        best_score: float | None = None,
        best_match_id: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Record a matching attempt.

        Args:
            candidates_checked: Number of Firefly transactions checked
            best_score: Best match score found
            best_match_id: Firefly ID of best match
            notes: Additional context
        """
        outcome = f"Matched against {candidates_checked} cached Firefly transactions"
        if best_score is not None:
            outcome += f"; best_score={best_score:.2f}"
        if best_match_id is not None:
            outcome += f"; best_match=tx#{best_match_id}"

        sources = [
            SourceReference(
                system=TraceSource.FIREFLY,
                field_name="cached_transactions",
            )
        ]

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.MATCHING,
            target_field="match_proposal",
            sources=sources,
            method=TraceMethod.FUZZY_MATCH,
            outcome=outcome,
            confidence=best_score,
            notes=sanitize_string(notes) if notes else None,
        )
        self.trace.add_event(event)

    def record_template_recognition(
        self,
        template_name: str,
        confidence: float,
    ) -> None:
        """Record recognition of a known document format."""
        self.trace.known_format_recognized = template_name

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.EXTRACTION,
            target_field="document_format",
            sources=[
                SourceReference(
                    system=TraceSource.RULES_ENGINE,
                    field_name="template_matcher",
                )
            ],
            method=TraceMethod.TEMPLATE_RECOGNITION,
            outcome=f"Known format recognized: '{template_name}'",
            confidence=confidence,
        )
        self.trace.add_event(event)

    def record_llm_suggestion(
        self,
        field: str,
        suggested_value: str,
        confidence: float,
        model_name: str,
        from_cache: bool = False,
    ) -> None:
        """Record an LLM suggestion."""
        method = TraceMethod.CACHE if from_cache else TraceMethod.LLM

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.SUGGESTION,
            target_field=field,
            sources=[
                SourceReference(
                    system=TraceSource.LLM if not from_cache else TraceSource.CACHE,
                    field_name=f"suggest_{field}",
                )
            ],
            method=method,
            outcome=f"LLM suggested {field}='{sanitize_string(suggested_value, 50)}'",
            confidence=confidence,
            notes=f"model={model_name}" if not from_cache else "from_cache=true",
        )
        self.trace.add_event(event)

    def record_decision(
        self,
        action: str,
        method: TraceMethod | str,
        reason: str,
        firefly_id: int | None = None,
    ) -> None:
        """Record a final decision."""
        if isinstance(method, str):
            method = TraceMethod(method)

        if firefly_id:
            self.trace.firefly_id = firefly_id

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.DECISION,
            target_field="action",
            sources=[],
            method=method,
            outcome=f"{action}: {sanitize_string(reason, 100)}",
        )
        self.trace.add_event(event)

    def record_write_action(
        self,
        action: str,
        firefly_id: int,
        success: bool,
        notes: str | None = None,
    ) -> None:
        """Record a Firefly write action."""
        self.trace.firefly_id = firefly_id

        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            stage=TraceStage.WRITE,
            target_field="firefly_transaction",
            sources=[
                SourceReference(
                    system=TraceSource.FIREFLY,
                    field_name="transaction",
                    identifier=str(firefly_id),
                )
            ],
            method=TraceMethod.RULE,
            outcome=f"{action} to Firefly tx#{firefly_id}: {'success' if success else 'failed'}",
            notes=sanitize_string(notes) if notes else None,
        )
        self.trace.add_event(event)

    def set_matching_result(self, result: str) -> None:
        """Set the overall matching result."""
        self.trace.matching_result = result

    def set_llm_usage(
        self,
        used: bool,
        model_name: str | None = None,
        endpoint_class: str = "disabled",
        reason: str | None = None,
    ) -> None:
        """Set LLM usage information."""
        self.trace.llm_usage = LLMUsageRecord(
            used=used,
            model_name=model_name,
            endpoint_class=endpoint_class,
            reason_not_used=reason if not used else None,
        )

    def build(self) -> InterpretationTrace:
        """Finalize and return the trace."""
        self.trace.completed_at = datetime.utcnow().isoformat()
        self.trace.duration_ms = int((time.time() - self._start_time) * 1000)
        return self.trace


# ============================================================================
# Safe Trace Logger
# ============================================================================


class SafeTraceLogger:
    """Logger that enforces privacy constraints on trace events.

    Rejects or sanitizes events containing disallowed patterns.
    """

    def __init__(self, strict: bool = True) -> None:
        """
        Args:
            strict: If True, reject events with sensitive data.
                   If False, sanitize instead.
        """
        self.strict = strict
        self.violations: list[str] = []

    def validate_event(self, event: TraceEvent) -> bool:
        """Validate an event for privacy compliance.

        Args:
            event: Event to validate

        Returns:
            True if event is safe, False otherwise
        """
        # Check outcome
        if contains_sensitive_data(event.outcome):
            self.violations.append(f"Sensitive data in outcome: {event.outcome[:50]}...")
            return False

        # Check notes
        if event.notes and contains_sensitive_data(event.notes):
            self.violations.append(f"Sensitive data in notes: {event.notes[:50]}...")
            return False

        # Check length constraints
        if len(event.outcome) > MAX_STRING_LENGTH:
            self.violations.append(f"Outcome exceeds max length: {len(event.outcome)}")
            return False

        return True

    def validate_trace(self, trace: InterpretationTrace) -> tuple[bool, list[str]]:
        """Validate a complete trace.

        Args:
            trace: Trace to validate

        Returns:
            (is_valid, list_of_violations)
        """
        self.violations = []

        for event in trace.events:
            if not self.validate_event(event):
                if self.strict:
                    return False, self.violations

        return len(self.violations) == 0, self.violations
