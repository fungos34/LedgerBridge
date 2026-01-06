"""
SSOT (Single Source of Truth) schemas for the pipeline.

These canonical schemas are the ONLY models used across all modules.
No duplicated "near-same" models allowed.
"""

from .dedupe import ExternalIdComponents, generate_external_id
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
    # Dedupe
    "generate_external_id",
    "ExternalIdComponents",
]
