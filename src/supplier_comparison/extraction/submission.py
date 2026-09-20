"""Pure submission admission rules for human-reviewed quote extraction."""

from __future__ import annotations

from collections import Counter

from .contracts import ExtractionBatch, Origin, QuoteFieldCandidate, ValidationStatus
from .criticality import POLICY_FIELDS
from .review_contracts import (
    CandidateValueSnapshot,
    CorrectionAction,
    CorrectionEvent,
    CriticalityAssessment,
    HumanReviewAction,
    ReviewEvent,
    SubmissionGate,
)


def evaluate_submission_gate(
    batch: ExtractionBatch,
    assessments: tuple[CriticalityAssessment, ...],
    *,
    review_events: tuple[ReviewEvent, ...] = (),
    corrections: tuple[CorrectionEvent, ...] = (),
    deterministic_blocking_fields: tuple[str, ...] = (),
) -> SubmissionGate:
    """Evaluate whether the current 30-field batch may be formally submitted.

    A current, version-bound human disposition is required for every field. A
    matching correction is itself a disposition; unchanged candidates require
    a ``ReviewEvent``. Required fields additionally need a usable value, and an
    ``UNKNOWN`` fee status is deliberately not usable. Stale events are ignored
    rather than accidentally confirming a newer candidate version.

    ``deterministic_blocking_fields`` is supplied by the deterministic review
    gate so contract, evidence, semantic, and cross-field failures cannot be
    bypassed by human confirmation.
    """

    candidates = tuple(batch.candidates)
    counts = Counter(candidate.field_name for candidate in candidates)
    by_name = {candidate.field_name: candidate for candidate in candidates}
    assessments_by_name = {item.field_name: item for item in assessments}
    candidate_set_valid = (
        len(candidates) == len(POLICY_FIELDS)
        and set(by_name) == POLICY_FIELDS
        and all(count == 1 for count in counts.values())
        and set(assessments_by_name) == POLICY_FIELDS
    )

    if not candidate_set_valid:
        return SubmissionGate(
            human_review_complete=False,
            submission_ready=False,
            calculation_ready=False,
            submission_blocking_fields=("__batch__",),
            unconfirmed_fields=tuple(sorted(POLICY_FIELDS)),
        )

    reviewed_fields = {
        event.field_name
        for event in review_events
        if event.field_name in by_name
        if _review_event_matches_current(batch, by_name[event.field_name], event)
    }
    reviewed_fields.update(
        event.field_name
        for event in corrections
        if event.field_name in by_name
        and _correction_matches_current(batch, by_name[event.field_name], event)
    )
    unconfirmed_fields = tuple(sorted(POLICY_FIELDS - reviewed_fields))

    blocking = set(deterministic_blocking_fields)
    for field_name, assessment in assessments_by_name.items():
        if assessment.is_critical and not _candidate_has_submission_value(
            by_name[field_name]
        ):
            blocking.add(field_name)

    blocking_fields = tuple(sorted(blocking))
    human_review_complete = not unconfirmed_fields
    submission_ready = human_review_complete and not blocking_fields
    return SubmissionGate(
        human_review_complete=human_review_complete,
        submission_ready=submission_ready,
        calculation_ready=submission_ready,
        submission_blocking_fields=blocking_fields,
        unconfirmed_fields=unconfirmed_fields,
    )


def _review_event_matches_current(
    batch: ExtractionBatch,
    candidate: QuoteFieldCandidate,
    event: ReviewEvent,
) -> bool:
    context = batch.parsed_input.context
    source_ids = {source.source_id for source in batch.parsed_input.sources}
    action_matches = (
        event.action == HumanReviewAction.CONFIRM_VALUE
        and candidate.validation_status
        in {ValidationStatus.EXTRACTED, ValidationStatus.VERIFIED}
    ) or (
        event.action == HumanReviewAction.CONFIRM_MISSING
        and candidate.validation_status == ValidationStatus.MISSING
    ) or (
        event.action == HumanReviewAction.CONFIRM_CONFLICT
        and candidate.validation_status == ValidationStatus.CONFLICT
    )
    return bool(
        action_matches
        and event.candidate_field_id == candidate.field_id
        and event.candidate_field_version == candidate.field_version
        and event.candidate_status == candidate.validation_status
        and event.task_revision == context.task_revision
        and event.quote_id == context.quote_id
        and event.quote_version == context.quote_version
        and event.document_id == context.document_id
        and event.document_version == context.document_version
        and event.document_sha256 == batch.parsed_input.document_sha256
        and all(source_id in source_ids for source_id in event.basis_source_ids)
    )


def _correction_matches_current(
    batch: ExtractionBatch,
    candidate: QuoteFieldCandidate,
    event: CorrectionEvent,
) -> bool:
    context = batch.parsed_input.context
    source_ids = {source.source_id for source in batch.parsed_input.sources}
    if (
        event.after != CandidateValueSnapshot.from_candidate(candidate)
        or event.task_revision != context.task_revision
        or event.quote_id != context.quote_id
        or event.quote_version != context.quote_version
        or event.document_id != context.document_id
        or event.document_version != context.document_version
        or event.document_sha256 != batch.parsed_input.document_sha256
        or any(source_id not in source_ids for source_id in event.basis_source_ids)
    ):
        return False
    if event.action == CorrectionAction.MARK_MISSING:
        return candidate.validation_status == ValidationStatus.MISSING
    return (
        candidate.validation_status == ValidationStatus.VERIFIED
        and candidate.origin == Origin(event.action.value)
    )


def _candidate_has_submission_value(candidate: QuoteFieldCandidate) -> bool:
    if candidate.validation_status not in {
        ValidationStatus.EXTRACTED,
        ValidationStatus.VERIFIED,
    }:
        return False
    if candidate.normalized_value is None:
        return False
    if candidate.field_name in {"shipping_fee_status", "other_fees_status"}:
        return candidate.normalized_value != "UNKNOWN"
    return True
