"""
Finance data extractors.

Provides:
- ExtractorRouter: Chooses extraction strategy
- E-Invoice extractor (ZUGFeRD, Factur-X, XRechnung, UBL, PEPPOL)
- OCR text heuristics extractor
- Base classes for custom extractors

Strategies are pluggable and testable.
"""

from .router import ExtractorRouter
from .ocr_extractor import OCRTextExtractor
from .einvoice_extractor import EInvoiceExtractor
from .base import BaseExtractor, ExtractionResult

__all__ = [
    "ExtractorRouter",
    "EInvoiceExtractor",
    "OCRTextExtractor",
    "BaseExtractor",
    "ExtractionResult",
]
