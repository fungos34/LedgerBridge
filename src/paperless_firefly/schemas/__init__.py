"""
SSOT (Single Source of Truth) schemas for the pipeline.

These canonical schemas are the ONLY models used across all modules.
No duplicated "near-same" models allowed.
"""

from .finance_extraction import (
    FinanceExtraction,
    TransactionProposal,
    LineItem,
    ConfidenceScores,
    Provenance,
    DocumentClassification,
    ReviewState,
    TransactionType,
)
from .firefly_payload import (
    FireflyTransactionStore,
    FireflyTransactionSplit,
    build_firefly_payload,
)
from .dedupe import generate_external_id, ExternalIdComponents

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
