#!/usr/bin/env python3
"""Run the frozen V7 holdout exactly once without loading hidden answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from supplier_comparison.extraction.adapters import (
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError
from supplier_comparison.extraction.pdf_ocr import PdfOcrConfig
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig

try:
    from scripts.freeze_v7_stage6 import (
        COMPONENT_PATHS,
        REPO_ROOT,
        safe_runtime_config,
    )
    from scripts.run_v7_model_evaluation import (
        CANDIDATE_VERSION,
        _run_jobs_with_independent_budgets,
        _write_json,
    )
    from scripts.run_v7_parser_ocr import (
        DEFAULT_MANIFEST,
        DEFAULT_TESSDATA,
        DEFAULT_TESSERACT,
        V7ParserJob,
    )
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from freeze_v7_stage6 import COMPONENT_PATHS, REPO_ROOT, safe_runtime_config
    from run_v7_model_evaluation import (
        CANDIDATE_VERSION,
        _run_jobs_with_independent_budgets,
        _write_json,
    )
    from run_v7_parser_ocr import (
        DEFAULT_MANIFEST,
        DEFAULT_TESSDATA,
        DEFAULT_TESSERACT,
        V7ParserJob,
    )


DEFAULT_FREEZE = REPO_ROOT / "docs/v7/stage6/V7_2_STAGE6_FREEZE.json"
DEFAULT_MANIFEST = REPO_ROOT / "data/generated/manifests/quote_V7_2_manifest.json"
DEFAULT_DICTIONARY = REPO_ROOT / "data/contracts/quote_data_field.csv"
MARKER_NAME = "HOLDOUT_RUN_STARTED.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("v7_holdout_preflight_invalid", "holdout preflight JSON is unreadable", path=str(path)) from exc
    if not isinstance(value, dict):
        raise ContractError("v7_holdout_preflight_invalid", "holdout preflight JSON must be an object", path=str(path))
    return value


def verify_freeze(freeze_path: Path) -> dict[str, object]:
    freeze = _load_json(freeze_path)
    if freeze.get("result_kind") != "V7_STAGE6_CONFIGURATION_FREEZE" or freeze.get("status") != "FROZEN":
        raise ContractError("v7_holdout_freeze_invalid", "V7 holdout requires a valid frozen configuration")
    if freeze.get("candidate_version") != CANDIDATE_VERSION:
        raise ContractError(
            "v7_holdout_candidate_version_invalid",
            "V7.2 holdout requires a V7.2 configuration freeze",
        )
    expected_components = freeze.get("components")
    current_components = {
        relative: _sha256(REPO_ROOT / relative) for relative in COMPONENT_PATHS
    }
    if expected_components != current_components:
        changed = sorted(
            path
            for path in set(current_components) | set(expected_components or {})
            if not isinstance(expected_components, dict)
            or expected_components.get(path) != current_components.get(path)
        )
        raise ContractError(
            "v7_holdout_code_changed_after_freeze",
            "code changed after the V7 configuration freeze",
            changed_components=changed,
        )
    if freeze.get("runtime_config") != safe_runtime_config():
        raise ContractError(
            "v7_holdout_runtime_changed_after_freeze",
            "runtime configuration changed after the V7 freeze",
        )
    validated = freeze.get("validated_results")
    if not isinstance(validated, dict):
        raise ContractError("v7_holdout_freeze_invalid", "freeze lacks validated result identities")
    for split in ("development", "calibration"):
        item = validated.get(split)
        if not isinstance(item, dict):
            raise ContractError("v7_holdout_freeze_invalid", "freeze lacks a split result", split=split)
        path = REPO_ROOT / str(item.get("path"))
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise ContractError(
                "v7_holdout_validated_result_changed",
                "validated Development/Calibration score changed after freeze",
                split=split,
            )
    return freeze


def load_holdout_jobs(
    manifest_path: Path,
    freeze: dict[str, object],
) -> tuple[dict[str, object], tuple[V7ParserJob, ...]]:
    manifest = _load_json(manifest_path)
    dataset = freeze.get("dataset")
    if not isinstance(dataset, dict) or _sha256(manifest_path) != dataset.get("manifest_sha256"):
        raise ContractError("v7_holdout_manifest_changed_after_freeze", "V7 manifest changed after freeze")
    if (
        manifest.get("dataset_version") != CANDIDATE_VERSION
        or dataset.get("dataset_version") != CANDIDATE_VERSION
        or manifest.get("split") != "holdout"
    ):
        raise ContractError(
            "v7_holdout_dataset_version_invalid",
            "holdout manifest and freeze must identify V7.2",
        )
    jobs = []
    for item in manifest.get("cases", []):
        if not isinstance(item, dict) or item.get("split") != "holdout":
            continue
        if item.get("reference_visibility") != "HASH_ONLY_BEFORE_RUN":
            raise ContractError(
                "v7_holdout_visibility_invalid",
                "holdout cases must expose only hashes before the run",
                case_id=item.get("case_id"),
            )
        input_file = str(item["input_file"])
        path = (REPO_ROOT / input_file).resolve()
        try:
            path.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise ContractError("v7_holdout_input_invalid", "holdout input escapes the repository") from exc
        if not path.is_file() or _sha256(path) != item.get("input_sha256"):
            raise ContractError(
                "v7_holdout_input_hash_mismatch",
                "holdout input differs from the frozen manifest",
                case_id=item.get("case_id"),
            )
        jobs.append(
            V7ParserJob(
                case_id=str(item["case_id"]),
                split="holdout",
                input_file=input_file,
                input_sha256=str(item["input_sha256"]),
                page_count=int(item["page_count"]),
            )
        )
    if len(jobs) != 6:
        raise ContractError("v7_holdout_case_count_invalid", "V7 holdout must contain exactly six inputs")
    return manifest, tuple(jobs)


def create_one_time_marker(output_dir: Path, freeze_path: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    marker = output_dir / MARKER_NAME
    try:
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ContractError(
            "v7_holdout_already_started",
            "the frozen V7 holdout has already been started and cannot be rerun",
            marker=str(marker),
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(
            {
                "result_kind": "V7_HOLDOUT_ONE_TIME_MARKER",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "freeze_path": str(freeze_path),
                "freeze_sha256": _sha256(freeze_path),
                "reference_answers_loaded": False,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
    return marker


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    cli.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    cli.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    cli.add_argument("--tesseract-binary", type=Path, default=DEFAULT_TESSERACT)
    cli.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA)
    cli.add_argument("--output-dir", type=Path, required=True)
    cli.add_argument("--confirm-one-time", action="store_true", required=True)
    args = cli.parse_args()
    try:
        freeze = verify_freeze(args.freeze.resolve())
        manifest, jobs = load_holdout_jobs(args.manifest.resolve(), freeze)
        marker = create_one_time_marker(args.output_dir.resolve(), args.freeze.resolve())
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1

    dictionary = QuoteDictionary.load(args.dictionary)
    config = OpenAICompatibleConfig.from_env()
    parser = PdfQuoteParser(
        quality_config=PdfQualityConfig(ocr_enabled=True),
        ocr_config=PdfOcrConfig(
            tesseract_binary=str(args.tesseract_binary.resolve()),
            tessdata_dir=str(args.tessdata_dir.resolve()),
        ),
    )
    adapter = OpenAICompatibleAdapter(config)
    results, total_model_calls = _run_jobs_with_independent_budgets(
        jobs,
        output_dir=args.output_dir,
        parser=parser,
        dictionary=dictionary,
        adapter=adapter,
        config=config,
        graph_run_prefix="graph_v7_2_holdout",
    )
    summary = {
        "result_kind": "V7_HOLDOUT_ONE_TIME_RUN",
        "status": "FAILED" if any(code for code, _ in results) else "PASSED",
        "dataset_version": manifest.get("dataset_version"),
        "candidate_version": CANDIDATE_VERSION,
        "dataset_revision": manifest.get("dataset_revision"),
        "freeze_sha256": _sha256(args.freeze),
        "one_time_marker": str(marker),
        "reference_answers_loaded": False,
        "inputs_run": len(results),
        "model_id": config.model_id,
        "model_calls_used": total_model_calls,
        "results": [item for _, item in results],
    }
    _write_json(args.output_dir / "holdout_run_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if any(code for code, _ in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
