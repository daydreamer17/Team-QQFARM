"""Generate V9 preference-sensitive synthetic quote data."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import textwrap
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/generated/inputs/development/quote_V9"
MANIFEST = ROOT / "data/generated/manifests/quote_V9_manifest.json"
REFERENCE = ROOT / "evaluation/reference/quote_V9/reference_answers.json"
RATE = Decimal("1.61")
Q = Decimal("0.01")

QUOTE_COLUMNS = (
    "scenario_id", "quote_id", "quote_version", "document_id", "supplier_alias",
    "supplier_id", "supplier_name", "supplier_country", "category", "item",
    "manufacturer", "manufacturer_part_number", "package", "revision", "condition",
    "currency", "unit_price", "price_basis_quantity", "price_basis_unit",
    "packaging_type", "units_per_pack", "order_multiple_units", "moq_quantity",
    "moq_unit", "shipping_fee_status", "shipping_fee_amount", "other_fees_status",
    "other_fees_amount", "fees_complete", "tax_mode", "lead_time_days", "day_basis",
    "delivery_semantics", "start_event", "start_date", "delivery_location",
    "payment_terms", "quote_date", "valid_until", "source_po_id", "is_synthetic",
)
REQ_COLUMNS = (
    "manufacturer", "manufacturer_part_number", "package", "revision", "condition",
    "allow_substitutes", "base_unit", "required_quantity", "quantity_unit",
    "budget_amount", "currency", "includes_shipping", "tax_mode",
    "other_fees_required", "planned_order_date", "delivery_deadline",
    "delivery_location", "ranking_preference", "secondary_preference",
)

QUOTES = [
    dict(a="A", sid="V9-SUP-A", name="Synthetic Aurora Express Components Pte. Ltd.",
         country="Singapore", ccy="SGD", price="7.95", pack="piece", upp=1,
         mult=50, moq=500, ship="0.00", days=2, pay="Net 15", package="QFN-32",
         note="Fastest compliant offer; highest confirmed total among Group A."),
    dict(a="B", sid="V9-SUP-B", name="Synthetic Bridgeway Semiconductors Ltd.",
         country="Malaysia", ccy="SGD", price="7.10", pack="tray", upp=100,
         mult=100, moq=500, ship="40.00", days=4, pay="Net 30", package="QFN-32",
         note="Balanced cost and delivery."),
    dict(a="C", sid="V9-SUP-C", name="Synthetic Cedar Economy Electronics Ltd.",
         country="Singapore", ccy="SGD", price="6.10", pack="tray", upp=500,
         mult=500, moq=1000, ship="50.00", days=10, pay="Net 30", package="QFN-32",
         note="Low total cost but long delivery and 250 excess pieces."),
    dict(a="D", sid="V9-SUP-D", name="Synthetic Delta Global Components Inc.",
         country="United States", ccy="USD", price="4.39", pack="tray", upp=100,
         mult=100, moq=500, ship="15.00", days=5, pay="Net 45", package="QFN-32",
         note="USD offer; SGD 12.42 above the lowest total after fixed conversion."),
    dict(a="E", sid="V9-SUP-E", name="Synthetic Evergreen Precision Parts Ltd.",
         country="Singapore", ccy="SGD", price="7.32", pack="piece", upp=1,
         mult=50, moq=500, ship="50.00", days=7, pay="Net 30", package="QFN-32",
         note="Exactly tied with Supplier C on total, but arrives earlier."),
    dict(a="F", sid="V9-SUP-F", name="Synthetic Fjord Volume Devices AS.",
         country="Norway", ccy="SGD", price="5.95", pack="tray", upp=400,
         mult=400, moq=800, ship="0.00", days=6, pay="Net 30", package="QFN-32",
         note="Lowest SGD unit price, but the order multiple causes 350 excess pieces."),
    dict(a="G", sid="V9-SUP-G", name="Synthetic Greenline Components Pte. Ltd.",
         country="Singapore", ccy="SGD", price="7.40", pack="piece", upp=1,
         mult=50, moq=500, ship="0.00", days=3, pay="Net 60", package="QFN-32",
         note="Short delivery and strongest payment terms."),
    dict(a="H", sid="V9-SUP-H", name="Synthetic Harbor Discount Electronics Ltd.",
         country="Malaysia", ccy="SGD", price="7.20", pack="piece", upp=1,
         mult=50, moq=500, ship="0.00", days=14, pay="Net 30", package="QFN-32",
         note="Low cost, but arrival is later than the required deadline."),
    dict(a="I", sid="V9-SUP-I", name="Synthetic Ironwood Bulk Supply Co.",
         country="Singapore", ccy="SGD", price="5.90", pack="tray", upp=500,
         mult=500, moq=2000, ship="0.00", days=4, pay="Net 30", package="QFN-32",
         note="Low unit price, but MOQ forces an over-budget purchase."),
    dict(a="J", sid="V9-SUP-J", name="Synthetic Juniper Alternate Parts Ltd.",
         country="Singapore", ccy="SGD", price="6.00", pack="piece", upp=1,
         mult=50, moq=500, ship="100.00", days=3, pay="Net 30", package="QFN-48",
         note="Very low cost, but the package does not match the requirement."),
]

SUPPLEMENTAL_CSV_QUOTES = [
    dict(a="K", sid="V9-SUP-K", name="Synthetic Keystone Dollar Components Inc.",
         country="United States", ccy="USD", price="4.50", pack="piece", upp=1,
         mult=50, moq=500, ship="75.00", days=6, pay="Net 30", package="QFN-32",
         note="USD-only CSV fixture; SGD 9,177.00 after the proposed fixed conversion."),
]

# V9 generator implementation follows.

COMMON_REQ = {
    "manufacturer": "QQ Demo Components",
    "manufacturer_part_number": "QW-MCU9-DEMO",
    "package": "QFN-32", "revision": "R1", "condition": "NEW",
    "allow_substitutes": "false", "base_unit": "piece",
    "required_quantity": "1250", "quantity_unit": "piece",
    "budget_amount": "10000.00", "currency": "SGD",
    "includes_shipping": "true", "tax_mode": "NOT_APPLICABLE",
    "other_fees_required": "true", "planned_order_date": "2026-09-20",
    "delivery_deadline": "2026-10-02", "delivery_location": "SG-DEMO-01",
}
REQS = [
    ("cost", {**COMMON_REQ, "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
              "secondary_preference": ""},
     "Current MVP-compatible preference. Equal lowest totals remain jointly recommended."),
    ("cost_then_fastest",
     {**COMMON_REQ, "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
      "secondary_preference": "FASTEST_CONFIRMED_DELIVERY"},
     "Target redesigned behavior. The current backend rejects this tie-break preference."),
    ("fastest",
     {**COMMON_REQ, "ranking_preference": "FASTEST_CONFIRMED_DELIVERY",
      "secondary_preference": "LOWEST_CONFIRMED_TOTAL_COST"},
     "Target redesigned behavior. The current backend rejects this primary preference."),
]


def quote_row(q: dict[str, object]) -> dict[str, str]:
    """Map one scenario definition to FIXED-QUOTE-CSV-V1."""
    a = str(q["a"])
    shipping_amount = Decimal(str(q["ship"]))
    row = {column: "" for column in QUOTE_COLUMNS}
    row.update({
        "scenario_id": "MCU-V9-TRADEOFF",
        "quote_id": f"quote-v9-{a.lower()}",
        "quote_version": "1",
        "document_id": f"doc-v9-{a.lower()}",
        "supplier_alias": a,
        "supplier_id": str(q["sid"]),
        "supplier_name": str(q["name"]),
        "supplier_country": str(q["country"]),
        "category": "Electronic components",
        "item": "QW evaluation controller",
        "manufacturer": "QQ Demo Components",
        "manufacturer_part_number": "QW-MCU9-DEMO",
        "package": str(q["package"]),
        "revision": "R1",
        "condition": "NEW",
        "currency": str(q["ccy"]),
        "unit_price": str(q["price"]),
        "price_basis_quantity": "1",
        "price_basis_unit": "piece",
        "packaging_type": str(q["pack"]).upper(),
        "units_per_pack": str(q["upp"]),
        "order_multiple_units": str(q["mult"]),
        "moq_quantity": str(q["moq"]),
        "moq_unit": "piece",
        "shipping_fee_status": (
            "KNOWN_AMOUNT" if shipping_amount > Decimal("0") else "FREE"
        ),
        "shipping_fee_amount": str(q["ship"]),
        "other_fees_status": "NOT_APPLICABLE",
        "other_fees_amount": "0.00",
        "fees_complete": "true",
        "tax_mode": "NOT_APPLICABLE",
        "lead_time_days": str(q["days"]),
        "day_basis": "CALENDAR_DAYS",
        "delivery_semantics": "ARRIVAL",
        "start_event": "ORDER_DATE",
        "start_date": "2026-09-20",
        "delivery_location": "SG-DEMO-01",
        "payment_terms": str(q["pay"]),
        "quote_date": "2026-09-17",
        "valid_until": "2026-10-10",
        "is_synthetic": "true",
    })
    return row


def write_csv(path: Path, rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def wrap_text(value: str, width: int = 78) -> list[str]:
    return textwrap.wrap(value, width=width, break_long_words=False) or [""]


def draw_pdf(path: Path, title: str, subtitle: str,
             sections: list[tuple[str, list[tuple[str, str]]]], note: str) -> None:
    canvas = Canvas(str(path), pagesize=A4, invariant=1)
    page_w, page_h = A4
    navy = HexColor("#12211B")
    green = HexColor("#176F54")
    grey = HexColor("#66736D")
    line = HexColor("#D8E1DC")
    canvas.setFillColor(green)
    canvas.rect(0, page_h - 76, page_w, 76, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.setFont("Helvetica-Bold", 21)
    canvas.drawString(42, page_h - 47, title)
    canvas.setFont("Helvetica", 9.5)
    canvas.drawRightString(page_w - 42, page_h - 45, subtitle)
    y = page_h - 105
    for heading, pairs in sections:
        canvas.setFillColor(navy)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(42, y, heading.upper())
        y -= 12
        canvas.setStrokeColor(line)
        canvas.line(42, y, page_w - 42, y)
        y -= 18
        for label, value in pairs:
            canvas.setFillColor(grey)
            canvas.setFont("Helvetica", 8.5)
            canvas.drawString(48, y, label)
            canvas.setFillColor(navy)
            canvas.setFont("Helvetica-Bold", 9.5)
            canvas.drawString(190, y, value)
            y -= 19
        y -= 7
    canvas.setFillColor(HexColor("#EEF6F2"))
    canvas.roundRect(42, 52, page_w - 84, 62, 8, fill=1, stroke=0)
    canvas.setFillColor(green)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(54, 94, "NON-BINDING TEST DESCRIPTION")
    canvas.setFillColor(navy)
    canvas.setFont("Helvetica", 8.5)
    for index, text_line in enumerate(wrap_text(note + " Only the explicit fields above are contractual.", 95)[:3]):
        canvas.drawString(54, 78 - index * 12, text_line)
    canvas.setFillColor(grey)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawRightString(page_w - 42, 28, "Synthetic V9 fixture - not a commercial offer")
    canvas.save()


def quote_pdf(path: Path, q: dict[str, object]) -> None:
    sections = [
        ("Supplier and item", [
            ("Supplier", str(q["name"])), ("Supplier code", str(q["sid"])),
            ("Country", str(q["country"])), ("Manufacturer", "QQ Demo Components"),
            ("Part number", "QW-MCU9-DEMO"),
            ("Package / revision", f'{q["package"]} / R1'),
            ("Condition", "NEW; no substitute offered"),
        ]),
        ("Commercial terms", [
            ("Unit price", f'{q["ccy"]} {q["price"]} per piece'),
            ("Packaging", f'{str(q["pack"]).upper()}; {q["upp"]} piece per pack'),
            ("Order multiple", f'{q["mult"]} piece'), ("Minimum order", f'{q["moq"]} piece'),
            ("Shipping fee", f'{q["ccy"]} {q["ship"]}'),
            ("Other fees", f'{q["ccy"]} 0.00; fees complete'),
            ("Tax", "NOT_APPLICABLE"), ("Payment terms", str(q["pay"])),
        ]),
        ("Delivery and validity", [
            ("Lead time", f'{q["days"]} calendar days after order date'),
            ("Delivery meaning", "Arrival at SG-DEMO-01"),
            ("Quote date", "2026-09-17"), ("Valid until", "2026-10-10 end of day"),
        ]),
    ]
    draw_pdf(path, f'QUOTATION V9-{q["a"]}', str(q["sid"]), sections, str(q["note"]))


def requirement_pdf(path: Path, slug: str, row: dict[str, str], note: str) -> None:
    sections = [
        ("Required item", [
            ("Manufacturer", row["manufacturer"]),
            ("Part number", row["manufacturer_part_number"]),
            ("Package / revision", f'{row["package"]} / {row["revision"]}'),
            ("Condition", f'{row["condition"]}; substitutes not allowed'),
        ]),
        ("Quantity and budget", [
            ("Required quantity", f'{row["required_quantity"]} {row["quantity_unit"]}'),
            ("Budget", f'{row["currency"]} {row["budget_amount"]}'),
            ("Shipping", "Included in evaluated total"),
            ("Other fees", "Must be confirmed"),
            ("Tax", row["tax_mode"]),
        ]),
        ("Schedule and preference", [
            ("Planned order date", row["planned_order_date"]),
            ("Arrival deadline", row["delivery_deadline"]),
            ("Delivery location", row["delivery_location"]),
            ("Primary preference", row["ranking_preference"]),
            ("Secondary preference", row["secondary_preference"] or "NONE"),
        ]),
    ]
    draw_pdf(path, "PROCUREMENT REQUIREMENT", slug.upper(), sections, note)


def expected(q: dict[str, object]) -> dict[str, object]:
    demand = 1250
    multiple = int(q["mult"])
    ordered = math.ceil(max(demand, int(q["moq"])) / multiple) * multiple
    native_total = (Decimal(str(q["price"])) * ordered + Decimal(str(q["ship"]))).quantize(Q)
    sgd_total = (native_total * (RATE if q["ccy"] == "USD" else Decimal("1"))).quantize(
        Q, rounding=ROUND_HALF_UP
    )
    arrival = date(2026, 9, 20) + timedelta(days=int(q["days"]))
    reasons = []
    if q["package"] != "QFN-32":
        reasons.append("PACKAGE_MISMATCH")
    if arrival > date(2026, 10, 2):
        reasons.append("DELIVERY_AFTER_DEADLINE")
    if sgd_total > Decimal("10000.00"):
        reasons.append("OVER_BUDGET")
    return {
        "supplier": q["a"], "ordered_quantity": ordered,
        "excess_quantity": ordered - demand, "native_currency": q["ccy"],
        "native_total": f"{native_total:.2f}", "sgd_total": f"{sgd_total:.2f}",
        "arrival_date": arrival.isoformat(),
        "feasibility": "INFEASIBLE" if reasons else "FEASIBLE",
        "reasons": reasons,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    REFERENCE.parent.mkdir(parents=True, exist_ok=True)
    rows = [quote_row(quote) for quote in QUOTES]
    for quote, row in zip(QUOTES, rows, strict=True):
        suffix = str(quote["a"]).lower()
        write_csv(OUT / f"v9_supplier_{suffix}.csv", [row], QUOTE_COLUMNS)
        quote_pdf(OUT / f"v9_supplier_{suffix}.pdf", quote)
    supplemental_rows = [quote_row(quote) for quote in SUPPLEMENTAL_CSV_QUOTES]
    for quote, row in zip(SUPPLEMENTAL_CSV_QUOTES, supplemental_rows, strict=True):
        suffix = str(quote["a"]).lower()
        write_csv(OUT / f"v9_supplier_{suffix}_usd.csv", [row], QUOTE_COLUMNS)
    write_csv(OUT / "quotes_v9_all.csv", [*rows, *supplemental_rows], QUOTE_COLUMNS)
    for slug, requirement, note in REQS:
        write_csv(OUT / f"procurement_requirement_v9_{slug}.csv", [requirement], REQ_COLUMNS)
        requirement_pdf(OUT / f"procurement_requirement_v9_{slug}.pdf", slug, requirement, note)

    results = [expected(quote) for quote in [*QUOTES, *SUPPLEMENTAL_CSV_QUOTES]]
    reference = {
        "scenario_id": "MCU-V9-TRADEOFF",
        "clock": "2026-09-20",
        "fixed_fx": {"USD_SGD": "1.61", "rounding": "ROUND_HALF_UP", "places": 2},
        "quotes": results,
        "groups": {
            "preference_tradeoff": ["A", "B", "C", "D", "E"],
            "boundary_conditions": ["F", "G", "H", "I", "J"],
            "near_tie": ["B", "C", "D", "E", "G"],
        },
        "expected_target_outcomes": {
            "preference_tradeoff.lowest_total": ["C", "E"],
            "preference_tradeoff.cost_then_fastest": ["E"],
            "preference_tradeoff.fastest": ["A"],
            "near_tie.lowest_total": ["C", "E"],
            "near_tie.cost_then_fastest": ["E"],
            "near_tie.fastest": ["G"],
            "near_tie.within_sgd_15_then_fastest": ["D"],
            "boundary_conditions.lowest_feasible_total": ["G"],
        },
    }
    REFERENCE.write_text(json.dumps(reference, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    readme = """# Quote V9 preference-sensitive fixtures

