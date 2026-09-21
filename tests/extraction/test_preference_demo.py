from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from supplier_comparison.extraction.csv_parser import FROZEN_CSV_COLUMNS


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/generated/inputs/development/preference_demo"


def test_preference_demo_manifest_and_quote_contract_are_self_consistent() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "preference_demo"
    assert manifest["is_synthetic"] is True
    assert manifest["planned_order_date"] == "2026-10-10"
    for relative_path, expected_sha256 in manifest["files"].items():
        path = DATASET / relative_path
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256

    expected = {
        "SUP-024": ("7.10", "8", "Net 30 after invoice"),
        "SUP-022": ("6.90", "7", "Net 60 after invoice"),
        "SUP-023": ("6.80", "6", "Net 45 after invoice"),
        "SUP-029": ("6.50", "9", "Net 15 after invoice"),
    }
    rows: dict[str, dict[str, str]] = {}
    for path in sorted((DATASET / "quotes").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == list(FROZEN_CSV_COLUMNS)
            loaded = list(reader)
        assert len(loaded) == 1
        rows[loaded[0]["supplier_id"]] = loaded[0]

    assert set(rows) == set(expected)
    for supplier_id, (unit_price, lead_days, payment_terms) in expected.items():
        row = rows[supplier_id]
        assert (row["unit_price"], row["lead_time_days"], row["payment_terms"]) == (
            unit_price,
            lead_days,
            payment_terms,
        )
        assert row["manufacturer_part_number"] == "QW-MCU9-DEMO"
        assert row["tax_mode"] == "EXCLUDED"
        assert row["is_synthetic"] == "true"


def test_preference_demo_requirement_exposes_v2_primary_and_secondary_fields() -> None:
    requirement = json.loads(
        (DATASET / "requirement/confirmed_requirement.json").read_text(encoding="utf-8")
    )
    assert requirement["ranking_preference"] == "LOWEST_CONFIRMED_TOTAL_COST"
    assert requirement["secondary_preference"] is None
    assert requirement["manufacturer_part_number"] == "QW-MCU9-DEMO"
    assert requirement["planned_order_date"] == "2026-10-10"
    assert requirement["delivery_deadline"] == "2026-10-20"
    source_text = (
        DATASET / "requirement/procurement_requirement.txt"
    ).read_text(encoding="utf-8")
    assert "Condition: NEW" in source_text
    assert "Budget includes shipping: yes" in source_text
    assert "Other fees must be confirmed: yes" in source_text
    assert "Primary ranking preference: LOWEST_CONFIRMED_TOTAL_COST" in source_text
    assert "Secondary ranking preference: none" in source_text
