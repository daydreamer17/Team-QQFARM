"""Generate the synthetic, preference-sensitive full_flow_demo2 dataset."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/generated/inputs/development/full_flow_demo2"
REFERENCE_OUT = ROOT / "evaluation/reference/full_flow_demo2"
Q = Decimal("0.01")
FX_RATE = Decimal("1.61")

BLUE = colors.HexColor("#2563EB")
NAVY = colors.HexColor("#14213D")
SLATE = colors.HexColor("#64748B")
LIGHT = colors.HexColor("#F1F5F9")
GREEN = colors.HexColor("#14866D")
AMBER = colors.HexColor("#D97706")
RED = colors.HexColor("#B91C1C")

REQUIREMENT = {
    "manufacturer": "QQ Demo Components",
    "manufacturer_part_number": "QW-MCU9-DEMO",
    "package": "QFN-32",
    "revision": "R1",
    "condition": "NEW",
    "allow_substitutes": False,
    "base_unit": "piece",
    "required_quantity": 1250,
    "quantity_unit": "piece",
    "budget_amount": "10000.00",
    "currency": "SGD",
    "includes_shipping": True,
    "tax_mode": "NOT_APPLICABLE",
    "other_fees_required": True,
    "planned_order_date": "2026-09-20",
    "delivery_deadline": "2026-10-02",
    "delivery_location": "SG-DEMO-01",
    "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
    "secondary_preference": None,
}

QUOTES = {
    "B": {
        "supplier_id": "V9-SUP-B",
        "supplier_name": "Synthetic Bridgeway Semiconductors Ltd.",
        "currency": "SGD",
        "unit_price": "7.10",
        "shipping": "40.00",
        "moq": 500,
        "multiple": 100,
        "ordered_quantity": 1300,
        "lead_days": 4,
        "payment_terms": "Net 30",
        "arrival": "2026-09-24",
        "native_total": "9270.00",
        "sgd_total": "9270.00",
    },
    "C": {
        "supplier_id": "V9-SUP-C",
        "supplier_name": "Synthetic Cedar Economy Electronics Ltd.",
        "currency": "SGD",
        "unit_price": "6.10",
        "shipping": None,
        "confirmed_shipping": "50.00",
        "moq": 1000,
        "multiple": 500,
        "ordered_quantity": 1500,
        "lead_days": 10,
        "payment_terms": "Net 30",
        "arrival": "2026-09-30",
        "known_subtotal": "9150.00",
        "sgd_total_after_answer": "9200.00",
    },
    "D": {
        "supplier_id": "V9-SUP-D",
        "supplier_name": "Synthetic Delta Global Components Inc.",
        "currency": "USD",
        "unit_price": "4.39",
        "shipping": "15.00",
        "moq": 500,
        "multiple": 100,
        "ordered_quantity": 1300,
        "lead_days": 5,
        "payment_terms": "Net 45",
        "arrival": "2026-09-25",
        "native_total": "5722.00",
        "sgd_total": "9212.42",
    },
    "E": {
        "supplier_id": "V9-SUP-E",
        "supplier_name": "Synthetic Evergreen Precision Parts Ltd.",
        "currency": "SGD",
        "unit_price": "7.32",
        "shipping": "50.00",
        "moq": 500,
        "multiple": 50,
        "ordered_quantity": 1250,
        "lead_days": 7,
        "payment_terms": "Net 30",
        "arrival": "2026-09-27",
        "native_total": "9200.00",
        "sgd_total": "9200.00",
        "superseded_price": "7.40",
    },
    "G": {
        "supplier_id": "V9-SUP-G",
        "supplier_name": "Synthetic Greenline Components Pte. Ltd.",
        "currency": "SGD",
        "unit_price": "7.40",
        "shipping": "0.00",
        "moq": 500,
        "multiple": 50,
        "ordered_quantity": 1250,
        "lead_days": 3,
        "payment_terms": "Net 60",
        "arrival": "2026-09-23",
        "native_total": "9250.00",
        "sgd_total": "9250.00",
    },
}

INTENTS = [
    {
        "intent_id": "cost_first",
        "persona": "成本控制型采购员",
        "user_text": "交期只要满足截止日期即可。我只关心最终确认的落地总成本，不要替我根据交期打破价格并列。",
    },
    {
        "intent_id": "cost_then_fastest",
        "persona": "成本与交期兼顾型采购员",
        "user_text": "优先选最终总成本最低的。如果最终价格一样，就选更早到货的。",
    },
    {
        "intent_id": "delivery_urgent",
        "persona": "产线紧急补料负责人",
        "user_text": "这个物料会影响生产。只要不超过预算并满足规格，就优先选最快到货的，价格作为第二考虑。",
    },
    {
        "intent_id": "balanced_tolerance",
        "persona": "平衡型项目采购员",
        "user_text": "不一定非要选绝对最低价。如果比最低报价最多贵 SGD 15，就选择其中到货最快的。允许使用已批准的汇率快照。",
    },
]

POLICY_METADATA = {
    "policy_set_id": "full-flow-demo2-electronics",
    "policy_set_version": "2026.09.2-demo",
    "policy_id": "POL-DEMO2-DECISION-GATE",
    "document_id": "DOC-DEMO2-DECISION-GATE",
    "document_version": "1.0.0",
    "title": "Fictional Electronics Preference and Evidence Policy",
    "effective_from": "2026-01-01T00:00:00Z",
    "effective_to": None,
    "categories": ["Electronics"],
    "regions": ["SG"],
}

POLICY_CLAUSES = [
    ("ASL-101", "Registry evidence", "APPROVED_SUPPLIER", "Approved supplier status must be supported by a current supplier registry record at the evaluation time.", {}),
    ("ASL-102", "Exact identity matching", "APPROVED_SUPPLIER", "Supplier approval must use the confirmed supplier identifier. Similar names must not be treated as the same supplier.", {}),
    ("ROHS-101", "Part-level evidence", "ROHS_COMPLIANCE", "Electronics purchases require current RoHS evidence matching both the supplier identifier and requested manufacturer part number.", {}),
    ("ROHS-102", "Missing evidence", "ROHS_COMPLIANCE", "Missing, expired, or mismatched RoHS evidence requires review and must not be inferred from document similarity.", {}),
    ("APR-101", "Approval threshold", "AMOUNT_APPROVAL", "A proposed award with total landed cost of SGD 10,000 or more requires manager approval before publication.", {"currency": "SGD", "threshold": "10000.00", "operator": ">="}),
    ("APR-102", "Approval evidence", "AMOUNT_APPROVAL", "Approval evidence must identify the approving actor, amount, timestamp, and task revision.", {}),
    ("FX-101", "Frozen exchange rate", "FX_CONVERSION", "Foreign-currency quotes may be compared only with an approved exchange-rate snapshot effective at the evaluation timestamp.", {}),
    ("FX-102", "Conversion precision", "FX_CONVERSION", "Converted totals use Decimal arithmetic and ROUND_HALF_UP to two decimal places after native total cost is calculated.", {"rounding": "ROUND_HALF_UP", "places": 2}),
    ("PREF-101", "Buyer-confirmed preference", "DECISION_PREFERENCE", "A ranking preference, tie-break rule, or cost tolerance must be confirmed by the buyer before it changes supplier selection.", {}),
    ("PREF-102", "Hard constraints first", "DECISION_PREFERENCE", "Package, revision, condition, budget, and delivery deadline are hard constraints and are evaluated before ranking preferences.", {}),
    ("UNK-101", "Unknown charges", "UNKNOWN_COST_HANDLING", "An unknown material charge remains unknown and must never be normalized to zero.", {}),
    ("UNK-102", "Decision impact", "UNKNOWN_COST_HANDLING", "Missing information blocks publication when a feasible value could change the recommended supplier; otherwise its non-impact must be proven by deterministic analysis.", {}),
]


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("DemoTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=23, leading=28, textColor=NAVY, spaceAfter=8),
        "subtitle": ParagraphStyle("DemoSubtitle", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=13, textColor=SLATE, spaceAfter=10),
        "h1": ParagraphStyle("DemoH1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=NAVY, spaceBefore=8, spaceAfter=7),
        "h2": ParagraphStyle("DemoH2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=BLUE, spaceBefore=6, spaceAfter=5),
        "body": ParagraphStyle("DemoBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=13, textColor=NAVY, spaceAfter=7),
        "small": ParagraphStyle("DemoSmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=10, textColor=SLATE),
        "email": ParagraphStyle("DemoEmail", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=15, textColor=NAVY, spaceAfter=9),
        "right": ParagraphStyle("DemoRight", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, alignment=TA_RIGHT, textColor=NAVY),
        "center": ParagraphStyle("DemoCenter", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=12, alignment=TA_CENTER, textColor=NAVY),
    }


def _footer(canvas, doc, *, label: str, color=BLUE):
    canvas.saveState()
    width, _height = A4
    canvas.setStrokeColor(color)
    canvas.setLineWidth(0.8)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SLATE)
    canvas.drawString(18 * mm, 9 * mm, f"{label} | Synthetic demonstration data - not a commercial offer")
    canvas.drawRightString(width - 18 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _build_pdf(path: Path, story: list, *, label: str, color=BLUE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=17 * mm,
        bottomMargin=20 * mm,
        title=label,
        author="QuoteWise synthetic data generator",
        invariant=1,
        pageCompression=1,
    )
    callback = lambda canvas, current_doc: _footer(canvas, current_doc, label=label, color=color)
    doc.build(story, onFirstPage=callback, onLaterPages=callback)


def _field_table(rows: list[tuple[str, str]], styles, *, color=BLUE, widths=(48 * mm, 120 * mm)) -> Table:
    table = Table([[Paragraph(f"<b>{key}</b>", styles["small"]), Paragraph(value, styles["body"])] for key, value in rows], colWidths=list(widths), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("TEXTCOLOR", (0, 0), (0, -1), color),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _line_items_table(headers: list[str], rows: list[list[str]], styles, *, color=BLUE, widths=None) -> Table:
    header_style = ParagraphStyle(
        "DemoTableHeader",
        parent=styles["small"],
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    data = [[Paragraph(value, header_style) for value in headers]]
    data.extend([[Paragraph(value, styles["small"]) for value in row] for row in rows])
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), color),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CBD5E1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
    ]))
    return table


def _generate_requirement(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("INTERNAL PURCHASE REQUEST", s["title"]),
        Paragraph("Request PR-MCU-V9-1250 | Electronics Procurement | Revision 1", s["subtitle"]),
        HRFlowable(width="100%", thickness=2, color=BLUE, spaceAfter=10),
        Paragraph("Requested material", s["h1"]),
        _field_table([
            ("Manufacturer", "QQ Demo Components"),
            ("Part number", "QW-MCU9-DEMO"),
            ("Specification", "QFN-32 package / Revision R1 / NEW condition"),
            ("Substitution", "Not permitted without a new buyer-approved requirement revision"),
            ("Required quantity", "1,250 pieces"),
        ], s),
        Spacer(1, 8),
        Paragraph("Commercial and delivery constraints", s["h1"]),
        _field_table([
            ("Budget ceiling", "SGD 10,000.00 including shipping and all mandatory charges"),
            ("Tax treatment", "Tax not applicable for this synthetic scenario"),
            ("Planned order date", "20 September 2026"),
            ("Required arrival", "No later than 2 October 2026 at SG-DEMO-01"),
        ], s, color=GREEN),
        Spacer(1, 10),
        Paragraph("Buyer instruction", s["h1"]),
        Paragraph("The technical specification, budget, and arrival date above are hard constraints. The buyer will provide the ranking preference separately before comparison. Do not infer a preference from supplier marketing text.", s["body"]),
        Paragraph("This request is synthetic demonstration data. It creates no authority to contact a supplier, approve an award, or place an order.", s["small"]),
    ]
    _build_pdf(path, story, label="PR-MCU-V9-1250", color=BLUE)


def _generate_quote_b(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("BRIDGEWAY SEMICONDUCTORS", s["title"]),
        Paragraph("Commercial response sent by account team", s["subtitle"]),
        _field_table([
            ("From", "sales@synthetic-bridgeway.example"),
            ("To", "Electronics Procurement Team"),
            ("Subject", "Re: 1,250 pcs QW-MCU9-DEMO - quotation BW-260917"),
            ("Date", "17 September 2026"),
        ], s, color=NAVY),
        Spacer(1, 10),
        Paragraph("Hello Procurement Team,", s["email"]),
        Paragraph("We can offer new QQ Demo Components QW-MCU9-DEMO devices in QFN-32, revision R1. Material will be supplied in trays of 100 pieces. Orders must be placed in complete trays; our minimum order is 500 pieces.", s["email"]),
        Paragraph("Our price is <b>SGD 7.10 per piece</b>. For this request we would supply 1,300 pieces. Delivery to SG-DEMO-01 is <b>4 calendar days after the order date</b>. Freight is a confirmed <b>SGD 40.00</b>; no other mandatory fees apply.", s["email"]),
        Paragraph("Payment terms are Net 30. This quotation remains valid through 10 October 2026.", s["email"]),
        Spacer(1, 7),
        _line_items_table(
            ["Item", "Order qty", "Unit price", "Freight", "Confirmed total"],
            [["QW-MCU9-DEMO / QFN-32 / R1 / NEW", "1,300 pcs", "SGD 7.10", "SGD 40.00", "SGD 9,270.00"]],
            s,
            color=NAVY,
            widths=[65 * mm, 24 * mm, 27 * mm, 25 * mm, 32 * mm],
        ),
        Spacer(1, 12),
        Paragraph("Regards,<br/><b>Mira Tan</b><br/>Synthetic Bridgeway Semiconductors Ltd.<br/>Supplier ID: V9-SUP-B", s["body"]),
    ]
    _build_pdf(path, story, label="BW-260917", color=NAVY)


def _generate_quote_c(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("CEDAR ECONOMY ELECTRONICS", s["title"]),
        Paragraph("Volume quotation CE-2026-0917 | Supplier ID V9-SUP-C", s["subtitle"]),
        HRFlowable(width="100%", thickness=2, color=AMBER, spaceAfter=10),
        Paragraph("Volume pricing", s["h1"]),
        _line_items_table(
            ["Part", "Specification", "Price basis", "Pack"],
            [["QW-MCU9-DEMO", "QQ Demo Components / QFN-32 / R1 / NEW", "SGD 6.10 per piece", "500 pcs per tray"]],
            s,
            color=AMBER,
            widths=[35 * mm, 70 * mm, 38 * mm, 30 * mm],
        ),
        Spacer(1, 10),
        Paragraph("Minimum order is 1,000 pieces. Full trays are mandatory, so a requirement for 1,250 pieces is fulfilled as <b>1,500 pieces</b>. The confirmed goods subtotal is <b>SGD 9,150.00</b>.", s["body"]),
        Paragraph("Commercial charges and delivery terms continue on page 2. Do not treat an omitted charge as zero.", ParagraphStyle("Alert", parent=s["body"], textColor=RED, borderColor=RED, borderWidth=0.7, borderPadding=7, backColor=colors.HexColor("#FEF2F2"))),
        PageBreak(),
        Paragraph("COMMERCIAL TERMS", s["title"]),
        Paragraph("Quotation CE-2026-0917 | Continued", s["subtitle"]),
        _field_table([
            ("Delivery", "10 calendar days after order date; arrival at SG-DEMO-01"),
            ("Freight", "TO BE CONFIRMED by buyer before award"),
            ("Other mandatory fees", "None"),
            ("Payment terms", "Net 30"),
            ("Quote date", "17 September 2026"),
            ("Valid until", "10 October 2026"),
        ], s, color=AMBER),
        Spacer(1, 10),
        Paragraph("Freight is intentionally not included in the SGD 9,150.00 goods subtotal. The final landed total cannot be confirmed until the buyer records the freight amount.", s["body"]),
        Paragraph("Synthetic test quotation only. No real supplier or commercial offer is represented.", s["small"]),
    ]
    _build_pdf(path, story, label="CE-2026-0917", color=AMBER)


def _generate_quote_d(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("DELTA GLOBAL COMPONENTS", s["title"]),
        Paragraph("QUOTATION DG-0917-US | Currency: USD", ParagraphStyle("Usd", parent=s["subtitle"], textColor=RED)),
        _field_table([
            ("Supplier", "Synthetic Delta Global Components Inc. (V9-SUP-D)"),
            ("Ship to", "SG-DEMO-01"),
            ("Quote date", "September 17, 2026"),
            ("Expiration", "October 10, 2026"),
        ], s, color=RED),
        Spacer(1, 10),
        _line_items_table(
            ["Description", "Qty", "Unit price", "Line total"],
            [["QQ Demo Components QW-MCU9-DEMO<br/>QFN-32 / Rev R1 / New", "1,300 pcs", "USD 4.39", "USD 5,707.00"]],
            s,
            color=RED,
            widths=[85 * mm, 27 * mm, 30 * mm, 31 * mm],
        ),
        Spacer(1, 8),
        _field_table([
            ("Packing", "100 pieces per tray; order multiple 100 pieces; MOQ 500 pieces"),
            ("Shipping", "USD 15.00"),
            ("Native total", "USD 5,722.00"),
            ("Delivery", "5 calendar days after order date, arrival basis"),
            ("Payment", "Net 45"),
        ], s, color=RED),
        Spacer(1, 10),
        Paragraph("Currency notice", s["h1"]),
        Paragraph("This quotation does not provide an SGD conversion. Any cross-currency comparison must use the buyer's separately approved exchange-rate snapshot effective at the evaluation time.", s["body"]),
    ]
    _build_pdf(path, story, label="DG-0917-US", color=RED)


def _generate_quote_e(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("EVERGREEN PRECISION PARTS", s["title"]),
        Paragraph("REVISED QUOTATION EP-0917 | Revision 2 - CURRENT", ParagraphStyle("Current", parent=s["subtitle"], textColor=GREEN)),
        Paragraph("This revision supersedes the indicative Revision 1 price. Use only the current commercial table below.", ParagraphStyle("Notice", parent=s["body"], textColor=GREEN, borderColor=GREEN, borderWidth=0.7, borderPadding=7, backColor=colors.HexColor("#ECFDF5"))),
        Spacer(1, 8),
        _line_items_table(
            ["Current item", "Required qty", "Current unit price", "Goods total"],
            [["QW-MCU9-DEMO<br/>QFN-32 / R1 / NEW", "1,250 pcs", "SGD 7.32", "SGD 9,150.00"]],
            s,
            color=GREEN,
            widths=[72 * mm, 31 * mm, 37 * mm, 33 * mm],
        ),
        Spacer(1, 8),
        _field_table([
            ("Order rules", "MOQ 500 pieces; order multiple 50 pieces; individual-piece price basis"),
            ("Shipping", "SGD 50.00"),
            ("Confirmed landed total", "SGD 9,200.00"),
            ("Delivery", "7 calendar days after order date; arrival 27 September 2026"),
            ("Payment", "Net 30"),
            ("Validity", "17 September 2026 through 10 October 2026"),
        ], s, color=GREEN),
        PageBreak(),
        Paragraph("REVISION HISTORY", s["title"]),
        Paragraph("Reference only - superseded commercial information", s["subtitle"]),
        _line_items_table(
            ["Revision", "Status", "Unit price", "Comment"],
            [
                ["Revision 1", "SUPERSEDED", "SGD 7.40", "Indicative price; not valid for award"],
                ["Revision 2", "CURRENT", "SGD 7.32", "Approved quotation price"],
            ],
            s,
            color=SLATE,
            widths=[27 * mm, 35 * mm, 35 * mm, 76 * mm],
        ),
        Spacer(1, 10),
        Paragraph("The Revision 1 value remains visible only for audit history. It must not overwrite or conflict with the Revision 2 current price.", s["body"]),
        Paragraph("Supplier ID: V9-SUP-E | Synthetic Evergreen Precision Parts Ltd.", s["small"]),
    ]
    _build_pdf(path, story, label="EP-0917-R2", color=GREEN)


def _generate_quote_g(path: Path) -> None:
    s = _styles()
    story = [
        Paragraph("GREENLINE RAPID-STOCK", ParagraphStyle("GreenTitle", parent=s["title"], textColor=GREEN)),
        Paragraph("Local inventory quotation GL-FAST-0926 | Supplier ID V9-SUP-G", s["subtitle"]),
        Paragraph("STOCK RESERVED FOR 1,250 PIECES", ParagraphStyle("Badge", parent=s["center"], textColor=colors.white, backColor=GREEN, borderPadding=8, spaceAfter=12)),
        _line_items_table(
            ["Part", "Specification", "Quantity", "Unit price"],
            [["QW-MCU9-DEMO", "QFN-32 / Revision R1 / NEW", "1,250 pcs", "SGD 7.40"]],
            s,
            color=GREEN,
            widths=[40 * mm, 72 * mm, 30 * mm, 31 * mm],
        ),
        Spacer(1, 10),
        _field_table([
            ("Packaging", "Individual-piece basis; order multiple 50 pieces; MOQ 500 pieces"),
            ("Shipping", "FREE - included at SGD 0.00"),
            ("Confirmed total", "SGD 9,250.00"),
            ("Delivery commitment", "Arrival in 3 calendar days after order date (23 September 2026)"),
            ("Payment terms", "Net 60"),
            ("Validity", "Quote date 17 September 2026; valid until 10 October 2026"),
        ], s, color=GREEN),
        Spacer(1, 10),
        Table(
            [
                [Paragraph("Availability note", s["h1"])],
                [Paragraph(
                    "Fast delivery is supported by synthetic local inventory. Price and delivery are separate facts; "
                    "this document does not instruct the buyer which ranking preference to use.",
                    s["body"],
                )],
            ],
            colWidths=[173 * mm],
            style=TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
            ]),
        ),
    ]
    _build_pdf(path, story, label="GL-FAST-0926", color=GREEN)


def _policy_text() -> str:
    lines = [
        "# Fictional Electronics Preference and Evidence Policy",
        "",
        "This synthetic policy exists only for the QuoteWise demonstration and grants no purchasing authority.",
    ]
    for clause_id, title, _code, text, _parameters in POLICY_CLAUSES:
        lines.extend(("", f"## [{clause_id}] {title}", text))
    return "\n".join(lines) + "\n"


def _runtime_readme() -> str:
    return """# full_flow_demo2

