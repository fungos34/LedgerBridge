"""
E-Invoice Extractor for structured XML formats.

Supports electronic invoice standards:
- ZUGFeRD / Factur-X (XML embedded in PDF, EN 16931)
- XRechnung (German government standard, EN 16931)
- UBL 2.1 (Universal Business Language)
- PEPPOL BIS 3.0 (Pan-European e-invoicing)

These formats provide the highest confidence extraction because
the data is structured XML, not OCR text.
"""

import io
import re
import zipfile
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Optional, Any
from xml.etree import ElementTree as ET

from .base import BaseExtractor, ExtractionResult


# XML namespaces for different standards
NAMESPACES = {
    # ZUGFeRD / Factur-X (Cross Industry Invoice)
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
    
    # UBL 2.1
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'ubl': 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2',
    'ubl_cn': 'urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2',
    
    # XRechnung (uses UBL or CII)
    'xr': 'urn:ce.eu:en16931:2017:xoev-de:kosit:extension:xrechnung_2.0',
}

# ZUGFeRD XML filename patterns (embedded in PDF)
ZUGFERD_FILENAMES = [
    'ZUGFeRD-invoice.xml',
    'zugferd-invoice.xml',
    'factur-x.xml',
    'xrechnung.xml',
]


def _safe_decimal(value: Optional[str]) -> Optional[Decimal]:
    """Safely convert string to Decimal."""
    if not value:
        return None
    try:
        # Handle both comma and dot as decimal separator
        cleaned = value.strip().replace(',', '.')
        # Remove any currency symbols or whitespace
        cleaned = re.sub(r'[^\d.\-]', '', cleaned)
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _safe_date(value: Optional[str], formats: list[str] = None) -> Optional[str]:
    """Parse date string to ISO format YYYY-MM-DD."""
    if not value:
        return None
    
    formats = formats or [
        '%Y%m%d',      # 20241118 (common in XML)
        '%Y-%m-%d',    # 2024-11-18
        '%d.%m.%Y',    # 18.11.2024
        '%d/%m/%Y',    # 18/11/2024
    ]
    
    value = value.strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


