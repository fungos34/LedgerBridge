"""
Confidence scoring module.

Computes per-field and overall confidence scores.
Determines review state based on thresholds.
"""

from .scorer import ConfidenceScorer, ConfidenceThresholds

__all__ = [
    "ConfidenceScorer",
    "ConfidenceThresholds",
]
