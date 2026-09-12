#!/usr/bin/env python3
"""Run the V7 PDF parser/OCR boundary without loading reference answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.errors import ContractError, ExtractionError
from supplier_comparison.extraction.pdf_ocr import PdfOcrConfig
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "data/generated/manifests/quote_V7_manifest.json"
DEFAULT_TESSERACT = REPO_ROOT / ".ocr-tools/tesseract/bin/tesseract"
DEFAULT_TESSDATA = REPO_ROOT / ".ocr-tools/tesseract/share/tessdata"
ALLOWED_OPEN_SPLITS = frozenset({"development", "calibration"})


@dataclass(frozen=True, slots=True)
class V7ParserJob:
    case_id: str
    split: str
    input_file: str
    input_sha256: str
    page_count: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "v7_manifest_invalid",
            "V7 manifest is not readable JSON",
            path=str(path),
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(
            "v7_manifest_invalid",
            "V7 manifest must be a JSON object",
            path=str(path),
        )
    return value


def load_v7_jobs(
    manifest_path: Path,
    split: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], tuple[V7ParserJob, ...]]:
    """Validate open-split inputs without opening any reference-answer file."""

    if split == "holdout":
        raise ContractError(
            "v7_holdout_run_blocked",
            "V7 holdout may run only after the Stage 6 freeze and one-time approval",
        )
    if split not in ALLOWED_OPEN_SPLITS:
        raise ContractError(
            "v7_split_invalid",
            "only V7 development and calibration are open before the holdout gate",
            split=split,
        )
    manifest = _load_json(manifest_path)
    if manifest.get("dataset_version") != "V7":
        raise ContractError(
            "v7_manifest_version_invalid",
            "manifest dataset_version must be V7",
        )
    jobs = []
    for item in manifest.get("cases", []):
        if not isinstance(item, dict) or item.get("split") != split:
            continue
        if item.get("reference_visibility") != "AVAILABLE_TO_B":
            raise ContractError(
                "v7_split_not_open",
                "selected V7 split is not available to B",
                case_id=item.get("case_id"),
            )
        input_file = item.get("input_file")
        if not isinstance(input_file, str):
            raise ContractError(
                "v7_manifest_case_invalid",
                "V7 case is missing input_file",
                case_id=item.get("case_id"),
            )
        path = (repo_root / input_file).resolve()
        try:
            path.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise ContractError(
                "v7_manifest_input_invalid",
                "V7 input escapes the repository",
                input_file=input_file,
            ) from exc
        if not path.is_file() or _sha256(path) != item.get("input_sha256"):
            raise ContractError(
                "v7_manifest_input_hash_mismatch",
                "V7 input is missing or differs from the manifest",
                input_file=input_file,
            )
        jobs.append(
            V7ParserJob(
                case_id=str(item["case_id"]),
                split=split,
                input_file=input_file,
                input_sha256=str(item["input_sha256"]),
                page_count=int(item["page_count"]),
            )
        )
    if not jobs:
        raise ContractError(
            "v7_split_empty",
            "manifest contains no cases for the selected V7 split",
            split=split,
        )
    return manifest, tuple(jobs)


def run_v7_parser_ocr(
    manifest_path: Path,
    split: str,
    *,
    repo_root: Path = REPO_ROOT,
    tesseract_binary: Path = DEFAULT_TESSERACT,
    tessdata_dir: Path = DEFAULT_TESSDATA,
) -> dict[str, Any]:
    manifest, jobs = load_v7_jobs(manifest_path, split, repo_root=repo_root)
    parser = PdfQuoteParser(
        quality_config=PdfQualityConfig(ocr_enabled=True),
        ocr_config=PdfOcrConfig(
            tesseract_binary=str(tesseract_binary.resolve()),
            tessdata_dir=str(tessdata_dir.resolve()),
        ),
    )
    cases = []
    for job in jobs:
        started_at = time.monotonic()
        path = repo_root / job.input_file
        context = DocumentContext(
            task_id=f"v7-{split}-parser-ocr",
            task_revision=1,
            scenario_id="V7-PARSER-OCR-EVALUATION",
            quote_id=job.case_id,
            quote_version=1,
            document_id=f"{job.case_id}-DOCUMENT",
            document_version=1,
        )
        try:
            parsed = parser.parse(path, context)
        except ExtractionError as exc:
            cases.append(
                {
                    "case_id": job.case_id,
                    "input_file": job.input_file,
                    "input_sha256": job.input_sha256,
                    "expected_page_count": job.page_count,
                    "status": "FAILED",
                    "duration_seconds": round(time.monotonic() - started_at, 6),
                    "error": {
                        "code": exc.code,
                        "type": type(exc).__name__,
                        "details": exc.details,
                    },
                }
            )
        else:
            cases.append(
                {
                    "case_id": job.case_id,
                    "input_file": job.input_file,
                    "input_sha256": job.input_sha256,
                    "expected_page_count": job.page_count,
                    "status": "PASSED",
                    "duration_seconds": round(time.monotonic() - started_at, 6),
                    "parsed_input": parsed.model_dump(mode="json"),
                }
            )
    return {
        "result_kind": "V7_PARSER_OCR_RUN",
        "dataset_version": "V7",
        "split": split,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "reference_answers_loaded": False,
        "external_model_calls": 0,
        "ocr_environment": {
            "tesseract_binary": str(tesseract_binary),
            "tessdata_dir": str(tessdata_dir),
        },
        "synthetic_only": manifest.get("synthetic_only"),
        "case_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split",
        required=True,
        help="development or calibration; holdout is intentionally blocked",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--tesseract-binary", type=Path, default=DEFAULT_TESSERACT)
    parser.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_v7_parser_ocr(
            args.manifest,
            args.split,
            tesseract_binary=args.tesseract_binary,
            tessdata_dir=args.tessdata_dir,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(case["status"] == "PASSED" for case in result["cases"])
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "output": str(args.output),
                "cases_passed": passed,
                "cases_failed": result["case_count"] - passed,
                "cases_total": result["case_count"],
                "external_model_calls": 0,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
