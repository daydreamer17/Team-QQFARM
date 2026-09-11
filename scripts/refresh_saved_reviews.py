#!/usr/bin/env python3
"""Re-run deterministic review over saved extraction batches without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supplier_comparison.extraction.contracts import AdapterEnvironment, ExtractionBatch
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.review import review_extraction_batch


REPO_ROOT = Path(__file__).resolve().parents[1]


def refresh_saved_reviews(results_root: Path, dictionary_path: Path) -> dict[str, int]:
    dictionary = QuoteDictionary.load(dictionary_path)
    refreshed = 0
    skipped = 0
    for path in sorted(results_root.rglob("*_pre_correction.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "PASSED" or not isinstance(payload.get("batch"), dict):
            skipped += 1
            continue
        batch = ExtractionBatch.model_validate(payload["batch"])
        envelope = review_extraction_batch(
            batch,
            dictionary,
            CriticalityContext(required_revision="R1", base_unit="piece"),
            input_is_synthetic=bool(payload.get("input_is_synthetic")),
            environment=AdapterEnvironment(payload.get("environment", "LOCAL")),
        )
        payload["review_envelope"] = envelope.model_dump(mode="json")
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        refreshed += 1
    return {"refreshed": refreshed, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=REPO_ROOT / "data/contracts/quote_data_field.csv",
    )
    args = parser.parse_args()
    summary = refresh_saved_reviews(args.results_root, args.dictionary)
    print(json.dumps({"status": "PASSED", **summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
