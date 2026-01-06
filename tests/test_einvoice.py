"""
Tests for e-invoice (ZUGFeRD, XRechnung, UBL) extraction.

These tests validate structured XML parsing for electronic invoices
which provide the highest confidence extraction.
"""

import pytest
from decimal import Decimal
from pathlib import Path

from paperless_firefly.extractors.einvoice_extractor import (
    EInvoiceExtractor,
    _safe_decimal,
    _safe_date,
)
from paperless_firefly.extractors.base import ExtractionResult

# Import fixture loaders
from fixtures import (
    get_zugferd_sample,
    get_xrechnung_sample,
    get_ubl_sample,
    get_peppol_sample,
    EXPECTED_ZUGFERD,
    EXPECTED_XRECHNUNG,
    EXPECTED_UBL,
    EXPECTED_PEPPOL,
)


# Sample CII (Cross Industry Invoice) XML - used by ZUGFeRD/Factur-X
SAMPLE_CII_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice 
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
    
    <rsm:ExchangedDocumentContext>
        <ram:GuidelineSpecifiedDocumentContextParameter>
            <ram:ID>urn:factur-x.eu:1p0:extended</ram:ID>
        </ram:GuidelineSpecifiedDocumentContextParameter>
    </rsm:ExchangedDocumentContext>
    
    <rsm:ExchangedDocument>
        <ram:ID>INV-2024-001234</ram:ID>
        <ram:Name>Rechnung</ram:Name>
        <ram:TypeCode>380</ram:TypeCode>
        <ram:IssueDateTime>
            <udt:DateTimeString format="102">20241118</udt:DateTimeString>
        </ram:IssueDateTime>
    </rsm:ExchangedDocument>
    
    <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeAgreement>
            <ram:SellerTradeParty>
                <ram:Name>Lieferant GmbH</ram:Name>
                <ram:PostalTradeAddress>
                    <ram:PostcodeCode>12345</ram:PostcodeCode>
                    <ram:LineOne>Musterstraße 1</ram:LineOne>
                    <ram:CityName>Musterstadt</ram:CityName>
                    <ram:CountryID>DE</ram:CountryID>
                </ram:PostalTradeAddress>
            </ram:SellerTradeParty>
            <ram:BuyerTradeParty>
                <ram:Name>Kunde AG</ram:Name>
            </ram:BuyerTradeParty>
        </ram:ApplicableHeaderTradeAgreement>
        
        <ram:IncludedSupplyChainTradeLineItem>
            <ram:AssociatedDocumentLineDocument>
                <ram:LineID>1</ram:LineID>
            </ram:AssociatedDocumentLineDocument>
            <ram:SpecifiedTradeProduct>
                <ram:Name>Beratungsleistung</ram:Name>
            </ram:SpecifiedTradeProduct>
            <ram:SpecifiedLineTradeDelivery>
                <ram:BilledQuantity unitCode="HUR">8</ram:BilledQuantity>
            </ram:SpecifiedLineTradeDelivery>
            <ram:SpecifiedLineTradeSettlement>
                <ram:ApplicableTradeTax>
                    <ram:TypeCode>VAT</ram:TypeCode>
                    <ram:CategoryCode>S</ram:CategoryCode>
                    <ram:RateApplicablePercent>19</ram:RateApplicablePercent>
                </ram:ApplicableTradeTax>
                <ram:SpecifiedTradeSettlementLineMonetarySummation>
                    <ram:LineTotalAmount>1200.00</ram:LineTotalAmount>
                </ram:SpecifiedTradeSettlementLineMonetarySummation>
            </ram:SpecifiedLineTradeSettlement>
        </ram:IncludedSupplyChainTradeLineItem>
        
        <ram:ApplicableHeaderTradeSettlement>
            <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
            <ram:ApplicableTradeTax>
                <ram:CalculatedAmount>228.00</ram:CalculatedAmount>
                <ram:TypeCode>VAT</ram:TypeCode>
                <ram:BasisAmount>1200.00</ram:BasisAmount>
                <ram:CategoryCode>S</ram:CategoryCode>
                <ram:RateApplicablePercent>19</ram:RateApplicablePercent>
            </ram:ApplicableTradeTax>
            <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
                <ram:LineTotalAmount>1200.00</ram:LineTotalAmount>
                <ram:TaxBasisTotalAmount>1200.00</ram:TaxBasisTotalAmount>
                <ram:TaxTotalAmount currencyID="EUR">228.00</ram:TaxTotalAmount>
                <ram:GrandTotalAmount>1428.00</ram:GrandTotalAmount>
                <ram:DuePayableAmount>1428.00</ram:DuePayableAmount>
            </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        </ram:ApplicableHeaderTradeSettlement>
    </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""

