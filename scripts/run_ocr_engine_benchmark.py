#!/usr/bin/env python3
"""Run one stage 3 OCR candidate on the frozen development set."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from supplier_comparison.ocr.benchmark import page_accuracy, percentile
from supplier_comparison.ocr.engine import (
    OcrEngineError,
    PaddleStructureV3Engine,
    TesseractEngine,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / ".ocr-cache/stage3_dataset/manifest.json"
DEFAULT_TESSERACT = REPO_ROOT / ".ocr-tools/tesseract/bin/tesseract"
DEFAULT_TESSDATA = REPO_ROOT / ".ocr-tools/tesseract/share/tessdata"


class PeakRssSampler:
    def __init__(self, interval_seconds: float = 0.02) -> None:
        self.interval_seconds = interval_seconds
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> "PeakRssSampler":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._capture()

    def _capture(self) -> None:
        process = psutil.Process()
        processes = [process]
        try:
            processes.extend(process.children(recursive=True))
        except (psutil.Error, OSError):
            pass
        total = 0
        for item in processes:
            try:
                total += item.memory_info().rss
            except (psutil.Error, OSError):
                continue
        self.peak_bytes = max(self.peak_bytes, total)

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._capture()


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset_kind") != "DEVELOPMENT":
        raise ValueError("OCR benchmark only accepts a development manifest")
    if payload.get("holdout_eligible") is not False:
        raise ValueError("stage 3 benchmark data must be ineligible as holdout")
    if payload.get("render_dpi") != 300:
        raise ValueError("stage 3 benchmark must use 300 DPI")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 6 <= len(cases) <= 8:
        raise ValueError("stage 3 benchmark must contain 6 to 8 pages")
    return payload


def _engine(name: str, args: argparse.Namespace):
    if name == "tesseract":
        return TesseractEngine(
            args.tesseract_binary,
            language="eng",
            page_segmentation_mode=3,
            tessdata_dir=args.tessdata_dir,
        )
    if name == "pp_structure_v3":
        return PaddleStructureV3Engine(device="cpu")
    raise ValueError(f"unknown OCR engine: {name}")


def _directory_size(paths: list[Path]) -> int:
    total = 0
    seen: set[Path] = set()
    for root in paths:
        if not root.exists():
            continue
        for path in [root, *root.rglob("*")]:
            if not path.is_file():
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            total += path.stat().st_size
    return total


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    manifest = _load_manifest(args.manifest)
    accuracy_pages = []
    page_seconds = []
    endurance_completed = 0
    engine = None
    error = None

    with PeakRssSampler() as memory:
        startup_started = time.perf_counter()
        try:
            engine = _engine(args.engine, args)
            engine.start()
            cold_start_seconds = time.perf_counter() - startup_started
            for case in manifest["cases"]:
                image_path = (REPO_ROOT / case["image_path"]).resolve()
                started = time.perf_counter()
                page_result = engine.recognize(
                    image_path,
                    timeout_seconds=args.page_timeout_seconds,
                )
                elapsed = time.perf_counter() - started
                page_seconds.append(elapsed)
                if page_result.image_sha256 != case["image_sha256"]:
                    raise ValueError(f"image hash mismatch after OCR: {case['case_id']}")
                metrics = page_accuracy(
                    page_result,
                    expected_text=case["expected_text"],
                    critical_tokens=case["critical_tokens"],
                    associations=case["table_associations"],
                )
                accuracy_pages.append(
                    {
                        "case_id": case["case_id"],
                        "elapsed_seconds": round(elapsed, 6),
                        **metrics,
                    }
                )
                print(
                    json.dumps(
                        {
                            "event": "accuracy_page_complete",
                            "engine": args.engine,
                            "case_id": case["case_id"],
                            "seconds": round(elapsed, 3),
                        }
                    ),
                    flush=True,
                )

            cases = manifest["cases"]
            for index in range(args.endurance_pages):
                case = cases[index % len(cases)]
                image_path = (REPO_ROOT / case["image_path"]).resolve()
                started = time.perf_counter()
                engine.recognize(image_path, timeout_seconds=args.page_timeout_seconds)
                elapsed = time.perf_counter() - started
                page_seconds.append(elapsed)
                endurance_completed += 1
                print(
                    json.dumps(
                        {
                            "event": "endurance_page_complete",
                            "engine": args.engine,
                            "page": endurance_completed,
                            "seconds": round(elapsed, 3),
                        }
                    ),
                    flush=True,
                )
        except (MemoryError, OcrEngineError, OSError, ValueError) as exc:
            cold_start_seconds = time.perf_counter() - startup_started
            error = {
                "code": getattr(exc, "code", "ocr_benchmark_failed"),
                "error_type": type(exc).__name__,
                "message": str(exc),
                "details": getattr(exc, "details", {}),
            }

    critical_matched = sum(page["critical_tokens_matched"] for page in accuracy_pages)
    critical_total = sum(page["critical_tokens_total"] for page in accuracy_pages)
    associations_matched = sum(
        page["table_associations_matched"] for page in accuracy_pages
    )
    associations_total = sum(
        page["table_associations_total"] for page in accuracy_pages
    )
    install_paths = (
        [REPO_ROOT / ".ocr-tools/tesseract"]
        if args.engine == "tesseract"
        else [REPO_ROOT / ".venv-ocr", REPO_ROOT / ".ocr-cache/paddlex"]
    )
    result = {
        "result_kind": "V7_STAGE3_OCR_ENGINE_BENCHMARK",
        "status": "PASSED" if error is None else "FAILED",
        "engine": args.engine,
        "engine_version": engine.version() if engine is not None else None,
        "dataset_id": manifest["dataset_id"],
        "dataset_definition_sha256": manifest["definition_sha256"],
        "render_dpi": manifest["render_dpi"],
        "accuracy_pages_expected": len(manifest["cases"]),
        "accuracy_pages_completed": len(accuracy_pages),
        "critical_tokens_matched": critical_matched,
        "critical_tokens_total": critical_total,
        "critical_token_exact_rate": (
            critical_matched / critical_total if critical_total else 0.0
        ),
        "table_associations_matched": associations_matched,
        "table_associations_total": associations_total,
        "table_association_rate": (
            associations_matched / associations_total if associations_total else 0.0
        ),
        "mean_character_accuracy": (
            sum(page["character_accuracy"] for page in accuracy_pages)
            / len(accuracy_pages)
            if accuracy_pages
            else 0.0
        ),
        "coordinates_complete": bool(accuracy_pages)
        and all(page["coordinates_complete"] for page in accuracy_pages),
        "confidence_complete": bool(accuracy_pages)
        and all(page["confidence_complete"] for page in accuracy_pages),
        "cold_start_seconds": round(cold_start_seconds, 6),
        "p95_page_seconds": round(percentile(page_seconds, 0.95), 6),
        "peak_rss_bytes": memory.peak_bytes,
        "installation_bytes": _directory_size(install_paths),
        "endurance_pages_expected": args.endurance_pages,
        "endurance_pages_completed": endurance_completed,
        "oom": error is not None and error["error_type"] == "MemoryError",
        "offline_startup": False,
        "page_results": accuracy_pages,
        "error": error,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("tesseract", "pp_structure_v3"), required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tesseract-binary", type=Path, default=DEFAULT_TESSERACT)
    parser.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument("--page-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--endurance-pages", type=int, default=20)
    args = parser.parse_args()
    os.environ.setdefault(
        "PADDLE_PDX_CACHE_HOME",
        str(REPO_ROOT / ".ocr-cache/paddlex"),
    )
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    result = run_benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "engine": result["engine"],
                "output": str(args.output),
                "critical_token_exact_rate": result["critical_token_exact_rate"],
                "table_association_rate": result["table_association_rate"],
                "endurance_pages_completed": result["endurance_pages_completed"],
                "peak_rss_bytes": result["peak_rss_bytes"],
                "p95_page_seconds": result["p95_page_seconds"],
            }
        )
    )
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
