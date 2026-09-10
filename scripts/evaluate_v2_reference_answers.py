#!/usr/bin/env python3
"""Score saved V2 pre-correction extractions against A's development answers."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = REPO_ROOT / "evaluation/reference/quote_V2/reference_answers.json"
DEFAULT_RESULTS_ROOT = REPO_ROOT / "evaluation/results/local/2026-09-10"
DEFAULT_OUTPUT = DEFAULT_RESULTS_ROOT / "v2_reference_score.json"
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative_to_repo(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "v2_score_json_invalid",
            "V2 scoring input is not readable JSON",
            path=str(path),
        ) from exc
    if not isinstance(payload, dict):
        raise ContractError(
            "v2_score_json_invalid",
            "V2 scoring input must be a JSON object",
            path=str(path),
        )
    return payload


def _validate_reference(
    reference_path: Path,
    dictionary_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], QuoteDictionary]:
    reference = _load_json(reference_path)
    if reference.get("reference_kind") != "DEVELOPMENT_REFERENCE_ANSWER":
        raise ContractError(
            "v2_reference_kind_invalid",
            "V2 scorer only accepts development reference answers",
            actual=reference.get("reference_kind"),
        )
    if reference.get("runtime_access") != "DENY":
        raise ContractError(
            "v2_reference_isolation_invalid",
            "V2 reference must remain unavailable to the runtime extractor",
            actual=reference.get("runtime_access"),
        )

    dictionary = QuoteDictionary.load(dictionary_path)
    supplier_dictionary = reference.get("dictionaries", {}).get("supplier_quote", {})
    if supplier_dictionary.get("version") != dictionary.version:
        raise ContractError(
            "v2_reference_dictionary_version_mismatch",
            "V2 reference dictionary version does not match the loaded dictionary",
            expected=dictionary.version,
            actual=supplier_dictionary.get("version"),
        )
    actual_dictionary_hash = _sha256(dictionary_path)
    if supplier_dictionary.get("sha256") != actual_dictionary_hash:
        raise ContractError(
            "v2_reference_dictionary_hash_mismatch",
            "V2 reference dictionary hash does not match the loaded dictionary",
            expected=supplier_dictionary.get("sha256"),
            actual=actual_dictionary_hash,
        )

    for file_record in reference.get("files", []):
        relative_path = file_record.get("relative_path")
        if not isinstance(relative_path, str):
            raise ContractError(
                "v2_reference_file_invalid",
                "V2 reference file record lacks a relative path",
            )
        input_path = (repo_root / relative_path).resolve()
        try:
            input_path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError(
                "v2_reference_file_invalid",
                "V2 reference file path escapes the repository",
                relative_path=relative_path,
            ) from exc
        if not input_path.is_file():
            raise ContractError(
                "v2_reference_input_missing",
                "V2 reference input does not exist",
                relative_path=relative_path,
            )
        actual_hash = _sha256(input_path)
        if file_record.get("sha256") != actual_hash:
            raise ContractError(
                "v2_reference_input_hash_mismatch",
                "V2 reference input hash does not match the bound file",
                relative_path=relative_path,
                expected=file_record.get("sha256"),
                actual=actual_hash,
            )
    return reference, dictionary


def _finished_at(result: dict[str, Any]) -> float:
    raw = result.get("batch", {}).get("run", {}).get("finished_at")
    if not isinstance(raw, str):
        return float("-inf")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")


def _discover_results(results_root: Path) -> tuple[list[tuple[Path, dict[str, Any]]], list[dict[str, str]]]:
    discovered: list[tuple[Path, dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(results_root.rglob("*.json")):
        try:
            result = _load_json(path)
        except ContractError:
            skipped.append({"path": _relative_to_repo(path), "reason": "UNREADABLE_JSON"})
            continue
        if result.get("dataset_version") != "V2":
            continue
        batch = result.get("batch")
        parsed_input = batch.get("parsed_input") if isinstance(batch, dict) else None
        if not isinstance(parsed_input, dict) or not parsed_input.get("original_filename"):
            skipped.append({"path": _relative_to_repo(path), "reason": "NO_SCORABLE_BATCH"})
            continue
        discovered.append((path, result))
    return discovered, skipped


def _select_results(
    discovered: list[tuple[Path, dict[str, Any]]],
    selection: Literal["latest-per-input", "all"],
) -> list[tuple[Path, dict[str, Any]]]:
    if selection == "all":
        return discovered
    selected: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path, result in discovered:
        filename = result["batch"]["parsed_input"]["original_filename"]
        previous = selected.get(filename)
        if previous is None or _finished_at(result) > _finished_at(previous[1]):
            selected[filename] = (path, result)
    return [selected[name] for name in sorted(selected)]


def _expected_contract(answer: dict[str, Any], field_name: str) -> tuple[str, str | None]:
    expectation = answer["field_expectation"]
    status = expectation["default"]["validation_status"]
    origin = expectation["default"]["origin"]
    for override_key in ("missing_fields", "unresolved_fields"):
        override = expectation.get(override_key, {}).get(field_name)
        if override is not None:
            status = override["validation_status"]
            origin = override.get("origin")
    return status, origin


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


def _file_index(reference: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for item in reference["files"]:
        if item.get("answer_id") not in reference["answers"]:
            continue
        key = (Path(item["relative_path"]).name, item["media_type"])
        if key in index:
            raise ContractError(
                "v2_reference_file_ambiguous",
                "V2 reference contains duplicate input identities",
                filename=key[0],
                media_type=key[1],
            )
        index[key] = item
    return index


def _score_result(
    path: Path,
    result: dict[str, Any],
    reference: dict[str, Any],
    field_names: tuple[str, ...],
    file_index: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    batch = result["batch"]
    parsed_input = batch["parsed_input"]
    key = (parsed_input["original_filename"], parsed_input["media_type"])
    file_record = file_index.get(key)
    if file_record is None:
        raise ContractError(
            "v2_result_input_not_in_reference",
            "saved V2 extraction is not bound to an input in A's reference",
            result_path=str(path),
            filename=key[0],
            media_type=key[1],
        )
    if parsed_input.get("document_sha256") != file_record["sha256"]:
        raise ContractError(
            "v2_result_input_hash_mismatch",
            "saved V2 extraction used different input bytes than A's reference",
            result_path=str(path),
            filename=key[0],
            expected=file_record["sha256"],
            actual=parsed_input.get("document_sha256"),
        )

    answer_id = file_record["answer_id"]
    answer = reference["answers"][answer_id]
    candidates = batch.get("candidates", [])
    candidate_map: dict[str, dict[str, Any]] = {}
    duplicate_fields: list[str] = []
    for candidate in candidates:
        field_name = candidate.get("field_name")
        if field_name in candidate_map:
            duplicate_fields.append(field_name)
        elif isinstance(field_name, str):
            candidate_map[field_name] = candidate

    field_scores: list[dict[str, Any]] = []
    for field_name in field_names:
        expected_status, expected_origin = _expected_contract(answer, field_name)
        expected_value = answer["normalized_quote"][field_name]
        actual = candidate_map.get(field_name)
        actual_status = actual.get("validation_status") if actual else None
        actual_value = actual.get("normalized_value") if actual else None
        actual_origin = actual.get("origin") if actual else None
        status_match = actual_status == expected_status
        value_match = _values_equal(field_name, actual_value, expected_value)
        origin_match = actual_origin == expected_origin
        field_scores.append(
            {
                "field_name": field_name,
                "expected_status": expected_status,
                "actual_status": actual_status,
                "expected_normalized_value": expected_value,
                "actual_normalized_value": actual_value,
                "status_match": status_match,
                "value_match": value_match,
                "scored_pass": status_match and value_match,
                "expected_origin": expected_origin,
                "actual_origin": actual_origin,
                "origin_contract_match": origin_match,
            }
        )

    passed = sum(item["scored_pass"] for item in field_scores)
    origin_mismatches = [
        item["field_name"] for item in field_scores if not item["origin_contract_match"]
    ]
    return {
        "result_path": _relative_to_repo(path),
        "input_filename": key[0],
        "input_media_type": key[1],
        "answer_id": answer_id,
        "prompt_version": batch.get("run", {}).get("prompt_version"),
        "provider": batch.get("run", {}).get("provider"),
        "model_id": batch.get("run", {}).get("model_id"),
        "source_run_status": result.get("status"),
        "field_count": len(field_scores),
        "passed_count": passed,
        "failed_count": len(field_scores) - passed,
        "match_rate": str((Decimal(passed) / Decimal(len(field_scores))).quantize(Decimal("0.0001"))),
        "failed_fields": [item["field_name"] for item in field_scores if not item["scored_pass"]],
        "origin_contract_mismatches": origin_mismatches,
        "duplicate_candidate_fields": sorted(set(duplicate_fields)),
        "extra_candidate_fields": sorted(set(candidate_map) - set(field_names)),
        "fields": field_scores,
    }


def evaluate_v2_results(
    reference_path: Path,
    results_root: Path,
    dictionary_path: Path,
    *,
    selection: Literal["latest-per-input", "all"] = "latest-per-input",
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    reference, dictionary = _validate_reference(reference_path, dictionary_path, repo_root=repo_root)
    discovered, skipped = _discover_results(results_root)
    selected = _select_results(discovered, selection)
    if not selected:
        raise ContractError(
            "v2_results_missing",
            "no scorable saved V2 extraction results were found",
            results_root=str(results_root),
        )

    field_names = tuple(definition.field_name for definition in dictionary.extractable_fields)
    document_scores = [
        _score_result(path, result, reference, field_names, _file_index(reference))
        for path, result in selected
    ]
    total_fields = sum(item["field_count"] for item in document_scores)
    total_passed = sum(item["passed_count"] for item in document_scores)

    prompt_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in document_scores:
        prompt_groups[item["prompt_version"] or "UNKNOWN"].append(item)
    reference_quote_inputs = {
        Path(item["relative_path"]).name
        for item in reference["files"]
        if item.get("answer_id", "").startswith("V2-")
        and item.get("answer_id") not in {"V2-REQ", "V2-SYSTEM-EVIDENCE-AUDIT"}
    }
    by_prompt_version: dict[str, Any] = {}
    for prompt_version, items in sorted(prompt_groups.items()):
        fields = sum(item["field_count"] for item in items)
        passed = sum(item["passed_count"] for item in items)
        input_filenames = sorted(item["input_filename"] for item in items)
        by_prompt_version[prompt_version] = {
            "document_count": len(items),
            "input_filenames": input_filenames,
            "complete_v2_quote_matrix": set(input_filenames) == reference_quote_inputs,
            "passed_fields": passed,
            "total_fields": fields,
            "field_match_rate": str((Decimal(passed) / Decimal(fields)).quantize(Decimal("0.0001"))),
        }

    origin_mismatch_count = sum(len(item["origin_contract_mismatches"]) for item in document_scores)
    return {
        "result_kind": "V2_DEVELOPMENT_REFERENCE_SCORE",
        "scoring_status": "DEVELOPMENT_ONLY_NOT_FINAL_A_APPROVAL",
        "reference_set_id": reference["reference_set_id"],
        "reference_kind": reference["reference_kind"],
        "reference_path": _relative_to_repo(reference_path),
        "reference_sha256": _sha256(reference_path),
        "dictionary_version": dictionary.version,
        "selection": selection,
        "score_definition": "validation_status AND normalized_value over B-extractable fields",
        "excluded_from_score": [
            "supplier_id because it is system-owned",
            "origin until the MISSING-origin contract is aligned",
            "raw wording and semantic evidence because this aggregate reference does not define them per field",
        ],
        "summary": {
            "document_count": len(document_scores),
            "total_fields": total_fields,
            "passed_fields": total_passed,
            "failed_fields": total_fields - total_passed,
            "field_match_rate": str(
                (Decimal(total_passed) / Decimal(total_fields)).quantize(Decimal("0.0001"))
            ),
            "mixed_prompt_versions": len(prompt_groups) > 1,
            "origin_contract_mismatch_count": origin_mismatch_count,
        },
        "by_prompt_version": by_prompt_version,
        "documents": document_scores,
        "unscorable_results": skipped,
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
    parser.add_argument(
        "--selection",
        choices=("latest-per-input", "all"),
        default="latest-per-input",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        score = evaluate_v2_results(
            args.reference,
            args.results_root,
            args.dictionary,
            selection=args.selection,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "PASSED",
                "scoring_status": score["scoring_status"],
                "output": str(args.output),
                **score["summary"],
                "failed_by_input": {
                    item["input_filename"]: item["failed_fields"] for item in score["documents"]
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
