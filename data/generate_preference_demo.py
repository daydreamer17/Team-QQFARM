"""Generate the isolated synthetic dataset used to demonstrate V2 preferences."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from supplier_comparison.extraction.csv_parser import FROZEN_CSV_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/generated/inputs/development/preference_demo"
SUPPLIERS = (
    ("SUP-024", "Sterling Components", "7.10", 8, "Net 30 after invoice", "A"),
    ("SUP-022", "Redwood Components", "6.90", 7, "Net 60 after invoice", "B"),
    ("SUP-023", "Schwarzwald Circuits", "6.80", 6, "Net 45 after invoice", "C"),
    ("SUP-029", "Great Wall Components", "6.50", 9, "Net 15 after invoice", "D"),
)


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="")


def generate(planned_order_date: date) -> None:
    deadline = planned_order_date + timedelta(days=10)
    quote_date = planned_order_date - timedelta(days=9)
    valid_until = planned_order_date + timedelta(days=21)
    requirement = {
        "manufacturer": "QQ Demo Components",
        "manufacturer_part_number": "QW-MCU9-DEMO",
        "package": "QFN-32",
        "revision": "R1",
        "condition": "NEW",
        "allow_substitutes": False,
        "base_unit": "piece",
        "required_quantity": 1000,
        "quantity_unit": "piece",
        "budget_amount": "8000.00",
        "currency": "SGD",
        "includes_shipping": True,
        "tax_mode": "EXCLUDED",
        "other_fees_required": True,
        "planned_order_date": planned_order_date.isoformat(),
        "delivery_deadline": deadline.isoformat(),
        "delivery_location": "SG-DEMO-01",
        "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
        "secondary_preference": None,
    }
    _write(OUT / "requirement/confirmed_requirement.json", json.dumps(requirement, ensure_ascii=False, indent=2) + "\n")
    _write(
        OUT / "requirement/procurement_requirement.txt",
        "SYNTHETIC PROCUREMENT REQUIREMENT\n"
        "Manufacturer: QQ Demo Components\nPart: QW-MCU9-DEMO\nPackage: QFN-32\nRevision: R1\n"
        "Condition: NEW\nBase unit: piece\nQuantity: 1000 pieces\nQuantity unit: piece\n"
        f"Budget: SGD 8000.00 before tax\nCurrency: SGD\nBudget includes shipping: yes\n"
        f"Other fees must be confirmed: yes\nPlanned order: {planned_order_date}\n"
        f"Required arrival: {deadline}\nDelivery location: SG-DEMO-01\nSubstitutes: not allowed\n"
        "Primary ranking preference: LOWEST_CONFIRMED_TOTAL_COST\nSecondary ranking preference: none\n",
    )
    for index, (supplier_id, name, unit_price, lead_days, payment, _grade) in enumerate(SUPPLIERS, 1):
        row = {column: "" for column in FROZEN_CSV_COLUMNS}
        row.update({
            "scenario_id": "PREFERENCE-DEMO-001", "quote_id": f"PREF-Q-{index:02d}",
            "quote_version": "1", "document_id": f"PREF-DOC-{index:02d}",
            "supplier_alias": f"{index:02d}", "supplier_id": supplier_id, "supplier_name": name,
            "supplier_country": "Synthetic", "category": "Electronic Components",
            "item": "Microcontroller MCU-9", "manufacturer": "QQ Demo Components",
            "manufacturer_part_number": "QW-MCU9-DEMO", "package": "QFN-32", "revision": "R1",
            "condition": "NEW", "currency": "SGD", "unit_price": unit_price,
            "price_basis_quantity": "1", "price_basis_unit": "piece", "packaging_type": "tray",
            "units_per_pack": "100", "order_multiple_units": "100", "moq_quantity": "100",
            "moq_unit": "piece", "shipping_fee_status": "FREE", "other_fees_status": "NOT_APPLICABLE",
            "other_fees_amount": "0.00", "fees_complete": "true", "tax_mode": "EXCLUDED",
            "lead_time_days": str(lead_days), "day_basis": "CALENDAR_DAYS",
            "delivery_semantics": "ARRIVAL", "start_event": "ORDER_DATE",
            "start_date": planned_order_date.isoformat(), "delivery_location": "SG-DEMO-01",
            "payment_terms": payment, "quote_date": quote_date.isoformat(),
            "valid_until": valid_until.isoformat(), "source_po_id": "", "is_synthetic": "true",
        })
        target = OUT / f"quotes/{supplier_id.lower()}_quote.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FROZEN_CSV_COLUMNS)
            writer.writeheader()
            writer.writerow(row)

    readme = f"""# preference_demo

用于演示 V2 六项排序指标及主/次指标，不替换硬约束回归数据。全部供应商、报价、物料和历史表现均为合成演示数据。

- 场景：`PREFERENCE-DEMO-001`
- 计划下单：`{planned_order_date}`
- 截止到货：`{deadline}`
- 历史数据：`synthetic-mcu9-supplier-performance / 2026-08-06-v1`

| 供应商 | 总成本 | 到货 | 付款 | 历史等级 |
|---|---:|---|---|---|
"""
    for supplier_id, name, price, days, payment, grade in SUPPLIERS:
        readme += f"| {name} ({supplier_id}) | SGD {float(price) * 1000:,.0f} | {planned_order_date + timedelta(days=days)} | {payment} | {grade} |\n"
    readme += """

## 人工演示流程

1. 在新建任务页上传 `requirement/procurement_requirement.txt`，或根据
   `requirement/confirmed_requirement.json` 核对表单；发布后的历史数据应为
   `2026-08-06-v1`。
2. 上传 `quotes/` 下四份 CSV，完成字段审核后运行比较。
3. 分别切换六个主指标，验证确定性结果：
   - 最低总成本：Great Wall Components（SUP-029）。
   - 最快到货：Schwarzwald Circuits（SUP-023）。
   - 最长账期：Redwood Components（SUP-022）。
   - 历史综合等级：Sterling Components（SUP-024）。
   - 历史准时率：Schwarzwald Circuits（SUP-023，11/11）。
   - 历史拒收订单行率：Sterling Components（SUP-024，0/55）。
4. 将主指标设为“最低已确认总成本”，成本容差设为 `400`，次指标设为
   “最快已确认到货”；容差组内应由次指标选出 SUP-023，`ranking_trace.secondary_applied`
   应为 `true`。
5. 打开“供应商信息”页，确认 scope、as-of、数据版本、样本量和“合成演示数据”
   标记均来自同一个冻结快照。

若需整体平移日期，运行：

    PYTHONPATH=src .venv/bin/python data/generate_preference_demo.py --planned-order-date YYYY-MM-DD
"""
    _write(OUT / "README.md", readme)

    files = sorted(path for path in OUT.rglob("*") if path.is_file() and path.name != "manifest.json")
    manifest = {
        "dataset_id": "preference_demo", "schema_version": "preference-demo/1.0.0",
        "generated_at": datetime(2026, 9, 21, tzinfo=timezone.utc).isoformat(),
        "is_synthetic": True, "planned_order_date": planned_order_date.isoformat(),
        "files": {str(path.relative_to(OUT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files},
    }
    _write(OUT / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--planned-order-date", type=date.fromisoformat, default=date(2026, 10, 10))
    generate(parser.parse_args().planned_order_date)
