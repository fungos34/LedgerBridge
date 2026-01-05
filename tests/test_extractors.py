"""Tests for OCR text extractor."""

from decimal import Decimal

import pytest

from paperless_firefly.extractors.ocr_extractor import (
    OCRTextExtractor,
    parse_german_amount,
    parse_english_amount,
)


class TestAmountParsing:
    """Tests for amount parsing functions."""
    
    def test_german_amount_simple(self):
        """Parse simple German amount (comma decimal)."""
        assert parse_german_amount("11,48") == Decimal("11.48")
        assert parse_german_amount("0,99") == Decimal("0.99")
        assert parse_german_amount("100,00") == Decimal("100.00")
    
    def test_german_amount_thousands(self):
        """Parse German amount with thousands separator."""
        assert parse_german_amount("1.234,56") == Decimal("1234.56")
        assert parse_german_amount("12.345,00") == Decimal("12345.00")
        assert parse_german_amount("1.234.567,89") == Decimal("1234567.89")
    
    def test_english_amount_simple(self):
        """Parse simple English amount (dot decimal)."""
        assert parse_english_amount("11.48") == Decimal("11.48")
        assert parse_english_amount("0.99") == Decimal("0.99")
    
    def test_english_amount_thousands(self):
        """Parse English amount with thousands separator."""
        assert parse_english_amount("1,234.56") == Decimal("1234.56")
        assert parse_english_amount("12,345.00") == Decimal("12345.00")


class TestOCRExtractor:
    """Tests for OCR text extractor."""
    
    @pytest.fixture
    def extractor(self):
        return OCRTextExtractor()
    
    def test_can_extract(self, extractor):
        """Extractor accepts any non-empty content."""
        assert extractor.can_extract("some text")
        assert extractor.can_extract("   text   ")
        assert not extractor.can_extract("")
        assert not extractor.can_extract("   ")
    
    def test_extract_german_receipt(self, extractor, sample_ocr_receipt):
        """Extract from German receipt."""
        result = extractor.extract(sample_ocr_receipt)
        
        # Amount should be found
        assert result.amount is not None
        assert result.amount == Decimal("11.48")
        assert result.amount_confidence > 0
        
        # Date should be found
        assert result.date == "2024-11-18"
        assert result.date_confidence > 0
        
        # Currency should be EUR
        assert result.currency == "EUR"
        
        # Invoice number should be found
        assert result.invoice_number == "R-2024-11832"
    
    def test_extract_german_invoice(self, extractor, sample_ocr_invoice):
        """Extract from German invoice."""
        result = extractor.extract(sample_ocr_invoice)
        
        # Amount - should find the total
        assert result.amount is not None
        # The extractor should find 2023.00 or similar large amount
        assert result.amount > Decimal("1000")
        
        # Date
        assert result.date == "2024-11-20"
        
        # Invoice number
        assert result.invoice_number == "INV-2024-001234"
    
    def test_extract_vendor(self, extractor):
        """Extract vendor from first lines."""
        text = """SPAR Österreich
        Filiale 5631
        Datum: 18.11.2024
        Gesamtbetrag EUR 11,48"""
        
        result = extractor.extract(text)
        
        assert result.vendor is not None
        assert "SPAR" in result.vendor
    
    def test_date_formats(self, extractor):
        """Test various date format extractions."""
        # German format
        result1 = extractor.extract("Datum: 18.11.2024 Betrag: EUR 10,00")
        assert result1.date == "2024-11-18"
        
        # ISO format
        result2 = extractor.extract("Date: 2024-11-18 Amount: EUR 10,00")
        assert result2.date == "2024-11-18"
        
        # Short year
        result3 = extractor.extract("Datum: 18.11.24 EUR 10,00")
        assert result3.date == "2024-11-18"
    
    def test_amount_with_total_keyword(self, extractor):
        """Amount near 'total' keyword gets higher confidence."""
        text = """
        Position 1: 5,00 EUR
        Position 2: 3,00 EUR
        Zwischensumme: 8,00 EUR
        Gesamtbetrag EUR 10,00
        """
        
        result = extractor.extract(text)
        
        # Should pick the total amount
        assert result.amount == Decimal("10.00")
        assert result.amount_confidence > 0.5
    
    def test_currency_detection(self, extractor):
        """Test currency detection."""
        eur_text = "Total: EUR 50,00"
        usd_text = "Total: $50.00"
        
        eur_result = extractor.extract(eur_text)
        usd_result = extractor.extract(usd_text)
        
        assert eur_result.currency == "EUR"
        assert usd_result.currency == "USD"
    
    def test_empty_content_handled(self, extractor):
        """Empty content returns default result."""
        result = extractor.extract("")
        
        assert result.amount is None
        assert result.date is None
        assert result.extraction_strategy == "ocr_heuristic"
    
    def test_confidence_scores(self, extractor, sample_ocr_receipt):
        """Confidence scores are in valid range."""
        result = extractor.extract(sample_ocr_receipt)
        
        assert 0 <= result.amount_confidence <= 1
        assert 0 <= result.date_confidence <= 1
        assert 0 <= result.currency_confidence <= 1
        assert 0 <= result.vendor_confidence <= 1