这是一套基于 V9 扩展的偏好敏感型合成演示数据。它用于展示：同一采购需求和
同一批报价，会因为用户确认的采购取舍不同而产生不同、可验证的推荐结果。

## 文件特点

- 五份报价采用不同版式和表达方式，但均为可提取文本的 PDF。
- C 的初始报价故意缺少运费，用于演示高价值补问和推荐翻转阈值。
- D 使用 USD，必须引用独立的已批准汇率快照，不能从报价中猜测汇率。
- E 同时保留已废弃旧价格和当前价格，用于验证版本语义。
- 用户意图独立于采购需求文件，不能从供应商营销文字推断。

## 推荐流程

1. 在规则资源库上传并发布 `policy/electronics_tradeoff_policy.txt`。
2. 创建任务并上传 `requirement/procurement_requirement.pdf`。
3. 上传 B、C、D、E、G 五份报价，并使用 `manifest.json` 中的供应商编号。
4. 从 `buyer_intents/` 选择一段用户原话，先让 Agent 生成结构化偏好草稿，
   经用户确认后再运行确定性比较。
5. C 的运费问题出现时，使用操作员参考目录中的合成回答继续流程。

当前后端尚未完整实现最快交付、二级排序、成本容差和外币换算。因此本数据包既是
演示输入，也是这些目标能力的回归夹具；不要把目标参考答案描述为当前实测结果。

