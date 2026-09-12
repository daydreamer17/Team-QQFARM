from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.evaluate_v7_model_results import _score_case, evaluate
from scripts.freeze_v7_stage6 import (
    _validate_score,
    _validate_v7_2_manifest,
    canonical_json_value,
)
from scripts.run_v7_holdout_once import create_one_time_marker
from scripts import run_v7_model_evaluation
from scripts.run_v7_model_evaluation import _run_jobs_with_independent_budgets
from scripts.run_v7_parser_ocr import load_v7_jobs
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_REFERENCE = (
    ROOT / "evaluation/reference/quote_V7/development/reference_answers.json"
)
V7_2_MANIFEST = ROOT / "data/generated/manifests/quote_V7_2_manifest.json"
V7_2_COMMITMENT = (
    ROOT / "evaluation/reference/quote_V7_2/holdout_commitment.json"
)
DICTIONARY = ROOT / "data/contracts/quote_data_field.csv"


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_v7_model_runner_keeps_holdout_closed_before_freeze(tmp_path: Path) -> None:
    with pytest.raises(ContractError) as caught:
        load_v7_jobs(tmp_path / "missing.json", "holdout", repo_root=tmp_path)

    assert caught.value.code == "v7_holdout_run_blocked"


def test_v7_model_scorer_keeps_missing_documents_in_denominator(
    tmp_path: Path,
) -> None:
    score = evaluate(DEVELOPMENT_REFERENCE, tmp_path)

    assert score["summary"]["expected_documents"] == 8
    assert score["summary"]["scored_documents"] == 0
    assert score["summary"]["expected_fields"] == 240
    assert score["summary"]["passed_fields"] == 0


def test_v7_model_scorer_rejects_runtime_that_loaded_answers(
    tmp_path: Path,
) -> None:
    reference = json.loads(DEVELOPMENT_REFERENCE.read_text(encoding="utf-8"))
    case = reference["cases"][0]
    _write_json(
        tmp_path / "v7-dev-01_pre_correction.json",
        {
            "result_kind": "V7_EXTRACTION_EVALUATION",
            "input": case["input_file"],
            "reference_answers_loaded": True,
        },
    )

    with pytest.raises(ContractError) as caught:
        evaluate(DEVELOPMENT_REFERENCE, tmp_path)

    assert caught.value.code == "v7_runtime_reference_isolation_invalid"


def test_v7_2_model_scorer_rejects_wrong_candidate_version(
    tmp_path: Path,
) -> None:
    reference = json.loads(DEVELOPMENT_REFERENCE.read_text(encoding="utf-8"))
    case = reference["cases"][0]
    _write_json(
        tmp_path / "v7-dev-01_pre_correction.json",
        {
            "result_kind": "V7_EXTRACTION_EVALUATION",
            "input": case["input_file"],
            "reference_answers_loaded": False,
            "candidate_version": "V7.1",
        },
    )

    with pytest.raises(ContractError) as caught:
        evaluate(DEVELOPMENT_REFERENCE, tmp_path)

    assert caught.value.code == "v7_candidate_version_mismatch"


def test_v7_model_scorer_accepts_structured_source_citations() -> None:
    digest = "a" * 64
    source_id = "src-test"
    case = {
        "case_id": "V7-DEV-TEST",
        "input_file": "sample.pdf",
        "input_sha256": digest,
        "field_expectations": {
            "manufacturer": {
                "validation_status": "EXTRACTED",
                "normalized_value": "Fictional Works",
                "origin": "DOCUMENT",
            }
        },
        "expected_review": {
            "review_status": "READY_FOR_DOWNSTREAM",
            "blocking_codes": [],
        },
    }
    result = {
        "status": "PASSED",
        "model_calls_used": 1,
        "batch": {
            "parsed_input": {
                "document_sha256": digest,
                "context": {"document_id": "DOC", "document_version": 1},
                "sources": [
                    {
                        "source_id": source_id,
                        "document_id": "DOC",
                        "document_version": 1,
                        "document_sha256": digest,
                        "page_number": 1,
                        "bbox": {"x0": 1, "top": 2, "x1": 3, "bottom": 4},
                    }
                ],
            },
            "candidates": [
                {
                    "field_name": "manufacturer",
                    "normalized_value": "Fictional Works",
                    "validation_status": "EXTRACTED",
                    "origin": "DOCUMENT",
                    "source_refs": [
                        {"source_id": source_id, "quoted_text": "Fictional Works"}
                    ],
                }
            ],
        },
        "review_envelope": {
            "review_status": "READY_FOR_DOWNSTREAM",
            "review": {
                "always_critical_fields": ["manufacturer"],
                "applicable_conditional_fields": [],
                "findings": [],
            },
        },
    }

    scored = _score_case(case, result, ("manufacturer",))

    assert scored["passed_fields"] == 1
    assert scored["legal_source_refs"] == 1
    assert scored["locatable_source_refs"] == 1


