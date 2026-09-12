"""Small B-to-C integration adapters without database or workflow concerns."""

from __future__ import annotations

from datetime import datetime

from supplier_comparison.extraction.contracts import ExtractionBatch
from supplier_comparison.extraction.criticality import NON_CRITICAL_FIELDS
from supplier_comparison.extraction.readiness import require_downstream_ready
from supplier_comparison.extraction.review_contracts import ReviewEnvelope

from .contracts import (
    ComparisonRequest,
    ComparisonResult,
    ProcurementRequirement,
    QuoteInput,
)
from .engine import compare_suppliers


def quote_input_from_extraction(batch: ExtractionBatch) -> QuoteInput:
    """Reuse B's normalized candidates as C's quote input."""

    context = batch.parsed_input.context
    return QuoteInput(
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        candidates=batch.candidates,
    )


def compare_extraction_batches(
    requirement: ProcurementRequirement,
    batches: tuple[ExtractionBatch, ...],
    *,
    evaluated_at: datetime,
) -> ComparisonResult:
    """Run C's pure comparison directly over completed B extraction batches."""

    request = ComparisonRequest(
        requirement=requirement,
        quotes=tuple(quote_input_from_extraction(batch) for batch in batches),
        evaluated_at=evaluated_at,
    )
    return compare_suppliers(request)


def quote_input_from_reviewed_extraction(envelope: ReviewEnvelope) -> QuoteInput:
    """Construct C input only after B's review envelope permits handoff.

    Display-only fields are deliberately omitted at this stricter boundary so
    they cannot silently become comparison inputs if C evolves later.
    """

    batch = require_downstream_ready(envelope)
    context = batch.parsed_input.context
    calculation_candidates = tuple(
        candidate
        for candidate in batch.candidates
        if candidate.field_name not in NON_CRITICAL_FIELDS
    )
    return QuoteInput(
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        candidates=calculation_candidates,
    )


def compare_reviewed_extractions(
    requirement: ProcurementRequirement,
    envelopes: tuple[ReviewEnvelope, ...],
    *,
    evaluated_at: datetime,
) -> ComparisonResult:
    """Run C over reviewed batches, including confirmed MISSING/PENDING cases."""

    request = ComparisonRequest(
        requirement=requirement,
        quotes=tuple(
            quote_input_from_reviewed_extraction(envelope)
            for envelope in envelopes
        ),
        evaluated_at=evaluated_at,
    )
    return compare_suppliers(request)
