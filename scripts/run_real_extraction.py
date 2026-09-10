#!/usr/bin/env python3
"""Run selected synthetic development quote inputs through the configured real model API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import DocumentContext, ValidationStatus
from supplier_comparison.extraction.csv_parser import ProfiledCsvQuoteParser
from supplier_comparison.extraction.development_data import DATASET_VERSIONS, supplier_quote_path
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ExtractionError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates


REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_ROOT = REPO_ROOT / "data/generated/inputs/development"
SUPPLIERS = {
    "A": "SUP-022",
    "B": "SUP-023",
    "C": "SUP-024",
}


def _write_result(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _pdf_path(dataset_version: str, supplier_alias: str) -> Path:
    return supplier_quote_path(DEVELOPMENT_ROOT, dataset_version, supplier_alias, "pdf")


def _profiled_csv_path(dataset_version: str, supplier_alias: str) -> Path:
    return supplier_quote_path(DEVELOPMENT_ROOT, dataset_version, supplier_alias, "csv")


def _context(dataset_version: str, supplier_alias: str) -> DocumentContext:
    version = DATASET_VERSIONS[dataset_version]
    return DocumentContext(
        task_id="TASK-MCU-DEMO-001-LOCAL-MODEL-TEST",
        task_revision=version,
        scenario_id="MCU-DEMO-001",
        quote_id=f"QUOTE-MCU-DEMO-001-{supplier_alias}",
        quote_version=version,
        document_id=f"DOC-MCU-DEMO-001-{supplier_alias}-V{version}",
        document_version=version,
        supplier_id=SUPPLIERS[supplier_alias],
    )


def _run_supplier(
    supplier_alias: str,
    dataset_version: str,
    input_format: str,
    output_path: Path,
    config: OpenAICompatibleConfig,
    dictionary: QuoteDictionary,
    budget: ModelCallBudget,
) -> tuple[int, dict]:
    started_at = datetime.now(timezone.utc)
    calls_before = budget.calls_used
    input_path = (
        _pdf_path(dataset_version, supplier_alias)
        if input_format == "pdf"
        else _profiled_csv_path(dataset_version, supplier_alias)
    )
    try:
        context = _context(dataset_version, supplier_alias)
        parsed = (
            PdfQuoteParser().parse(input_path, context)
            if input_format == "pdf"
            else ProfiledCsvQuoteParser().parse_row(
                input_path,
                context,
                2,
                profile_id=f"v2_supplier_{supplier_alias.lower()}",
            )
        )
        extraction_run_id = f"extract_local_{uuid4().hex}"
        batch = extract_quote_candidates(
            parsed,
            dictionary,
            OpenAICompatibleAdapter(config),
            budget,
            extraction_run_id,
        )
        shipping = {
            candidate.field_name: candidate
            for candidate in batch.candidates
            if candidate.field_name in {"shipping_fee_status", "shipping_fee_amount"}
        }
        shipping_missing = None
        if supplier_alias == "B":
            shipping_missing = len(shipping) == 2 and all(
                candidate.validation_status == ValidationStatus.MISSING
                and candidate.normalized_value is None
                and not candidate.source_refs
                for candidate in shipping.values()
            )
        extracted_values_complete = all(
            candidate.validation_status != ValidationStatus.EXTRACTED
            or (candidate.raw_value is not None and candidate.normalized_value is not None)
            for candidate in batch.candidates
        )
        checks = {
            "candidate_count_is_30": len(batch.candidates) == 30,
            "extracted_fields_have_values": extracted_values_complete,
        }
        if shipping_missing is not None:
            checks["supplier_b_shipping_remains_missing"] = shipping_missing
        all_checks_passed = all(checks.values())
        record = {
            "result_kind": "REAL_MODEL_EXTRACTION",
            "environment": "LOCAL",
            "input_is_synthetic": True,
            "dataset_version": dataset_version,
            "input_format": input_format,
            "correction_state": "PRE_CORRECTION",
            "status": "PASSED" if all_checks_passed else "FAILED_OUTPUT_QUALITY",
            "checks": checks,
            "batch": batch.model_dump(mode="json"),
        }
        _write_result(output_path, record)
        summary = {
            "supplier": supplier_alias,
            "dataset_version": dataset_version,
            "input_format": input_format,
            "status": record["status"],
            "output": str(output_path),
            "calls_used_for_supplier": budget.calls_used - calls_before,
            "calls_used_total": budget.calls_used,
            "model_id": config.model_id,
            "candidate_count": len(batch.candidates),
            "supplier_b_shipping_missing": shipping_missing,
            "total_tokens": batch.run.total_tokens if batch.run else None,
        }
        print(json.dumps(summary, ensure_ascii=False))
        return (0 if all_checks_passed else 2), summary
    except ExtractionError as exc:
        record = {
            "result_kind": "REAL_MODEL_EXTRACTION",
            "environment": "LOCAL",
            "input_is_synthetic": True,
            "dataset_version": dataset_version,
            "input_format": input_format,
            "correction_state": "PRE_CORRECTION",
            "status": "FAILED",
            "input": str(input_path),
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "provider": config.provider,
            "model_id": config.model_id,
            "calls_used": budget.calls_used,
            "error": {"code": exc.code, "message": str(exc), "details": exc.details},
        }
        _write_result(output_path, record)
        summary = {
            "supplier": supplier_alias,
            "dataset_version": dataset_version,
            "input_format": input_format,
            "status": "FAILED",
            "output": str(output_path),
            "error_code": exc.code,
            "calls_used_for_supplier": budget.calls_used - calls_before,
            "calls_used_total": budget.calls_used,
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 1, summary


def _exit_code(codes: list[int]) -> int:
    if any(code == 1 for code in codes):
        return 1
    if any(code == 2 for code in codes):
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--supplier", choices=sorted(SUPPLIERS))
    target.add_argument("--all-suppliers", action="store_true")
    parser.add_argument("--dataset-version", choices=sorted(DATASET_VERSIONS), default="V1")
    parser.add_argument("--input-format", choices=("pdf", "csv"), default="pdf")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    if args.input_format == "csv" and args.dataset_version != "V2":
        parser.error("--input-format csv currently requires --dataset-version V2")

    if args.all_suppliers:
        if args.output_dir is None or args.output is not None:
            parser.error("--all-suppliers requires --output-dir and does not accept --output")
        jobs = [
            (
                alias,
                args.output_dir
                / (
                    f"{args.dataset_version.lower()}_{args.input_format}_supplier_"
                    f"{alias.lower()}_pre_correction.json"
                ),
            )
            for alias in sorted(SUPPLIERS)
        ]
    else:
        if args.output is None or args.output_dir is not None:
            parser.error("--supplier requires --output and does not accept --output-dir")
        jobs = [(args.supplier, args.output)]

    config = OpenAICompatibleConfig.from_env()
    dictionary = QuoteDictionary.load(REPO_ROOT / "data/contracts/quote_data_field.csv")
    budget = ModelCallBudget(graph_run_id=f"graph_local_{uuid4().hex}")
    results = [
        _run_supplier(
            alias,
            args.dataset_version,
            args.input_format,
            output,
            config,
            dictionary,
            budget,
        )
        for alias, output in jobs
    ]
    codes = [code for code, _summary in results]
    if args.all_suppliers:
        batch_status = (
            "FAILED"
            if any(code == 1 for code in codes)
            else "FAILED_OUTPUT_QUALITY"
            if any(code == 2 for code in codes)
            else "PASSED"
        )
        print(
            json.dumps(
                {
                    "status": batch_status,
                    "dataset_version": args.dataset_version,
                    "input_format": args.input_format,
                    "suppliers_run": len(results),
                    "calls_used": budget.calls_used,
                    "results": [summary for _code, summary in results],
                },
                ensure_ascii=False,
            )
        )
    return _exit_code(codes)


if __name__ == "__main__":
    raise SystemExit(main())