def test_v7_freeze_rejects_critical_silent_error() -> None:
    score = {
        "candidate_version": "V7.2",
        "split": "calibration",
        "summary": {
            "expected_documents": 6,
            "scored_documents": 6,
            "document_success_rate": "1.0000",
            "critical_silent_error_count": 1,
            "source_identity_rate": "1.0000",
            "source_location_rate": "1.0000",
            "conservative_field_accuracy": "0.9667",
        },
    }

    with pytest.raises(ContractError) as caught:
        _validate_score(score, "calibration", 6)

    assert caught.value.code == "v7_freeze_gate_failed"
    assert "critical_silent_errors" in caught.value.details["failed_checks"]


def test_v7_2_manifest_and_commitment_match_freeze_contract() -> None:
    manifest = json.loads(V7_2_MANIFEST.read_text(encoding="utf-8"))
    commitment = json.loads(V7_2_COMMITMENT.read_text(encoding="utf-8"))
    dictionary = QuoteDictionary.load(DICTIONARY)

    _validate_v7_2_manifest(manifest, commitment, dictionary, DICTIONARY)

    manifest["answer_fields_in_manifest"] = True
    with pytest.raises(ContractError) as caught:
        _validate_v7_2_manifest(manifest, commitment, dictionary, DICTIONARY)

    assert caught.value.code == "v7_2_freeze_manifest_invalid"
    assert "answer_fields_hidden" in caught.value.details["failed_checks"]


def test_v7_holdout_marker_is_exclusive(tmp_path: Path) -> None:
    freeze = tmp_path / "freeze.json"
    _write_json(freeze, {"status": "FROZEN"})
    output = tmp_path / "holdout"

    marker = create_one_time_marker(output, freeze)

    assert marker.is_file()
    with pytest.raises(ContractError) as caught:
        create_one_time_marker(output, freeze)
    assert caught.value.code == "v7_holdout_already_started"


def test_v7_freeze_runtime_config_uses_canonical_json_container_types() -> None:
    value = {
        "preprocessing_steps": ("PDFIUM_RENDER_RGB",),
        "nested": {"values": (1, 2)},
    }

    normalized = canonical_json_value(value)

    assert normalized == {
        "preprocessing_steps": ["PDFIUM_RENDER_RGB"],
        "nested": {"values": [1, 2]},
    }


def test_v7_2_runner_gives_each_document_an_independent_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jobs = tuple(
        SimpleNamespace(case_id=f"V72-DEV-{index:02d}") for index in range(1, 4)
    )
    attempts_by_case = {
        "V72-DEV-01": 8,
        "V72-DEV-02": 1,
        "V72-DEV-03": 2,
    }
    seen_budgets: list[Any] = []

    def fake_run_job(job: Any, **kwargs: Any) -> tuple[int, dict[str, object]]:
        budget = kwargs["budget"]
        seen_budgets.append(budget)
        for _ in range(attempts_by_case[job.case_id]):
            budget.consume()
        return 0, {
            "case_id": job.case_id,
            "status": "PASSED",
            "model_calls_used": budget.calls_used,
        }

    monkeypatch.setattr(run_v7_model_evaluation, "_run_job", fake_run_job)

    results, total_calls = _run_jobs_with_independent_budgets(
        jobs,  # type: ignore[arg-type]
        output_dir=tmp_path,
        parser=None,  # type: ignore[arg-type]
        dictionary=None,  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        config=None,  # type: ignore[arg-type]
        graph_run_prefix="graph_v7_2_development",
    )

    assert len(results) == 3
    assert total_calls == 11
    assert [budget.calls_used for budget in seen_budgets] == [8, 1, 2]
    assert len({id(budget) for budget in seen_budgets}) == 3
    assert len({budget.graph_run_id for budget in seen_budgets}) == 3
    assert all(budget.max_calls == 8 for budget in seen_budgets)


def test_v7_2_document_failure_does_not_block_later_documents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    jobs = tuple(
        SimpleNamespace(case_id=f"V72-CAL-{index:02d}") for index in range(1, 4)
    )
    executed: list[str] = []

    def fake_run_job(job: Any, **kwargs: Any) -> tuple[int, dict[str, object]]:
        budget = kwargs["budget"]
        budget.consume()
        executed.append(job.case_id)
        failed = job.case_id == "V72-CAL-02"
        return int(failed), {
            "case_id": job.case_id,
            "status": "FAILED" if failed else "PASSED",
            "model_calls_used": budget.calls_used,
        }

    monkeypatch.setattr(run_v7_model_evaluation, "_run_job", fake_run_job)

    results, total_calls = _run_jobs_with_independent_budgets(
        jobs,  # type: ignore[arg-type]
        output_dir=tmp_path,
        parser=None,  # type: ignore[arg-type]
        dictionary=None,  # type: ignore[arg-type]
        adapter=None,  # type: ignore[arg-type]
        config=None,  # type: ignore[arg-type]
        graph_run_prefix="graph_v7_2_calibration",
    )

    assert executed == ["V72-CAL-01", "V72-CAL-02", "V72-CAL-03"]
    assert [code for code, _ in results] == [0, 1, 0]
    assert total_calls == 3
