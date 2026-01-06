"""
Base extractor interface and common types.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional


@dataclass
class ExtractionResult:
    """Result from an extraction attempt."""

    # Extracted values
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    date: Optional[str] = None  # YYYY-MM-DD
    vendor: Optional[str] = None
    description: Optional[str] = None
    invoice_number: Optional[str] = None

    # Tax details
    total_net: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    tax_rate: Optional[Decimal] = None

    # Line items (if found)
    line_items: list[dict[str, Any]] = field(default_factory=list)

    # Confidence scores (0.0 - 1.0)
    amount_confidence: float = 0.0
    date_confidence: float = 0.0
    currency_confidence: float = 0.0
    vendor_confidence: float = 0.0
    description_confidence: float = 0.0
    invoice_number_confidence: float = 0.0
    line_items_confidence: float = 0.0

    # Metadata
    extraction_strategy: str = ""
    raw_matches: dict[str, Any] = field(default_factory=dict)  # Debug info


class BaseExtractor(ABC):
    """
    Base class for all extractors.

    Each extractor implements a specific strategy:
    - OCR text heuristics
    - Structured XML (Factur-X, UBL)
    - PDF form fields
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Extractor name for logging and provenance."""
        pass

    @property
    @abstractmethod
    def priority(self) -> int:
        """
        Priority for extractor selection.
        Higher = more trusted, tried first.
        """
        pass

    @abstractmethod
    def can_extract(self, content: str, file_bytes: Optional[bytes] = None) -> bool:
        """
        Check if this extractor can handle the given content.

        Args:
            content: OCR/text content
            file_bytes: Original file bytes (for detecting embedded data)

        Returns:
            True if this extractor should be attempted
        """
        pass

    @abstractmethod
    def extract(self, content: str, file_bytes: Optional[bytes] = None) -> ExtractionResult:
        """
        Extract finance data from content.

        Args:
            content: OCR/text content
            file_bytes: Original file bytes

        Returns:
            ExtractionResult with extracted values and confidence scores
        """
        pass