`evaluation/reference/full_flow_demo2/` 只能用于离线验收，不得挂载到运行时 Worker
或 Agent。所有采购需求、报价、Policy 和汇率记录都是虚构测试数据。
"""


def _reference_answers() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "dataset_id": "full_flow_demo2",
        "scenario_id": "MCU-V9-PERSONALIZED",
        "is_synthetic": True,
        "runtime_access": "FORBIDDEN",
        "requirement": REQUIREMENT,
        "fx_snapshot": {
            "pair": "USD/SGD",
            "rate": "1.61",
            "rounding": "ROUND_HALF_UP",
            "places": 2,
        },
        "operator_answers": [
            {
                "supplier_id": "V9-SUP-C",
                "issue_type": "CONFIRM_MISSING",
                "answer": {"answer_type": "CONFIRM_MISSING"},
            },
            {
                "supplier_id": "V9-SUP-C",
                "issue_type": "SHIPPING_AMOUNT",
                "answer": {"answer_type": "SHIPPING_AMOUNT", "amount": "50.00", "currency": "SGD"},
            },
        ],
        "quote_facts_after_answers": QUOTES,
        "intent_expectations": [
            {
                "intent_id": "cost_first",
                "normalized": {
                    "primary_objective": "LOWEST_CONFIRMED_TOTAL_COST",
                    "secondary_objective": None,
                    "tie_policy": "KEEP_JOINT_RECOMMENDATION",
                    "unknown_value_policy": "BLOCK",
                    "allow_approved_fx": True,
                },
                "recommended_supplier_ids": ["V9-SUP-C", "V9-SUP-E"],
                "explanation": "C and E tie at SGD 9,200.00; the buyer prohibited an automatic delivery tie-break.",
            },
            {
                "intent_id": "cost_then_fastest",
                "normalized": {
                    "primary_objective": "LOWEST_CONFIRMED_TOTAL_COST",
                    "secondary_objective": "FASTEST_CONFIRMED_DELIVERY",
                    "cost_tolerance": {"amount": "0.00", "currency": "SGD"},
                    "unknown_value_policy": "BLOCK",
                    "allow_approved_fx": True,
                },
                "recommended_supplier_ids": ["V9-SUP-E"],
                "explanation": "E breaks the C/E cost tie by arriving three days earlier.",
            },
            {
                "intent_id": "delivery_urgent",
                "normalized": {
                    "primary_objective": "FASTEST_CONFIRMED_DELIVERY",
                    "secondary_objective": "LOWEST_CONFIRMED_TOTAL_COST",
                    "unknown_value_policy": "BLOCK_IF_DECISION_RELEVANT",
                    "allow_approved_fx": True,
                },
                "recommended_supplier_ids": ["V9-SUP-G"],
                "explanation": "G is feasible and arrives first on 23 September 2026.",
            },
            {
                "intent_id": "balanced_tolerance",
                "normalized": {
                    "primary_objective": "WITHIN_COST_TOLERANCE_THEN_FASTEST",
                    "cost_tolerance": {"amount": "15.00", "currency": "SGD"},
                    "unknown_value_policy": "BLOCK",
                    "allow_approved_fx": True,
                },
                "recommended_supplier_ids": ["V9-SUP-D"],
                "explanation": "D is SGD 12.42 above the minimum and arrives before C and E.",
                "flip_threshold": "If the tolerance is below SGD 12.42, D exits and E becomes the fastest minimum-cost choice.",
            },
        ],
        "current_implementation_limits": [
            "FASTEST_CONFIRMED_DELIVERY is not implemented by the current deterministic backend.",
            "Secondary-preference tie-breaking is not implemented.",
            "Cost-tolerance ranking is not implemented.",
            "Approved FX snapshot conversion is not implemented in the comparison engine.",
            "Natural-language buyer preference confirmation is not implemented.",
        ],
    }


def generate() -> None:
    requirement_dir = OUT / "requirement"
    quotes_dir = OUT / "quotes"
    intent_dir = OUT / "buyer_intents"
    policy_dir = OUT / "policy"
    evidence_dir = OUT / "system_evidence"
    for directory in (requirement_dir, quotes_dir, intent_dir, policy_dir, evidence_dir, REFERENCE_OUT):
        directory.mkdir(parents=True, exist_ok=True)

    _generate_requirement(requirement_dir / "procurement_requirement.pdf")
    _write_json(requirement_dir / "confirmed_requirement.json", REQUIREMENT)
    _write_text(
        requirement_dir / "procurement_requirement.txt",
        "Purchase 1,250 pieces of QQ Demo Components QW-MCU9-DEMO, QFN-32, revision R1, NEW.\n"
        "No substitutes. Budget is SGD 10,000 including shipping and mandatory fees.\n"
        "Order on 20 September 2026 for arrival by 2 October 2026 at SG-DEMO-01.\n"
        "The buyer will confirm the ranking preference separately.\n",
    )

    generators = {"B": _generate_quote_b, "C": _generate_quote_c, "D": _generate_quote_d, "E": _generate_quote_e, "G": _generate_quote_g}
    quote_entries = []
    for alias, generator in generators.items():
        path = quotes_dir / f"supplier_{alias.lower()}_quote.pdf"
        generator(path)
        quote_entries.append({
            "supplier_alias": alias,
            "supplier_id": QUOTES[alias]["supplier_id"],
            "is_synthetic": True,
            "upload_file": path.relative_to(ROOT).as_posix(),
        })

    for intent in INTENTS:
        _write_text(intent_dir / f"{intent['intent_id']}.txt", str(intent["user_text"]) + "\n")
    _write_json(intent_dir / "intent_catalog.json", {"intents": INTENTS})

    _write_text(policy_dir / "electronics_tradeoff_policy.txt", _policy_text())
    _write_json(policy_dir / "upload_metadata.json", POLICY_METADATA)
    _write_json(
        policy_dir / "reviewed_clauses.json",
        {
            "clauses": [
                {"clause_id": clause_id, "title": title, "text": text, "control_code": code, "rule_parameters": parameters}
                for clause_id, title, code, text, parameters in POLICY_CLAUSES
            ]
        },
    )
    _write_json(
        evidence_dir / "fx_snapshot_usd_sgd.json",
        {
            "snapshot_id": "FX-DEMO2-USD-SGD-20260920",
            "base_currency": "USD",
            "quote_currency": "SGD",
            "rate": "1.61",
            "effective_at": "2026-09-20T00:00:00+08:00",
            "rounding": "ROUND_HALF_UP",
            "decimal_places": 2,
            "source": "Synthetic approved treasury snapshot",
            "is_synthetic": True,
        },
    )
    _write_text(OUT / "README.md", _runtime_readme())
    _write_json(REFERENCE_OUT / "reference_answers.json", _reference_answers())
    _write_text(
        REFERENCE_OUT / "README.md",
        "# full_flow_demo2 离线参考答案\n\n"
        "本目录包含用户意图标准化结果、人工回答和预期推荐，只用于离线验收。\n"
        "不得将本目录挂载给运行时 Worker 或 Agent，也不得用它代替真实提取结果。\n",
    )

    manifest_path = OUT / "manifest.json"
    runtime_files = sorted(path for path in OUT.rglob("*") if path.is_file() and path != manifest_path)
    manifest = {
        "schema_version": "1.0.0",
        "dataset_id": "full_flow_demo2",
        "scenario_id": "MCU-V9-PERSONALIZED",
        "is_synthetic": True,
        "runtime_safe": True,
        "requirement": {
            "upload_file": (requirement_dir / "procurement_requirement.pdf").relative_to(ROOT).as_posix(),
            "confirmed_values": (requirement_dir / "confirmed_requirement.json").relative_to(ROOT).as_posix(),
        },
        "quotes": quote_entries,
        "buyer_intents": {
            "catalog": (intent_dir / "intent_catalog.json").relative_to(ROOT).as_posix(),
            "files": [(intent_dir / f"{intent['intent_id']}.txt").relative_to(ROOT).as_posix() for intent in INTENTS],
        },
        "policy": {
            "upload_file": (policy_dir / "electronics_tradeoff_policy.txt").relative_to(ROOT).as_posix(),
            "metadata_file": (policy_dir / "upload_metadata.json").relative_to(ROOT).as_posix(),
            "reviewed_clauses_file": (policy_dir / "reviewed_clauses.json").relative_to(ROOT).as_posix(),
            "binding": {
                "policy_set_id": POLICY_METADATA["policy_set_id"],
                "policy_set_version": POLICY_METADATA["policy_set_version"],
                "policy_index_version": "SELECT_FROM_PUBLISHED_POLICY",
                "category": "Electronics",
                "region": "SG",
            },
        },
        "system_evidence": {
            "fx_snapshot": (evidence_dir / "fx_snapshot_usd_sgd.json").relative_to(ROOT).as_posix(),
        },
        "known_current_limits": [
            "fastest ranking",
            "secondary tie-break",
            "cost tolerance",
            "FX conversion",
            "natural-language preference confirmation",
        ],
        "files": [
            {"path": path.relative_to(ROOT).as_posix(), "size_bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in runtime_files
        ],
    }
    _write_json(manifest_path, manifest)
    print(f"Generated {len(runtime_files) + 1} runtime files in {OUT}")
    print(f"Generated operator reference in {REFERENCE_OUT}")


if __name__ == "__main__":
    generate()
