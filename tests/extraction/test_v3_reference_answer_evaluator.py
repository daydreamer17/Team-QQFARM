from __future__ import annotations

import json
from pathlib import Path

from scripts.evaluate_v3_reference_answers import evaluate_v3_results
from supplier_comparison.extraction.dictionary import QuoteDictionary


REPO_ROOT = Path(__file__).resolve().parents[2]
REFERENCE = REPO_ROOT / "evaluation/reference/quote_V3/reference_answers.json"
DICTIONARY = REPO_ROOT / "data/contracts/quote_data_field.csv"


def _matching_candidates(answer: dict, dictionary: QuoteDictionary) -> list[dict]:
    candidates = []
    missing = answer["field_expectation"].get("missing_fields", {})
    for definition in dictionary.extractable_fields:
        field_name = definition.field_name
        status = missing.get(field_name, {}).get("validation_status", "EXTRACTED")
        value = answer["normalized_quote"][field_name]
        candidates.append(
            {
                "field_name": field_name,
                "normalized_value": value,
                "validation_status": status,
            }
        )
    return candidates


def test_v3_evaluator_separates_document_and_conditional_field_rates(tmp_path: Path) -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    dictionary = QuoteDictionary.load(DICTIONARY)
    file_record = next(
        item for item in reference["files"] if item["answer_id"] == "V3-A" and item["media_type"] == "text/csv"
    )
    passed = {
        "dataset_version": "V3",
        "status": "PASSED",
        "batch": {
            "parsed_input": {
                "original_filename": Path(file_record["relative_path"]).name,
                "media_type": "text/csv",
                "document_sha256": file_record["sha256"],
            },
            "candidates": _matching_candidates(reference["answers"]["V3-A"], dictionary),
            "run": {"finished_at": "2026-09-10T10:00:00Z", "prompt_version": "test/1"},
        },
    }
    failed_record = next(
        item for item in reference["files"] if item["answer_id"] == "V3-B" and item["media_type"] == "text/csv"
    )
    failed = {
        "dataset_version": "V3",
        "input_format": "csv",
        "input": str(REPO_ROOT / failed_record["relative_path"]),
        "status": "FAILED",
        "finished_at": "2026-09-10T10:00:00Z",
        "error": {"code": "source_semantic_mismatch"},
    }
    (tmp_path / "passed.json").write_text(json.dumps(passed), encoding="utf-8")
    (tmp_path / "failed.json").write_text(json.dumps(failed), encoding="utf-8")

    score = evaluate_v3_results(REFERENCE, tmp_path, DICTIONARY)

    assert score["summary"] == {
        "expected_documents": 10,
        "successful_documents": 1,
        "failed_documents": 1,
        "missing_documents": 8,
        "document_success_rate": "0.1000",
        "prompt_versions": ["test/1"],
        "mixed_prompt_versions": False,
        "passed_fields": 30,
        "scorable_fields": 30,
        "conditional_field_match_rate": "1.0000",
        "expected_fields": 300,
        "conservative_field_coverage_rate": "0.1000",
    }
