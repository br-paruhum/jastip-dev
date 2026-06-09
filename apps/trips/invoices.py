"""PDF invoice generation (reportlab) for a buy request."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
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

CLAY = colors.HexColor("#CC785C")
CLAY_DARK = colors.HexColor("#99492F")
INK = colors.HexColor("#1A1A18")
SLATE = colors.HexColor("#3D3A35")
LINE = colors.HexColor("#E3DED2")
CREAM = colors.HexColor("#F5F3EC")


def _money(value) -> str:
    return f"{Decimal(value):,.2f}"


def render_invoice_pdf(req) -> bytes:
    """Return the PDF bytes of the invoice for a BuyRequest."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {req.reference}",
    )
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Title"], textColor=CLAY_DARK, fontSize=22, spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], textColor=SLATE, fontSize=9)
    label = ParagraphStyle("label", parent=styles["Normal"], textColor=SLATE, fontSize=9)
    small = ParagraphStyle("small", parent=styles["Normal"], textColor=SLATE, fontSize=8.5, leading=12)
    right = ParagraphStyle("right", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=9)

    cur = req.currency
    plan = req.plan
    story = []

    # Header
    story.append(Paragraph("Jastip.me", h))
    story.append(Paragraph("Proxy purchasing invoice", sub))
    story.append(Spacer(1, 10))

    meta = Table([
        [Paragraph("<b>Invoice</b>", label), Paragraph(req.reference, sub),
         Paragraph("<b>Travel date</b>", label), Paragraph(plan.travel_date.strftime("%d-%b-%Y"), sub)],
        [Paragraph("<b>Route</b>", label),
         Paragraph(f"{plan.from_city}, {plan.from_country} &rarr; {plan.to_city}, {plan.to_country}", sub),
         Paragraph("<b>Currency</b>", label), Paragraph(cur, sub)],
        [Paragraph("<b>Buyer</b>", label), Paragraph(req.buyer.display_name, sub),
         Paragraph("<b>Traveler</b>", label), Paragraph(plan.traveler.display_name, sub)],
    ], colWidths=[24 * mm, 64 * mm, 24 * mm, 62 * mm])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta)
    story.append(Spacer(1, 12))

    # Items table
    data = [["#", "Item", "Qty", f"Unit cost ({cur})", f"Line total ({cur})"]]
    for i, item in enumerate(req.items.all(), start=1):
        data.append([
            str(i), item.name, str(item.quantity),
            _money(item.estimated_unit_cost), _money(item.estimated_line_total),
        ])
    items_tbl = Table(data, colWidths=[10 * mm, 76 * mm, 14 * mm, 40 * mm, 34 * mm])
    items_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), CLAY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CREAM]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 10))

    # Totals
    rows = [
        ("Items subtotal (estimated)", _money(req.items_estimated_total)),
        (f"Margin ({plan.margin_percent}%)", _money(req.margin_amount)),
        (f"Shipment ({req.shipment_weight_kg} kg)", _money(req.shipment_cost)),
    ]
    if req.custom_fare_amount and req.custom_fare_amount > 0:
        rows.append(("Custom fare", _money(req.custom_fare_amount)))
    rows.append(("ESTIMATED TOTAL", _money(req.estimated_invoice_total)))
    rows.append(("Deposit due now (50% items + shipment)", _money(req.deposit_due)))

    totals_data = [[Paragraph(lbl, right if lbl.isupper() else label), f"{cur} {val}"] for lbl, val in rows]
    totals = Table(totals_data, colWidths=[120 * mm, 54 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, -2), (-1, -2), 0.8, INK),
        ("FONTNAME", (0, -2), (-1, -2), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), CLAY_DARK),
        ("BACKGROUND", (0, -1), (-1, -1), CREAM),
    ]))
    story.append(totals)
    story.append(Spacer(1, 18))

    # Bank details
    bank = settings.BANK_DETAILS
    story.append(Paragraph("Please make your bank transfer to:", ParagraphStyle(
        "bankhead", parent=styles["Normal"], fontSize=10, textColor=INK, spaceAfter=6, fontName="Helvetica-Bold")))
    bank_tbl = Table([
        ["Bank Name", f": {bank['bank_name']}"],
        ["Branch", f": {bank['branch']}"],
        ["Account No.", f": {bank['account_no']}"],
        ["Account Name", f": {bank['account_name']}"],
    ], colWidths=[30 * mm, 144 * mm])
    bank_tbl.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), SLATE),
        ("FONTNAME", (1, 2), (1, 3), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, 2), (1, 3), INK),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0, colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), CREAM),
    ]))
    story.append(bank_tbl)
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"To avoid cancellation, please make payment within {settings.PAYMENT_DEADLINE_HOURS} hours, "
        "then upload your deposit proof on the request page.", small))

    doc.build(story)
    return buf.getvalue()
