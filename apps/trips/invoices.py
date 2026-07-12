"""PDF invoice generation (reportlab) for a buy request."""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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


def _acct(value) -> str:
    """Accounting format: negatives shown in parentheses."""
    v = Decimal(value)
    if v < 0:
        return f"({abs(v):,.2f})"
    return f"{v:,.2f}"


def _party_block(name, *lines) -> str:
    """Name + address lines as a single <br/>-joined, escaped paragraph string."""
    parts = [escape(name or "Not yet assigned")]
    parts += [escape(str(ln)) for ln in lines if ln]
    return "<br/>".join(parts)


def render_customs_invoice_pdf(req) -> bytes:
    """Customs (commercial) invoice PDF, matching customs_invoice_print.html and
    NewCustomsInvoiceFormat.pdf. Values are in IDR and INCLUDE the proxy margin;
    Country of Origin is the order's sourcing country; shipping is the carrier's
    fee; packages fixed at 1. All figures come from Order.customs_invoice()
    so the PDF and the printable HTML stay identical."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Invoice {req.reference}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("cititle", parent=styles["Title"], textColor=INK, fontSize=20,
                           alignment=TA_CENTER, spaceAfter=18, leading=24)
    label = ParagraphStyle("clabel", parent=styles["Normal"], textColor=INK, fontSize=10, leading=15)
    small = ParagraphStyle("csmall", parent=styles["Normal"], textColor=INK, fontSize=9.5, leading=14)
    item_cell = ParagraphStyle("citem", parent=styles["Normal"], fontSize=9.5, leading=12, textColor=INK)

    def idr0(value) -> str:
        return f"{Decimal(value or 0):,.0f}"

    ci = req.customs_invoice()
    ccy = ci.get("currency") or ""
    story = [Paragraph("Invoice", title)]

    # Meta: DATE / PURCHASE ORDER, then SHIPPER / RECEIVER blocks.
    date_str = ci["date"].strftime("%d-%b-%Y") if ci["date"] else "—"
    proxy = req.proxy_buyer
    proxy_loc = ", ".join(
        p for p in [getattr(proxy, "city", ""), getattr(proxy, "country", "")] if p
    ) if proxy else ""
    proxy_user = getattr(proxy, "user", None)
    proxy_addr = getattr(proxy_user, "buyer_invoice_address", "") if proxy_user else ""
    proxy_name = (getattr(proxy_user, "full_name", "") if proxy_user else "") or getattr(proxy, "name", "")
    shipper = _party_block(proxy_name, proxy_addr, proxy_loc)
    receiver = _party_block(
        req.buyer.full_name, req.buyer.buyer_invoice_address, req.buyer.buyer_destination_city
    )
    meta = Table([
        [Paragraph(f"<b>DATE:</b> {date_str}", label),
         Paragraph(f"<b>PURCHASE ORDER:</b> {escape(ci['reference'])}", label)],
        [Paragraph(f"<b>SHIPPER:</b><br/>{shipper}", small),
         Paragraph(f"<b>RECEIVER:</b><br/>{receiver}", small)],
    ], colWidths=[87 * mm, 87 * mm])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
    ]))
    story.append(meta)
    story.append(Spacer(1, 16))

    # Items + totals — Qty / Description / Country of Origin / Unit / Total, with
    # values in the order's origin currency. Long headers wrap; Description is
    # centred and given the spare width so it stays the table's widest column.
    hdr = ParagraphStyle("chdr", parent=styles["Normal"], fontName="Helvetica-Bold",
                         fontSize=9.5, leading=11, textColor=INK)
    hdr_c = ParagraphStyle("chdrc", parent=hdr, alignment=TA_CENTER)
    hdr_r = ParagraphStyle("chdrr", parent=hdr, alignment=TA_RIGHT)
    data = [[Paragraph("Qty", hdr_r), Paragraph("Description of Goods", hdr_c),
             Paragraph("Country of Origin", hdr_c),
             Paragraph(f"Unit Value ({escape(ccy)})", hdr_r),
             Paragraph(f"Total Value ({escape(ccy)})", hdr_r)]]
    for r in ci["rows"]:
        data.append([str(r.quantity), Paragraph(escape(r.name), item_cell), escape(r.origin or ""),
                     idr0(r.unit_value), idr0(r.total_value)])
    sub_row = len(data)
    data.append(["", "Sub-Total", "", "", idr0(ci["subtotal"])])
    data.append(["", "Shipping Charges", "", "", idr0(ci["shipping"])])
    total_row = len(data)
    data.append(["", "Total Value", "", "", idr0(ci["total"])])
    usd_row = None
    if ci.get("total_usd") is not None:
        usd_row = len(data)
        data.append(["", "Total Value - USD Eqv.", "", "", f"USD {Decimal(ci['total_usd']):,.2f}"])
    data.append(["", "", "", "", ""])
    data.append(["", f"Number of Package: {ci['num_packages']}", "", "", ""])

    peach = colors.HexColor("#F5CBA7")
    peach_line = colors.HexColor("#D9A47F")
    items_tbl = Table(data, colWidths=[14 * mm, 80 * mm, 28 * mm, 26 * mm, 26 * mm])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), peach),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("ALIGN", (0, 0), (0, -1), "RIGHT"),    # Qty
        ("ALIGN", (2, 0), (2, -1), "CENTER"),   # Country of Origin
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),   # value columns
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, peach_line),
        ("FONTNAME", (1, sub_row), (1, sub_row), "Helvetica-Bold"),
        ("FONTNAME", (4, sub_row), (4, sub_row), "Helvetica-Bold"),
        ("FONTNAME", (1, total_row), (-1, total_row), "Helvetica-Bold"),
        ("LINEABOVE", (0, total_row), (-1, total_row), 1.0, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if usd_row is not None:
        style_cmds.append(("FONTNAME", (1, usd_row), (-1, usd_row), "Helvetica-Bold"))
    items_tbl.setStyle(TableStyle(style_cmds))
    story.append(items_tbl)
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "This invoice is prepared for customs declaration purposes. Goods are carried "
        "personally by the traveler on behalf of the buyer named above.", small))

    doc.build(story)
    return buf.getvalue()


def render_invoice_pdf(req) -> bytes:
    """Return the PDF bytes of the invoice for a Order."""
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
    # Item-name cell: a flowable Paragraph so long names wrap within the column.
    item_cell = ParagraphStyle("item_cell", parent=styles["Normal"], fontSize=8.5, leading=10.5, textColor=INK)

    cur = req.currency
    plan = req.plan
    story = []

    # Header
    story.append(Paragraph("ProxyBuying", h))
    story.append(Paragraph("Proxy purchasing invoice", sub))
    story.append(Spacer(1, 10))

    meta = Table([
        [Paragraph("<b>Invoice</b>", label), Paragraph(req.reference, sub),
         Paragraph("<b>Travel date</b>", label), Paragraph(plan.travel_date.strftime("%d-%b-%Y"), sub)],
        [Paragraph("<b>Route</b>", label),
         Paragraph(f"{plan.from_city}, {plan.from_country} &rarr; {plan.to_city}, {plan.to_country}", sub),
         Paragraph("<b>Currency</b>", label), Paragraph(cur, sub)],
        [Paragraph("<b>Buyer</b>", label), Paragraph(req.buyer.display_name, sub),
         Paragraph("<b>Carrier</b>", label), Paragraph(plan.traveler.display_name, sub)],
    ], colWidths=[24 * mm, 64 * mm, 24 * mm, 62 * mm])
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(meta)
    story.append(Spacer(1, 12))

    # Items table — two columns (Estimate / Actual)
    act_weight = req.actual_weight_kg if req.has_actual_weight else ""
    data = [
        ["#", "Item", "Qty", "", "", f"Unit Price ({cur})", "", f"Total ({cur})", ""],
        ["", "", "Est", "Act", "Unit", "Est", "Actual", "Est", "Actual"],
    ]
    for i, item in enumerate(req.items.all(), start=1):
        data.append([
            str(i), Paragraph(escape(item.name), item_cell), str(item.quantity), str(item.actual_quantity), item.unit,
            _money(item.estimated_unit_cost), _money(item.actual_unit_cost),
            _money(item.estimated_line_total), _money(item.actual_line_total),
        ])
    n_items = req.items.count()
    body_start = 2
    sub_row = body_start + n_items
    data.append(["", "Sub-Total Items", str(req.est_qty_total), str(req.act_qty_total), "",
                 "", "", _money(req.items_estimated_total), _money(req.items_actual_total)])
    _mp = plan.margin_percent
    _mp_str = f"{int(_mp) if _mp == _mp.to_integral_value() else _mp}%"
    data.append(["", "Margin", "", _mp_str, "", "", "",
                 _money(req.estimated_margin), _money(req.actual_margin)])
    data.append(["", "Shipment (kg)", str(req.estimated_weight_kg), str(act_weight), "",
                 "", _money(req.effective_cost_per_kg),
                 _money(req.estimated_shipment_cost), _money(req.shipment_cost)])
    data.append(["", "Custom Duty", "", "", "", "", "",
                 _money(req.estimated_custom), _money(req.actual_custom)])
    total_row = len(data)
    data.append(["", "Total Invoice", "", "", "", "", "",
                 _money(req.estimated_invoice_total), _money(req.invoice_total)])

    items_tbl = Table(data, colWidths=[8 * mm, 40 * mm, 11 * mm, 11 * mm, 12 * mm, 22 * mm, 22 * mm, 24 * mm, 24 * mm])
    items_tbl.setStyle(TableStyle([
        # grouped header spans
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)),
        ("SPAN", (2, 0), (4, 0)), ("SPAN", (5, 0), (6, 0)), ("SPAN", (7, 0), (8, 0)),
        ("BACKGROUND", (0, 0), (-1, 1), CLAY),
        ("TEXTCOLOR", (0, 0), (-1, 1), colors.white),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (2, 0), (-1, 1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (2, body_start), (-1, -1), "RIGHT"),
        ("ALIGN", (4, body_start), (4, -1), "CENTER"),  # Unit column
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, body_start), (-1, -1), "MIDDLE"),  # keep numbers centered against a wrapped item name
        ("ROWBACKGROUNDS", (0, body_start), (-1, sub_row - 1), [colors.white, CREAM]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        # subtotal + summary rows bold
        ("FONTNAME", (1, sub_row), (-1, sub_row), "Helvetica-Bold"),
        ("LINEABOVE", (0, sub_row), (-1, sub_row), 0.6, SLATE),
        # total invoice row
        ("FONTNAME", (1, total_row), (-1, total_row), "Helvetica-Bold"),
        ("LINEABOVE", (0, total_row), (-1, total_row), 0.8, INK),
        ("BACKGROUND", (0, total_row), (-1, total_row), CREAM),
        ("TEXTCOLOR", (0, total_row), (-1, total_row), CLAY_DARK),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 10))

    # Settlement summary (deposit, payments, due)
    settle = [
        ("Deposit Due", _money(req.deposit_due)),
        ("Deposit Paid", f"({_money(req.deposit_paid_amount)})"),
        ("Unpaid / (Overpaid)", _acct(req.invoice_unpaid_overpaid)),
        ("Final (Payment)/Refund Made", _acct(req.final_settlement)),
        ("Total Due", _acct(req.total_due)),
    ]
    settle_data = [[lbl, f"{cur} {val}"] for lbl, val in settle]
    totals = Table(settle_data, colWidths=[120 * mm, 54 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, LINE),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), CLAY_DARK),
        ("BACKGROUND", (0, -1), (-1, -1), CREAM),
    ]))
    story.append(totals)
    story.append(Spacer(1, 10))

    # Discrepancy / dispute note
    story.append(Paragraph(
        "* Actual shipment weight was based on Carrier final measurement. Buyer can verify this when "
        "picking up the package and should settle the discrepancy directly to carrier or totally waive it. "
        "ProxyBuying will not get involved on any dispute regarding this issue. As long as buyer click "
        "&ldquo;Clear&rdquo; button, ProxyBuying will paid carrier the full amount after deducting the "
        "2.5% commission.", small))
    story.append(Spacer(1, 14))

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
