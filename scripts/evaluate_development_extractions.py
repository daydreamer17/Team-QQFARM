#!/usr/bin/env python3
"""Compare pre-correction development PDF extractions with A's frozen data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.model_payload import ModelExtractionPayload
from supplier_comparison.extraction.normalization import normalize_model_payload


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = {
    "A": REPO_ROOT / "evaluation/results/local/2026-09-10/deepseek_v4_flash_supplier_a_pre_correction.json",
    "B": REPO_ROOT
    / "evaluation/results/local/2026-09-10/deepseek_v4_flash_supplier_b_pre_correction_attempt3.json",
    "C": REPO_ROOT / "evaluation/results/local/2026-09-10/deepseek_v4_flash_supplier_c_pre_correction.json",
}
DEFAULT_REFERENCE = REPO_ROOT / "data/generated/inputs/development/quotes.csv"
DEFAULT_OUTPUT = REPO_ROOT / "evaluation/results/local/2026-09-10/development_pdf_field_review.json"

MODEL_CANDIDATE_KEYS = (
    "field_name",
    "raw_value",
    "normalized_value",
    "unit",
    "validation_status",
    "source_refs",
)
PDF_ONLY_MISSING = {
    "A": {"category"},
    "B": {"category", "packaging_type", "units_per_pack"},
    "C": {"category"},
}
MONEY_FIELDS = {"unit_price", "shipping_fee_amount", "other_fees_amount"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_reference(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {row["supplier_alias"]: {key: value or "" for key, value in row.items()} for row in rows}


def _pipeline_candidates(batch: dict) -> tuple[dict[str, object], list[dict]]:
    wire_payload = {
        "candidates": [
            {key: candidate[key] for key in MODEL_CANDIDATE_KEYS}
            for candidate in batch["candidates"]
        ]
    }
    normalized, replay_events = normalize_model_payload(ModelExtractionPayload.model_validate(wire_payload))
    return (
        {candidate.field_name: candidate.model_dump(mode="json") for candidate in normalized.candidates},
        [event.model_dump(mode="json") for event in replay_events],
    )


def _values_equal(field_name: str, actual: object, expected: str, value_type: str) -> bool:
    if actual is None:
        return False
    if field_name in MONEY_FIELDS:
        try:
            return Decimal(str(actual)) == Decimal(expected)
        except InvalidOperation:
            return False
    if value_type.startswith("integer"):
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return False
    return str(actual) == expected


def _citation_shape_pass(candidate: dict, source_map: dict[str, str]) -> bool:
    if candidate["validation_status"] == "MISSING":
        return (
            candidate["raw_value"] is None
            and candidate["normalized_value"] is None
            and candidate["source_refs"] == []
        )
    if not candidate["source_refs"]:
        return False
    for citation in candidate["source_refs"]:
        source_text = source_map.get(citation["source_id"])
        if source_text is None:
            return False
        quoted = re.sub(r"\s+", " ", citation["quoted_text"]).strip()
        source = re.sub(r"\s+", " ", source_text).strip()
        if quoted not in source:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", type=Path, default=DEFAULT_RESULTS["A"])
    parser.add_argument("--b", type=Path, default=DEFAULT_RESULTS["B"])
    parser.add_argument("--c", type=Path, default=DEFAULT_RESULTS["C"])
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    result_paths = {"A": args.a, "B": args.b, "C": args.c}
    reference_rows = _load_reference(args.reference)
    dictionary = QuoteDictionary.load(REPO_ROOT / "data/contracts/quote_data_field.csv")
    reviews: dict[str, dict] = {}
    total_passed = 0
    total_fields = 0

    for alias, result_path in result_paths.items():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        batch = result["batch"]
        original_candidates = {candidate["field_name"]: candidate for candidate in batch["candidates"]}
        pipeline_candidates, replay_events = _pipeline_candidates(batch)
        source_map = {source["source_id"]: source["raw_text"] for source in batch["parsed_input"]["sources"]}
        expected_row = reference_rows[alias]
        field_reviews: list[dict] = []

        for definition in dictionary.extractable_fields:
            field_name = definition.field_name
            actual = pipeline_candidates[field_name]
            original = original_candidates[field_name]
            expected_raw = expected_row.get(field_name, "")
            expected_missing = field_name in PDF_ONLY_MISSING[alias] or expected_raw == ""
            expected_status = "MISSING" if expected_missing else "EXTRACTED"
            status_match = actual["validation_status"] == expected_status
            value_match = (
                actual["normalized_value"] is None
                if expected_missing
                else _values_equal(field_name, actual["normalized_value"], expected_raw, definition.value_type)
            )
            origin_match = original["origin"] == "DOCUMENT"
            citation_pass = _citation_shape_pass(actual, source_map)
            passed = status_match and value_match and origin_match and citation_pass
            total_fields += 1
            total_passed += int(passed)
            field_reviews.append(
                {
                    "field_name": field_name,
                    "expected_status": expected_status,
                    "actual_status": actual["validation_status"],
                    "expected_normalized_value": None if expected_missing else expected_raw,
                    "original_model_normalized_value": original["normalized_value"],
                    "pipeline_normalized_value": actual["normalized_value"],
                    "status_match": status_match,
                    "value_match": value_match,
                    "origin_document": origin_match,
                    "citation_location_pass": citation_pass,
                    "passed": passed,
                }
            )

        passed_count = sum(item["passed"] for item in field_reviews)
        reviews[alias] = {
            "source_result": str(result_path.relative_to(REPO_ROOT)),
            "source_result_sha256": _sha256(result_path),
            "field_count": len(field_reviews),
            "passed_count": passed_count,
            "failed_count": len(field_reviews) - passed_count,
            "deterministic_replay_normalization_events": replay_events,
            "failed_fields": [item["field_name"] for item in field_reviews if not item["passed"]],
            "fields": field_reviews,
        }

    record = {
        "result_kind": "A_CONTRACT_DEVELOPMENT_FIELD_REVIEW",
        "environment": "LOCAL",
        "input_is_synthetic": True,
        "correction_state": "PRE_HUMAN_CORRECTION_WITH_DETERMINISTIC_NORMALIZATION",
        "status": "PASSED" if total_passed == total_fields else "FAILED_FIELD_REVIEW",
        "review_basis": {
            "reference_csv": str(args.reference.relative_to(REPO_ROOT)),
            "reference_csv_sha256": _sha256(args.reference),
            "dictionary_version": dictionary.version,
            "pdf_specific_rules": {
                "category": "MISSING unless the PDF itself states a category",
                "supplier_b_individual_ordering": (
                    "supports order_multiple_units=1 only; packaging_type and units_per_pack remain MISSING"
                ),
                "sgd_fee_amounts": "compare with Decimal and persist exact values with two decimal places",
            },
            "limitation": (
                "citation_location is checked deterministically; final human semantic sign-off remains A-owned"
            ),
        },
        "summary": {
            "total_fields": total_fields,
            "passed_fields": total_passed,
            "failed_fields": total_fields - total_passed,
            "field_match_rate": str((Decimal(total_passed) / Decimal(total_fields)).quantize(Decimal("0.0001"))),
        },
        "suppliers": reviews,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": record["status"],
                "output": str(args.output),
                **record["summary"],
                "failed_by_supplier": {
                    alias: review["failed_fields"] for alias, review in reviews.items()
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