# Sample UBL 2.1 Invoice XML - used by XRechnung (UBL variant), PEPPOL
SAMPLE_UBL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
         xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
         xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
    
    <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
    <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xoev-de:kosit:standard:xrechnung_2.0</cbc:CustomizationID>
    <cbc:ID>XRECH-2024-00567</cbc:ID>
    <cbc:IssueDate>2024-11-20</cbc:IssueDate>
    <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
    <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
    
    <cac:AccountingSupplierParty>
        <cac:Party>
            <cac:PartyName>
                <cbc:Name>Software Solutions AG</cbc:Name>
            </cac:PartyName>
            <cac:PostalAddress>
                <cbc:StreetName>Hauptstraße 42</cbc:StreetName>
                <cbc:CityName>Berlin</cbc:CityName>
                <cbc:PostalZone>10115</cbc:PostalZone>
                <cac:Country>
                    <cbc:IdentificationCode>DE</cbc:IdentificationCode>
                </cac:Country>
            </cac:PostalAddress>
        </cac:Party>
    </cac:AccountingSupplierParty>
    
    <cac:AccountingCustomerParty>
        <cac:Party>
            <cac:PartyName>
                <cbc:Name>Kunde GmbH</cbc:Name>
            </cac:PartyName>
        </cac:Party>
    </cac:AccountingCustomerParty>
    
    <cac:TaxTotal>
        <cbc:TaxAmount currencyID="EUR">190.00</cbc:TaxAmount>
        <cac:TaxSubtotal>
            <cbc:TaxableAmount currencyID="EUR">1000.00</cbc:TaxableAmount>
            <cbc:TaxAmount currencyID="EUR">190.00</cbc:TaxAmount>
            <cac:TaxCategory>
                <cbc:ID>S</cbc:ID>
                <cbc:Percent>19</cbc:Percent>
            </cac:TaxCategory>
        </cac:TaxSubtotal>
    </cac:TaxTotal>
    
    <cac:LegalMonetaryTotal>
        <cbc:LineExtensionAmount currencyID="EUR">1000.00</cbc:LineExtensionAmount>
        <cbc:TaxExclusiveAmount currencyID="EUR">1000.00</cbc:TaxExclusiveAmount>
        <cbc:TaxInclusiveAmount currencyID="EUR">1190.00</cbc:TaxInclusiveAmount>
        <cbc:PayableAmount currencyID="EUR">1190.00</cbc:PayableAmount>
    </cac:LegalMonetaryTotal>
    
    <cac:InvoiceLine>
        <cbc:ID>1</cbc:ID>
        <cbc:InvoicedQuantity unitCode="EA">1</cbc:InvoicedQuantity>
        <cbc:LineExtensionAmount currencyID="EUR">1000.00</cbc:LineExtensionAmount>
        <cac:Item>
            <cbc:Name>Software-Lizenz Premium</cbc:Name>
        </cac:Item>
        <cac:Price>
            <cbc:PriceAmount currencyID="EUR">1000.00</cbc:PriceAmount>
        </cac:Price>
    </cac:InvoiceLine>
</Invoice>
"""

# Minimal CII XML for edge case testing
MINIMAL_CII_XML = """<?xml version="1.0"?>
<rsm:CrossIndustryInvoice 
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
    <rsm:ExchangedDocument>
        <ram:ID>MIN-001</ram:ID>
    </rsm:ExchangedDocument>
    <rsm:SupplyChainTradeTransaction>
        <ram:ApplicableHeaderTradeSettlement>
            <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
            <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
                <ram:GrandTotalAmount>99.99</ram:GrandTotalAmount>
            </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        </ram:ApplicableHeaderTradeSettlement>
    </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""


