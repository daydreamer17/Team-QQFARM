"""Safe handoff checks for reviewed extraction envelopes."""

from __future__ import annotations

from .contracts import ExtractionBatch
from .errors import DownstreamNotReadyError
from .review_contracts import ReviewEnvelope, ReviewStatus


def require_downstream_ready(envelope: ReviewEnvelope) -> ExtractionBatch:
    """Return the compatible batch or reject the handoff before C is called."""

    if (
        envelope.review_status != ReviewStatus.READY_FOR_DOWNSTREAM
        or not envelope.downstream_ready
        or envelope.batch is None
    ):
        blocking_fields = (
            list(envelope.review.blocking_fields)
            if envelope.review is not None
            else []
        )
        raise DownstreamNotReadyError(
            "extraction_not_ready_for_downstream",
            "reviewed extraction is not safe to hand to comparison rules",
            review_status=envelope.review_status.value,
            blocking_fields=blocking_fields,
            errors=list(envelope.errors),
        )
    return envelope.batch
