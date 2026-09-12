from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluate_v7_parser_ocr import evaluate_v7_parser_ocr
from scripts.run_v7_parser_ocr import load_v7_jobs
from supplier_comparison.extraction.errors import ContractError


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_runner_blocks_holdout_before_reading_manifest(tmp_path: Path) -> None:
    with pytest.raises(ContractError) as caught:
        load_v7_jobs(tmp_path / "missing.json", "holdout", repo_root=tmp_path)

    assert caught.value.code == "v7_holdout_run_blocked"


def test_offline_scorer_blocks_holdout(tmp_path: Path) -> None:
    reference_path = tmp_path / "reference.json"
    run_path = tmp_path / "run.json"
    _write_json(
        reference_path,
        {"dataset_version": "V7", "split": "holdout", "cases": []},
    )
    _write_json(
        run_path,
        {
            "dataset_version": "V7",
            "split": "holdout",
            "reference_answers_loaded": False,
            "external_model_calls": 0,
            "cases": [],
        },
    )

    with pytest.raises(ContractError) as caught:
        evaluate_v7_parser_ocr(reference_path, run_path)

    assert caught.value.code == "v7_holdout_score_blocked"


def test_runner_validates_open_input_hash(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.pdf"
    input_path.write_bytes(b"%PDF-test")
    digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "manifest.json"
    _write_json(
        manifest_path,
        {
            "dataset_version": "V7",
            "cases": [
                {
                    "case_id": "V7-DEV-TEST",
                    "split": "development",
                    "input_file": "sample.pdf",
                    "input_sha256": digest,
                    "page_count": 1,
                    "reference_visibility": "AVAILABLE_TO_B",
                }
            ],
        },
    )

    _, jobs = load_v7_jobs(manifest_path, "development", repo_root=tmp_path)

    assert jobs[0].input_sha256 == digest


def test_offline_scorer_counts_missing_result_in_denominator(tmp_path: Path) -> None:
    reference_path = tmp_path / "reference.json"
    run_path = tmp_path / "run.json"
    _write_json(
        reference_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "cases": [
                {
                    "case_id": "V7-DEV-MISSING",
                    "input_file": "missing.pdf",
                    "input_sha256": "a" * 64,
                    "expected_page_routes": [{"page_number": 1, "route": "OCR"}],
                    "evidence_expectations": [],
                    "ocr_ground_truth": [],
                    "table_expectations": [],
                    "security_expectation": {"hidden_text_conflict_present": False},
                }
            ],
        },
    )
    _write_json(
        run_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "reference_answers_loaded": False,
            "external_model_calls": 0,
            "cases": [],
        },
    )

    score = evaluate_v7_parser_ocr(reference_path, run_path)

    assert score["summary"]["expected_documents"] == 1
    assert score["summary"]["parser_success_documents"] == 0
    assert score["summary"]["page_route_match_rate"] == "0.0000"
    assert score["summary"]["page_silent_omission_count"] == 1
    assert score["documents"][0]["status"] == "MISSING_RESULT"


def test_explicit_parser_failure_is_not_reported_as_silent_omission(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "reference.json"
    run_path = tmp_path / "run.json"
    digest = "f" * 64
    _write_json(
        reference_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "cases": [
                {
                    "case_id": "V7-DEV-FAILED",
                    "input_file": "failed.pdf",
                    "input_sha256": digest,
                    "expected_page_routes": [{"page_number": 1, "route": "OCR"}],
                    "evidence_expectations": [],
                    "ocr_ground_truth": [],
                    "table_expectations": [],
                    "security_expectation": {"hidden_text_conflict_present": False},
                }
            ],
        },
    )
    _write_json(
        run_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "reference_answers_loaded": False,
            "external_model_calls": 0,
            "cases": [
                {
                    "case_id": "V7-DEV-FAILED",
                    "input_sha256": digest,
                    "status": "FAILED",
                    "error": {"code": "pdf_ocr_engine_unavailable"},
                }
            ],
        },
    )

    score = evaluate_v7_parser_ocr(reference_path, run_path)

    assert score["summary"]["page_route_match_rate"] == "0.0000"
    assert score["summary"]["page_silent_omission_count"] == 0
    assert score["documents"][0]["error_code"] == "pdf_ocr_engine_unavailable"


