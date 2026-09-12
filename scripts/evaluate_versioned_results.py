#!/usr/bin/env python3
"""Score V5/V6 saved extractions and review routing against isolated answers."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
DICTIONARY_PATH = REPO_ROOT / "data/contracts/quote_data_field.csv"
MONEY_FIELDS = frozenset({"unit_price", "shipping_fee_amount", "other_fees_amount"})
INTEGER_FIELDS = frozenset(
    {
        "price_basis_quantity",
        "units_per_pack",
        "order_multiple_units",
        "moq_quantity",
        "lead_time_days",
    }
)


@dataclass(frozen=True, slots=True)
class ReferenceCase:
    case_id: str
    input_path: str
    input_sha256: str
    media_type: str
    answer: dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "evaluation_json_invalid",
            "evaluation input is not readable JSON",
            path=str(path),
        ) from exc
    if not isinstance(payload, dict):
        raise ContractError(
            "evaluation_json_invalid",
            "evaluation input must be a JSON object",
            path=str(path),
        )
    return payload


def _values_equal(field_name: str, actual: object, expected: object) -> bool:
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


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))
    )


def _reference_cases(reference: dict[str, Any]) -> tuple[ReferenceCase, ...]:
    if isinstance(reference.get("answers"), dict):
        cases = []
        answers = reference["answers"]
        for item in reference.get("files", []):
            answer_id = item["answer_id"]
            cases.append(
                ReferenceCase(
                    case_id=f"{answer_id}-{Path(item['relative_path']).suffix[1:].upper()}",
                    input_path=item["relative_path"],
                    input_sha256=item["sha256"],
                    media_type=item["media_type"],
                    answer=answers[answer_id],
                )
            )
        return tuple(cases)
    if isinstance(reference.get("answers"), list):
        return tuple(
            ReferenceCase(
                case_id=item["case_id"],
                input_path=item["input_file"],
                input_sha256=item["input_sha256"],
                media_type=item["media_type"],
                answer=item,
            )
            for item in reference["answers"]
        )
    raise ContractError(
        "evaluation_reference_shape_invalid",
        "reference answers must be a V5 mapping or V6 list",
    )


def validate_reference(
    reference_path: Path,
    dictionary_path: Path = DICTIONARY_PATH,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], QuoteDictionary, tuple[ReferenceCase, ...]]:
    reference = _load_json(reference_path)
    if reference.get("reference_kind") not in {
        "DEVELOPMENT_REFERENCE_ANSWER",
        "OFFLINE_HIDDEN_ANSWER",
    }:
        raise ContractError(
            "evaluation_reference_kind_invalid",
            "only isolated V5/V6 reference answers are supported",
        )
    if reference.get("runtime_access") not in {"DENY", "FORBIDDEN"}:
        raise ContractError(
            "evaluation_reference_isolation_invalid",
            "reference answers must be denied to runtime extraction",
        )
    dictionary = QuoteDictionary.load(dictionary_path)
    metadata = reference.get("dictionary", {})
    if (
        metadata.get("version") != dictionary.version
        or metadata.get("sha256") != _sha256(dictionary_path)
    ):
        raise ContractError(
            "evaluation_reference_dictionary_mismatch",
            "reference dictionary version/hash does not match active bytes",
            expected_version=dictionary.version,
            expected_sha256=_sha256(dictionary_path),
            actual=metadata,
        )
    field_order = tuple(reference.get("field_order", ()))
    if field_order != tuple(dictionary.fields):
        raise ContractError(
            "evaluation_reference_field_order_mismatch",
            "reference field order must match the 31-field dictionary",
        )
    cases = _reference_cases(reference)
    for case in cases:
        input_path = (repo_root / case.input_path).resolve()
        try:
            input_path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError(
                "evaluation_reference_input_invalid",
                "reference input escapes the repository",
                input_path=case.input_path,
            ) from exc
        if not input_path.is_file() or _sha256(input_path) != case.input_sha256:
            raise ContractError(
                "evaluation_reference_input_hash_mismatch",
                "reference input is missing or has different bytes",
                input_path=case.input_path,
            )
    return reference, dictionary, cases


def _discover_results(results_root: Path) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for path in sorted(results_root.rglob("*.json")):
        payload = _load_json(path)
        input_path = payload.get("input")
        if not isinstance(input_path, str):
            continue
        payload["_result_path"] = str(path)
        selected[input_path] = payload
    return selected


def _expected_status(answer: dict[str, Any], field_name: str) -> tuple[str, str | None]:
    direct = answer.get("field_expectations")
    if isinstance(direct, dict):
        item = direct[field_name]
        return item["validation_status"], item.get("origin")
    grouped = answer["field_expectation"]
    status = grouped["default"]["validation_status"]
    origin = grouped["default"].get("origin")
    for key in ("missing_fields", "unresolved_fields"):
        if field_name in grouped.get(key, {}):
            override = grouped[key][field_name]
            return override["validation_status"], override.get("origin")
    return status, origin


def _score_case(
    case: ReferenceCase,
    result: dict[str, Any] | None,
    field_names: tuple[str, ...],
) -> dict[str, Any]:
    base = {
        "case_id": case.case_id,
        "input_path": case.input_path,
        "media_type": case.media_type,
        "total_fields": len(field_names),
    }
    if result is None:
        return {
            **base,
            "status": "MISSING_RESULT",
            "passed_fields": 0,
            "failed_fields": list(field_names),
            "critical_silent_errors": [],
        }
    if result.get("status") != "PASSED" or not isinstance(result.get("batch"), dict):
        return {
            **base,
            "status": "FAILED_EXTRACTION",
            "error_code": result.get("error", {}).get("code"),
            "result_path": result.get("_result_path"),
            "passed_fields": 0,
            "failed_fields": list(field_names),
            "critical_silent_errors": [],
            "model_calls_used": result.get("model_calls_used", 0),
        }
    batch = result["batch"]
    if batch.get("parsed_input", {}).get("document_sha256") != case.input_sha256:
        raise ContractError(
            "evaluation_result_input_hash_mismatch",
            "saved extraction used input bytes different from the reference",
            input_path=case.input_path,
        )
    actual = {
        candidate.get("field_name"): candidate
        for candidate in batch.get("candidates", [])
        if isinstance(candidate, dict)
    }
    fields = []
    for field_name in field_names:
        expected_status, expected_origin = _expected_status(case.answer, field_name)
        candidate = actual.get(field_name)
        status_match = (
            candidate is not None
            and candidate.get("validation_status") == expected_status
        )
        value_match = candidate is not None and _values_equal(
            field_name,
            candidate.get("normalized_value"),
            case.answer["normalized_quote"][field_name],
        )
        origin_match = candidate is not None and candidate.get("origin") == expected_origin
        fields.append(
            {
                "field_name": field_name,
                "status_match": status_match,
                "value_match": value_match,
                "origin_match": origin_match,
                "passed": status_match and value_match,
            }
        )
    failed_fields = [item["field_name"] for item in fields if not item["passed"]]
    envelope = result.get("review_envelope", {})
    review = envelope.get("review", {}) if isinstance(envelope, dict) else {}
    critical_fields = set(review.get("always_critical_fields", ())) | set(
        review.get("applicable_conditional_fields", ())
    )
    review_ready = envelope.get("downstream_ready") is True
    critical_silent_errors = sorted(
        field_name
        for field_name in failed_fields
        if review_ready and field_name in critical_fields
    )
    calls = result.get("model_calls_used", 0)
    expected_calls = case.answer.get("route_expectation", {}).get(
        "expected_model_calls"
    )
    route_calls_match = None
    if isinstance(expected_calls, dict) and isinstance(calls, int):
        route_calls_match = expected_calls["minimum"] <= calls <= expected_calls["maximum"]
    return {
        **base,
        "status": "SCORED",
        "result_path": result.get("_result_path"),
        "passed_fields": len(field_names) - len(failed_fields),
        "failed_fields": failed_fields,
        "field_match_rate": _rate(len(field_names) - len(failed_fields), len(field_names)),
        "origin_mismatch_fields": [
            item["field_name"] for item in fields if not item["origin_match"]
        ],
        "review_status": envelope.get("review_status"),
        "downstream_ready": envelope.get("downstream_ready"),
        "calculation_inputs_complete": envelope.get("calculation_inputs_complete"),
        "critical_silent_errors": critical_silent_errors,
        "model_calls_used": calls,
        "route_model_calls_match": route_calls_match,
        "route": result.get("route"),
        "semantic_review_fields": result.get("semantic_review_fields", []),
    }


def evaluate_results(
    reference_path: Path,
    results_root: Path,
    *,
    media_type: str | None = None,
    dictionary_path: Path = DICTIONARY_PATH,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    reference, dictionary, all_cases = validate_reference(
        reference_path,
        dictionary_path,
        repo_root=repo_root,
    )
    cases = tuple(
        case for case in all_cases if media_type is None or case.media_type == media_type
    )
    results = _discover_results(results_root)
    field_names = tuple(
        definition.field_name for definition in dictionary.extractable_fields
    )
    documents = [
        _score_case(case, results.get(case.input_path), field_names) for case in cases
    ]
    passed_fields = sum(item["passed_fields"] for item in documents)
    expected_fields = len(documents) * len(field_names)
    scored_documents = sum(item["status"] == "SCORED" for item in documents)
    review_statuses = Counter(
        item.get("review_status")
        for item in documents
        if item.get("review_status") is not None
    )
    critical_silent_errors = [
        {"case_id": item["case_id"], "field_name": field_name}
        for item in documents
        for field_name in item.get("critical_silent_errors", [])
    ]
    route_checks = [
        item.get("route_model_calls_match")
        for item in documents
        if item.get("route_model_calls_match") is not None
    ]
    return {
        "result_kind": "VERSIONED_EXTRACTION_REVIEW_SCORE",
        "scoring_status": "OFFLINE_PRE_CORRECTION",
        "reference_set_id": reference["reference_set_id"],
        "reference_path": str(reference_path),
        "results_root": str(results_root),
        "dictionary_version": dictionary.version,
        "media_type_filter": media_type,
        "summary": {
            "expected_documents": len(documents),
            "scored_documents": scored_documents,
            "failed_or_missing_documents": len(documents) - scored_documents,
            "document_success_rate": _rate(scored_documents, len(documents)),
            "passed_fields": passed_fields,
            "expected_fields": expected_fields,
            "conservative_field_match_rate": _rate(passed_fields, expected_fields),
            "review_status_counts": dict(sorted(review_statuses.items())),
            "downstream_ready_documents": sum(
                item.get("downstream_ready") is True for item in documents
            ),
            "manual_review_documents": sum(
                item.get("review_status") == "REVIEW_REQUIRED" for item in documents
            ),
            "rejected_documents": sum(
                item.get("review_status") == "REJECTED" for item in documents
            ),
            "critical_silent_error_count": len(critical_silent_errors),
            "route_model_call_checks_passed": sum(value is True for value in route_checks),
            "route_model_call_checks_total": len(route_checks),
            "model_calls_used": sum(
                int(item.get("model_calls_used") or 0) for item in documents
            ),
        },
        "critical_silent_errors": critical_silent_errors,
        "documents": documents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--media-type",
        choices=("application/pdf", "text/csv"),
    )
    parser.add_argument("--dictionary", type=Path, default=DICTIONARY_PATH)
    args = parser.parse_args()
    try:
        score = evaluate_results(
            args.reference,
            args.results_root,
            media_type=args.media_type,
            dictionary_path=args.dictionary,
        )
    except ContractError as exc:
        print(
            json.dumps(
                {"status": "FAILED", "error_code": exc.code, "details": exc.details}
            )
        )
        return 1
    _write = json.dumps(score, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_write, encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": str(args.output), **score["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
