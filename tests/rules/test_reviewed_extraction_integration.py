from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from supplier_comparison.extraction.contracts import (
    AdapterEnvironment,
    DocumentContext,
    ExtractionBatch,
)
from supplier_comparison.extraction.corrections import apply_candidate_correction
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import DownstreamNotReadyError
from supplier_comparison.extraction.human_review import create_review_event
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.review_contracts import (
    CorrectionAction,
    CorrectionState,
    HumanReviewAction,
    ReviewEnvelope,
    ReviewStatus,
)
from supplier_comparison.rules import (
    ComparisonDisposition,
    FeasibilityStatus,
    ProcurementRequirement,
    compare_reviewed_extractions,
    quote_input_from_reviewed_extraction,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "data/contracts/quote_data_field.csv"
REQUIREMENT_PATH = ROOT / "data/generated/inputs/development/quote_V2/procurement_requirement_v2.csv"
QUOTES_PATH = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"
NOW = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
REVIEWED_AT = datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)


def _requirement() -> ProcurementRequirement:
    with REQUIREMENT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    row["secondary_preference"] = row["secondary_preference"] or None
    return ProcurementRequirement.model_validate(row)


def _context(alias: str) -> DocumentContext:
    supplier_numbers = {"A": "022", "B": "023", "C": "024"}
    return DocumentContext(
        task_id="TASK-MCU-DEMO-001",
        task_revision=1,
        scenario_id="MCU-DEMO-001",
        quote_id=f"QUOTE-MCU-DEMO-001-{alias}",
        quote_version=1,
        document_id=f"DOC-MCU-DEMO-001-{alias}-V1",
        document_version=1,
        supplier_id=f"SUP-{supplier_numbers[alias]}",
    )


def _batches(dictionary: QuoteDictionary):
    parser = FixedCsvQuoteParser(dictionary)
    return {
        alias: parser.parse_row(QUOTES_PATH, _context(alias), row_number)
        for alias, row_number in (("A", 2), ("B", 3), ("C", 4))
    }


def _review(batch, dictionary, *, review_events=(), corrections=()):
    return review_extraction_batch(
        batch,
        dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=REVIEWED_AT,
        review_events=review_events,
        corrections=corrections,
    )


def test_unreviewed_missing_quote_is_blocked_before_c() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    supplier_b = _review(_batches(dictionary)["B"], dictionary)

    with pytest.raises(DownstreamNotReadyError) as raised:
        quote_input_from_reviewed_extraction(supplier_b)
    assert raised.value.code == "extraction_not_ready_for_downstream"
    assert raised.value.details["review_status"] == "REVIEW_REQUIRED"


def test_schema_1_1_reviewed_batch_reaches_c_without_changing_rule_input() -> None:
    """C accepts B's new envelope/batch version without consuming OCR metadata."""

    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    legacy_batch = _batches(dictionary)["A"]
    payload = legacy_batch.model_dump(mode="json")
    payload["schema_version"] = "1.1"
    schema_1_1_batch = ExtractionBatch.model_validate(payload)

    envelope = _review(schema_1_1_batch, dictionary)
    quote = quote_input_from_reviewed_extraction(envelope)

    assert envelope.batch is not None
    assert envelope.batch.schema_version == "1.1"
    assert quote.quote_id == legacy_batch.parsed_input.context.quote_id
    assert quote.candidates


def test_b_detected_critical_conflict_is_blocked_before_c() -> None:
    """C trusts B's gate and must never calculate a quote carrying a conflict."""

    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    payload = _batches(dictionary)["A"].model_dump(mode="json")
    revision = next(
        candidate
        for candidate in payload["candidates"]
        if candidate["field_name"] == "revision"
    )
    revision["validation_status"] = "CONFLICT"
    conflict_batch = ExtractionBatch.model_validate(payload)
    envelope = _review(conflict_batch, dictionary)

    assert envelope.downstream_ready is False
    with pytest.raises(DownstreamNotReadyError) as raised:
        quote_input_from_reviewed_extraction(envelope)
    assert raised.value.code == "extraction_not_ready_for_downstream"


def test_reviewed_adapter_excludes_noncritical_display_fields() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    envelope = _review(_batches(dictionary)["A"], dictionary)

    quote = quote_input_from_reviewed_extraction(envelope)

    field_names = {candidate.field_name for candidate in quote.candidates}
    assert field_names.isdisjoint(
        {"supplier_country", "category", "item", "payment_terms"}
    )
    assert len(field_names) == 26


