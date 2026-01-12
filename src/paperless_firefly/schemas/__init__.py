"""
SSOT (Single Source of Truth) schemas for the pipeline.

These canonical schemas are the ONLY models used across all modules.
No duplicated "near-same" models allowed.
"""

from .dedupe import (
    EXTERNAL_ID_SEPARATOR,
    HASH_PREFIX_LENGTH,
    LEGACY_EXTERNAL_ID_PREFIX,
    PAPERLESS_LINK_MARKER,
    ExternalIdComponents,
    compute_file_hash,
    compute_transaction_hash,
    extract_document_id_from_external_id,
    generate_external_id,
    generate_external_id_v2,
    is_spark_external_id,
    parse_external_id,
)
from .finance_extraction import (
    ConfidenceScores,
    DocumentClassification,
    FinanceExtraction,
    LineItem,
    Provenance,
    ReviewState,
    TransactionProposal,
    TransactionType,
)
from .firefly_payload import (
    FireflyTransactionSplit,
    FireflyTransactionStore,
    build_firefly_payload,
    build_firefly_payload_with_splits,
    validate_firefly_payload,
)
from .interpretation_trace import (
    InterpretationTrace,
    LLMUsageRecord,
    SafeTraceLogger,
    SourceReference,
    TraceBuilder,
    TraceEvent,
    TraceMethod,
    TraceSource,
    TraceStage,
    contains_sensitive_data,
    sanitize_string,
)
from .split_builder import (
    AmountValidationError,
    RoundingStrategy,
    SplitItem,
    SplitTransactionPayload,
    SplitValidationError,
    build_split_transaction_payload,
    build_splits_from_line_items,
    normalize_amount_for_firefly,
    validate_amount,
)

__all__ = [
    # Finance Extraction (canonical input schema)
    "FinanceExtraction",
    "TransactionProposal",
    "LineItem",
    "ConfidenceScores",
    "Provenance",
    "DocumentClassification",
    "ReviewState",
    "TransactionType",
    # Firefly Payload (canonical output schema)
    "FireflyTransactionStore",
    "FireflyTransactionSplit",
    "build_firefly_payload",
    "build_firefly_payload_with_splits",
    "validate_firefly_payload",
    # Amount Validation (SSOT)
    "validate_amount",
    "normalize_amount_for_firefly",
    "AmountValidationError",
    # Split Builder
    "SplitTransactionPayload",
    "SplitItem",
    "SplitValidationError",
    "RoundingStrategy",
    "build_split_transaction_payload",
    "build_splits_from_line_items",
    # Interpretation Trace
    "InterpretationTrace",
    "TraceBuilder",
    "TraceEvent",
    "TraceMethod",
    "TraceSource",
    "TraceStage",
    "SourceReference",
    "LLMUsageRecord",
    "SafeTraceLogger",
    "contains_sensitive_data",
    "sanitize_string",
    # Dedupe
    "generate_external_id",
    "ExternalIdComponents",
]
