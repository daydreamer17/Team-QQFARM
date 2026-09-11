"""Small B-to-C integration adapters without database or workflow concerns."""

from __future__ import annotations

from datetime import datetime

from supplier_comparison.extraction.contracts import ExtractionBatch

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
