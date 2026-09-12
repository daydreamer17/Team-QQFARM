#!/usr/bin/env python3
"""Offline-score V7 model outputs against an isolated open reference set."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = REPO_ROOT / "data/contracts/quote_data_field.csv"
EXPECTED_CANDIDATE_VERSION = "V7.2"
MONEY_FIELDS = frozenset({"unit_price", "shipping_fee_amount", "other_fees_amount"})
INTEGER_FIELDS = frozenset(
    {"price_basis_quantity", "units_per_pack", "order_multiple_units", "moq_quantity", "lead_time_days"}
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("v7_evaluation_json_invalid", "V7 evaluation JSON is unreadable", path=str(path)) from exc
    if not isinstance(value, dict):
        raise ContractError("v7_evaluation_json_invalid", "V7 evaluation JSON must be an object", path=str(path))
    return value


def _canonical_input_path(value: str) -> str:
    return value.replace("\\", "/")


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001")))


def _equal(field_name: str, actual: object, expected: object) -> bool:
    if actual is None or expected is None:
        return actual is expected
    if field_name in MONEY_FIELDS:
        try:
            return Decimal(str(actual)) == Decimal(str(expected))
        except InvalidOperation:
            return False
    if field_name in INTEGER_FIELDS:
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return False
    return actual == expected


def _discover_results(root: Path) -> dict[str, dict[str, Any]]:
    results = {}
    for path in sorted(root.rglob("*_pre_correction.json")):
        value = _load_json(path)
        if value.get("result_kind") != "V7_EXTRACTION_EVALUATION":
            continue
        input_file = value.get("input")
        if isinstance(input_file, str):
            value["_result_path"] = str(path)
            results[_canonical_input_path(input_file)] = value
    return results


def _blocking_findings(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    review = envelope.get("review")
    if not isinstance(review, dict):
        return []
    return [
        item
        for item in review.get("findings", [])
        if isinstance(item, dict)
        and item.get("severity") == "BLOCKING"
        and item.get("resolved") is not True
    ]


def _score_case(case: dict[str, Any], result: dict[str, Any] | None, fields: tuple[str, ...]) -> dict[str, Any]:
    base = {"case_id": case["case_id"], "input_file": case["input_file"], "expected_fields": len(fields)}
    if result is None:
        return {**base, "status": "MISSING_RESULT", "passed_fields": 0, "failed_fields": list(fields), "critical_silent_errors": []}
    if result.get("status") != "PASSED" or not isinstance(result.get("batch"), dict):
        return {
            **base,
            "status": "FAILED_EXTRACTION",
            "passed_fields": 0,
            "failed_fields": list(fields),
            "critical_silent_errors": [],
            "model_calls_used": result.get("model_calls_used", 0),
            "error_code": result.get("error", {}).get("code"),
            "result_path": result.get("_result_path"),
        }
    batch = result["batch"]
    parsed = batch.get("parsed_input", {})
    if parsed.get("document_sha256") != case["input_sha256"]:
        raise ContractError("v7_result_input_hash_mismatch", "saved result used different input bytes", case_id=case["case_id"])
    sources = {item.get("source_id"): item for item in parsed.get("sources", []) if isinstance(item, dict)}
    candidates = {item.get("field_name"): item for item in batch.get("candidates", []) if isinstance(item, dict)}
    field_results = []
    document_source_refs = 0
    legal_source_refs = 0
    locatable_source_refs = 0
    for field_name in fields:
        expected = case["field_expectations"][field_name]
        candidate = candidates.get(field_name)
        status_match = candidate is not None and candidate.get("validation_status") == expected["validation_status"]
        value_match = candidate is not None and _equal(field_name, candidate.get("normalized_value"), expected.get("normalized_value"))
        origin_match = candidate is not None and candidate.get("origin") == expected.get("origin")
        if candidate is not None and candidate.get("origin") == "DOCUMENT" and candidate.get("validation_status") != "MISSING":
            refs = candidate.get("source_refs", [])
            document_source_refs += len(refs)
            for citation in refs:
                source_id = (
                    citation.get("source_id")
                    if isinstance(citation, dict)
                    else citation
                )
                source = sources.get(source_id)
                if source is None:
                    continue
                if (
                    source.get("document_id") == parsed.get("context", {}).get("document_id")
                    and source.get("document_version") == parsed.get("context", {}).get("document_version")
                    and source.get("document_sha256") == parsed.get("document_sha256")
                ):
                    legal_source_refs += 1
                bbox = source.get("bbox")
                if source.get("page_number") is not None and isinstance(bbox, dict) and all(bbox.get(key) is not None for key in ("x0", "top", "x1", "bottom")):
                    locatable_source_refs += 1
        field_results.append(
            {
                "field_name": field_name,
                "status_match": status_match,
                "value_match": value_match,
                "origin_match": origin_match,
                "passed": status_match and value_match,
            }
        )
    failed_fields = [item["field_name"] for item in field_results if not item["passed"]]
    envelope = result.get("review_envelope", {})
    blocking = _blocking_findings(envelope) if isinstance(envelope, dict) else []
    blocked_fields = {item.get("field_name") for item in blocking}
    review = envelope.get("review", {}) if isinstance(envelope, dict) else {}
    critical_fields = set(review.get("always_critical_fields", [])) | set(review.get("applicable_conditional_fields", []))
    critical_silent_errors = sorted(
        field_name for field_name in failed_fields if field_name in critical_fields and field_name not in blocked_fields
    )
    actual_codes = sorted({code for item in blocking for code in item.get("codes", [])})
    expected_review = case["expected_review"]
    expected_codes = sorted(expected_review.get("blocking_codes", []))
    return {
        **base,
        "status": "SCORED",
        "result_path": result.get("_result_path"),
        "passed_fields": len(fields) - len(failed_fields),
        "failed_fields": failed_fields,
        "field_match_rate": _rate(len(fields) - len(failed_fields), len(fields)),
        "origin_mismatch_fields": [item["field_name"] for item in field_results if not item["origin_match"]],
        "review_status": envelope.get("review_status"),
        "expected_review_status": expected_review["review_status"],
        "review_status_match": envelope.get("review_status") == expected_review["review_status"],
        "blocking_codes": actual_codes,
        "expected_blocking_codes": expected_codes,
        "expected_blocking_codes_present": set(expected_codes).issubset(actual_codes),
        "critical_silent_errors": critical_silent_errors,
        "document_source_refs": document_source_refs,
        "legal_source_refs": legal_source_refs,
        "locatable_source_refs": locatable_source_refs,
        "model_calls_used": result.get("model_calls_used", 0),
    }


def evaluate(
    reference_path: Path,
    results_root: Path,
    dictionary_path: Path = DICTIONARY_PATH,
    *,
    repo_root: Path = REPO_ROOT,
    case_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    reference = _load_json(reference_path)
    if reference.get("dataset_version") != "V7" or reference.get("split") not in {"development", "calibration"}:
        raise ContractError("v7_reference_invalid", "only open V7 Development/Calibration references are accepted")
    dictionary = QuoteDictionary.load(dictionary_path)
    if reference.get("dictionary_version") != dictionary.version or reference.get("dictionary_sha256") != _sha256(dictionary_path):
        raise ContractError("v7_reference_dictionary_mismatch", "V7 reference dictionary identity differs from active bytes")
    fields = tuple(reference.get("scored_fields", []))
    if fields != tuple(definition.field_name for definition in dictionary.extractable_fields):
        raise ContractError("v7_reference_fields_invalid", "V7 scored fields must match the 30 extractable fields")
    reference_cases = reference.get("cases", [])
    if not isinstance(reference_cases, list):
        raise ContractError("v7_reference_cases_invalid", "V7 reference cases must be a list")
    if case_ids:
        requested_case_ids = set(case_ids)
        available_case_ids = {
            case.get("case_id") for case in reference_cases if isinstance(case, dict)
        }
        unknown_case_ids = sorted(requested_case_ids - available_case_ids)
        if unknown_case_ids:
            raise ContractError(
                "v7_reference_case_unknown",
                "requested V7 case IDs are not present in the open reference",
                case_ids=unknown_case_ids,
            )
        selected_cases = [
            case for case in reference_cases if case.get("case_id") in requested_case_ids
        ]
    else:
        selected_cases = reference_cases
    for case in selected_cases:
        input_path = (repo_root / case["input_file"]).resolve()
        try:
            input_path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError(
                "v7_reference_input_invalid",
                "V7 reference input escapes the repository",
                case_id=case.get("case_id"),
            ) from exc
        if not input_path.is_file() or _sha256(input_path) != case.get("input_sha256"):
            raise ContractError(
                "v7_reference_input_hash_mismatch",
                "V7 reference input is missing or has different bytes",
                case_id=case.get("case_id"),
            )
    results = _discover_results(results_root)
    if any(item.get("reference_answers_loaded") is not False for item in results.values()):
        raise ContractError(
            "v7_runtime_reference_isolation_invalid",
            "V7 runtime result must attest that reference answers were not loaded",
        )
    candidate_versions = {
        item.get("candidate_version") for item in results.values()
    }
    if candidate_versions and candidate_versions != {EXPECTED_CANDIDATE_VERSION}:
        raise ContractError(
            "v7_candidate_version_mismatch",
            "V7.2 scoring accepts only V7.2 candidate outputs",
            candidate_versions=sorted(str(item) for item in candidate_versions),
        )
    documents = [
        _score_case(
            case,
            results.get(_canonical_input_path(case["input_file"])),
            fields,
        )
        for case in selected_cases
    ]
    passed_fields = sum(item["passed_fields"] for item in documents)
    expected_fields = len(documents) * len(fields)
    scored = sum(item["status"] == "SCORED" for item in documents)
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
        "dataset_version": "V7",
        "candidate_version": (
            EXPECTED_CANDIDATE_VERSION if candidate_versions else None
        ),
        "dataset_revision": reference.get("dataset_revision"),
        "development_patch_revision": reference.get("development_patch_revision"),
        "split": reference["split"],
        "selected_case_ids": [case["case_id"] for case in selected_cases],
        "reference_path": str(reference_path),
        "results_root": str(results_root),
        "summary": {
            "expected_documents": len(documents),
            "scored_documents": scored,
            "failed_or_missing_documents": len(documents) - scored,
            "document_success_rate": _rate(scored, len(documents)),
            "passed_fields": passed_fields,
            "expected_fields": expected_fields,
            "conservative_field_accuracy": _rate(passed_fields, expected_fields),
            "review_status_counts": dict(sorted(Counter(item.get("review_status") for item in documents if item.get("review_status")).items())),
            "review_status_matches": sum(item.get("review_status_match") is True for item in documents),
            "expected_blocking_code_checks_passed": sum(item.get("expected_blocking_codes_present") is True for item in documents),
            "critical_silent_error_count": len(critical_silent_errors),
            "legal_source_refs": legal_refs,
            "document_source_refs": source_refs,
            "source_identity_rate": _rate(legal_refs, source_refs),
            "locatable_source_refs": locatable_refs,
            "source_location_rate": _rate(locatable_refs, source_refs),
            "model_calls_used": sum(int(item.get("model_calls_used") or 0) for item in documents),
        },
        "critical_silent_errors": critical_silent_errors,
        "documents": documents,
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--reference", type=Path, required=True)
    cli.add_argument("--results-root", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--dictionary", type=Path, default=DICTIONARY_PATH)
    cli.add_argument("--case-id", action="append")
    args = cli.parse_args()
    try:
        score = evaluate(
            args.reference,
            args.results_root,
            args.dictionary,
            case_ids=tuple(args.case_id) if args.case_id else None,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    _write = json.dumps(score, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_write, encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": str(args.output), **score["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
