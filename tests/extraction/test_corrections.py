from __future__ import annotations

from datetime import datetime, timezone

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    ExtractionBatch,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.extraction.corrections import apply_candidate_correction
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.review_contracts import (
    CorrectionAction,
    CorrectionState,
    ReviewStatus,
)

from .conftest import context_for, quotes_csv_path


NOW = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
CONTEXT = CriticalityContext(required_revision="R1", base_unit="piece")


def _supplier_b(quote_dictionary):
    return FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("b"), 3
    )


def _supplier_c_with_invalid_shipping_status(quote_dictionary) -> ExtractionBatch:
    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("c"), 4
    )
    candidates = []
    for candidate in batch.candidates:
        if candidate.field_name != "shipping_fee_status":
            candidates.append(candidate)
            continue
        values = candidate.model_dump(mode="python")
        values.update(
            raw_value="PAID",
            normalized_value="PAID",
            producer=CandidateProducer.MODEL_ADAPTER,
            adapter_version="test-adapter/1.0",
            prompt_version="test-prompt/1.0",
        )
        candidates.append(QuoteFieldCandidate.model_validate(values))
    values = batch.model_dump(mode="python")
    values["candidates"] = tuple(candidates)
    return ExtractionBatch.model_validate(values)


def test_user_input_is_immutable_versioned_and_re_reviewed(quote_dictionary) -> None:
    original = _supplier_b(quote_dictionary)
    with_status, status_event = apply_candidate_correction(
        original,
        field_name="shipping_fee_status",
        action=CorrectionAction.USER_INPUT,
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        unit=None,
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Buyer supplied the previously missing shipping status.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )
    corrected, amount_event = apply_candidate_correction(
        with_status,
        field_name="shipping_fee_amount",
        action=CorrectionAction.USER_INPUT,
        raw_value="S$200.00",
        normalized_value="200.00",
        unit="SGD",
        reason_code="AUTHORIZED_SHIPPING_ANSWER",
        reason="Buyer supplied the previously missing shipping amount.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )

    original_status = next(
        item for item in original.candidates if item.field_name == "shipping_fee_status"
    )
    corrected_status = next(
        item for item in corrected.candidates if item.field_name == "shipping_fee_status"
    )
    assert original_status.validation_status == ValidationStatus.MISSING
    assert original_status.origin is None
    assert corrected_status.validation_status == ValidationStatus.VERIFIED
    assert corrected_status.origin == Origin.USER_INPUT
    assert corrected_status.field_version == original_status.field_version + 1
    assert corrected_status.field_id != original_status.field_id
    assert corrected_status.source_refs == ()
    assert status_event.before.normalized_value is None
    assert status_event.after.normalized_value == "KNOWN_AMOUNT"

    envelope = review_extraction_batch(
        corrected,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        corrections=(status_event, amount_event),
    )
    assert envelope.correction_state == CorrectionState.POST_CORRECTION
    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True
    assert envelope.calculation_inputs_complete is True


def test_user_input_cannot_overwrite_an_existing_document_value(quote_dictionary) -> None:
    batch = _supplier_b(quote_dictionary)

    try:
        apply_candidate_correction(
            batch,
            field_name="unit_price",
            action=CorrectionAction.USER_INPUT,
            raw_value="7.00",
            normalized_value="7.00",
            unit="SGD",
            reason_code="WRONG_ACTION",
            reason="This should be a correction, not missing-field input.",
            reviewer_id="reviewer-1",
            reviewed_at=NOW,
        )
    except ValueError as exc:
        assert "USER_INPUT can only fill" in str(exc)
    else:
        raise AssertionError("USER_INPUT unexpectedly overwrote a document value")


def test_user_correction_preserves_before_after_and_document_basis(quote_dictionary) -> None:
    original = _supplier_c_with_invalid_shipping_status(quote_dictionary)
    before = next(
        item for item in original.candidates if item.field_name == "shipping_fee_status"
    )
    corrected, event = apply_candidate_correction(
        original,
        field_name="shipping_fee_status",
        action=CorrectionAction.USER_CORRECTION,
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        unit=None,
        reason_code="INVALID_FEE_STATUS",
        reason="The document provides a separate shipping amount.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )
    after = next(
        item for item in corrected.candidates if item.field_name == "shipping_fee_status"
    )

    assert before.normalized_value == "PAID"
    assert after.normalized_value == "KNOWN_AMOUNT"
    assert after.origin == Origin.USER_CORRECTION
    assert after.producer == CandidateProducer.MODEL_ADAPTER
    assert after.validation_status == ValidationStatus.VERIFIED
    assert event.before.normalized_value == "PAID"
    assert event.after.normalized_value == "KNOWN_AMOUNT"
    assert event.basis_source_ids == tuple(ref.source_id for ref in before.source_refs)

    envelope = review_extraction_batch(
        corrected,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        corrections=(event,),
    )
    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
