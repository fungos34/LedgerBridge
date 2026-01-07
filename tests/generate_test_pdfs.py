#!/usr/bin/env python3
"""
Generate test PDF invoices from XML fixture files.

These PDFs can be uploaded to Paperless NGX for integration testing
the LedgerBridge pipeline.

The script creates:
1. Simple visual PDFs with invoice data (for all formats)
2. ZUGFeRD-style PDFs with embedded XML (for ZUGFeRD fixtures)

Usage:
    python generate_test_pdfs.py

Output:
    tests/fixtures/generated/
        - peppol_invoice.pdf
        - ubl_invoice.pdf
        - xrechnung_invoice.pdf
        - zugferd_invoice.pdf (with embedded XML)
"""

import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
except ImportError:
    print("ReportLab not installed. Install with: pip install reportlab")
    print("Or run: pip install -e '.[dev]'")
    sys.exit(1)


@dataclass
class InvoiceData:
    """Extracted invoice data for PDF generation."""

    invoice_id: str
    issue_date: str
    due_date: str | None
    seller_name: str
    seller_address: str
    seller_vat: str | None
    buyer_name: str
    buyer_address: str
    currency: str
    subtotal: str
    tax_amount: str
    total: str
    line_items: list
    notes: str | None = None
    format_type: str = "Unknown"


