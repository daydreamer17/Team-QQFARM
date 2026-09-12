#!/usr/bin/env python3
"""Re-run deterministic review over saved extraction batches without model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supplier_comparison.extraction.contracts import (
    AdapterEnvironment,
    ExtractionBatch,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.model_payload import ModelExtractionPayload
from supplier_comparison.extraction.normalization import normalize_model_payload
from supplier_comparison.extraction.review import review_extraction_batch


REPO_ROOT = Path(__file__).resolve().parents[1]


def _renormalize_batch(batch: ExtractionBatch) -> ExtractionBatch:
    model_payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": candidate.field_name,
                    "raw_value": candidate.raw_value,
                    "normalized_value": candidate.normalized_value,
                    "unit": candidate.unit,
                    "validation_status": candidate.validation_status.value,
                    "source_refs": [
                        citation.model_dump(mode="python")
                        for citation in candidate.source_refs
                    ],
                }
                for candidate in batch.candidates
            ]
        }
    )
    normalized, events = normalize_model_payload(model_payload)
    normalized_by_field = {
        candidate.field_name: candidate for candidate in normalized.candidates
    }
    candidates = []
    for candidate in batch.candidates:
        replacement = normalized_by_field[candidate.field_name]
        candidates.append(
            QuoteFieldCandidate.model_validate(
                {
                    **candidate.model_dump(mode="python"),
                    "raw_value": replacement.raw_value,
                    "normalized_value": replacement.normalized_value,
                    "unit": replacement.unit,
                    "validation_status": replacement.validation_status,
                    "origin": (
                        None
                        if replacement.validation_status == ValidationStatus.MISSING
                        else candidate.origin or Origin.DOCUMENT
                    ),
                    "source_refs": replacement.source_refs,
                }
            )
        )
    existing_events = {
        (event.field_name, event.input_value, event.output_value, event.rule_id)
        for event in batch.normalization_events
    }
    new_events = tuple(
        event
        for event in events
        if (event.field_name, event.input_value, event.output_value, event.rule_id)
        not in existing_events
    )
    return batch.model_copy(
        update={
            "candidates": tuple(candidates),
            "normalization_events": (*batch.normalization_events, *new_events),
        }
    )


def refresh_saved_reviews(results_root: Path, dictionary_path: Path) -> dict[str, int]:
    dictionary = QuoteDictionary.load(dictionary_path)
    refreshed = 0
    skipped = 0
    for path in sorted(results_root.rglob("*_pre_correction.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "PASSED" or not isinstance(payload.get("batch"), dict):
            skipped += 1
            continue
        batch = _renormalize_batch(ExtractionBatch.model_validate(payload["batch"]))
        envelope = review_extraction_batch(
            batch,
            dictionary,
            CriticalityContext(required_revision="R1", base_unit="piece"),
            input_is_synthetic=bool(payload.get("input_is_synthetic")),
            environment=AdapterEnvironment(payload.get("environment", "LOCAL")),
        )
        payload["review_envelope"] = envelope.model_dump(mode="json")
        payload["batch"] = batch.model_dump(mode="json")
        payload["deterministic_replay"] = {
            "normalization_reapplied": True,
            "model_calls_added": 0,
        }
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