class EInvoiceExtractor(BaseExtractor):
    """
    Extract finance data from e-invoice XML formats.
    
    This extractor provides the highest confidence because the data
    is structured XML from authoritative sources.
    
    Supported formats:
    - ZUGFeRD/Factur-X: XML embedded in PDF (via PDF attachment extraction)
    - XRechnung: German government standard  
    - UBL 2.1: Universal Business Language
    - PEPPOL BIS: Pan-European standard
    """
    
    @property
    def name(self) -> str:
        return "einvoice_xml"
    
    @property
    def priority(self) -> int:
        return 100  # Highest priority - structured data
    
    def can_extract(self, content: str, file_bytes: Optional[bytes] = None) -> bool:
        """
        Check if file contains e-invoice XML.
        
        Looks for:
        1. XML embedded in PDF (ZUGFeRD/Factur-X)
        2. Raw XML content (UBL/XRechnung)
        """
        if file_bytes:
            # Check for embedded XML in PDF
            if self._has_embedded_xml(file_bytes):
                return True
        
        if content:
            # Check for XML markers in content
            content_lower = content.lower()
            xml_indicators = [
                'crossindustryinvoice',
                'urn:un:unece:uncefact',
                'ubl:invoice',
                'creditnote',
                '<invoice',
                'factur-x',
                'zugferd',
                'xrechnung',
            ]
            return any(ind in content_lower for ind in xml_indicators)
        
        return False
    
    def _has_embedded_xml(self, file_bytes: bytes) -> bool:
        """Check if PDF contains embedded ZUGFeRD/Factur-X XML."""
        try:
            # ZUGFeRD embeds XML as PDF attachment
            # Quick check for XML filename markers in PDF
            content_str = file_bytes.decode('latin-1', errors='ignore')
            for filename in ZUGFERD_FILENAMES:
                if filename.lower() in content_str.lower():
                    return True
            
            # Check for /EmbeddedFiles in PDF
            if b'/EmbeddedFiles' in file_bytes:
                return True
                
        except Exception:
            pass
        return False
    
    def extract(self, content: str, file_bytes: Optional[bytes] = None) -> ExtractionResult:
        """Extract finance data from e-invoice XML."""
        result = ExtractionResult(extraction_strategy=self.name)
        result.raw_matches = {}
        
        xml_content = None
        xml_source = None
        
        # Try to extract XML from PDF first
        if file_bytes:
            xml_content = self._extract_xml_from_pdf(file_bytes)
            if xml_content:
                xml_source = 'pdf_embedded'
        
        # Fall back to content if it's XML
        if not xml_content and content:
            if content.strip().startswith('<?xml') or '<CrossIndustryInvoice' in content:
                xml_content = content
                xml_source = 'content'
        
        if not xml_content:
            return result
        
        result.raw_matches['xml_source'] = xml_source
        
        # Try different parsers
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as e:
            result.raw_matches['parse_error'] = str(e)
            return result
        
        # Detect format and parse
        root_tag = root.tag.lower()
        
        if 'crossindustryinvoice' in root_tag or '{urn:un:unece:uncefact' in root.tag:
            return self._parse_cii(root, result)
        elif 'invoice' in root_tag or '{urn:oasis:names:specification:ubl' in root.tag:
            return self._parse_ubl(root, result)
        elif 'creditnote' in root_tag:
            return self._parse_ubl_credit_note(root, result)
        
        # Fallback: try both parsers
        cii_result = self._parse_cii(root, result)
        if cii_result.amount:
            return cii_result
        
        return self._parse_ubl(root, result)
    
    def _extract_xml_from_pdf(self, pdf_bytes: bytes) -> Optional[str]:
        """
        Extract embedded XML from PDF (ZUGFeRD/Factur-X).
        
        ZUGFeRD/Factur-X embeds XML as a PDF attachment.
        """
        try:
            # Method 1: Look for embedded file streams
            # PDF attachments are stored between stream/endstream markers
            # with /EmbeddedFiles and /Filespec references
            
            content = pdf_bytes.decode('latin-1', errors='ignore')
            
            # Find ZUGFeRD XML markers
            for filename in ZUGFERD_FILENAMES:
                # Look for the filename in PDF
                if filename.lower() not in content.lower():
                    continue
                
                # Try to find XML content between stream markers
                xml_matches = re.findall(
                    r'stream\s*\n(.*?)\nendstream',
                    content,
                    re.DOTALL
                )
                
                for match in xml_matches:
                    if '<?xml' in match[:100] or '<rsm:CrossIndustryInvoice' in match:
                        # Found XML content
                        try:
                            # Verify it's valid XML
                            ET.fromstring(match.strip())
                            return match.strip()
                        except ET.ParseError:
                            continue
            
            # Method 2: Check if PDF bytes contain raw XML
            # Some tools just concatenate XML to PDF
            if b'<?xml' in pdf_bytes:
                idx = pdf_bytes.find(b'<?xml')
                xml_candidate = pdf_bytes[idx:].decode('utf-8', errors='ignore')
                # Find the end of XML
                for end_tag in ['</rsm:CrossIndustryInvoice>', '</Invoice>', '</CreditNote>']:
                    end_idx = xml_candidate.find(end_tag)
                    if end_idx > 0:
                        xml_str = xml_candidate[:end_idx + len(end_tag)]
                        try:
                            ET.fromstring(xml_str)
                            return xml_str
                        except ET.ParseError:
                            continue
            
        except Exception as e:
            pass
        
        return None
    
    def _parse_cii(self, root: ET.Element, result: ExtractionResult) -> ExtractionResult:
        """
        Parse Cross Industry Invoice (CII) format.
        
        Used by ZUGFeRD, Factur-X, XRechnung (CII variant).
        """
        result.extraction_strategy = f"{self.name}/cii"
        ns = NAMESPACES
        
        try:
            # Find the main document context
            # Structure: rsm:CrossIndustryInvoice/rsm:SupplyChainTradeTransaction
            
            # Invoice number
            invoice_id = root.find('.//ram:ExchangedDocument/ram:ID', ns)
            if invoice_id is not None and invoice_id.text:
                result.invoice_number = invoice_id.text.strip()
                result.invoice_number_confidence = 0.95
            
            # Issue date
            issue_date = root.find('.//ram:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString', ns)
            if issue_date is None:
                issue_date = root.find('.//ram:IssueDateTime/udt:DateTimeString', ns)
            if issue_date is not None and issue_date.text:
                result.date = _safe_date(issue_date.text)
                if result.date:
                    result.date_confidence = 0.95
            
            # Seller (vendor) information
            seller_name = root.find('.//ram:SellerTradeParty/ram:Name', ns)
            if seller_name is not None and seller_name.text:
                result.vendor = seller_name.text.strip()
                result.vendor_confidence = 0.95
            
            # Currency from monetary summation
            currency_elem = root.find('.//ram:InvoiceCurrencyCode', ns)
            if currency_elem is not None and currency_elem.text:
                result.currency = currency_elem.text.strip().upper()
                result.currency_confidence = 0.98
            
            # Amount - look for grand total
            # ApplicableHeaderTradeSettlement/SpecifiedTradeSettlementHeaderMonetarySummation
            monetary_sum = root.find(
                './/ram:ApplicableHeaderTradeSettlement/'
                'ram:SpecifiedTradeSettlementHeaderMonetarySummation',
                ns
            )
            
            if monetary_sum is not None:
                # GrandTotalAmount is the final total
                grand_total = monetary_sum.find('ram:GrandTotalAmount', ns)
                if grand_total is not None and grand_total.text:
                    result.amount = _safe_decimal(grand_total.text)
                    if result.amount:
                        result.amount_confidence = 0.98
                
                # Tax totals
                tax_total = monetary_sum.find('ram:TaxTotalAmount', ns)
                if tax_total is not None and tax_total.text:
                    result.tax_amount = _safe_decimal(tax_total.text)
                
                # Net amount (before tax)
                tax_basis = monetary_sum.find('ram:TaxBasisTotalAmount', ns)
                if tax_basis is not None and tax_basis.text:
                    result.total_net = _safe_decimal(tax_basis.text)
            
            # Tax rate from applicable trade tax
            trade_tax = root.find('.//ram:ApplicableTradeTax/ram:RateApplicablePercent', ns)
            if trade_tax is not None and trade_tax.text:
                result.tax_rate = _safe_decimal(trade_tax.text)
            
            # Line items
            line_items = root.findall(
                './/ram:IncludedSupplyChainTradeLineItem',
                ns
            )
            
            for i, line_item in enumerate(line_items):
                item_data = self._parse_cii_line_item(line_item, ns, i + 1)
                if item_data:
                    result.line_items.append(item_data)
            
            if result.line_items:
                result.line_items_confidence = 0.9
            
            # Description from first line item or document name
            if not result.description:
                doc_name = root.find('.//ram:ExchangedDocument/ram:Name', ns)
                if doc_name is not None and doc_name.text:
                    result.description = doc_name.text.strip()
                    result.description_confidence = 0.8
                elif result.line_items:
                    result.description = result.line_items[0].get('description', '')
                    result.description_confidence = 0.7
            
        except Exception as e:
            result.raw_matches['cii_error'] = str(e)
        
        return result
    
    def _parse_cii_line_item(self, item: ET.Element, ns: dict, position: int) -> Optional[dict[str, Any]]:
        """Parse a CII line item."""
        try:
            data = {'position': position}
            
            # Description
            name = item.find('.//ram:SpecifiedTradeProduct/ram:Name', ns)
            if name is not None and name.text:
                data['description'] = name.text.strip()
            
            # Quantity
            qty = item.find('.//ram:SpecifiedLineTradeDelivery/ram:BilledQuantity', ns)
            if qty is not None and qty.text:
                data['quantity'] = str(_safe_decimal(qty.text))
            
            # Unit price
            price = item.find('.//ram:NetPriceProductTradePrice/ram:ChargeAmount', ns)
            if price is not None and price.text:
                data['unit_price'] = str(_safe_decimal(price.text))
            
            # Line total
            total = item.find('.//ram:SpecifiedLineTradeSettlement/'
                            'ram:SpecifiedTradeSettlementLineMonetarySummation/'
                            'ram:LineTotalAmount', ns)
            if total is not None and total.text:
                data['total'] = str(_safe_decimal(total.text))
            
            # Tax rate
            tax = item.find('.//ram:ApplicableTradeTax/ram:RateApplicablePercent', ns)
            if tax is not None and tax.text:
                data['tax_rate'] = str(_safe_decimal(tax.text))
            
            return data if 'description' in data else None
            
        except Exception:
            return None
    
    def _parse_ubl(self, root: ET.Element, result: ExtractionResult) -> ExtractionResult:
        """
        Parse UBL 2.1 Invoice format.
        
        Used by XRechnung (UBL variant), PEPPOL BIS.
        """
        result.extraction_strategy = f"{self.name}/ubl"
        ns = NAMESPACES
        
        try:
            # Invoice ID
            invoice_id = root.find('.//cbc:ID', ns)
            if invoice_id is None:
                # Try without namespace
                invoice_id = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}ID')
            if invoice_id is not None and invoice_id.text:
                result.invoice_number = invoice_id.text.strip()
                result.invoice_number_confidence = 0.95
            
            # Issue date
            issue_date = root.find('.//cbc:IssueDate', ns)
            if issue_date is None:
                issue_date = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}IssueDate')
            if issue_date is not None and issue_date.text:
                result.date = _safe_date(issue_date.text)
                if result.date:
                    result.date_confidence = 0.95
            
            # Currency
            currency = root.find('.//cbc:DocumentCurrencyCode', ns)
            if currency is None:
                currency = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}DocumentCurrencyCode')
            if currency is not None and currency.text:
                result.currency = currency.text.strip().upper()
                result.currency_confidence = 0.98
            
            # Supplier (vendor)
            supplier_name = root.find('.//cac:AccountingSupplierParty//cbc:Name', ns)
            if supplier_name is None:
                # Try alternate path
                supplier_name = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}AccountingSupplierParty//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}Name')
            if supplier_name is not None and supplier_name.text:
                result.vendor = supplier_name.text.strip()
                result.vendor_confidence = 0.95
            
            # Total amount (PayableAmount)
            payable = root.find('.//cbc:PayableAmount', ns)
            if payable is None:
                payable = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}PayableAmount')
            if payable is not None and payable.text:
                result.amount = _safe_decimal(payable.text)
                if result.amount:
                    result.amount_confidence = 0.98
                # Currency might be in attribute
                if not result.currency and payable.get('currencyID'):
                    result.currency = payable.get('currencyID').upper()
                    result.currency_confidence = 0.98
            
            # Tax amount
            tax_amount = root.find('.//cbc:TaxAmount', ns)
            if tax_amount is None:
                tax_amount = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}TaxAmount')
            if tax_amount is not None and tax_amount.text:
                result.tax_amount = _safe_decimal(tax_amount.text)
            
            # Tax exclusive amount (net)
            tax_exclusive = root.find('.//cbc:TaxExclusiveAmount', ns)
            if tax_exclusive is None:
                tax_exclusive = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}TaxExclusiveAmount')
            if tax_exclusive is not None and tax_exclusive.text:
                result.total_net = _safe_decimal(tax_exclusive.text)
            
            # Tax rate from TaxTotal/TaxSubtotal
            tax_percent = root.find('.//cac:TaxSubtotal/cbc:Percent', ns)
            if tax_percent is None:
                tax_percent = root.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}TaxSubtotal/{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}Percent')
            if tax_percent is not None and tax_percent.text:
                result.tax_rate = _safe_decimal(tax_percent.text)
            
            # Line items
            invoice_lines = root.findall('.//cac:InvoiceLine', ns)
            if not invoice_lines:
                invoice_lines = root.findall('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}InvoiceLine')
            
            for i, line in enumerate(invoice_lines):
                item_data = self._parse_ubl_line_item(line, ns, i + 1)
                if item_data:
                    result.line_items.append(item_data)
            
            if result.line_items:
                result.line_items_confidence = 0.9
            
        except Exception as e:
            result.raw_matches['ubl_error'] = str(e)
        
        return result
    
    def _parse_ubl_credit_note(self, root: ET.Element, result: ExtractionResult) -> ExtractionResult:
        """Parse UBL Credit Note (similar to Invoice)."""
        result = self._parse_ubl(root, result)
        result.extraction_strategy = f"{self.name}/ubl_credit_note"
        
        # Credit notes have negative amounts (or should be treated as income)
        if result.amount:
            result.raw_matches['is_credit_note'] = True
        
        return result
    
    def _parse_ubl_line_item(self, line: ET.Element, ns: dict, position: int) -> Optional[dict[str, Any]]:
        """Parse a UBL Invoice line item."""
        try:
            data = {'position': position}
            
            # Description/Name
            name = line.find('.//cbc:Name', ns)
            if name is None:
                name = line.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}Name')
            if name is not None and name.text:
                data['description'] = name.text.strip()
            
            # Quantity
            qty = line.find('.//cbc:InvoicedQuantity', ns)
            if qty is None:
                qty = line.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}InvoicedQuantity')
            if qty is not None and qty.text:
                data['quantity'] = str(_safe_decimal(qty.text))
            
            # Price
            price = line.find('.//cbc:PriceAmount', ns)
            if price is None:
                price = line.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}PriceAmount')
            if price is not None and price.text:
                data['unit_price'] = str(_safe_decimal(price.text))
            
            # Line total
            total = line.find('.//cbc:LineExtensionAmount', ns)
            if total is None:
                total = line.find('.//{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}LineExtensionAmount')
            if total is not None and total.text:
                data['total'] = str(_safe_decimal(total.text))
            
            return data if 'description' in data else None
            
        except Exception:
            return None