class TestHelperFunctions:
    """Tests for utility functions."""
    
    def test_safe_decimal_valid(self):
        """Should parse valid decimal strings."""
        assert _safe_decimal("123.45") == Decimal("123.45")
        assert _safe_decimal("1000") == Decimal("1000")
        assert _safe_decimal("-50.00") == Decimal("-50.00")
    
    def test_safe_decimal_with_comma(self):
        """Should handle German decimal format."""
        assert _safe_decimal("123,45") == Decimal("123.45")
        assert _safe_decimal("1.234,56") == Decimal("1234.56")
    
    def test_safe_decimal_with_currency(self):
        """Should strip currency symbols."""
        assert _safe_decimal("€ 100.00") == Decimal("100.00")
        assert _safe_decimal("EUR 50") == Decimal("50")
    
    def test_safe_decimal_none(self):
        """Should return None for empty/invalid input."""
        assert _safe_decimal(None) is None
        assert _safe_decimal("") is None
        assert _safe_decimal("invalid") is None
    
    def test_safe_date_iso(self):
        """Should parse ISO format dates."""
        assert _safe_date("2024-11-18") == "2024-11-18"
    
    def test_safe_date_compact(self):
        """Should parse compact format (common in XML)."""
        assert _safe_date("20241118") == "2024-11-18"
    
    def test_safe_date_german(self):
        """Should parse German format."""
        assert _safe_date("18.11.2024") == "2024-11-18"
    
    def test_safe_date_none(self):
        """Should return None for empty/invalid input."""
        assert _safe_date(None) is None
        assert _safe_date("") is None
        assert _safe_date("invalid") is None


class TestEInvoiceExtractor:
    """Tests for the e-invoice XML extractor."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    # === Detection Tests ===
    
    def test_name_and_priority(self, extractor):
        """Should have correct name and high priority."""
        assert extractor.name == "einvoice_xml"
        assert extractor.priority == 100  # Highest priority
    
    def test_can_extract_cii_content(self, extractor):
        """Should detect CII XML in content."""
        assert extractor.can_extract(SAMPLE_CII_XML, None) is True
    
    def test_can_extract_ubl_content(self, extractor):
        """Should detect UBL XML in content."""
        assert extractor.can_extract(SAMPLE_UBL_XML, None) is True
    
    def test_cannot_extract_plain_text(self, extractor):
        """Should not detect plain text as e-invoice."""
        assert extractor.can_extract("This is just plain text.", None) is False
    
    def test_cannot_extract_ocr_text(self, extractor):
        """Should not detect OCR text as e-invoice."""
        ocr_text = """
        SPAR
        Datum: 18.11.2024
        Beleg-Nr.: 12345
        Summe EUR 11,48
        """
        assert extractor.can_extract(ocr_text, None) is False
    
    def test_can_extract_with_zugferd_keyword(self, extractor):
        """Should detect content mentioning ZUGFeRD."""
        content = "This document contains a ZUGFeRD invoice attachment"
        assert extractor.can_extract(content, None) is True
    
    # === CII/ZUGFeRD Parsing Tests ===
    
    def test_extract_cii_invoice_number(self, extractor):
        """Should extract invoice number from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.invoice_number == "INV-2024-001234"
        assert result.invoice_number_confidence >= 0.9
    
    def test_extract_cii_date(self, extractor):
        """Should extract issue date from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.date == "2024-11-18"
        assert result.date_confidence >= 0.9
    
    def test_extract_cii_vendor(self, extractor):
        """Should extract seller/vendor from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.vendor == "Lieferant GmbH"
        assert result.vendor_confidence >= 0.9
    
    def test_extract_cii_currency(self, extractor):
        """Should extract currency from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.currency == "EUR"
        assert result.currency_confidence >= 0.95
    
    def test_extract_cii_amount(self, extractor):
        """Should extract grand total from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.amount == Decimal("1428.00")
        assert result.amount_confidence >= 0.95
    
    def test_extract_cii_tax_details(self, extractor):
        """Should extract tax information from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.tax_amount == Decimal("228.00")
        assert result.total_net == Decimal("1200.00")
        assert result.tax_rate == Decimal("19")
    
    def test_extract_cii_line_items(self, extractor):
        """Should extract line items from CII."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert len(result.line_items) >= 1
        
        first_item = result.line_items[0]
        assert first_item["description"] == "Beratungsleistung"
        assert first_item.get("quantity") == "8"
    
    def test_extract_cii_strategy(self, extractor):
        """Should report CII extraction strategy."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert "cii" in result.extraction_strategy
    
    # === UBL/XRechnung Parsing Tests ===
    
    def test_extract_ubl_invoice_number(self, extractor):
        """Should extract invoice number from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.invoice_number == "XRECH-2024-00567"
        assert result.invoice_number_confidence >= 0.9
    
    def test_extract_ubl_date(self, extractor):
        """Should extract issue date from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.date == "2024-11-20"
        assert result.date_confidence >= 0.9
    
    def test_extract_ubl_vendor(self, extractor):
        """Should extract supplier from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.vendor == "Software Solutions AG"
        assert result.vendor_confidence >= 0.9
    
    def test_extract_ubl_currency(self, extractor):
        """Should extract currency from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.currency == "EUR"
        assert result.currency_confidence >= 0.95
    
    def test_extract_ubl_amount(self, extractor):
        """Should extract payable amount from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.amount == Decimal("1190.00")
        assert result.amount_confidence >= 0.95
    
    def test_extract_ubl_tax_details(self, extractor):
        """Should extract tax from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert result.tax_amount == Decimal("190.00")
        assert result.total_net == Decimal("1000.00")
        assert result.tax_rate == Decimal("19")
    
    def test_extract_ubl_line_items(self, extractor):
        """Should extract line items from UBL."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert len(result.line_items) >= 1
        
        first_item = result.line_items[0]
        assert "Software-Lizenz Premium" in first_item.get("description", "")
    
    def test_extract_ubl_strategy(self, extractor):
        """Should report UBL extraction strategy."""
        result = extractor.extract(SAMPLE_UBL_XML, None)
        assert "ubl" in result.extraction_strategy
    
    # === Edge Cases ===
    
    def test_extract_minimal_cii(self, extractor):
        """Should handle minimal CII with only required fields."""
        result = extractor.extract(MINIMAL_CII_XML, None)
        assert result.invoice_number == "MIN-001"
        assert result.amount == Decimal("99.99")
        assert result.currency == "EUR"
    
    def test_extract_invalid_xml(self, extractor):
        """Should handle invalid XML gracefully."""
        result = extractor.extract("<invalid>xml", None)
        # Should not crash, return empty result
        assert result.amount is None
    
    def test_extract_empty_content(self, extractor):
        """Should handle empty content."""
        result = extractor.extract("", None)
        assert result.amount is None
    
    def test_extract_non_invoice_xml(self, extractor):
        """Should handle XML that isn't an invoice."""
        other_xml = '<?xml version="1.0"?><root><item>Not an invoice</item></root>'
        result = extractor.extract(other_xml, None)
        # Should not crash, may return empty result
        assert result is not None


