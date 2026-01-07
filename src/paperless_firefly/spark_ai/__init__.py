"""Spark AI module for LLM-assisted transaction categorization.

This module implements the LLM integration as specified in Spark v1.0 Phase 6/7.
It provides optional AI-powered categorization with calibration and fallback support.
"""

from paperless_firefly.spark_ai.prompts import CategoryPrompt
from paperless_firefly.spark_ai.service import SparkAIService

__all__ = ["SparkAIService", "CategoryPrompt"]
