"""Pure helpers for recording human confirmation without changing candidates."""

from __future__ import annotations

from datetime import datetime

from .contracts import ExtractionBatch, ValidationStatus
from .files import stable_id
from .review_contracts import HumanReviewAction, ReviewEvent


def create_review_event(
    batch: ExtractionBatch,
    *,
    field_name: str,
    action: HumanReviewAction,
    reviewer_id: str,
    reviewed_at: datetime,
    reason_code: str | None = None,
    basis_source_ids: tuple[str, ...] = (),
) -> ReviewEvent:
    """Create a version-bound confirmation for an unchanged MISSING/CONFLICT field."""

    matching = [candidate for candidate in batch.candidates if candidate.field_name == field_name]
    if len(matching) != 1:
        raise ValueError("human review field must exist exactly once in the batch")
    candidate = matching[0]
    expected_status = {
        HumanReviewAction.CONFIRM_MISSING: ValidationStatus.MISSING,
        HumanReviewAction.CONFIRM_CONFLICT: ValidationStatus.CONFLICT,
    }[action]
    if candidate.validation_status != expected_status:
        raise ValueError("human review action does not match candidate status")
    known_source_ids = {source.source_id for source in batch.parsed_input.sources}
    selected_source_ids = basis_source_ids
    if action == HumanReviewAction.CONFIRM_CONFLICT and not selected_source_ids:
        selected_source_ids = tuple(ref.source_id for ref in candidate.source_refs)
    if any(source_id not in known_source_ids for source_id in selected_source_ids):
        raise ValueError("human review cites a source outside the current parsed input")

    context = batch.parsed_input.context
    selected_reason = reason_code or (
        "DOCUMENT_DOES_NOT_STATE_VALUE"
        if action == HumanReviewAction.CONFIRM_MISSING
        else "DOCUMENT_CONFLICT_UNRESOLVED"
    )
    review_event_id = stable_id(
        "review_event",
        {
            "action": action.value,
            "candidate_field_id": candidate.field_id,
            "candidate_field_version": candidate.field_version,
            "document_sha256": batch.parsed_input.document_sha256,
            "reviewed_at": reviewed_at.isoformat(),
            "reviewer_id": reviewer_id,
        },
    )
    return ReviewEvent(
        review_event_id=review_event_id,
        field_name=field_name,
        candidate_field_id=candidate.field_id,
        action=action,
        candidate_status=candidate.validation_status,
        reason_code=selected_reason,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        task_revision=context.task_revision,
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        document_id=context.document_id,
        document_version=context.document_version,
        document_sha256=batch.parsed_input.document_sha256,
        basis_source_ids=selected_source_ids,
    )
