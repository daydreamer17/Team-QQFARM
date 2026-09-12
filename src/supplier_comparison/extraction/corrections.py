"""Pure, immutable application of human input and field corrections."""

from __future__ import annotations

from datetime import datetime

from .contracts import (
    ExtractionBatch,
    NormalizedScalar,
    Origin,
    QuoteFieldCandidate,
    SourceCitation,
    ValidationStatus,
)
from .files import stable_id
from .review_contracts import (
    CandidateValueSnapshot,
    CorrectionAction,
    CorrectionEvent,
)


def apply_candidate_correction(
    batch: ExtractionBatch,
    *,
    field_name: str,
    action: CorrectionAction,
    raw_value: str,
    normalized_value: NormalizedScalar,
    unit: str | None,
    reason_code: str,
    reason: str,
    reviewer_id: str,
    reviewed_at: datetime,
    basis_source_ids: tuple[str, ...] = (),
) -> tuple[ExtractionBatch, CorrectionEvent]:
    """Return a new batch and an auditable correction event.

    ``USER_INPUT`` supplies information absent from the document and therefore
    cannot cite document sources. ``USER_CORRECTION`` may retain or replace the
    document sources that explain why the original extraction was wrong.
    """

    matching = [candidate for candidate in batch.candidates if candidate.field_name == field_name]
    if len(matching) != 1:
        raise ValueError("correction field must exist exactly once in the batch")
    before = matching[0]
    if action == CorrectionAction.USER_INPUT and before.validation_status != ValidationStatus.MISSING:
        raise ValueError("USER_INPUT can only fill a MISSING candidate")
    if action == CorrectionAction.USER_INPUT and basis_source_ids:
        raise ValueError("USER_INPUT cannot cite document sources")

    source_by_id = {source.source_id: source for source in batch.parsed_input.sources}
    if any(source_id not in source_by_id for source_id in basis_source_ids):
        raise ValueError("correction cites a source outside the current parsed input")
    if action == CorrectionAction.USER_INPUT:
        source_refs: tuple[SourceCitation, ...] = ()
        selected_ids: tuple[str, ...] = ()
    else:
        selected_ids = basis_source_ids or tuple(ref.source_id for ref in before.source_refs)
        source_refs = tuple(
            SourceCitation(
                source_id=source_id,
                quoted_text=source_by_id[source_id].raw_text,
            )
            for source_id in selected_ids
        )

    new_field_version = before.field_version + 1
    new_field_id = stable_id(
        "fld",
        {
            "quote_id": before.quote_id,
            "quote_version": before.quote_version,
            "field_name": before.field_name,
            "field_version": new_field_version,
        },
    )
    values = before.model_dump(mode="python")
    values.update(
        field_id=new_field_id,
        field_version=new_field_version,
        raw_value=raw_value,
        normalized_value=normalized_value,
        unit=unit,
        validation_status=ValidationStatus.VERIFIED,
        origin=Origin(action.value),
        source_refs=source_refs,
    )
    after = QuoteFieldCandidate.model_validate(values)

    candidates = tuple(
        after if candidate.field_name == field_name else candidate
        for candidate in batch.candidates
    )
    batch_values = batch.model_dump(mode="python")
    batch_values.update(candidates=candidates, created_at=reviewed_at)
    corrected_batch = ExtractionBatch.model_validate(batch_values)

    context = batch.parsed_input.context
    correction_id = stable_id(
        "correction",
        {
            "before_field_id": before.field_id,
            "after_field_id": after.field_id,
            "action": action.value,
            "reviewer_id": reviewer_id,
            "reviewed_at": reviewed_at.isoformat(),
        },
    )
    event = CorrectionEvent(
        correction_id=correction_id,
        field_name=field_name,
        action=action,
        before=CandidateValueSnapshot.from_candidate(before),
        after=CandidateValueSnapshot.from_candidate(after),
        reason_code=reason_code,
        reason=reason,
        basis_source_ids=selected_ids,
        reviewer_id=reviewer_id,
        reviewed_at=reviewed_at,
        task_revision=context.task_revision,
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        document_id=context.document_id,
        document_version=context.document_version,
        document_sha256=batch.parsed_input.document_sha256,
    )
    return corrected_batch, event
