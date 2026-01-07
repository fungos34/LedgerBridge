"""
OCR text heuristics extractor.

Extracts finance data from OCR text using pattern matching.
This is the lowest confidence but most widely applicable extractor.

Supported formats:
- Dates: d.m.Y, d/m/Y, Y-m-d, d. Month Y (German)
- Amounts: 1.234,56 (German), 1,234.56 (English)
- Currency: EUR, €, USD, $
"""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .base import BaseExtractor, ExtractionResult

# Date patterns (ordered by specificity)
DATE_PATTERNS = [
    # ISO format: 2024-11-18
    (r"\b(\d{4})-(\d{2})-(\d{2})\b", "%Y-%m-%d", "iso"),
    # German format: 18.11.2024
    (r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", "%d.%m.%Y", "german_dot"),
    # German format: 18.11.24 (2-digit year)
    (r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b", "%d.%m.%y", "german_dot_short"),
    # Slash format: 18/11/2024
    (r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", "%d/%m/%Y", "slash"),
    # German month names
    (
        r"\b(\d{1,2})\.\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember)\s*(\d{4})\b",
        None,
        "german_month",
    ),
]

# German month name mapping
GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# Amount patterns
AMOUNT_PATTERNS = [
    # German format with thousands separator: 1.234,56
    (r"(?:EUR|€)\s*(\d{1,3}(?:\.\d{3})*,\d{2})\b", "german", "eur_prefix"),
    (r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\s*(?:EUR|€)", "german", "eur_suffix"),
    # German format without thousands: 123,45
    (r"(?:EUR|€)\s*(\d+,\d{2})\b", "german", "eur_prefix_simple"),
    (r"\b(\d+,\d{2})\s*(?:EUR|€)", "german", "eur_suffix_simple"),
    # English format: 1,234.56
    (r"(?:USD|\$)\s*(\d{1,3}(?:,\d{3})*\.\d{2})\b", "english", "usd_prefix"),
    (r"\b(\d{1,3}(?:,\d{3})*\.\d{2})\s*(?:USD|\$)", "english", "usd_suffix"),
    # Generic formats (lower confidence)
    (r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b", "german", "generic_german"),
    (r"\b(\d+,\d{2})\b", "german", "generic_german_simple"),
    (r"\b(\d{1,3}(?:,\d{3})*\.\d{2})\b", "english", "generic_english"),
]

# Currency patterns
CURRENCY_PATTERNS = [
    (r"\bEUR\b", "EUR"),
    (r"€", "EUR"),
    (r"\bUSD\b", "USD"),
    (r"\$", "USD"),
    (r"\bGBP\b", "GBP"),
    (r"£", "GBP"),
    (r"\bCHF\b", "CHF"),
]

# Invoice number patterns
INVOICE_PATTERNS = [
    # RE-2024-12345, R-2024-12345
    (
        r"\b(?:RE|R|INV|INVOICE|Rechnung|Rechnungsnr\.?|Rechnungsnummer|Beleg-?Nr\.?)[:\s#-]*([A-Z0-9]+-?\d{4,}(?:-\d+)?)\b",
        "invoice_number",
        0.9,
    ),
    # Generic alphanumeric
    (r"\b(?:Belegnummer|Beleg-Nr\.?|Nr\.?)[:\s#]*([A-Z0-9/-]{5,20})\b", "receipt_number", 0.7),
]

# Total amount keywords (to identify the right amount)
TOTAL_KEYWORDS = [
    (r"(?:Gesamt|Total|Summe|Endbetrag|Gesamtbetrag|Gesamtsumme|Brutto|TOTAL|SUMME)", 1.0),
    (r"(?:zu\s+zahlen|Zahlbetrag|Rechnungsbetrag)", 0.9),
    (r"(?:inkl\.\s*MwSt|inkl\.\s*USt|incl\.\s*VAT)", 0.8),
]


def parse_german_amount(amount_str: str) -> Decimal:
    """Parse German format amount (1.234,56) to Decimal."""
    # Remove thousands separators (dots) and convert comma to dot
    cleaned = amount_str.replace(".", "").replace(",", ".")
    return Decimal(cleaned)


def parse_english_amount(amount_str: str) -> Decimal:
    """Parse English format amount (1,234.56) to Decimal."""
    # Remove thousands separators (commas)
    cleaned = amount_str.replace(",", "")
    return Decimal(cleaned)


def parse_date_match(match: re.Match, date_format: str | None, pattern_type: str) -> str | None:
    """Parse a date regex match into YYYY-MM-DD format."""
    try:
        if pattern_type == "german_month":
            day = int(match.group(1))
            month = GERMAN_MONTHS.get(match.group(2).lower())
            year = int(match.group(3))
            if month:
                return f"{year:04d}-{month:02d}-{day:02d}"
        elif date_format:
            date_str = match.group(0)
            parsed = datetime.strptime(date_str, date_format)
            return parsed.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        pass
    return None


class OCRTextExtractor(BaseExtractor):
    """
    Extract finance data from OCR text using pattern matching.

    This is the fallback extractor when structured data is not available.
    Confidence scores are lower due to OCR quality uncertainty.
    """

    @property
    def name(self) -> str:
        return "ocr_heuristic"

    @property
    def priority(self) -> int:
        return 10  # Lowest priority (last resort)

    def can_extract(self, content: str, file_bytes: bytes | None = None) -> bool:
        """OCR extractor can always attempt extraction if there's content."""
        return bool(content and content.strip())

    def extract(self, content: str, file_bytes: bytes | None = None) -> ExtractionResult:
        """Extract finance data using pattern matching."""
        result = ExtractionResult(extraction_strategy=self.name)
        result.raw_matches = {}

        # Normalize content
        content = content.strip()

        # Extract date
        date_result = self._extract_date(content)
        if date_result:
            result.date = date_result["date"]
            result.date_confidence = date_result["confidence"]
            result.raw_matches["date"] = date_result

        # Extract currency
        currency_result = self._extract_currency(content)
        if currency_result:
            result.currency = currency_result["currency"]
            result.currency_confidence = currency_result["confidence"]
            result.raw_matches["currency"] = currency_result

        # Extract amount (using currency context)
        amount_result = self._extract_amount(content, result.currency)
        if amount_result:
            result.amount = amount_result["amount"]
            result.amount_confidence = amount_result["confidence"]
            result.raw_matches["amount"] = amount_result

            # If we found amount with currency, ensure currency is set
            if not result.currency and amount_result.get("currency"):
                result.currency = amount_result["currency"]
                result.currency_confidence = 0.7

        # Default to EUR if no currency found (common in German documents)
        if not result.currency:
            result.currency = "EUR"
            result.currency_confidence = 0.5

        # Extract invoice number
        invoice_result = self._extract_invoice_number(content)
        if invoice_result:
            result.invoice_number = invoice_result["number"]
            result.invoice_number_confidence = invoice_result["confidence"]
            result.raw_matches["invoice_number"] = invoice_result

        # Extract vendor (from first lines - usually header)
        vendor_result = self._extract_vendor(content)
        if vendor_result:
            result.vendor = vendor_result["vendor"]
            result.vendor_confidence = vendor_result["confidence"]
            result.raw_matches["vendor"] = vendor_result

        # Generate description
        result.description = self._generate_description(result)
        result.description_confidence = min(
            result.vendor_confidence if result.vendor else 0.3,
            result.date_confidence if result.date else 0.3,
        )

        return result

    def _extract_date(self, content: str) -> dict[str, Any] | None:
        """Extract most likely document date."""
        candidates: list[dict[str, Any]] = []

        for pattern, date_format, pattern_type in DATE_PATTERNS:
            for match in re.finditer(pattern, content, re.IGNORECASE):
                parsed_date = parse_date_match(match, date_format, pattern_type)
                if parsed_date:
                    # Calculate confidence based on pattern type and context
                    confidence = 0.6  # Base confidence for OCR

                    # Boost if near date keywords
                    context_start = max(0, match.start() - 50)
                    context = content[context_start : match.start()].lower()
                    if any(
                        kw in context for kw in ["datum", "date", "rechnungsdatum", "belegdatum"]
                    ):
                        confidence = min(confidence + 0.2, 0.85)

                    # ISO format is most reliable
                    if pattern_type == "iso":
                        confidence = min(confidence + 0.1, 0.9)

                    candidates.append(
                        {
                            "date": parsed_date,
                            "confidence": confidence,
                            "match": match.group(0),
                            "position": match.start(),
                            "pattern_type": pattern_type,
                        }
                    )

        if not candidates:
            return None

        # Sort by confidence, then by position (prefer earlier dates)
        candidates.sort(key=lambda x: (-x["confidence"], x["position"]))
        return candidates[0]

    def _extract_currency(self, content: str) -> dict[str, Any] | None:
        """Extract currency from content."""
        for pattern, currency in CURRENCY_PATTERNS:
            if re.search(pattern, content):
                return {
                    "currency": currency,
                    "confidence": 0.8,  # Currency symbols are fairly reliable
                }
        return None

    def _extract_amount(self, content: str, currency: str | None = None) -> dict[str, Any] | None:
        """
        Extract the most likely total amount.

        Strategy:
        1. Find all amounts in the text
        2. Score them by proximity to total keywords
        3. Return the best candidate
        """
        candidates: list[dict[str, Any]] = []

        # Determine expected format from currency
        expected_format = "german" if currency in ("EUR", "CHF") else None

        for pattern, num_format, pattern_type in AMOUNT_PATTERNS:
            for match in re.finditer(pattern, content):
                try:
                    amount_str = match.group(1) if match.lastindex else match.group(0)

                    # Parse amount
                    if num_format == "german":
                        amount = parse_german_amount(amount_str)
                    else:
                        amount = parse_english_amount(amount_str)

                    # Skip unreasonably small or large amounts
                    if amount <= 0 or amount > Decimal("1000000"):
                        continue

                    # Calculate confidence
                    confidence = 0.4  # Base confidence for amounts

                    # Check proximity to total keywords
                    context_start = max(0, match.start() - 100)
                    context_end = min(len(content), match.end() + 50)
                    context = content[context_start:context_end].lower()

                    for keyword_pattern, boost in TOTAL_KEYWORDS:
                        if re.search(keyword_pattern, context, re.IGNORECASE):
                            confidence = min(confidence + boost * 0.3, 0.85)
                            break

                    # Boost if format matches expected format
                    if expected_format and num_format == expected_format:
                        confidence = min(confidence + 0.1, 0.9)

                    # Boost if amount has currency symbol attached
                    if "prefix" in pattern_type or "suffix" in pattern_type:
                        confidence = min(confidence + 0.1, 0.9)

                    # Determine currency from pattern
                    detected_currency = None
                    if "eur" in pattern_type.lower():
                        detected_currency = "EUR"
                    elif "usd" in pattern_type.lower():
                        detected_currency = "USD"

                    candidates.append(
                        {
                            "amount": amount,
                            "confidence": confidence,
                            "match": match.group(0),
                            "position": match.start(),
                            "format": num_format,
                            "currency": detected_currency,
                        }
                    )
                except (InvalidOperation, ValueError):
                    continue

        if not candidates:
            return None

        # Sort by confidence (descending), then by amount (prefer larger totals)
        candidates.sort(key=lambda x: (-x["confidence"], -x["amount"]))
        return candidates[0]

    def _extract_invoice_number(self, content: str) -> dict[str, Any] | None:
        """Extract invoice/receipt number."""
        for pattern, number_type, confidence in INVOICE_PATTERNS:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return {
                    "number": match.group(1),
                    "confidence": confidence * 0.8,  # Reduce for OCR
                    "type": number_type,
                    "match": match.group(0),
                }
        return None

    def _extract_vendor(self, content: str) -> dict[str, Any] | None:
        """
        Extract vendor name from document.

        Strategy:
        - First non-empty line is often the company name
        - Look for common patterns like "Firma", company suffixes
        """
        lines = [line.strip() for line in content.split("\n") if line.strip()]

        if not lines:
            return None

        # Common company suffixes
        company_suffixes = ["GmbH", "AG", "KG", "e.K.", "OHG", "Ltd", "Inc", "GesmbH"]

        # Check first few lines for company name
        for i, line in enumerate(lines[:5]):
            # Skip very short lines
            if len(line) < 3:
                continue

            # Skip lines that look like addresses or dates
            if re.match(r"^[\d\s,./\-]+$", line):
                continue

            # Check for company suffix
            has_suffix = any(suffix in line for suffix in company_suffixes)

            # First meaningful line with high confidence
            if i == 0 and len(line) > 5:
                return {
                    "vendor": line[:100],  # Limit length
                    "confidence": 0.6 if has_suffix else 0.5,
                    "source": "first_line",
                }

            # Line with company suffix
            if has_suffix:
                return {
                    "vendor": line[:100],
                    "confidence": 0.7,
                    "source": "company_suffix",
                }

        # Fallback to first line
        if lines:
            return {
                "vendor": lines[0][:100],
                "confidence": 0.3,
                "source": "fallback",
            }

        return None

    def _generate_description(self, result: ExtractionResult) -> str:
        """Generate a human-readable description."""
        parts = []

        if result.vendor:
            parts.append(result.vendor)

        if result.date:
            parts.append(result.date)

        if not parts:
            return "Unknown transaction"

        return " - ".join(parts)