class TestCIIVariants:
    """Tests for different CII/ZUGFeRD profile variants."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    def test_zugferd_basic_profile(self, extractor):
        """Should handle ZUGFeRD BASIC profile."""
        # BASIC profile has minimal fields
        basic_xml = """<?xml version="1.0"?>
        <rsm:CrossIndustryInvoice 
            xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
            xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
            <rsm:ExchangedDocument>
                <ram:ID>BASIC-001</ram:ID>
            </rsm:ExchangedDocument>
            <rsm:SupplyChainTradeTransaction>
                <ram:ApplicableHeaderTradeSettlement>
                    <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
                    <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
                        <ram:GrandTotalAmount>50.00</ram:GrandTotalAmount>
                    </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
                </ram:ApplicableHeaderTradeSettlement>
            </rsm:SupplyChainTradeTransaction>
        </rsm:CrossIndustryInvoice>"""
        
        result = extractor.extract(basic_xml, None)
        assert result.invoice_number == "BASIC-001"
        assert result.amount == Decimal("50.00")


class TestUBLVariants:
    """Tests for different UBL variants."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    def test_peppol_bis(self, extractor):
        """Should handle PEPPOL BIS format."""
        # PEPPOL uses UBL with specific customization ID
        peppol_xml = SAMPLE_UBL_XML.replace(
            "urn:xoev-de:kosit:standard:xrechnung_2.0",
            "urn:fdc:peppol.eu:2017:poacc:billing:3.0"
        )
        result = extractor.extract(peppol_xml, None)
        assert result.amount == Decimal("1190.00")


class TestConfidenceScoring:
    """Tests for confidence score assignment."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    def test_structured_xml_high_confidence(self, extractor):
        """Structured XML should have high confidence scores."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        
        # All main fields should have high confidence
        assert result.amount_confidence >= 0.95
        assert result.date_confidence >= 0.9
        assert result.vendor_confidence >= 0.9
        assert result.currency_confidence >= 0.95
        assert result.invoice_number_confidence >= 0.9
    
    def test_line_items_confidence(self, extractor):
        """Line items from XML should have high confidence."""
        result = extractor.extract(SAMPLE_CII_XML, None)
        assert result.line_items_confidence >= 0.8


