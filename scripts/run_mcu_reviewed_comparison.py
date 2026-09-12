"""Run the deterministic MCU comparison from reviewed extraction envelopes."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from supplier_comparison.extraction.corrections import apply_candidate_correction
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.review_contracts import (
    CorrectionAction,
    ReviewEnvelope,
)
from supplier_comparison.rules import (
    ComparisonDisposition,
    FeasibilityStatus,
    ProcurementRequirement,
    compare_reviewed_extractions,
)


DICTIONARY_PATH = REPO_ROOT / "data" / "contracts" / "quote_data_field.csv"
REQUIREMENT_PATH = (
    REPO_ROOT
    / "data"
    / "generated"
    / "inputs"
    / "development"
    / "quote_V2"
    / "procurement_requirement_v2.csv"
)
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "evaluation"
    / "results"
    / "local"
    / "2026-09-12"
    / "reviewed_comparison"
)
EVALUATED_AT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
REVIEWER_ID = "synthetic-demo-reviewer"


def _load_requirement() -> ProcurementRequirement:
    with REQUIREMENT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    row["secondary_preference"] = row["secondary_preference"] or None
    return ProcurementRequirement.model_validate(row)


def _load_envelope(path: Path) -> ReviewEnvelope:
    return ReviewEnvelope.model_validate_json(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _validate_before(result) -> None:
    by_id = {item.quote_id[-1]: item for item in result.supplier_results}
    assert by_id["A"].status == FeasibilityStatus.INFEASIBLE
    assert by_id["A"].actual_quantity == 2000
    assert str(by_id["A"].total_cost) == "12800.00"
    assert by_id["B"].status == FeasibilityStatus.PENDING
    assert str(by_id["B"].known_cost_subtotal) == "6800.00"
    assert by_id["B"].total_cost is None
    assert by_id["C"].status == FeasibilityStatus.FEASIBLE
    assert str(by_id["C"].total_cost) == "7100.00"
    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.final_recommendation_allowed is False


def _validate_after(result) -> None:
    by_id = {item.quote_id[-1]: item for item in result.supplier_results}
    assert by_id["B"].status == FeasibilityStatus.FEASIBLE
    assert str(by_id["B"].total_cost) == "7000.00"
    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.recommended_quote_ids == ("QUOTE-MCU-DEMO-001-B",)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelope-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    envelopes = tuple(
        _load_envelope(args.envelope_dir / f"supplier_{alias}_review_envelope.json")
        for alias in ("a", "b", "c")
    )
    requirement = _load_requirement()
    before = compare_reviewed_extractions(
        requirement,
        envelopes,
        evaluated_at=EVALUATED_AT,
    )
    _validate_before(before)

    reviewed_at = datetime.now(timezone.utc)
    supplier_b = envelopes[1]
    assert supplier_b.batch is not None
    with_status, status_event = apply_candidate_correction(
        supplier_b.batch,
        field_name="shipping_fee_status",
        action=CorrectionAction.USER_INPUT,
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        unit=None,
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Authorized buyer supplied the missing shipping status for the synthetic demo.",
        reviewer_id=REVIEWER_ID,
        reviewed_at=reviewed_at,
    )
    corrected_b, amount_event = apply_candidate_correction(
        with_status,
        field_name="shipping_fee_amount",
        action=CorrectionAction.USER_INPUT,
        raw_value="S$200.00",
        normalized_value="200.00",
        unit="SGD",
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Authorized buyer supplied the missing shipping amount for the synthetic demo.",
        reviewer_id=REVIEWER_ID,
        reviewed_at=reviewed_at,
    )
    dictionary = QuoteDictionary.load(DICTIONARY_PATH)
    completed_b = review_extraction_batch(
        corrected_b,
        dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=reviewed_at,
        corrections=(status_event, amount_event),
        source_result=supplier_b.source_result,
    )
    after_envelopes = (envelopes[0], completed_b, envelopes[2])
    after = compare_reviewed_extractions(
        requirement,
        after_envelopes,
        evaluated_at=EVALUATED_AT,
    )
    _validate_after(after)

    _write_json(
        args.output_dir / "comparison_before_b_shipping.json",
        before.model_dump(mode="json"),
    )
    _write_json(
        args.output_dir / "supplier_b_shipping_200_review_envelope.json",
        completed_b.model_dump(mode="json"),
    )
    _write_json(
        args.output_dir / "comparison_after_b_shipping.json",
        after.model_dump(mode="json"),
    )

    print(
        json.dumps(
            {
                "before": {
                    "disposition": before.disposition.value,
                    "final_recommendation_allowed": before.final_recommendation_allowed,
                    "blocking_pending_quote_ids": before.blocking_pending_quote_ids,
                },
                "after": {
                    "disposition": after.disposition.value,
                    "recommended_quote_ids": after.recommended_quote_ids,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
