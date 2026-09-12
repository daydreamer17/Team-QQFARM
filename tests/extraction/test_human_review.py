from __future__ import annotations

from datetime import datetime, timezone

from supplier_comparison.extraction.contracts import (
    ExtractionBatch,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.human_review import create_review_event
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.review_contracts import (
    HumanReviewAction,
    ReviewStatus,
)

from .conftest import context_for, quotes_csv_path


NOW = datetime(2026, 9, 11, 13, 0, tzinfo=timezone.utc)
CONTEXT = CriticalityContext(required_revision="R1", base_unit="piece")


def _supplier_b(quote_dictionary) -> ExtractionBatch:
    return FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("b"), 3
    )


def _replace_with_conflict(batch: ExtractionBatch, field_name: str) -> ExtractionBatch:
    candidates = []
    for candidate in batch.candidates:
        if candidate.field_name != field_name:
            candidates.append(candidate)
            continue
        values = candidate.model_dump(mode="python")
        values.update(
            raw_value=f"Conflicting {candidate.raw_value}",
            normalized_value=None,
            validation_status=ValidationStatus.CONFLICT,
        )
        candidates.append(QuoteFieldCandidate.model_validate(values))
    values = batch.model_dump(mode="python")
    values["candidates"] = tuple(candidates)
    return ExtractionBatch.model_validate(values)


def _replace_with_missing(batch: ExtractionBatch, field_name: str) -> ExtractionBatch:
    candidates = []
    for candidate in batch.candidates:
        if candidate.field_name != field_name:
            candidates.append(candidate)
            continue
        values = candidate.model_dump(mode="python")
        values.update(
            raw_value=None,
            normalized_value=None,
            unit=None,
            validation_status=ValidationStatus.MISSING,
            origin=None,
            source_refs=(),
        )
        candidates.append(QuoteFieldCandidate.model_validate(values))
    values = batch.model_dump(mode="python")
    values["candidates"] = tuple(candidates)
    return ExtractionBatch.model_validate(values)


def test_confirmed_critical_missing_can_be_handed_to_c_as_pending(quote_dictionary) -> None:
    batch = _replace_with_missing(_supplier_b(quote_dictionary), "packaging_type")
    batch = _replace_with_missing(batch, "units_per_pack")
    event = create_review_event(
        batch,
        field_name="shipping_fee_status",
        action=HumanReviewAction.CONFIRM_MISSING,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=(event,),
    )

    candidate = next(
        item for item in batch.candidates if item.field_name == "shipping_fee_status"
    )
    assert candidate.validation_status == ValidationStatus.MISSING
    assert candidate.origin is None
    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True
    assert envelope.calculation_inputs_complete is False
    assert envelope.review is not None
    assert "packaging_type" not in envelope.review.blocking_fields
    assert "units_per_pack" not in envelope.review.blocking_fields
    assert any(
        finding.resolution_event_id == event.review_event_id
        and "HUMAN_CONFIRMED_MISSING" in finding.codes
        for finding in envelope.review.findings
    )


def test_stale_human_review_event_rejects_the_batch(quote_dictionary) -> None:
    batch = _supplier_b(quote_dictionary)
    event = create_review_event(
        batch,
        field_name="shipping_fee_status",
        action=HumanReviewAction.CONFIRM_MISSING,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    ).model_copy(update={"quote_version": 2})
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=(event,),
    )

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.review is not None
    assert any(
        "HUMAN_REVIEW_EVENT_INVALID" in finding.codes
        for finding in envelope.review.findings
    )


def test_confirmed_document_conflict_stays_non_calculable(quote_dictionary) -> None:
    batch = _replace_with_conflict(_supplier_b(quote_dictionary), "unit_price")
    conflict_candidate = next(
        item for item in batch.candidates if item.field_name == "unit_price"
    )
    event = create_review_event(
        batch,
        field_name="unit_price",
        action=HumanReviewAction.CONFIRM_CONFLICT,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
        basis_source_ids=tuple(ref.source_id for ref in conflict_candidate.source_refs),
    )
    shipping_event = create_review_event(
        batch,
        field_name="shipping_fee_status",
        action=HumanReviewAction.CONFIRM_MISSING,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=(event, shipping_event),
    )

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.calculation_inputs_complete is False
