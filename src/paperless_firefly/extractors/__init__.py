"""
Finance data extractors.

Provides:
- ExtractorRouter: Chooses extraction strategy
- OCR text heuristics extractor
- Structured attachment extractor (Factur-X, UBL)
- PDF text extractor

Strategies are pluggable and testable.
"""

from .router import ExtractorRouter
from .ocr_extractor import OCRTextExtractor
from .base import BaseExtractor, ExtractionResult

__all__ = [
    "ExtractorRouter",
    "OCRTextExtractor",
    "BaseExtractor",
    "ExtractionResult",
]
