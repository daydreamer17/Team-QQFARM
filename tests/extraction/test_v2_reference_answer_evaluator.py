from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_v2_reference_answers import (
    DEFAULT_REFERENCE,
    _expected_contract,
    _values_equal,
    evaluate_v2_results,
)
from supplier_comparison.extraction.errors import ContractError


def _reference() -> dict:
    return json.loads(DEFAULT_REFERENCE.read_text(encoding="utf-8"))


def _result_payload(
    quote_dictionary,
    *,
    finished_at: str,
    start_event_status: str = "EXTRACTED",
    start_event_value: str | None = "ORDER_DATE",
    document_hash: str | None = None,
) -> dict:
    reference = _reference()
    answer = reference["answers"]["V2-A-CSV"]
    file_record = next(
        item for item in reference["files"] if item["answer_id"] == "V2-A-CSV"
    )
    candidates = []
    for definition in quote_dictionary.extractable_fields:
        field_name = definition.field_name
        expected_status, expected_origin = _expected_contract(answer, field_name)
        candidates.append(
            {
                "field_name": field_name,
                "normalized_value": answer["normalized_quote"][field_name],
                "validation_status": expected_status,
                "origin": expected_origin or "DOCUMENT",
            }
        )
    start_event = next(item for item in candidates if item["field_name"] == "start_event")
    start_event["validation_status"] = start_event_status
    start_event["normalized_value"] = start_event_value
    return {
        "dataset_version": "V2",
        "correction_state": "PRE_CORRECTION",
        "status": "PASSED",
        "batch": {
            "parsed_input": {
                "original_filename": Path(file_record["relative_path"]).name,
                "media_type": file_record["media_type"],
                "document_sha256": document_hash or file_record["sha256"],
            },
            "candidates": candidates,
            "run": {
                "prompt_version": "quote-extraction/test",
                "provider": "fixed-test",
                "model_id": "fixed-test",
                "finished_at": finished_at,
            },
        },
    }


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_v2_evaluator_selects_latest_result_per_input(quote_dictionary, tmp_path) -> None:
    _write_result(
        tmp_path / "old/result.json",
        _result_payload(
            quote_dictionary,
            finished_at="2026-09-10T01:00:00Z",
        ),
    )
    _write_result(
        tmp_path / "new/result.json",
        _result_payload(
            quote_dictionary,
            finished_at="2026-09-10T02:00:00Z",
            start_event_status="CONFLICT",
            start_event_value=None,
        ),
    )

    score = evaluate_v2_results(
        DEFAULT_REFERENCE,
        tmp_path,
        Path("data/contracts/quote_data_field.csv"),
    )

    assert score["summary"]["document_count"] == 1
    assert score["summary"]["passed_fields"] == 29
    assert score["documents"][0]["failed_fields"] == ["start_event"]


def test_v2_evaluator_uses_decimal_money_comparison() -> None:
    assert _values_equal("other_fees_amount", "0", "0.00")
    assert not _values_equal("other_fees_amount", "0.01", "0.00")


def test_v2_unresolved_field_is_extracted_with_null_value() -> None:
    answer = _reference()["answers"]["V2-C"]

    assert _expected_contract(answer, "start_event") == ("EXTRACTED", "DOCUMENT")
    assert answer["normalized_quote"]["start_event"] is None


def test_v2_evaluator_rejects_result_from_different_input_bytes(
    quote_dictionary, tmp_path
) -> None:
    _write_result(
        tmp_path / "result.json",
        _result_payload(
            quote_dictionary,
            finished_at="2026-09-10T02:00:00Z",
            document_hash="0" * 64,
        ),
    )

    with pytest.raises(ContractError) as raised:
        evaluate_v2_results(
            DEFAULT_REFERENCE,
            tmp_path,
            Path("data/contracts/quote_data_field.csv"),
        )

    assert raised.value.code == "v2_result_input_hash_mismatch"
