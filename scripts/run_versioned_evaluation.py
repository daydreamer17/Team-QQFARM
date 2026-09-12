#!/usr/bin/env python3
"""Run V5 or V6 inputs without exposing offline reference answers to runtime."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import (
    AdapterEnvironment,
    DocumentContext,
    ExtractionBatch,
)
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ExtractionError
from supplier_comparison.extraction.hybrid_csv import (
    RegisteredHybridCsvParser,
    merge_semantic_review,
    selected_dictionary,
)
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.service import extract_quote_candidates


REPO_ROOT = Path(__file__).resolve().parents[1]
INPUTS_ROOT = REPO_ROOT / "data/generated/inputs"
DICTIONARY_PATH = REPO_ROOT / "data/contracts/quote_data_field.csv"
V5_BATCHES = {
    "V5-BATCH-1": ("A", "B", "C", "D"),
    "V5-BATCH-2": ("E", "F", "G", "H"),
}
V6_SPLIT_CODES = {
    "development": ("DEV", "D"),
    "calibration": ("CAL", "C"),
    "holdout": ("HOLD", "H"),
}


@dataclass(frozen=True, slots=True)
class EvaluationJob:
    case_id: str
    input_path: Path
    context: DocumentContext


def _relative(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _v5_jobs(batch_id: str, input_format: str) -> tuple[EvaluationJob, ...]:
    jobs = []
    for alias in V5_BATCHES[batch_id]:
        path = (
            INPUTS_ROOT
            / "development"
            / "quote_V5"
            / f"supplier_{alias.lower()}_quote_v5.{input_format}"
        )
        jobs.append(
            EvaluationJob(
                case_id=f"V5-{alias}-{input_format.upper()}",
                input_path=path,
                context=DocumentContext(
                    task_id=f"TASK-{batch_id}-LOCAL-EVAL",
                    task_revision=5,
                    scenario_id="QUOTE-V5-GENERALIZATION",
                    quote_id=f"QUOTE-V5-{alias}",
                    quote_version=1,
                    document_id=f"DOC-V5-{alias}-{input_format.upper()}",
                    document_version=1,
                    supplier_id=f"V5-SUP-{alias}",
                ),
            )
        )
    return tuple(jobs)


def _v6_jobs(split: str) -> tuple[EvaluationJob, ...]:
    case_code, supplier_prefix = V6_SPLIT_CODES[split]
    directory = INPUTS_ROOT / split / "quote_V6"
    jobs = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".csv", ".pdf"}:
            continue
        number = path.stem.split("_")[1]
        case_id = f"V6-{case_code}-{number}"
        jobs.append(
            EvaluationJob(
                case_id=case_id,
                input_path=path,
                context=DocumentContext(
                    task_id=f"TASK-V6-{case_code}-LOCAL-EVAL",
                    task_revision=6,
                    scenario_id=case_id,
                    quote_id=f"QUOTE-{case_id}",
                    quote_version=1,
                    document_id=f"DOC-{case_id}",
                    document_version=1,
                    supplier_id=f"V6-SUP-{supplier_prefix}{number}",
                ),
            )
        )
    if len(jobs) != 5:
        raise ValueError(f"V6 {split} must contain exactly five inputs")
    return tuple(jobs)


def _extract_job(
    job: EvaluationJob,
    dictionary: QuoteDictionary,
    adapter: OpenAICompatibleAdapter,
    budget: ModelCallBudget,
) -> tuple[ExtractionBatch, str, tuple[str, ...]]:
    if job.input_path.suffix.lower() == ".pdf":
        parsed = PdfQuoteParser().parse(job.input_path, job.context)
        batch = extract_quote_candidates(
            parsed,
            dictionary,
            adapter,
            budget,
            f"extract_{uuid4().hex}",
        )
        return batch, "PDF_TEXT_MODEL", tuple(
            definition.field_name for definition in dictionary.extractable_fields
        )

    parsed = RegisteredHybridCsvParser(dictionary).parse_row(
        job.input_path,
        job.context,
    )
    if not parsed.semantic_review_fields:
        return parsed.batch, "REGISTERED_CSV_DETERMINISTIC", ()
    semantic_batch = extract_quote_candidates(
        parsed.batch.parsed_input,
        selected_dictionary(dictionary, parsed.semantic_review_fields),
        adapter,
        budget,
        f"extract_{uuid4().hex}",
    )
    return (
        merge_semantic_review(
            parsed.batch,
            semantic_batch,
            parsed.semantic_review_fields,
        ),
        "REGISTERED_CSV_HYBRID",
        parsed.semantic_review_fields,
    )


def _job_requires_model(
    job: EvaluationJob,
    dictionary: QuoteDictionary,
) -> bool:
    """Return whether this route needs model configuration or credentials."""

    if job.input_path.suffix.lower() == ".pdf":
        return True
    parsed = RegisteredHybridCsvParser(dictionary).parse_row(
        job.input_path,
        job.context,
    )
    return bool(parsed.semantic_review_fields)


def _deterministic_only_config() -> OpenAICompatibleConfig:
    """Supply non-secret run metadata when every selected route uses zero calls."""

    return OpenAICompatibleConfig(
        provider="deterministic",
        model_id="no-model-call",
        base_url="https://not-used.invalid",
        environment=AdapterEnvironment.LOCAL,
        max_attempts=1,
    )


def _run_job(
    job: EvaluationJob,
    output_dir: Path,
    dictionary: QuoteDictionary,
    adapter: OpenAICompatibleAdapter,
    config: OpenAICompatibleConfig,
    budget: ModelCallBudget,
) -> tuple[int, dict[str, object]]:
    calls_before = budget.calls_used
    started_at = datetime.now(timezone.utc)
    output_path = output_dir / f"{job.case_id.lower()}_pre_correction.json"
    try:
        batch, route, semantic_fields = _extract_job(
            job,
            dictionary,
            adapter,
            budget,
        )
        envelope = review_extraction_batch(
            batch,
            dictionary,
            CriticalityContext(required_revision="R1", base_unit="piece"),
            input_is_synthetic=True,
            environment=config.environment,
        )
        record: dict[str, object] = {
            "result_kind": "VERSIONED_EXTRACTION_EVALUATION",
            "environment": config.environment.value,
            "input_is_synthetic": True,
            "dataset_version": job.case_id.split("-")[0],
            "case_id": job.case_id,
            "input": _relative(job.input_path),
            "input_format": job.input_path.suffix.lower().lstrip("."),
            "correction_state": "PRE_CORRECTION",
            "status": "PASSED",
            "route": route,
            "semantic_review_fields": semantic_fields,
            "model_calls_used": budget.calls_used - calls_before,
            "model_id": config.model_id,
            "batch": batch.model_dump(mode="json"),
            "review_envelope": envelope.model_dump(mode="json"),
        }
        code = 0
    except ExtractionError as exc:
        record = {
            "result_kind": "VERSIONED_EXTRACTION_EVALUATION",
            "environment": config.environment.value,
            "input_is_synthetic": True,
            "dataset_version": job.case_id.split("-")[0],
            "case_id": job.case_id,
            "input": _relative(job.input_path),
            "input_format": job.input_path.suffix.lower().lstrip("."),
            "correction_state": "PRE_CORRECTION",
            "status": "FAILED",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "model_calls_used": budget.calls_used - calls_before,
            "model_id": config.model_id,
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
        "model_calls_used": record["model_calls_used"],
        "output": _relative(output_path),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return code, summary


def _run_jobs_with_independent_budgets(
    jobs: tuple[EvaluationJob, ...],
    *,
    output_dir: Path,
    dictionary: QuoteDictionary,
    adapter: OpenAICompatibleAdapter,
    config: OpenAICompatibleConfig,
) -> tuple[list[tuple[int, dict[str, object]]], int]:
    """Keep the eight-call limit independent for every input document."""

    results: list[tuple[int, dict[str, object]]] = []
    total_calls = 0
    for job in jobs:
        budget = ModelCallBudget(
            graph_run_id=f"graph_eval_{job.case_id.lower()}_{uuid4().hex}"
        )
        result = _run_job(
            job,
            output_dir,
            dictionary,
            adapter,
            config,
            budget,
        )
        results.append(result)
        total_calls += budget.calls_used
    return results, total_calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-version", choices=("V5", "V6"), required=True)
    parser.add_argument("--split", choices=tuple(V6_SPLIT_CODES), default="development")
    parser.add_argument("--batch-id", choices=tuple(V5_BATCHES))
    parser.add_argument("--input-format", choices=("pdf", "csv"), default="pdf")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--case-id",
        action="append",
        help="Run only the named case; repeat to select multiple cases.",
    )
    args = parser.parse_args()

    if args.dataset_version == "V5":
        if args.batch_id is None:
            parser.error("V5 requires --batch-id to preserve the four-file task scope")
        if args.input_format != "pdf":
            parser.error("V5 stage-6 runner currently evaluates the selected PDF branch")
        jobs = _v5_jobs(args.batch_id, args.input_format)
    else:
        if args.batch_id is not None:
            parser.error("V6 does not use --batch-id")
        jobs = _v6_jobs(args.split)

    if args.case_id:
        selected = set(args.case_id)
        known = {job.case_id for job in jobs}
        unknown = selected - known
        if unknown:
            parser.error(f"unknown --case-id values: {', '.join(sorted(unknown))}")
        jobs = tuple(job for job in jobs if job.case_id in selected)

    dictionary = QuoteDictionary.load(DICTIONARY_PATH)
    config = (
        OpenAICompatibleConfig.from_env()
        if any(_job_requires_model(job, dictionary) for job in jobs)
        else _deterministic_only_config()
    )
    adapter = OpenAICompatibleAdapter(config)
    results, total_model_calls = _run_jobs_with_independent_budgets(
        jobs,
        output_dir=args.output_dir,
        dictionary=dictionary,
        adapter=adapter,
        config=config,
    )
    print(
        json.dumps(
            {
                "status": "FAILED" if any(code for code, _ in results) else "PASSED",
                "dataset_version": args.dataset_version,
                "split": args.split if args.dataset_version == "V6" else None,
                "batch_id": args.batch_id,
                "inputs_run": len(results),
                "model_calls_used": total_model_calls,
                "results": [summary for _, summary in results],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 1 if any(code for code, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
