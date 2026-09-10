from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from scripts.validate_extraction_reference import ReferenceField, validate_reference_set
from supplier_comparison.extraction.errors import ContractError

from .conftest import DATA_ROOT


V2_A_PDF = (
    DATA_ROOT
    / "generated"
    / "inputs"
    / "development"
    / "quote_V2"
    / "supplier_a_quote_v2.pdf"
)


def _reference_payload(quote_dictionary) -> dict:
    return {
        "schema_version": "1.0",
        "reference_set_id": "MCU-DEMO-001-V2-DRAFT",
        "documents": [
            {
                "schema_version": "1.0",
                "dataset_id": "quote_V2",
                "split": "development",
                "template_id": "quote-v2-supplier-a",
                "dictionary_version": quote_dictionary.version,
                "input_path": "data/generated/inputs/development/quote_V2/supplier_a_quote_v2.pdf",
                "input_sha256": hashlib.sha256(V2_A_PDF.read_bytes()).hexdigest(),
                "scenario_id": "MCU-DEMO-001",
                "quote_id": "QUOTE-MCU-DEMO-001-A",
                "quote_version": 2,
                "document_id": "DOC-MCU-DEMO-001-A-V2",
                "document_version": 2,
                "supplier_alias": "A",
                "is_synthetic": True,
                "review_status": "DRAFT",
                "reviewed_by": None,
                "fields": [
                    {
                        "field_name": definition.field_name,
                        "expected_status": "MISSING",
                        "expected_raw_values": [],
                        "expected_normalized_value": None,
                        "expected_unit": None,
                        "semantic_evidence": [],
                        "critical": False,
                        "notes": None,
                    }
                    for definition in quote_dictionary.extractable_fields
                ],
            }
        ],
    }


def _write_reference(tmp_path, payload: dict):
    path = tmp_path / "reference.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_reference_field_status_controls_value_and_evidence_shape() -> None:
    missing = ReferenceField(field_name="shipping_fee_amount", expected_status="MISSING")
    assert missing.expected_normalized_value is None

    with pytest.raises(ValidationError):
        ReferenceField(
            field_name="shipping_fee_amount",
            expected_status="MISSING",
            expected_normalized_value="0.00",
        )
    with pytest.raises(ValidationError):
        ReferenceField(
            field_name="shipping_fee_amount",
            expected_status="EXTRACTED",
            expected_normalized_value="0.00",
        )


def test_draft_reference_validates_but_cannot_be_used_for_scoring(
    quote_dictionary, tmp_path
) -> None:
    reference_path = _write_reference(tmp_path, _reference_payload(quote_dictionary))
    loaded = validate_reference_set(
        reference_path,
        DATA_ROOT / "contracts" / "quote_data_field.csv",
    )
    assert loaded.documents[0].review_status == "DRAFT"

    with pytest.raises(ContractError) as raised:
        validate_reference_set(
            reference_path,
            DATA_ROOT / "contracts" / "quote_data_field.csv",
            require_approved=True,
        )
    assert raised.value.code == "reference_not_approved"


def test_reference_requires_the_complete_dictionary_field_set(quote_dictionary, tmp_path) -> None:
    payload = _reference_payload(quote_dictionary)
    payload["documents"][0]["fields"].pop()

    with pytest.raises(ContractError) as raised:
        validate_reference_set(
            _write_reference(tmp_path, payload),
            DATA_ROOT / "contracts" / "quote_data_field.csv",
        )
    assert raised.value.code == "reference_field_set_mismatch"


def test_reference_is_bound_to_the_exact_input_hash(quote_dictionary, tmp_path) -> None:
    payload = _reference_payload(quote_dictionary)
    payload["documents"][0]["input_sha256"] = "0" * 64

    with pytest.raises(ContractError) as raised:
        validate_reference_set(
            _write_reference(tmp_path, payload),
            DATA_ROOT / "contracts" / "quote_data_field.csv",
        )
    assert raised.value.code == "reference_input_hash_mismatch"
