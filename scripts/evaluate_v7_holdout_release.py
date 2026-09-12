#!/usr/bin/env python3
"""Offline-score a released V7.2 Holdout with the frozen scoring primitives."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate_v7_model_results import (
    DICTIONARY_PATH,
    EXPECTED_CANDIDATE_VERSION,
    _discover_results,
    _load_json,
    _rate,
    _score_case,
    _sha256,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


FROZEN_SCORER = REPO_ROOT / "scripts/evaluate_v7_model_results.py"


def _validate_release(
    reference_path: Path,
    results_root: Path,
    freeze_path: Path,
    expected_freeze_sha256: str,
) -> dict[str, str]:
    actual_freeze_sha256 = _sha256(freeze_path)
    if actual_freeze_sha256 != expected_freeze_sha256:
        raise ContractError(
            "v7_holdout_freeze_hash_mismatch",
            "holdout freeze bytes differ from the explicitly approved SHA-256",
            expected=expected_freeze_sha256,
            actual=actual_freeze_sha256,
        )
    freeze = _load_json(freeze_path)
    frozen_scorer_sha256 = freeze.get("components", {}).get(
        "scripts/evaluate_v7_model_results.py"
    )
    reference_sha256 = _sha256(reference_path)
    checks = {
        "freeze_status": freeze.get("status") == "FROZEN",
        "candidate_version": freeze.get("candidate_version")
        == EXPECTED_CANDIDATE_VERSION,
        "dataset_version": freeze.get("dataset", {}).get("dataset_version")
        == "V7.2",
        "reference_commitment": freeze.get("dataset", {}).get(
            "holdout_reference_commitment_sha256"
        )
        == reference_sha256,
        "frozen_scorer_unchanged": frozen_scorer_sha256 == _sha256(FROZEN_SCORER),
    }
    if not all(checks.values()):
        raise ContractError(
            "v7_holdout_release_invalid",
            "released reference or frozen scorer does not match the approved freeze",
            failed_checks=sorted(name for name, passed in checks.items() if not passed),
        )

    marker_path = results_root / "HOLDOUT_RUN_STARTED.json"
    summary_path = results_root / "holdout_run_summary.json"
    marker = _load_json(marker_path)
    summary = _load_json(summary_path)
    runtime_checks = {
        "marker_kind": marker.get("result_kind") == "V7_HOLDOUT_ONE_TIME_MARKER",
        "marker_freeze": marker.get("freeze_sha256") == actual_freeze_sha256,
        "marker_isolated": marker.get("reference_answers_loaded") is False,
        "summary_kind": summary.get("result_kind") == "V7_HOLDOUT_ONE_TIME_RUN",
        "summary_passed": summary.get("status") == "PASSED",
        "summary_freeze": summary.get("freeze_sha256") == actual_freeze_sha256,
        "summary_isolated": summary.get("reference_answers_loaded") is False,
        "summary_candidate": summary.get("candidate_version")
        == EXPECTED_CANDIDATE_VERSION,
    }
    if not all(runtime_checks.values()):
        raise ContractError(
            "v7_holdout_runtime_invalid",
            "saved runtime does not prove a completed isolated frozen Holdout run",
            failed_checks=sorted(
                name for name, passed in runtime_checks.items() if not passed
            ),
        )
    return {
        "freeze_path": str(freeze_path),
        "freeze_sha256": actual_freeze_sha256,
        "reference_sha256": reference_sha256,
        "frozen_scorer_sha256": frozen_scorer_sha256,
        "one_time_marker": str(marker_path),
    }


def evaluate_holdout(
    reference_path: Path,
    results_root: Path,
    freeze_path: Path,
    expected_freeze_sha256: str,
    dictionary_path: Path = DICTIONARY_PATH,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    release = _validate_release(
        reference_path,
        results_root,
        freeze_path,
        expected_freeze_sha256,
    )
    reference = _load_json(reference_path)
    if reference.get("dataset_version") != "V7.2" or reference.get("split") != "holdout":
        raise ContractError(
            "v7_holdout_reference_invalid",
            "released reference must identify the V7.2 Holdout split",
        )
    dictionary = QuoteDictionary.load(dictionary_path)
    if (
        reference.get("dictionary_version") != dictionary.version
        or reference.get("dictionary_sha256") != _sha256(dictionary_path)
    ):
        raise ContractError(
            "v7_reference_dictionary_mismatch",
            "V7.2 reference dictionary identity differs from active bytes",
        )
    fields = tuple(reference.get("scored_fields", []))
    if fields != tuple(
        definition.field_name for definition in dictionary.extractable_fields
    ):
        raise ContractError(
            "v7_reference_fields_invalid",
            "V7.2 Holdout fields must match the 30 extractable fields",
        )
    for case in reference.get("cases", []):
        input_path = (repo_root / case["input_file"]).resolve()
        try:
            input_path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError(
                "v7_reference_input_invalid",
                "V7.2 reference input escapes the repository",
                case_id=case.get("case_id"),
            ) from exc
        if not input_path.is_file() or _sha256(input_path) != case.get("input_sha256"):
            raise ContractError(
                "v7_reference_input_hash_mismatch",
                "V7.2 reference input is missing or has different bytes",
                case_id=case.get("case_id"),
            )

    results = _discover_results(results_root)
    if any(item.get("reference_answers_loaded") is not False for item in results.values()):
        raise ContractError(
            "v7_runtime_reference_isolation_invalid",
            "Holdout runtime result must attest that reference answers were not loaded",
        )
    candidate_versions = {item.get("candidate_version") for item in results.values()}
    if candidate_versions and candidate_versions != {EXPECTED_CANDIDATE_VERSION}:
        raise ContractError(
            "v7_candidate_version_mismatch",
            "V7.2 Holdout scoring accepts only V7.2 candidate outputs",
            candidate_versions=sorted(str(item) for item in candidate_versions),
        )

    documents = [
        _score_case(case, results.get(case["input_file"]), fields)
        for case in reference.get("cases", [])
    ]
    passed_fields = sum(item["passed_fields"] for item in documents)
    expected_fields = len(documents) * len(fields)
    scored_documents = sum(item["status"] == "SCORED" for item in documents)
    critical_silent_errors = [
        {"case_id": item["case_id"], "field_name": field_name}
        for item in documents
        for field_name in item.get("critical_silent_errors", [])
    ]
    source_refs = sum(item.get("document_source_refs", 0) for item in documents)
    legal_refs = sum(item.get("legal_source_refs", 0) for item in documents)
    locatable_refs = sum(item.get("locatable_source_refs", 0) for item in documents)
    return {
        "result_kind": "V7_EXTRACTION_REVIEW_SCORE",
        "scoring_status": "OFFLINE_PRE_CORRECTION",
        "dataset_version": "V7.2",
        "candidate_version": (
            EXPECTED_CANDIDATE_VERSION if candidate_versions else None
        ),
        "split": "holdout",
        "reference_path": str(reference_path),
        "results_root": str(results_root),
        "holdout_release": release,
        "summary": {
            "expected_documents": len(documents),
            "scored_documents": scored_documents,
            "failed_or_missing_documents": len(documents) - scored_documents,
            "document_success_rate": _rate(scored_documents, len(documents)),
            "passed_fields": passed_fields,
            "expected_fields": expected_fields,
            "conservative_field_accuracy": _rate(passed_fields, expected_fields),
            "review_status_counts": dict(
                sorted(
                    Counter(
                        item.get("review_status")
                        for item in documents
                        if item.get("review_status")
                    ).items()
                )
            ),
            "review_status_matches": sum(
                item.get("review_status_match") is True for item in documents
            ),
            "expected_blocking_code_checks_passed": sum(
                item.get("expected_blocking_codes_present") is True
                for item in documents
            ),
            "critical_silent_error_count": len(critical_silent_errors),
            "legal_source_refs": legal_refs,
            "document_source_refs": source_refs,
            "source_identity_rate": _rate(legal_refs, source_refs),
            "locatable_source_refs": locatable_refs,
            "source_location_rate": _rate(locatable_refs, source_refs),
            "model_calls_used": sum(
                int(item.get("model_calls_used") or 0) for item in documents
            ),
        },
        "critical_silent_errors": critical_silent_errors,
        "documents": documents,
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reference", type=Path, required=True)
    cli.add_argument("--results-root", type=Path, required=True)
    cli.add_argument("--freeze", type=Path, required=True)
    cli.add_argument("--expected-freeze-sha256", required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--dictionary", type=Path, default=DICTIONARY_PATH)
    args = cli.parse_args()
    try:
        score = evaluate_holdout(
            args.reference,
            args.results_root,
            args.freeze,
            args.expected_freeze_sha256,
            args.dictionary,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(score, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASSED", "output": str(args.output), **score["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
