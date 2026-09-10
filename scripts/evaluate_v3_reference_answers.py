#!/usr/bin/env python3
"""Score saved V3 extractions against A's development reference answers."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = REPO_ROOT / "evaluation/reference/quote_V3/reference_answers.json"
DEFAULT_RESULTS_ROOT = (
    REPO_ROOT / "evaluation/results/local/2026-09-10/v3_prompt_1_6_0/full"
)
DEFAULT_OUTPUT = DEFAULT_RESULTS_ROOT / "v3_reference_score.json"
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


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("v3_score_json_invalid", "V3 scoring input is not readable JSON", path=str(path)) from exc
    if not isinstance(payload, dict):
        raise ContractError("v3_score_json_invalid", "V3 scoring input must be a JSON object", path=str(path))
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001")))


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


def _expected_status(answer: dict[str, Any], field_name: str) -> str:
    expectation = answer["field_expectation"]
    for override_key in ("missing_fields", "unresolved_fields"):
        override = expectation.get(override_key, {}).get(field_name)
        if override is not None:
            return override["validation_status"]
    return expectation["default"]["validation_status"]


def _validate_reference(
    reference_path: Path,
    dictionary_path: Path,
    repo_root: Path,
) -> tuple[dict[str, Any], QuoteDictionary]:
    reference = _load_json(reference_path)
    if reference.get("reference_kind") != "DEVELOPMENT_REFERENCE_ANSWER":
        raise ContractError("v3_reference_kind_invalid", "V3 scorer requires a development reference")
    if reference.get("runtime_access") != "DENY":
        raise ContractError("v3_reference_isolation_invalid", "V3 reference must be isolated from runtime")

    dictionary = QuoteDictionary.load(dictionary_path)
    if reference.get("dictionary_version") != dictionary.version:
        raise ContractError(
            "v3_reference_dictionary_version_mismatch",
            "V3 reference dictionary version does not match",
            expected=dictionary.version,
            actual=reference.get("dictionary_version"),
        )

    for record in reference.get("files", []):
        path = (repo_root / record["relative_path"]).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError("v3_reference_file_invalid", "V3 reference path escapes repository") from exc
        if not path.is_file() or _sha256(path) != record.get("sha256"):
            raise ContractError(
                "v3_reference_input_hash_mismatch",
                "V3 reference input is missing or has different bytes",
                relative_path=record["relative_path"],
            )
    return reference, dictionary


def _result_identity(result: dict[str, Any]) -> tuple[str, str] | None:
    parsed = result.get("batch", {}).get("parsed_input", {})
    filename = parsed.get("original_filename")
    media_type = parsed.get("media_type")
    if isinstance(filename, str) and isinstance(media_type, str):
        return filename, media_type
    input_path = result.get("input")
    input_format = result.get("input_format")
    if isinstance(input_path, str) and input_format in {"pdf", "csv"}:
        return Path(input_path).name, (
            "application/pdf" if input_format == "pdf" else "text/csv"
        )
    return None


def _finished_at(result: dict[str, Any]) -> float:
    value = result.get("batch", {}).get("run", {}).get("finished_at") or result.get("finished_at")
    if not isinstance(value, str):
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def _discover_latest(results_root: Path) -> dict[tuple[str, str], tuple[Path, dict[str, Any]]]:
    selected: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    for path in sorted(results_root.rglob("*.json")):
        result = _load_json(path)
        if result.get("dataset_version") != "V3":
            continue
        identity = _result_identity(result)
        if identity is None:
            continue
        previous = selected.get(identity)
        if previous is None or _finished_at(result) > _finished_at(previous[1]):
            selected[identity] = (path, result)
    return selected


def _score_passed_result(
    result: dict[str, Any],
    answer: dict[str, Any],
    field_names: tuple[str, ...],
) -> tuple[int, list[str]]:
    candidate_map = {
        candidate.get("field_name"): candidate
        for candidate in result["batch"].get("candidates", [])
        if isinstance(candidate.get("field_name"), str)
    }
    failed_fields: list[str] = []
    for field_name in field_names:
        actual = candidate_map.get(field_name)
        status_match = actual is not None and actual.get("validation_status") == _expected_status(
            answer, field_name
        )
        value_match = actual is not None and _values_equal(
            field_name,
            actual.get("normalized_value"),
            answer["normalized_quote"][field_name],
        )
        if not status_match or not value_match:
            failed_fields.append(field_name)
    return len(field_names) - len(failed_fields), failed_fields


def evaluate_v3_results(
    reference_path: Path,
    results_root: Path,
    dictionary_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    reference, dictionary = _validate_reference(reference_path, dictionary_path, repo_root)
    selected = _discover_latest(results_root)
    field_names = tuple(definition.field_name for definition in dictionary.extractable_fields)
    documents: list[dict[str, Any]] = []
    passed_fields = 0
    scorable_fields = 0

    for file_record in reference["files"]:
        identity = (Path(file_record["relative_path"]).name, file_record["media_type"])
        selected_result = selected.get(identity)
        if selected_result is None:
            documents.append({"input_filename": identity[0], "status": "MISSING_RESULT"})
            continue
        path, result = selected_result
        document = {
            "input_filename": identity[0],
            "input_media_type": identity[1],
            "answer_id": file_record["answer_id"],
            "result_path": _relative(path, repo_root),
            "status": result.get("status"),
        }
        if result.get("status") != "PASSED" or not isinstance(result.get("batch"), dict):
            document["error_code"] = result.get("error", {}).get("code")
            documents.append(document)
            continue
        parsed_hash = result["batch"]["parsed_input"].get("document_sha256")
        if parsed_hash != file_record["sha256"]:
            raise ContractError(
                "v3_result_input_hash_mismatch",
                "saved V3 extraction used different input bytes than the reference",
                input_filename=identity[0],
            )
        passed, failed = _score_passed_result(
            result,
            reference["answers"][file_record["answer_id"]],
            field_names,
        )
        document.update(
            {
                "passed_fields": passed,
                "total_fields": len(field_names),
                "field_match_rate": _rate(passed, len(field_names)),
                "failed_fields": failed,
                "prompt_version": result["batch"].get("run", {}).get("prompt_version"),
            }
        )
        passed_fields += passed
        scorable_fields += len(field_names)
        documents.append(document)

    successful_documents = sum(item["status"] == "PASSED" for item in documents)
    failed_documents = sum(item["status"] not in {"PASSED", "MISSING_RESULT"} for item in documents)
    missing_documents = sum(item["status"] == "MISSING_RESULT" for item in documents)
    prompt_versions = sorted(
        {
            item["prompt_version"]
            for item in documents
            if isinstance(item.get("prompt_version"), str)
        }
    )
    expected_fields = len(documents) * len(field_names)
    return {
        "result_kind": "V3_DEVELOPMENT_REFERENCE_SCORE",
        "scoring_status": "DEVELOPMENT_REFERENCE_PRE_CORRECTION",
        "reference_set_id": reference["reference_set_id"],
        "reference_path": _relative(reference_path, repo_root),
        "results_root": _relative(results_root, repo_root),
        "score_definition": {
            "field_pass": "validation_status AND normalized_value match",
            "conditional_field_match_rate": "only documents that passed extraction and evidence validation",
            "conservative_field_coverage_rate": "failed or missing documents contribute zero passed fields",
        },
        "summary": {
            "expected_documents": len(documents),
            "successful_documents": successful_documents,
            "failed_documents": failed_documents,
            "missing_documents": missing_documents,
            "document_success_rate": _rate(successful_documents, len(documents)),
            "prompt_versions": prompt_versions,
            "mixed_prompt_versions": len(prompt_versions) > 1,
            "passed_fields": passed_fields,
            "scorable_fields": scorable_fields,
            "conditional_field_match_rate": _rate(passed_fields, scorable_fields),
            "expected_fields": expected_fields,
            "conservative_field_coverage_rate": _rate(passed_fields, expected_fields),
        },
        "documents": documents,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=REPO_ROOT / "data/contracts/quote_data_field.csv",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        score = evaluate_v3_results(args.reference, args.results_root, args.dictionary)
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": str(args.output), **score["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
