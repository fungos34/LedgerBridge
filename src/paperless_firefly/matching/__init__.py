"""Spark matching engine for correlating Paperless documents with Firefly transactions."""

from paperless_firefly.matching.engine import MatchingEngine, MatchResult, MatchScore

__all__ = ["MatchingEngine", "MatchResult", "MatchScore"]