class TestExtractorRouterIntegration:
    """Tests for e-invoice extractor with router."""
    
    def test_einvoice_higher_priority_than_ocr(self):
        """E-invoice extractor should be tried before OCR."""
        from paperless_firefly.extractors.router import ExtractorRouter
        
        router = ExtractorRouter()
        
        # Get extractor priorities
        priorities = {e.name: e.priority for e in router.extractors}
        
        assert priorities.get("einvoice_xml", 0) > priorities.get("ocr_heuristic", 0)
    
    def test_router_uses_einvoice_for_xml(self):
        """Router should use e-invoice extractor for XML content."""
        from paperless_firefly.extractors.router import ExtractorRouter
        from paperless_firefly.paperless_client import PaperlessDocument
        
        router = ExtractorRouter()
        
        # Create mock document with XML content
        doc = PaperlessDocument(
            id=123,
            title="Test Invoice",
            content=SAMPLE_CII_XML,
            created="2024-11-18",
            added="2024-11-18T10:00:00Z",
            modified="2024-11-18T10:00:00Z",
            document_type="Invoice",
            correspondent=None,
            tags=[],
        )
        
        # Extract
        result = router.extract(
            document=doc,
            file_bytes=b"",
            source_hash="abc123def456789012",  # Must be at least 16 characters
            paperless_base_url="http://test",
            default_source_account="Test Account",
        )
        
        # Should use e-invoice extractor
        assert "einvoice" in result.provenance.extraction_strategy or \
               "cii" in result.provenance.extraction_strategy or \
               "ubl" in result.provenance.extraction_strategy


class TestRealFixtureFiles:
    """Tests using the real fixture files from tests/fixtures/."""
    
    @pytest.fixture
    def extractor(self):
        return EInvoiceExtractor()
    
    def test_zugferd_fixture(self, extractor):
        """Test extraction from ZUGFeRD fixture file."""
        xml_content = get_zugferd_sample()
        result = extractor.extract(xml_content, None)
        
        assert result.invoice_number == EXPECTED_ZUGFERD["invoice_number"]
        assert result.vendor == EXPECTED_ZUGFERD["vendor"]
        assert result.amount == Decimal(EXPECTED_ZUGFERD["amount"])
        assert result.currency == EXPECTED_ZUGFERD["currency"]
        assert result.tax_amount == Decimal(EXPECTED_ZUGFERD["tax_amount"])
        assert result.total_net == Decimal(EXPECTED_ZUGFERD["total_net"])
        assert result.amount_confidence >= 0.95
    
    def test_xrechnung_fixture(self, extractor):
        """Test extraction from XRechnung fixture file."""
        xml_content = get_xrechnung_sample()
        result = extractor.extract(xml_content, None)
        
        assert result.invoice_number == EXPECTED_XRECHNUNG["invoice_number"]
        assert result.vendor == EXPECTED_XRECHNUNG["vendor"]
        assert result.amount == Decimal(EXPECTED_XRECHNUNG["amount"])
        assert result.currency == EXPECTED_XRECHNUNG["currency"]
        assert result.tax_amount == Decimal(EXPECTED_XRECHNUNG["tax_amount"])
        assert result.total_net == Decimal(EXPECTED_XRECHNUNG["total_net"])
    
    def test_ubl_fixture(self, extractor):
        """Test extraction from UBL fixture file."""
        xml_content = get_ubl_sample()
        result = extractor.extract(xml_content, None)
        
        assert result.invoice_number == EXPECTED_UBL["invoice_number"]
        assert result.vendor == EXPECTED_UBL["vendor"]
        assert result.amount == Decimal(EXPECTED_UBL["amount"])
        assert result.currency == EXPECTED_UBL["currency"]
        assert result.tax_amount == Decimal(EXPECTED_UBL["tax_amount"])
        assert result.total_net == Decimal(EXPECTED_UBL["total_net"])
    
    def test_peppol_fixture(self, extractor):
        """Test extraction from PEPPOL fixture file."""
        xml_content = get_peppol_sample()
        result = extractor.extract(xml_content, None)
        
        assert result.invoice_number == EXPECTED_PEPPOL["invoice_number"]
        assert result.vendor == EXPECTED_PEPPOL["vendor"]
        assert result.amount == Decimal(EXPECTED_PEPPOL["amount"])
        assert result.currency == EXPECTED_PEPPOL["currency"]
        assert result.tax_amount == Decimal(EXPECTED_PEPPOL["tax_amount"])
        assert result.total_net == Decimal(EXPECTED_PEPPOL["total_net"])
