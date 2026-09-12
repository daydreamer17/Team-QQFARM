from __future__ import annotations

import json

from scripts.migrate_v2_system_evidence import migrate_reference

from .conftest import DATA_ROOT, quote_path


def _field(reference, field_name: str):
    return next(item for item in reference.documents[0].fields if item.field_name == field_name)


def test_legacy_subset_becomes_a_complete_non_authoritative_v2_draft(tmp_path) -> None:
    legacy = {
        "scenario_id": "MCU-DEMO-001",
        "quote_id": "QUOTE-MCU-DEMO-001-B",
        "supplier_alias": "B",
        "field_records": [
            {
                "field_name": "supplier_name",
                "raw_value": "Schwarzwald Circuits",
                "normalized_value": "Schwarzwald Circuits",
                "unit": None,
                "validation_status": "VERIFIED",
                "source_refs": [{"evidence_text": "Schwarzwald Circuits"}],
            },
            {
                "field_name": "shipping_fee_status",
                "raw_value": None,
                "normalized_value": None,
                "unit": None,
                "validation_status": "MISSING",
                "source_refs": [],
            },
            {
                "field_name": "other_fees_status",
                "raw_value": "None",
                "normalized_value": "KNOWN_ZERO",
                "unit": None,
                "validation_status": "VERIFIED",
                "source_refs": [{"evidence_text": "None"}],
            },
        ],
    }
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

    reference = migrate_reference(
        legacy_path,
        quote_path("b", version=2, extension="csv"),
        DATA_ROOT / "contracts" / "quote_data_field.csv",
    )

    assert reference.documents[0].review_status == "DRAFT"
    assert len(reference.documents[0].fields) == 30
    assert _field(reference, "supplier_name").expected_status == "EXTRACTED"
    assert _field(reference, "shipping_fee_status").expected_status == "MISSING"
    assert _field(reference, "shipping_fee_amount").expected_status == "MISSING"
    assert _field(reference, "other_fees_status").expected_normalized_value == "NOT_APPLICABLE"
    assert _field(reference, "other_fees_amount").expected_normalized_value == "0.00"
    assert _field(reference, "packaging_type").expected_status is None
