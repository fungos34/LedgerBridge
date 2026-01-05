"""
CLI runner module.

Provides commands:
- scan: Find candidate documents
- extract: Generate FinanceExtraction
- review: Interactive review
- import: Push to Firefly III
- pipeline: End-to-end processing
"""

from .main import create_cli, main

__all__ = [
    "create_cli",
    "main",
]
