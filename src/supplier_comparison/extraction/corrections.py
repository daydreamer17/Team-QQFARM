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
    raw_value: str | None,
    normalized_value: NormalizedScalar | None,
    unit: str | None,
    reason_code: str,
    reason: str,
    reviewer_id: str,
    reviewed_at: datetime,
    basis_source_ids: tuple[str, ...] = (),
    draft_revision: int | None = None,
) -> tuple[ExtractionBatch, CorrectionEvent]:
    """Return a new batch and an auditable correction event.

    ``USER_INPUT`` supplies information absent from the document and therefore
    cannot cite document sources. ``USER_CORRECTION`` may retain or replace the
    document sources that explain why the original extraction was wrong.
    ``MARK_MISSING`` clears a false-positive extraction while retaining the
    before-value and human action in the immutable correction event.
    """

    matching = [candidate for candidate in batch.candidates if candidate.field_name == field_name]
    if len(matching) != 1:
        raise ValueError("correction field must exist exactly once in the batch")
    before = matching[0]
    if action == CorrectionAction.USER_INPUT and before.validation_status != ValidationStatus.MISSING:
        raise ValueError("USER_INPUT can only fill a MISSING candidate")
    if action == CorrectionAction.USER_INPUT and basis_source_ids:
        raise ValueError("USER_INPUT cannot cite document sources")
    if action == CorrectionAction.MARK_MISSING:
        if before.validation_status == ValidationStatus.MISSING:
            raise ValueError("MARK_MISSING requires a non-MISSING candidate")
        if raw_value is not None or normalized_value is not None or unit is not None:
            raise ValueError("MARK_MISSING requires null raw, normalized, and unit values")

    source_by_id = {source.source_id: source for source in batch.parsed_input.sources}
    if any(source_id not in source_by_id for source_id in basis_source_ids):
        raise ValueError("correction cites a source outside the current parsed input")
    if action in {CorrectionAction.USER_INPUT, CorrectionAction.MARK_MISSING}:
        source_refs: tuple[SourceCitation, ...] = ()
        selected_ids = (
            ()
            if action == CorrectionAction.USER_INPUT
            else basis_source_ids or tuple(ref.source_id for ref in before.source_refs)
        )
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
    if action == CorrectionAction.MARK_MISSING:
        values.update(
            field_id=new_field_id,
            field_version=new_field_version,
            raw_value=None,
            normalized_value=None,
            unit=None,
            validation_status=ValidationStatus.MISSING,
            origin=None,
            source_refs=(),
        )
    else:
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
    identity = {
        "before_field_id": before.field_id,
        "after_field_id": after.field_id,
        "action": action.value,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at.isoformat(),
    }
    if draft_revision is not None:
        identity["draft_revision"] = draft_revision
    correction_id = stable_id("correction", identity)
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
        draft_revision=draft_revision,
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        document_id=context.document_id,
        document_version=context.document_version,
        document_sha256=batch.parsed_input.document_sha256,
    )
    return corrected_batch, event
