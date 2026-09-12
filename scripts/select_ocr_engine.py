#!/usr/bin/env python3
"""Apply the frozen stage 3 hard gates to two benchmark results."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supplier_comparison.ocr.benchmark import OcrBenchmarkGates, select_engine


def _load(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"result must be a JSON object: {path}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tesseract-result", type=Path, required=True)
    parser.add_argument("--paddle-result", type=Path, required=True)
    parser.add_argument("--tesseract-offline", type=Path, required=True)
    parser.add_argument("--paddle-offline", type=Path, required=True)
    parser.add_argument("--worker-memory-limit-mb", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tesseract = _load(args.tesseract_result)
    paddle = _load(args.paddle_result)
    tesseract["offline_startup"] = (
        _load(args.tesseract_offline).get("status") == "PASSED"
    )
    paddle["offline_startup"] = _load(args.paddle_offline).get("status") == "PASSED"
    results = {"tesseract": tesseract, "pp_structure_v3": paddle}
    gates = OcrBenchmarkGates()
    decision = select_engine(
        results,
        worker_memory_limit_bytes=args.worker_memory_limit_mb * 1024 * 1024,
        gates=gates,
    )
    output = {
        "result_kind": "V7_STAGE3_OCR_ENGINE_SELECTION",
        "dataset_id": tesseract.get("dataset_id"),
        "worker_memory_limit_bytes": args.worker_memory_limit_mb * 1024 * 1024,
        "gates": {
            "critical_token_exact_rate": gates.critical_token_exact_rate,
            "table_association_rate": gates.table_association_rate,
            "endurance_pages": gates.endurance_pages,
            "peak_rss_fraction": gates.peak_rss_fraction,
            "p95_page_seconds": gates.p95_page_seconds,
            "coordinates_required": True,
            "confidence_required": True,
            "offline_startup_required": True,
        },
        "decision": decision,
        "engine_summaries": {
            name: {
                key: result.get(key)
                for key in (
                    "status",
                    "engine_version",
                    "critical_token_exact_rate",
                    "table_association_rate",
                    "mean_character_accuracy",
                    "coordinates_complete",
                    "confidence_complete",
                    "cold_start_seconds",
                    "p95_page_seconds",
                    "peak_rss_bytes",
                    "installation_bytes",
                    "endurance_pages_completed",
                    "oom",
                    "offline_startup",
                    "error",
                )
            }
            for name, result in results.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "PASSED", **decision, "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
