#!/usr/bin/env python3
"""Offline-score saved V7 parser/OCR output against isolated open answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_OPEN_SPLITS = frozenset({"development", "calibration"})
CONFLICT_PREFIX = "NATIVE_IMAGE_CRITICAL_TOKEN_CONFLICT:"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(code, "evaluation artifact is not readable JSON", path=str(path)) from exc
    if not isinstance(value, dict):
        raise ContractError(code, "evaluation artifact must be a JSON object", path=str(path))
    return value


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _anchor_present(text: str, anchor: str) -> bool:
    normalized_text = _normalized(text)
    normalized_anchor = _normalized(anchor)
    prefix = r"(?<![A-Za-z0-9])" if normalized_anchor and normalized_anchor[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if normalized_anchor and normalized_anchor[-1].isalnum() else ""
    return re.search(prefix + re.escape(normalized_anchor) + suffix, normalized_text) is not None


def _rate(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0000"
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001")))


def _source_is_locatable(source: dict[str, Any]) -> bool:
    bbox = source.get("bbox")
    return (
        isinstance(source.get("page_number"), int)
        and isinstance(source.get("block_id"), str)
        and bool(source["block_id"])
        and isinstance(bbox, dict)
        and isinstance(bbox.get("x0"), (int, float))
        and isinstance(bbox.get("top"), (int, float))
        and isinstance(bbox.get("x1"), (int, float))
        and isinstance(bbox.get("bottom"), (int, float))
        and bbox["x1"] > bbox["x0"]
        and bbox["bottom"] > bbox["top"]
    )


def _ocr_metadata_complete(source: dict[str, Any]) -> bool:
    metadata = source.get("ocr_metadata")
    return (
        isinstance(metadata, dict)
        and bool(metadata.get("engine"))
        and bool(metadata.get("engine_version"))
        and isinstance(metadata.get("rendered_page_sha256"), str)
        and len(metadata["rendered_page_sha256"]) == 64
        and isinstance(metadata.get("render_dpi"), int)
        and metadata["render_dpi"] > 0
        and metadata.get("confidence") is not None
    )


def _table_mapping_match(
    mapping: dict[str, Any],
    page_number: int,
    sources: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> bool:
    page_sources = [source for source in sources if source.get("page_number") == page_number]
    by_id = {source.get("source_id"): source for source in page_sources}
    label = str(mapping.get("label", ""))
    value = str(mapping.get("value_text", ""))
    # A single row/line atom may legitimately contain both label and value.
    if any(
        _anchor_present(str(source.get("raw_text", "")), label)
        and _anchor_present(str(source.get("raw_text", "")), value)
        for source in page_sources
    ):
        return True
    for group in groups:
        if group.get("page_number") != page_number:
            continue
        texts = [str(by_id[source_id].get("raw_text", "")) for source_id in group.get("source_ids", []) if source_id in by_id]
        combined = " ".join(texts)
        if _anchor_present(combined, label) and _anchor_present(combined, value):
            return True
    return False


def _score_case(reference: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    case_id = str(reference["case_id"])
    expected_routes = reference.get("expected_page_routes", [])
    expected_evidence = reference.get("evidence_expectations", [])
    expected_ocr = reference.get("ocr_ground_truth", [])
    expected_mappings = [
        (int(table["page_number"]), mapping)
        for table in reference.get("table_expectations", [])
        for mapping in table.get("mappings", [])
    ]
    base = {
        "case_id": case_id,
        "input_file": reference["input_file"],
        "expected_pages": len(expected_routes),
        "expected_evidence": len(expected_evidence),
        "expected_critical_tokens": sum(len(page.get("critical_tokens", [])) for page in expected_ocr),
        "expected_table_mappings": len(expected_mappings),
    }
    if result is None:
        return {
            **base,
            "status": "MISSING_RESULT",
            "route_matches": 0,
            "evidence_matches": 0,
            "critical_tokens_matched": 0,
            "table_mappings_matched": 0,
            "locatable_sources": 0,
            "total_sources": 0,
            "ocr_metadata_complete": 0,
            "total_ocr_sources": 0,
            "silent_page_omissions": len(expected_routes),
            "failures": ["RESULT_MISSING"],
        }
    if result.get("input_sha256") != reference.get("input_sha256"):
        raise ContractError(
            "v7_result_input_hash_mismatch",
            "saved parser result used bytes different from the reference",
            case_id=case_id,
        )
    if result.get("status") != "PASSED" or not isinstance(result.get("parsed_input"), dict):
        return {
            **base,
            "status": "FAILED_PARSER",
            "error_code": result.get("error", {}).get("code"),
            "route_matches": 0,
            "evidence_matches": 0,
            "critical_tokens_matched": 0,
            "table_mappings_matched": 0,
            "locatable_sources": 0,
            "total_sources": 0,
            "ocr_metadata_complete": 0,
            "total_ocr_sources": 0,
            "silent_page_omissions": 0,
            "failures": ["PARSER_FAILED"],
        }

    parsed = result["parsed_input"]
    analyses = parsed.get("page_analyses", [])
    sources = parsed.get("sources", [])
    groups = parsed.get("context_groups", [])
    routes = {item.get("page_number"): item.get("route") for item in analyses}
    route_failures = [
        {
            "page_number": expected["page_number"],
            "expected": expected["route"],
            "actual": routes.get(expected["page_number"]),
        }
        for expected in expected_routes
        if routes.get(expected["page_number"]) != expected["route"]
    ]
    unexpected_pages = sorted(set(routes) - {item["page_number"] for item in expected_routes})

    evidence_failures = []
    for expectation in expected_evidence:
        matching = [
            source
            for source in sources
            if source.get("page_number") == expectation.get("page_number")
            and _source_is_locatable(source)
            and _anchor_present(str(source.get("raw_text", "")), str(expectation.get("text_contains", "")))
        ]
        if not matching:
            # Reference source_id is an A-side human label, not B's stable source ID.
            evidence_failures.append(
                {
                    "field_name": expectation.get("field_name"),
                    "page_number": expectation.get("page_number"),
                    "text_contains": expectation.get("text_contains"),
                }
            )

    critical_token_failures = []
    for page in expected_ocr:
        page_number = page["page_number"]
        ocr_text = " ".join(
            str(source.get("raw_text", ""))
            for source in sources
            if source.get("page_number") == page_number and source.get("kind") == "PDF_OCR_BLOCK"
        )
        for token in page.get("critical_tokens", []):
            if not _anchor_present(ocr_text, token):
                critical_token_failures.append({"page_number": page_number, "token": token})

    mapping_failures = [
        {
            "page_number": page_number,
            "field_name": mapping.get("field_name"),
            "label": mapping.get("label"),
            "value_text": mapping.get("value_text"),
        }
        for page_number, mapping in expected_mappings
        if not _table_mapping_match(mapping, page_number, sources, groups)
    ]
    locatable_sources = sum(_source_is_locatable(source) for source in sources)
    ocr_sources = [source for source in sources if source.get("kind") == "PDF_OCR_BLOCK"]
    ocr_metadata_complete = sum(_ocr_metadata_complete(source) for source in ocr_sources)
    expected_conflict = bool(reference.get("security_expectation", {}).get("hidden_text_conflict_present"))
    actual_conflict = any(
        any(str(reason).startswith(CONFLICT_PREFIX) for reason in analysis.get("quality_reasons", []))
        for analysis in analyses
    )
    failures = []
    if route_failures or unexpected_pages or len(analyses) != len(expected_routes):
        failures.append("PAGE_ROUTE_MISMATCH")
    if evidence_failures:
        failures.append("EVIDENCE_TEXT_NOT_LOCATABLE")
    if critical_token_failures:
        failures.append("OCR_CRITICAL_TOKEN_MISMATCH")
    if mapping_failures:
        failures.append("TABLE_ASSOCIATION_MISMATCH")
    if locatable_sources != len(sources):
        failures.append("SOURCE_COORDINATES_INCOMPLETE")
    if ocr_metadata_complete != len(ocr_sources):
        failures.append("OCR_METADATA_INCOMPLETE")
    if expected_conflict != actual_conflict:
        failures.append("HIDDEN_TEXT_CONFLICT_MISMATCH")
    return {
        **base,
        "status": "SCORED",
        "route_matches": len(expected_routes) - len(route_failures),
        "route_failures": route_failures,
        "unexpected_pages": unexpected_pages,
        "evidence_matches": len(expected_evidence) - len(evidence_failures),
        "evidence_failures": evidence_failures,
        "critical_tokens_matched": base["expected_critical_tokens"] - len(critical_token_failures),
        "critical_token_failures": critical_token_failures,
        "table_mappings_matched": len(expected_mappings) - len(mapping_failures),
        "table_mapping_failures": mapping_failures,
        "locatable_sources": locatable_sources,
        "total_sources": len(sources),
        "ocr_metadata_complete": ocr_metadata_complete,
        "total_ocr_sources": len(ocr_sources),
        "silent_page_omissions": sum(
            expected["page_number"] not in routes for expected in expected_routes
        ),
        "expected_hidden_text_conflict": expected_conflict,
        "actual_hidden_text_conflict": actual_conflict,
        "reference_source_ids_compared": False,
        "failures": failures,
    }


def evaluate_v7_parser_ocr(reference_path: Path, run_path: Path) -> dict[str, Any]:
    reference = _load_json(reference_path, code="v7_reference_invalid")
    run = _load_json(run_path, code="v7_run_invalid")
    split = reference.get("split")
    if split == "holdout":
        raise ContractError(
            "v7_holdout_score_blocked",
            "V7 holdout scoring requires the post-freeze one-time procedure",
        )
    if split not in ALLOWED_OPEN_SPLITS or run.get("split") != split:
        raise ContractError(
            "v7_evaluation_split_mismatch",
            "reference and parser run must use the same open V7 split",
            reference_split=split,
            run_split=run.get("split"),
        )
    if reference.get("dataset_version") != "V7" or run.get("dataset_version") != "V7":
        raise ContractError("v7_evaluation_version_invalid", "both artifacts must be V7")
    if run.get("reference_answers_loaded") is not False or run.get("external_model_calls") != 0:
        raise ContractError(
            "v7_parser_run_isolation_invalid",
            "parser/OCR run must not load answers or call an external model",
        )
    results = {
        item.get("case_id"): item
        for item in run.get("cases", [])
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    cases = [_score_case(case, results.get(case.get("case_id"))) for case in reference.get("cases", [])]
    expected_pages = sum(case["expected_pages"] for case in cases)
    route_matches = sum(case["route_matches"] for case in cases)
    expected_evidence = sum(case["expected_evidence"] for case in cases)
    evidence_matches = sum(case["evidence_matches"] for case in cases)
    expected_tokens = sum(case["expected_critical_tokens"] for case in cases)
    token_matches = sum(case["critical_tokens_matched"] for case in cases)
    expected_mappings = sum(case["expected_table_mappings"] for case in cases)
    mapping_matches = sum(case["table_mappings_matched"] for case in cases)
    passed_parser = sum(case["status"] == "SCORED" for case in cases)
    clean_cases = sum(case["status"] == "SCORED" and not case["failures"] for case in cases)
    total_sources = sum(case["total_sources"] for case in cases)
    locatable_sources = sum(case["locatable_sources"] for case in cases)
    total_ocr_sources = sum(case["total_ocr_sources"] for case in cases)
    complete_ocr_sources = sum(case["ocr_metadata_complete"] for case in cases)
    silent_page_omissions = sum(case["silent_page_omissions"] for case in cases)
    return {
        "result_kind": "V7_PARSER_OCR_OFFLINE_SCORE",
        "dataset_version": "V7",
        "split": split,
        "scoring_status": "OPEN_REFERENCE_OFFLINE",
        "reference_path": str(reference_path),
        "reference_sha256": _sha256(reference_path),
        "run_path": str(run_path),
        "run_sha256": _sha256(run_path),
        "reference_source_id_policy": "HUMAN_LABEL_NOT_RUNTIME_ID",
        "field_candidate_scoring_performed": False,
        "summary": {
            "expected_documents": len(cases),
            "parser_success_documents": passed_parser,
            "clean_parser_ocr_documents": clean_cases,
            "failed_or_missing_documents": len(cases) - passed_parser,
            "document_parser_success_rate": _rate(passed_parser, len(cases)),
            "page_routes_matched": route_matches,
            "expected_page_routes": expected_pages,
            "page_route_match_rate": _rate(route_matches, expected_pages),
            "page_silent_omission_count": silent_page_omissions,
            "evidence_texts_located": evidence_matches,
            "expected_evidence_texts": expected_evidence,
            "evidence_text_location_rate": _rate(evidence_matches, expected_evidence),
            "critical_tokens_matched": token_matches,
            "expected_critical_tokens": expected_tokens,
            "critical_token_exact_rate": _rate(token_matches, expected_tokens),
            "table_mappings_matched": mapping_matches,
            "expected_table_mappings": expected_mappings,
            "table_mapping_rate": _rate(mapping_matches, expected_mappings),
            "locatable_sources": locatable_sources,
            "total_sources": total_sources,
            "source_location_rate": _rate(locatable_sources, total_sources),
            "ocr_metadata_complete_sources": complete_ocr_sources,
            "total_ocr_sources": total_ocr_sources,
            "ocr_metadata_completeness_rate": _rate(complete_ocr_sources, total_ocr_sources),
            "cases_with_failures": sum(bool(case["failures"]) for case in cases),
            "external_model_calls": 0,
        },
        "documents": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        score = evaluate_v7_parser_ocr(args.reference, args.run)
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASSED", "output": str(args.output), **score["summary"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