This folder contains 10 synthetic, single-line PDF/CSV quote pairs, one supplemental
USD CSV quote, and three matching procurement requirements.
All files are test data, not real commercial offers.

## Supplemental USD CSV fixture

- `v9_supplier_k_usd.csv` is QFN-32 / R1 / NEW and totals USD 5,700.00.
- At 1 USD = 1.61 SGD with two-decimal ROUND_HALF_UP rounding, its comparison
  total is SGD 9,177.00.
- The current deterministic backend still reports `CURRENCY_MISMATCH`; this
  fixture verifies the planned fixed-rate conversion behavior.

## Recommended task groups (maximum five quotes per task)

- Preference trade-off: A, B, C, D, E.
- Boundary conditions: F, G, H, I, J.
- Near-tie sensitivity: B, C, D, E, G.

## Expected behavior

- Cost-only: C and E tie at SGD 9,200.00 and may be jointly recommended.
- Cost then fastest: E wins the C/E tie.
- Fastest: A wins Group 1; G wins the near-tie group.
- A tolerance of SGD 15 above the minimum plus fastest preference makes D win the near-tie group.
- H is late, I is over budget, and J has the wrong package.

## Current implementation limits

`procurement_requirement_v9_cost.csv` is compatible with the current deterministic preference value.
The other two requirements are target-regression fixtures: the current backend does not yet implement
FASTEST_CONFIRMED_DELIVERY or secondary-preference tie-breaking. Supplier D is also a target-regression
fixture because the current backend rejects currency mismatch; the intended demo conversion is fixed at
1 USD = 1.61 SGD, rounded to two decimals with ROUND_HALF_UP.

The independent expected answers are stored under `evaluation/reference/quote_V9/` and must not be
mounted into the runtime Agent.
"""
    (OUT / "README.md").write_text(readme, encoding="utf-8")

    generated = sorted(path for path in OUT.iterdir() if path.is_file())
    manifest = {
        "generator": "data/generate_v9_tradeoff_data.py",
        "scenario_id": "MCU-V9-TRADEOFF",
        "files": [{"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path)} for path in generated],
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(generated)} data files in {OUT}")


if __name__ == "__main__":
    main()