def test_user_correction_without_its_event_is_rejected() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    batches = _batches(dictionary)
    corrected_c, _omitted_event = apply_candidate_correction(
        batches["C"],
        field_name="shipping_fee_status",
        action=CorrectionAction.USER_CORRECTION,
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        unit=None,
        reason_code="TEST_CORRECTION",
        reason="Exercise the correction audit gate without local model artifacts.",
        reviewer_id="reviewer-1",
        reviewed_at=REVIEWED_AT,
    )

    supplier_a = _review(batches["A"], dictionary)
    supplier_b = _review(batches["B"], dictionary)
    supplier_c = _review(corrected_c, dictionary)

    assert supplier_a.review_status.value == "READY_FOR_DOWNSTREAM"
    assert supplier_b.review_status.value == "REVIEW_REQUIRED"
    assert supplier_b.review is not None
    assert supplier_b.review.blocking_fields == ("shipping_fee_status",)
    assert supplier_c.review_status.value == "REJECTED"
    assert supplier_c.review is not None
    assert any(
        "CORRECTION_AUDIT_MISSING" in finding.codes
        for finding in supplier_c.review.findings
    )
    with pytest.raises(DownstreamNotReadyError):
        quote_input_from_reviewed_extraction(supplier_c)


def test_model_failed_envelope_is_blocked_before_c() -> None:
    failed = ReviewEnvelope(
        environment=AdapterEnvironment.LOCAL,
        input_is_synthetic=False,
        correction_state=CorrectionState.PRE_CORRECTION,
        review_status=ReviewStatus.MODEL_FAILED,
        downstream_ready=False,
        calculation_inputs_complete=False,
        errors=("provider request failed",),
    )

    with pytest.raises(DownstreamNotReadyError) as raised:
        quote_input_from_reviewed_extraction(failed)
    assert raised.value.details["review_status"] == "MODEL_FAILED"


def test_confirmed_missing_reaches_c_as_pending_without_final_recommendation() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    batches = _batches(dictionary)
    shipping_review = create_review_event(
        batches["B"],
        field_name="shipping_fee_status",
        action=HumanReviewAction.CONFIRM_MISSING,
        reviewer_id="reviewer-1",
        reviewed_at=REVIEWED_AT,
    )
    envelopes = (
        _review(batches["A"], dictionary),
        _review(batches["B"], dictionary, review_events=(shipping_review,)),
        _review(batches["C"], dictionary),
    )

    result = compare_reviewed_extractions(
        _requirement(), envelopes, evaluated_at=NOW
    )

    supplier_b = next(
        item for item in result.supplier_results if item.quote_id.endswith("-B")
    )
    assert envelopes[1].downstream_ready is True
    assert envelopes[1].calculation_inputs_complete is False
    assert supplier_b.status == FeasibilityStatus.PENDING
    assert supplier_b.total_cost is None
    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.final_recommendation_allowed is False


def test_corrected_shipping_reaches_c_with_confirmed_total() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    batches = _batches(dictionary)
    with_status, status_event = apply_candidate_correction(
        batches["B"],
        field_name="shipping_fee_status",
        action=CorrectionAction.USER_INPUT,
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        unit=None,
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Buyer supplied the missing shipping status.",
        reviewer_id="reviewer-1",
        reviewed_at=REVIEWED_AT,
    )
    corrected_b, amount_event = apply_candidate_correction(
        with_status,
        field_name="shipping_fee_amount",
        action=CorrectionAction.USER_INPUT,
        raw_value="S$200.00",
        normalized_value="200.00",
        unit="SGD",
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Buyer supplied the missing shipping amount.",
        reviewer_id="reviewer-1",
        reviewed_at=REVIEWED_AT,
    )
    envelopes = (
        _review(batches["A"], dictionary),
        _review(
            corrected_b,
            dictionary,
            corrections=(status_event, amount_event),
        ),
        _review(batches["C"], dictionary),
    )

    result = compare_reviewed_extractions(
        _requirement(), envelopes, evaluated_at=NOW
    )

    supplier_b = next(
        item for item in result.supplier_results if item.quote_id.endswith("-B")
    )
    assert envelopes[1].calculation_inputs_complete is True
    assert supplier_b.status == FeasibilityStatus.FEASIBLE
    assert str(supplier_b.total_cost) == "7000.00"
    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.recommended_quote_ids == ("QUOTE-MCU-DEMO-001-B",)
