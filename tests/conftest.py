"""Test fixtures and utilities."""

from pathlib import Path

import pytest

# Sample OCR text for testing
SAMPLE_OCR_TEXT_DE = """
SPAR Österreich
Filiale 5631
Herrengasse 12
8010 Graz

Datum: 18.11.2024
Beleg-Nr.: R-2024-11832

Butter 250g                     2,49
Milch 1L                        1,29
Brot                            3,20
Käse 200g                       4,50

------------------------------------
Summe EUR                      11,48
Inkl. 10% MwSt.                 1,04
Inkl. 20% MwSt.                 0,00

Gesamtbetrag EUR               11,48

Bezahlt mit Karte
Vielen Dank für Ihren Einkauf!
"""

SAMPLE_OCR_TEXT_INVOICE = """
Max Mustermann GmbH
Musterstraße 123
12345 Musterstadt

Rechnung
Rechnungsnummer: INV-2024-001234
Rechnungsdatum: 2024-11-20

An:
Firma Beispiel AG
Beispielweg 45
67890 Beispielstadt

Position    Beschreibung              Menge    Einzelpreis    Gesamt
1           Beratungsleistung         8 Std.   150,00 EUR     1.200,00 EUR
2           Software-Lizenz           1        500,00 EUR       500,00 EUR

                                    Nettobetrag:    1.700,00 EUR
                                    MwSt. 19%:        323,00 EUR
                                    Gesamtbetrag:   2.023,00 EUR

Zahlbar innerhalb von 14 Tagen.
IBAN: DE89 3704 0044 0532 0130 00
"""


@pytest.fixture
def sample_ocr_receipt() -> str:
    """Sample German receipt OCR text."""
    return SAMPLE_OCR_TEXT_DE


@pytest.fixture
def sample_ocr_invoice() -> str:
    """Sample German invoice OCR text."""
    return SAMPLE_OCR_TEXT_INVOICE


@pytest.fixture
def sample_paperless_document() -> dict:
    """Sample Paperless document API response."""
    return {
        "id": 12345,
        "title": "SPAR Einkauf 18.11.2024",
        "content": SAMPLE_OCR_TEXT_DE,
        "created": "2024-11-18",
        "added": "2024-11-19T08:14:22Z",
        "modified": "2024-11-19T08:15:01Z",
        "correspondent": 5,
        "document_type": 2,
        "tags": [1, 3],
        "archive_serial_number": 7421,
        "original_file_name": "receipt_18112024.pdf",
        "custom_fields": [],
    }


@pytest.fixture
def sample_extraction_dict() -> dict:
    """Sample FinanceExtraction as dictionary."""
    return {
        "paperless_document_id": 12345,
        "source_hash": "abc123def456789012345678901234567890123456789012345678901234",
        "paperless_url": "http://localhost:8000/documents/12345/",
        "paperless_title": "SPAR Einkauf 18.11.2024",
        "raw_text": SAMPLE_OCR_TEXT_DE,
        "document_classification": {
            "document_type": "Receipt",
            "correspondent": "SPAR",
            "tags": ["finance/inbox", "receipt"],
            "storage_path": None,
        },
        "proposal": {
            "transaction_type": "withdrawal",
            "date": "2024-11-18",
            "amount": "11.48",
            "currency": "EUR",
            "description": "SPAR - 2024-11-18",
            "source_account": "Checking Account",
            "destination_account": "SPAR",
            "category": None,
            "tags": ["finance/inbox", "receipt"],
            "notes": "Extracted from Paperless document 12345",
            "external_id": "paperless:12345:abc123def4567890:11.48:2024-11-18",
            "invoice_number": "R-2024-11832",
            "due_date": None,
            "payment_reference": None,
            "total_net": None,
            "tax_amount": None,
            "tax_rate": None,
        },
        "line_items": [],
        "confidence": {
            "overall": 0.65,
            "amount": 0.75,
            "date": 0.80,
            "currency": 0.90,
            "description": 0.60,
            "vendor": 0.70,
            "invoice_number": 0.50,
            "line_items": 0.0,
            "review_state": "REVIEW",
        },
        "provenance": {
            "source_system": "paperless",
            "parser_version": "0.1.0",
            "parsed_at": "2024-11-19T10:00:00Z",
            "ruleset_id": None,
            "extraction_strategy": "ocr_heuristic",
        },
        "structured_payloads": [],
        "created_at": "2024-11-19T10:00:00Z",
    }


@pytest.fixture
def temp_db(tmp_path) -> Path:
    """Temporary database path for testing."""
    return tmp_path / "test_state.db"