def parse_ubl_invoice(xml_path: Path) -> InvoiceData:
    """Parse UBL-based invoice (UBL, PEPPOL, XRechnung)."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Define namespaces
    ns = {
        "inv": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
        "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
        "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    }

    def get_text(xpath: str) -> str | None:
        elem = root.find(xpath, ns)
        return elem.text if elem is not None else None

    # Basic invoice info
    invoice_id = get_text("cbc:ID") or "Unknown"
    issue_date = get_text("cbc:IssueDate") or ""
    due_date = get_text("cbc:DueDate")
    currency = get_text("cbc:DocumentCurrencyCode") or "EUR"
    notes = get_text("cbc:Note")

    # Determine format type from CustomizationID
    customization_id = get_text("cbc:CustomizationID") or ""
    if "peppol" in customization_id.lower():
        format_type = "PEPPOL BIS 3.0"
    elif "xrechnung" in customization_id.lower():
        format_type = "XRechnung 2.0"
    else:
        format_type = "UBL 2.1"

    # Seller info
    seller = root.find("cac:AccountingSupplierParty/cac:Party", ns)
    seller_name = ""
    seller_address = ""
    seller_vat = None

    if seller:
        name_elem = seller.find("cac:PartyName/cbc:Name", ns)
        seller_name = name_elem.text if name_elem is not None else ""

        addr = seller.find("cac:PostalAddress", ns)
        if addr:
            street = addr.find("cbc:StreetName", ns)
            city = addr.find("cbc:CityName", ns)
            postal = addr.find("cbc:PostalZone", ns)
            country = addr.find("cac:Country/cbc:IdentificationCode", ns)

            parts = []
            if street is not None and street.text:
                parts.append(street.text)
            if postal is not None and postal.text:
                parts.append(postal.text)
            if city is not None and city.text:
                parts.append(city.text)
            if country is not None and country.text:
                parts.append(country.text)
            seller_address = ", ".join(parts)

        vat_elem = seller.find("cac:PartyTaxScheme/cbc:CompanyID", ns)
        if vat_elem is not None:
            seller_vat = vat_elem.text

    # Buyer info
    buyer = root.find("cac:AccountingCustomerParty/cac:Party", ns)
    buyer_name = ""
    buyer_address = ""

    if buyer:
        name_elem = buyer.find("cac:PartyName/cbc:Name", ns)
        buyer_name = name_elem.text if name_elem is not None else ""

        addr = buyer.find("cac:PostalAddress", ns)
        if addr:
            street = addr.find("cbc:StreetName", ns)
            city = addr.find("cbc:CityName", ns)
            postal = addr.find("cbc:PostalZone", ns)
            country = addr.find("cac:Country/cbc:IdentificationCode", ns)

            parts = []
            if street is not None and street.text:
                parts.append(street.text)
            if postal is not None and postal.text:
                parts.append(postal.text)
            if city is not None and city.text:
                parts.append(city.text)
            if country is not None and country.text:
                parts.append(country.text)
            buyer_address = ", ".join(parts)

    # Monetary totals
    monetary = root.find("cac:LegalMonetaryTotal", ns)
    subtotal = "0.00"
    total = "0.00"

    if monetary:
        subtotal_elem = monetary.find("cbc:TaxExclusiveAmount", ns)
        if subtotal_elem is not None:
            subtotal = subtotal_elem.text

        total_elem = monetary.find("cbc:PayableAmount", ns)
        if total_elem is not None:
            total = total_elem.text

    # Tax
    tax_total = root.find("cac:TaxTotal/cbc:TaxAmount", ns)
    tax_amount = tax_total.text if tax_total is not None else "0.00"

    # Line items
    line_items = []
    for line in root.findall("cac:InvoiceLine", ns):
        line_id = line.find("cbc:ID", ns)
        quantity = line.find("cbc:InvoicedQuantity", ns)
        amount = line.find("cbc:LineExtensionAmount", ns)
        item_name = line.find("cac:Item/cbc:Name", ns)
        item_desc = line.find("cac:Item/cbc:Description", ns)
        price = line.find("cac:Price/cbc:PriceAmount", ns)

        line_items.append(
            {
                "id": line_id.text if line_id is not None else "",
                "description": (
                    item_name.text
                    if item_name is not None
                    else (item_desc.text if item_desc is not None else "")
                ),
                "quantity": quantity.text if quantity is not None else "1",
                "unit_price": price.text if price is not None else "0.00",
                "total": amount.text if amount is not None else "0.00",
            }
        )

    return InvoiceData(
        invoice_id=invoice_id,
        issue_date=issue_date,
        due_date=due_date,
        seller_name=seller_name,
        seller_address=seller_address,
        seller_vat=seller_vat,
        buyer_name=buyer_name,
        buyer_address=buyer_address,
        currency=currency,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total,
        line_items=line_items,
        notes=notes,
        format_type=format_type,
    )


def parse_zugferd_invoice(xml_path: Path) -> InvoiceData:
    """Parse ZUGFeRD/Factur-X (CII) invoice."""
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Define namespaces
    ns = {
        "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
        "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
        "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    }

    def get_text(xpath: str) -> str | None:
        elem = root.find(xpath, ns)
        return elem.text if elem is not None else None

    # Document info
    invoice_id = get_text(".//rsm:ExchangedDocument/ram:ID") or "Unknown"

    # Parse date (format: 20260106 -> 2026-01-06)
    date_str = get_text(".//rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString") or ""
    if len(date_str) == 8:
        issue_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        issue_date = date_str

    # Seller
    seller = root.find(".//ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty", ns)
    seller_name = ""
    seller_address = ""
    seller_vat = None

    if seller:
        name_elem = seller.find("ram:Name", ns)
        seller_name = name_elem.text if name_elem is not None else ""

        addr = seller.find("ram:PostalTradeAddress", ns)
        if addr:
            line1 = addr.find("ram:LineOne", ns)
            postal = addr.find("ram:PostcodeCode", ns)
            city = addr.find("ram:CityName", ns)
            country = addr.find("ram:CountryID", ns)

            parts = []
            if line1 is not None and line1.text:
                parts.append(line1.text)
            if postal is not None and postal.text:
                parts.append(postal.text)
            if city is not None and city.text:
                parts.append(city.text)
            if country is not None and country.text:
                parts.append(country.text)
            seller_address = ", ".join(parts)

        vat_elem = seller.find("ram:SpecifiedTaxRegistration/ram:ID", ns)
        if vat_elem is not None:
            seller_vat = vat_elem.text

    # Buyer
    buyer = root.find(".//ram:ApplicableHeaderTradeAgreement/ram:BuyerTradeParty", ns)
    buyer_name = ""
    buyer_address = ""

    if buyer:
        name_elem = buyer.find("ram:Name", ns)
        buyer_name = name_elem.text if name_elem is not None else ""

        addr = buyer.find("ram:PostalTradeAddress", ns)
        if addr:
            line1 = addr.find("ram:LineOne", ns)
            postal = addr.find("ram:PostcodeCode", ns)
            city = addr.find("ram:CityName", ns)
            country = addr.find("ram:CountryID", ns)

            parts = []
            if line1 is not None and line1.text:
                parts.append(line1.text)
            if postal is not None and postal.text:
                parts.append(postal.text)
            if city is not None and city.text:
                parts.append(city.text)
            if country is not None and country.text:
                parts.append(country.text)
            buyer_address = ", ".join(parts)

    # Settlement (totals)
    settlement = root.find(".//ram:ApplicableHeaderTradeSettlement", ns)
    currency = "EUR"
    subtotal = "0.00"
    tax_amount = "0.00"
    total = "0.00"

    if settlement:
        currency_elem = settlement.find("ram:InvoiceCurrencyCode", ns)
        if currency_elem is not None:
            currency = currency_elem.text

        monetary = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", ns)
        if monetary:
            subtotal_elem = monetary.find("ram:TaxBasisTotalAmount", ns)
            if subtotal_elem is not None:
                subtotal = subtotal_elem.text

            tax_elem = monetary.find("ram:TaxTotalAmount", ns)
            if tax_elem is not None:
                tax_amount = tax_elem.text

            total_elem = monetary.find("ram:GrandTotalAmount", ns)
            if total_elem is not None:
                total = total_elem.text

    # Due date
    due_date = None
    payment = root.find(
        ".//ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString", ns
    )
    if payment is not None and payment.text and len(payment.text) == 8:
        due_date = f"{payment.text[:4]}-{payment.text[4:6]}-{payment.text[6:8]}"

    # Line items
    line_items = []
    for line in root.findall(".//ram:IncludedSupplyChainTradeLineItem", ns):
        line_id = line.find("ram:AssociatedDocumentLineDocument/ram:LineID", ns)
        item_name = line.find("ram:SpecifiedTradeProduct/ram:Name", ns)
        quantity = line.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", ns)
        price = line.find(
            "ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount", ns
        )
        amount = line.find(
            "ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount",
            ns,
        )

        line_items.append(
            {
                "id": line_id.text if line_id is not None else "",
                "description": item_name.text if item_name is not None else "",
                "quantity": quantity.text if quantity is not None else "1",
                "unit_price": price.text if price is not None else "0.00",
                "total": amount.text if amount is not None else "0.00",
            }
        )

    # Notes
    notes = get_text(".//rsm:ExchangedDocument/ram:IncludedNote/ram:Content")

    return InvoiceData(
        invoice_id=invoice_id,
        issue_date=issue_date,
        due_date=due_date,
        seller_name=seller_name,
        seller_address=seller_address,
        seller_vat=seller_vat,
        buyer_name=buyer_name,
        buyer_address=buyer_address,
        currency=currency,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total=total,
        line_items=line_items,
        notes=notes,
        format_type="ZUGFeRD 2.1 / Factur-X",
    )


def generate_pdf(invoice: InvoiceData, output_path: Path, xml_content: str | None = None):
    """Generate a PDF invoice from parsed data."""

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    styles = getSampleStyleSheet()
    story = []

    # Header style
    header_style = ParagraphStyle(
        "Header",
        parent=styles["Heading1"],
        fontSize=24,
        spaceAfter=10,
        textColor=colors.HexColor("#1e40af"),
    )

    subheader_style = ParagraphStyle(
        "SubHeader",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.grey,
        spaceAfter=20,
    )

    section_style = ParagraphStyle(
        "Section",
        parent=styles["Heading2"],
        fontSize=12,
        spaceBefore=15,
        spaceAfter=8,
        textColor=colors.HexColor("#374151"),
    )

    # Title
    story.append(Paragraph("INVOICE", header_style))
    story.append(Paragraph(f"Format: {invoice.format_type}", subheader_style))

    # Invoice details table
    invoice_info = [
        ["Invoice Number:", invoice.invoice_id],
        ["Issue Date:", invoice.issue_date],
    ]
    if invoice.due_date:
        invoice_info.append(["Due Date:", invoice.due_date])
    invoice_info.append(["Currency:", invoice.currency])

    info_table = Table(invoice_info, colWidths=[80, 200])
    info_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 20))

    # Seller & Buyer
    story.append(Paragraph("From:", section_style))
    story.append(Paragraph(f"<b>{invoice.seller_name}</b>", styles["Normal"]))
    story.append(Paragraph(invoice.seller_address, styles["Normal"]))
    if invoice.seller_vat:
        story.append(Paragraph(f"VAT: {invoice.seller_vat}", styles["Normal"]))
    story.append(Spacer(1, 10))

    story.append(Paragraph("To:", section_style))
    story.append(Paragraph(f"<b>{invoice.buyer_name}</b>", styles["Normal"]))
    story.append(Paragraph(invoice.buyer_address, styles["Normal"]))
    story.append(Spacer(1, 20))

    # Line items
    if invoice.line_items:
        story.append(Paragraph("Line Items:", section_style))

        table_data = [["#", "Description", "Qty", "Unit Price", "Total"]]
        for item in invoice.line_items:
            table_data.append(
                [
                    item["id"],
                    item["description"][:50] + ("..." if len(item["description"]) > 50 else ""),
                    item["quantity"],
                    f"{invoice.currency} {item['unit_price']}",
                    f"{invoice.currency} {item['total']}",
                ]
            )

        items_table = Table(table_data, colWidths=[30, 200, 40, 80, 80])
        items_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        story.append(items_table)
        story.append(Spacer(1, 20))

    # Totals
    story.append(Paragraph("Summary:", section_style))

    totals_data = [
        ["Subtotal (excl. VAT):", f"{invoice.currency} {invoice.subtotal}"],
        ["VAT:", f"{invoice.currency} {invoice.tax_amount}"],
        ["Total:", f"{invoice.currency} {invoice.total}"],
    ]

    totals_table = Table(totals_data, colWidths=[120, 100])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (0, -1), "RIGHT"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, -1), (-1, -1), 8),
            ]
        )
    )
    story.append(totals_table)

    # Notes
    if invoice.notes:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Notes:", section_style))
        story.append(Paragraph(invoice.notes, styles["Normal"]))

    # Footer
    story.append(Spacer(1, 40))
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.grey,
        alignment=1,  # Center
    )
    story.append(
        Paragraph(
            f"Generated for LedgerBridge testing on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            footer_style,
        )
    )
    story.append(
        Paragraph("This is a test document generated from XML e-invoice fixtures.", footer_style)
    )

    # Build PDF
    doc.build(story)
    print(f"  ✓ Generated: {output_path.name}")


def main():
    """Generate test PDFs from all XML fixtures."""
    fixtures_dir = Path(__file__).parent / "fixtures"
    output_dir = fixtures_dir / "generated"
    output_dir.mkdir(exist_ok=True)

    print("🔧 Generating test PDF invoices from XML fixtures...\n")

    # Process each fixture
    fixtures = [
        ("peppol_sample.xml", "peppol_invoice.pdf", "ubl"),
        ("ubl_sample.xml", "ubl_invoice.pdf", "ubl"),
        ("xrechnung_sample.xml", "xrechnung_invoice.pdf", "ubl"),
        ("zugferd_sample.xml", "zugferd_invoice.pdf", "zugferd"),
    ]

    for xml_name, pdf_name, format_type in fixtures:
        xml_path = fixtures_dir / xml_name
        pdf_path = output_dir / pdf_name

        if not xml_path.exists():
            print(f"  ⚠ Skipping {xml_name}: file not found")
            continue

        try:
            # Parse invoice
            if format_type == "zugferd":
                invoice = parse_zugferd_invoice(xml_path)
                xml_content = xml_path.read_text(encoding="utf-8")
            else:
                invoice = parse_ubl_invoice(xml_path)
                xml_content = None

            # Generate PDF
            generate_pdf(invoice, pdf_path, xml_content)

        except Exception as e:
            print(f"  ✗ Failed to process {xml_name}: {e}")
            import traceback

            traceback.print_exc()

    print(f"\n✓ PDFs generated in: {output_dir}")
    print("\nTo test with Paperless NGX:")
    print("  1. Upload the generated PDFs to Paperless")
    print("  2. Add the 'finance/inbox' tag to each document")
    print("  3. Run the LedgerBridge extraction: paperless-firefly extract")


if __name__ == "__main__":
    main()