def test_scorer_uses_location_and_text_not_reference_source_id(tmp_path: Path) -> None:
    reference_path = tmp_path / "reference.json"
    run_path = tmp_path / "run.json"
    digest = "b" * 64
    _write_json(
        reference_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "cases": [
                {
                    "case_id": "V7-DEV-TEST",
                    "input_file": "test.pdf",
                    "input_sha256": digest,
                    "expected_page_routes": [{"page_number": 1, "route": "OCR"}],
                    "evidence_expectations": [
                        {
                            "source_id": "A-HUMAN-LABEL",
                            "field_name": "manufacturer_part_number",
                            "page_number": 1,
                            "text_contains": "V7-QF-04-R0",
                        }
                    ],
                    "ocr_ground_truth": [
                        {"page_number": 1, "critical_tokens": ["V7-QF-04-R0"]}
                    ],
                    "table_expectations": [],
                    "security_expectation": {"hidden_text_conflict_present": False},
                }
            ],
        },
    )
    _write_json(
        run_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "reference_answers_loaded": False,
            "external_model_calls": 0,
            "cases": [
                {
                    "case_id": "V7-DEV-TEST",
                    "input_sha256": digest,
                    "status": "PASSED",
                    "parsed_input": {
                        "page_analyses": [
                            {"page_number": 1, "route": "OCR", "quality_reasons": []}
                        ],
                        "sources": [
                            {
                                "source_id": "src-runtime-generated",
                                "kind": "PDF_OCR_BLOCK",
                                "page_number": 1,
                                "block_id": "ocrblk-1",
                                "raw_text": "Maker part no.: V7-QF-04-R0",
                                "bbox": {"x0": 1, "top": 2, "x1": 10, "bottom": 12},
                                "ocr_metadata": {
                                    "engine": "tesseract",
                                    "engine_version": "5.5.1",
                                    "rendered_page_sha256": "c" * 64,
                                    "render_dpi": 300,
                                    "confidence": "0.9500",
                                },
                            }
                        ],
                        "context_groups": [],
                    },
                }
            ],
        },
    )

    score = evaluate_v7_parser_ocr(reference_path, run_path)

    document = score["documents"][0]
    assert document["evidence_matches"] == 1
    assert document["critical_tokens_matched"] == 1
    assert document["reference_source_ids_compared"] is False
    assert document["failures"] == []


def test_critical_token_zero_letter_o_confusion_is_not_silently_accepted(
    tmp_path: Path,
) -> None:
    reference_path = tmp_path / "reference.json"
    run_path = tmp_path / "run.json"
    digest = "d" * 64
    base_case = {
        "case_id": "V7-DEV-CONFUSABLE",
        "input_file": "test.pdf",
        "input_sha256": digest,
        "expected_page_routes": [{"page_number": 1, "route": "OCR"}],
        "evidence_expectations": [],
        "ocr_ground_truth": [
            {"page_number": 1, "critical_tokens": ["V7-QF-04-R0"]}
        ],
        "table_expectations": [],
        "security_expectation": {"hidden_text_conflict_present": False},
    }
    _write_json(reference_path, {"dataset_version": "V7", "split": "development", "cases": [base_case]})
    _write_json(
        run_path,
        {
            "dataset_version": "V7",
            "split": "development",
            "reference_answers_loaded": False,
            "external_model_calls": 0,
            "cases": [
                {
                    "case_id": base_case["case_id"],
                    "input_sha256": digest,
                    "status": "PASSED",
                    "parsed_input": {
                        "page_analyses": [{"page_number": 1, "route": "OCR", "quality_reasons": []}],
                        "sources": [
                            {
                                "source_id": "src-1",
                                "kind": "PDF_OCR_BLOCK",
                                "page_number": 1,
                                "block_id": "ocrblk-1",
                                "raw_text": "Maker part no.: V7-QF-04-RO",
                                "bbox": {"x0": 1, "top": 2, "x1": 10, "bottom": 12},
                                "ocr_metadata": {
                                    "engine": "tesseract",
                                    "engine_version": "5.5.1",
                                    "rendered_page_sha256": "e" * 64,
                                    "render_dpi": 300,
                                    "confidence": "0.9900",
                                },
                            }
                        ],
                        "context_groups": [],
                    },
                }
            ],
        },
    )

    score = evaluate_v7_parser_ocr(reference_path, run_path)

    assert score["summary"]["critical_token_exact_rate"] == "0.0000"
    assert score["documents"][0]["critical_token_failures"] == [
        {"page_number": 1, "token": "V7-QF-04-R0"}
    ]
    assert "OCR_CRITICAL_TOKEN_MISMATCH" in score["documents"][0]["failures"]
