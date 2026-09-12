#!/usr/bin/env python3
"""Run V7 Development/Calibration extraction without loading reference answers."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError, ExtractionError
from supplier_comparison.extraction.pdf_ocr import PdfOcrConfig
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.service import extract_quote_candidates

try:
    from scripts.run_v7_parser_ocr import (
        DEFAULT_MANIFEST,
        DEFAULT_TESSDATA,
        DEFAULT_TESSERACT,
        REPO_ROOT,
        V7ParserJob,
        load_v7_jobs,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from run_v7_parser_ocr import (
        DEFAULT_MANIFEST,
        DEFAULT_TESSDATA,
        DEFAULT_TESSERACT,
        REPO_ROOT,
        V7ParserJob,
        load_v7_jobs,
    )


DICTIONARY_PATH = REPO_ROOT / "data/contracts/quote_data_field.csv"
CANDIDATE_VERSION = "V7.2"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(resolved)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _context(job: V7ParserJob) -> DocumentContext:
    return DocumentContext(
        task_id=f"TASK-V7-{job.split.upper()}-LOCAL-EVAL",
        task_revision=7,
        scenario_id=job.case_id,
        quote_id=f"QUOTE-{job.case_id}",
        quote_version=1,
        document_id=f"DOC-{job.case_id}",
        document_version=1,
    )


def _run_job(
    job: V7ParserJob,
    *,
    output_dir: Path,
    parser: PdfQuoteParser,
    dictionary: QuoteDictionary,
    adapter: OpenAICompatibleAdapter,
    config: OpenAICompatibleConfig,
    budget: ModelCallBudget,
) -> tuple[int, dict[str, object]]:
    started_at = datetime.now(timezone.utc)
    calls_before = budget.calls_used
    output_path = output_dir / f"{job.case_id.lower()}_pre_correction.json"
    try:
        parsed = parser.parse(REPO_ROOT / job.input_file, _context(job))
        batch = extract_quote_candidates(
            parsed,
            dictionary,
            adapter,
            budget,
            f"extract_{uuid4().hex}",
        )
        envelope = review_extraction_batch(
            batch,
            dictionary,
            CriticalityContext(required_revision="R1", base_unit="piece"),
            input_is_synthetic=True,
            environment=config.environment,
        )
        record: dict[str, object] = {
            "result_kind": "V7_EXTRACTION_EVALUATION",
            "dataset_version": "V7",
            "candidate_version": CANDIDATE_VERSION,
            "split": job.split,
            "environment": config.environment.value,
            "input_is_synthetic": True,
            "reference_answers_loaded": False,
            "case_id": job.case_id,
            "input": job.input_file,
            "input_sha256": job.input_sha256,
            "correction_state": "PRE_CORRECTION",
            "status": "PASSED",
            "route": "PDF_PAGE_ROUTED_MODEL",
            "model_id": config.model_id,
            "logical_graph_run_id": budget.graph_run_id,
            "model_calls_used": budget.calls_used - calls_before,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "batch": batch.model_dump(mode="json"),
            "review_envelope": envelope.model_dump(mode="json"),
        }
        code = 0
    except ExtractionError as exc:
        record = {
            "result_kind": "V7_EXTRACTION_EVALUATION",
            "dataset_version": "V7",
            "candidate_version": CANDIDATE_VERSION,
            "split": job.split,
            "environment": config.environment.value,
            "input_is_synthetic": True,
            "reference_answers_loaded": False,
            "case_id": job.case_id,
            "input": job.input_file,
            "input_sha256": job.input_sha256,
            "correction_state": "PRE_CORRECTION",
            "status": "FAILED",
            "model_id": config.model_id,
            "logical_graph_run_id": budget.graph_run_id,
            "model_calls_used": budget.calls_used - calls_before,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
            },
        }
        code = 1
    _write_json(output_path, record)
    summary = {
        "case_id": job.case_id,
        "status": record["status"],
        "review_status": (
            record.get("review_envelope", {}).get("review_status")
            if isinstance(record.get("review_envelope"), dict)
            else None
        ),
        "logical_graph_run_id": budget.graph_run_id,
        "model_calls_used": record["model_calls_used"],
        "output": _relative(output_path),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return code, summary


def _run_jobs_with_independent_budgets(
    jobs: tuple[V7ParserJob, ...],
    *,
    output_dir: Path,
    parser: PdfQuoteParser,
    dictionary: QuoteDictionary,
    adapter: OpenAICompatibleAdapter,
    config: OpenAICompatibleConfig,
    graph_run_prefix: str,
) -> tuple[list[tuple[int, dict[str, object]]], int]:
    """Run every document as its own logical graph run and aggregate calls."""

    results: list[tuple[int, dict[str, object]]] = []
    total_model_calls = 0
    for job in jobs:
        budget = ModelCallBudget(
            graph_run_id=f"{graph_run_prefix}_{job.case_id.lower()}_{uuid4().hex}"
        )
        result = _run_job(
            job,
            output_dir=output_dir,
            parser=parser,
            dictionary=dictionary,
            adapter=adapter,
            config=config,
            budget=budget,
        )
        results.append(result)
        total_model_calls += budget.calls_used
    return results, total_model_calls


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--split", choices=("development", "calibration", "holdout"), required=True)
    cli.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    cli.add_argument("--dictionary", type=Path, default=DICTIONARY_PATH)
    cli.add_argument("--tesseract-binary", type=Path, default=DEFAULT_TESSERACT)
    cli.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--case-id", action="append")
    args = cli.parse_args()

    try:
        manifest, jobs = load_v7_jobs(args.manifest, args.split)
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    if args.case_id:
        selected = set(args.case_id)
        known = {job.case_id for job in jobs}
        unknown = selected - known
        if unknown:
            cli.error(f"unknown --case-id values: {', '.join(sorted(unknown))}")
        jobs = tuple(job for job in jobs if job.case_id in selected)

    dictionary = QuoteDictionary.load(args.dictionary)
    config = OpenAICompatibleConfig.from_env()
    adapter = OpenAICompatibleAdapter(config)
    parser = PdfQuoteParser(
        quality_config=PdfQualityConfig(ocr_enabled=True),
        ocr_config=PdfOcrConfig(
            tesseract_binary=str(args.tesseract_binary.resolve()),
            tessdata_dir=str(args.tessdata_dir.resolve()),
        ),
    )
    results, total_model_calls = _run_jobs_with_independent_budgets(
        jobs,
        output_dir=args.output_dir,
        parser=parser,
        dictionary=dictionary,
        adapter=adapter,
        config=config,
        graph_run_prefix=f"graph_v7_2_{args.split}",
    )
    summary = {
        "status": "FAILED" if any(code for code, _ in results) else "PASSED",
        "dataset_version": "V7",
        "candidate_version": CANDIDATE_VERSION,
        "dataset_revision": manifest.get("dataset_revision"),
        "development_patch_revision": manifest.get("development_patch_revision"),
        "split": args.split,
        "inputs_run": len(results),
        "model_id": config.model_id,
        "model_calls_used": total_model_calls,
        "reference_answers_loaded": False,
        "results": [item for _, item in results],
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if any(code for code, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
