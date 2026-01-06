"""
Test fixtures for e-invoice formats.

This module provides sample invoice files for testing:
- ZUGFeRD/Factur-X (CII format)
- XRechnung (UBL format, German CIUS)
- UBL 2.1 (Universal Business Language)
- PEPPOL BIS 3.0 (Pan-European standard)
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent


def load_fixture(name: str) -> str:
    """Load a fixture file as string."""
    filepath = FIXTURES_DIR / name
    return filepath.read_text(encoding="utf-8")


def get_zugferd_sample() -> str:
    """Get ZUGFeRD/Factur-X sample XML."""
    return load_fixture("zugferd_sample.xml")


def get_xrechnung_sample() -> str:
    """Get XRechnung sample XML."""
    return load_fixture("xrechnung_sample.xml")


def get_ubl_sample() -> str:
    """Get UBL 2.1 sample XML."""
    return load_fixture("ubl_sample.xml")


def get_peppol_sample() -> str:
    """Get PEPPOL BIS 3.0 sample XML."""
    return load_fixture("peppol_sample.xml")


# Expected extraction results for validation
EXPECTED_ZUGFERD = {
    "invoice_number": "ZUGFERD-2024-001234",
    "vendor": "Muster IT-Solutions GmbH",
    "amount": "2140.81",
    "currency": "EUR",
    "tax_amount": "341.81",
    "total_net": "1799.00",
}

EXPECTED_XRECHNUNG = {
    "invoice_number": "XRECH-2026-00789",
    "vendor": "Digital Services GmbH",
    "amount": "2975.00",
    "currency": "EUR",
    "tax_amount": "475.00",
    "total_net": "2500.00",
}

EXPECTED_UBL = {
    "invoice_number": "UBL-INV-2026-00456",
    "vendor": "ACME Corporation Ltd",
    "amount": "960.00",
    "currency": "EUR",
    "tax_amount": "160.00",
    "total_net": "800.00",
}

EXPECTED_PEPPOL = {
    "invoice_number": "PEPPOL-2026-00123",
    "vendor": "Nordic IT Solutions AB",
    "amount": "5950.00",
    "currency": "EUR",
    "tax_amount": "950.00",
    "total_net": "5000.00",
}
