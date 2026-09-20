from __future__ import annotations

from datetime import datetime, timezone

from supplier_comparison.extraction.contracts import ExtractionBatch, Origin, ValidationStatus
from supplier_comparison.extraction.corrections import apply_candidate_correction
from supplier_comparison.extraction.criticality import CriticalityContext, resolve_criticalities
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.human_review import create_review_event
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.review_contracts import (
    CorrectionAction,
    HumanReviewAction,
    ReviewEnvelope,
)
from supplier_comparison.extraction.submission import evaluate_submission_gate

from .conftest import context_for, quotes_csv_path


NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
CONTEXT = CriticalityContext(required_revision="R1", base_unit="piece")


def _supplier_a(quote_dictionary) -> ExtractionBatch:
    return FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("a"), 2
    )


def _confirm_all_values(batch: ExtractionBatch):
    return tuple(
        create_review_event(
            batch,
            field_name=candidate.field_name,
            action=HumanReviewAction.CONFIRM_VALUE,
            reviewer_id="reviewer-1",
            reviewed_at=NOW,
        )
        for candidate in batch.candidates
    )


def test_all_current_values_require_confirmation_before_submission(
    quote_dictionary,
) -> None:
    batch = _supplier_a(quote_dictionary)

    before = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
    )
    assert before.downstream_ready is True
    assert before.human_review_complete is False
    assert before.submission_ready is False
    assert before.calculation_ready is False
    assert len(before.unconfirmed_fields) == 30

    after = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=_confirm_all_values(batch),
    )
    assert after.human_review_complete is True
    assert after.unconfirmed_fields == ()
    assert after.submission_blocking_fields == ()
    assert after.submission_ready is True
    assert after.calculation_ready is True


def test_confirm_value_is_version_bound_and_preserves_document_candidate(
    quote_dictionary,
) -> None:
    batch = _supplier_a(quote_dictionary)
    candidate = next(item for item in batch.candidates if item.field_name == "unit_price")

    event = create_review_event(
        batch,
        field_name="unit_price",
        action=HumanReviewAction.CONFIRM_VALUE,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
        draft_revision=4,
    )

    assert event.candidate_field_id == candidate.field_id
    assert event.candidate_field_version == candidate.field_version
    assert event.draft_revision == 4
    assert event.candidate_status == ValidationStatus.EXTRACTED
    assert event.basis_source_ids == tuple(ref.source_id for ref in candidate.source_refs)
    assert candidate.origin == Origin.DOCUMENT


def test_confirmed_required_missing_still_blocks_formal_submission(
    quote_dictionary,
) -> None:
    original = _supplier_a(quote_dictionary)
    batch, correction = apply_candidate_correction(
        original,
        field_name="manufacturer",
        action=CorrectionAction.MARK_MISSING,
        raw_value=None,
        normalized_value=None,
        unit=None,
        reason_code="FALSE_POSITIVE_EXTRACTION",
        reason="The reviewer confirmed that the document does not name a manufacturer.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
        draft_revision=7,
    )
    events = tuple(
        create_review_event(
            batch,
            field_name=candidate.field_name,
            action=(
                HumanReviewAction.CONFIRM_MISSING
                if candidate.validation_status == ValidationStatus.MISSING
                else HumanReviewAction.CONFIRM_VALUE
            ),
            reviewer_id="reviewer-1",
            reviewed_at=NOW,
        )
        for candidate in batch.candidates
    )

    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=events,
        corrections=(correction,),
    )

    assert envelope.downstream_ready is True
    assert envelope.human_review_complete is True
    assert envelope.submission_ready is False
    assert envelope.calculation_ready is False
    assert envelope.submission_blocking_fields == ("manufacturer",)
    assert correction.draft_revision == 7


def test_marking_optional_field_missing_is_audited_and_does_not_block(
    quote_dictionary,
) -> None:
    original = _supplier_a(quote_dictionary)
    batch, correction = apply_candidate_correction(
        original,
        field_name="supplier_country",
        action=CorrectionAction.MARK_MISSING,
        raw_value=None,
        normalized_value=None,
        unit=None,
        reason_code="FALSE_POSITIVE_EXTRACTION",
        reason="The country value is not actually stated in the quote.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )
    events = tuple(
        create_review_event(
            batch,
            field_name=candidate.field_name,
            action=HumanReviewAction.CONFIRM_VALUE,
            reviewer_id="reviewer-1",
            reviewed_at=NOW,
        )
        for candidate in batch.candidates
        if candidate.field_name != "supplier_country"
    )

    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=events,
        corrections=(correction,),
    )

    missing = next(
        item for item in batch.candidates if item.field_name == "supplier_country"
    )
    assert missing.validation_status == ValidationStatus.MISSING
    assert missing.normalized_value is None
    assert correction.before.normalized_value == "USA"
    assert correction.after.validation_status == ValidationStatus.MISSING
    assert correction.draft_revision is None
    assert envelope.human_review_complete is True
    assert envelope.submission_ready is True


def test_stale_confirmation_does_not_confirm_a_new_field_version(
    quote_dictionary,
) -> None:
    original = _supplier_a(quote_dictionary)
    original_events = _confirm_all_values(original)
    corrected, _ = apply_candidate_correction(
        original,
        field_name="unit_price",
        action=CorrectionAction.USER_CORRECTION,
        raw_value="S$6.50",
        normalized_value="6.50",
        unit="SGD",
        reason_code="HUMAN_CORRECTION",
        reason="Reviewer corrected the unit price.",
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )

    gate = evaluate_submission_gate(
        corrected,
        resolve_criticalities(corrected, CONTEXT),
        review_events=original_events,
    )

    assert gate.human_review_complete is False
    assert gate.submission_ready is False
    assert gate.unconfirmed_fields == ("unit_price",)


def test_deterministic_blocker_cannot_be_confirmed_away(quote_dictionary) -> None:
    batch = _supplier_a(quote_dictionary)
    gate = evaluate_submission_gate(
        batch,
        resolve_criticalities(batch, CONTEXT),
        review_events=_confirm_all_values(batch),
        deterministic_blocking_fields=("unit_price",),
    )

    assert gate.human_review_complete is True
    assert gate.submission_ready is False
    assert gate.submission_blocking_fields == ("unit_price",)


def test_legacy_review_envelope_without_v11_fields_remains_readable(
    quote_dictionary,
) -> None:
    batch = _supplier_a(quote_dictionary)
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=_confirm_all_values(batch),
    )
    payload = envelope.model_dump(mode="python")
    payload["schema_version"] = "review-envelope/1.0.0"
    for field_name in (
        "human_review_complete",
        "submission_ready",
        "calculation_ready",
        "submission_blocking_fields",
        "unconfirmed_fields",
        "review_policy_version",
    ):
        payload.pop(field_name)
    for event in payload["review_events"]:
        event.pop("candidate_field_version")
        event.pop("draft_revision")

    restored = ReviewEnvelope.model_validate(payload)

    assert restored.schema_version == "review-envelope/1.0.0"
    assert restored.human_review_complete is False
    assert restored.submission_ready is False
    assert restored.calculation_ready is False
    assert restored.unconfirmed_fields == ()
    assert restored.review_events[0].candidate_field_version is None
    assert restored.review_events[0].draft_revision is None
