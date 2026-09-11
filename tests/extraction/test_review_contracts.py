from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from supplier_comparison.extraction.contracts import AdapterEnvironment
from supplier_comparison.extraction.review import model_failed_envelope
from supplier_comparison.extraction.review_contracts import ReviewEnvelope


def test_model_failed_requires_an_error_and_has_no_fake_batch() -> None:
    envelope = model_failed_envelope(
        environment=AdapterEnvironment.LOCAL,
        input_is_synthetic=True,
        errors=("timeout",),
    )
    assert envelope.batch is None
    assert envelope.review is None
    assert envelope.checks is None

    with pytest.raises(ValidationError, match="requires errors"):
        model_failed_envelope(
            environment=AdapterEnvironment.LOCAL,
            input_is_synthetic=True,
            errors=(),
        )


def test_ready_flag_cannot_disagree_with_review_status(quote_dictionary) -> None:
    from supplier_comparison.extraction.criticality import CriticalityContext
    from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
    from supplier_comparison.extraction.review import review_extraction_batch

    from .conftest import context_for, quotes_csv_path

    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("a"), 2
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc),
    )
    payload = envelope.model_dump(mode="python")
    payload["downstream_ready"] = False

    with pytest.raises(ValidationError, match="must match READY_FOR_DOWNSTREAM"):
        ReviewEnvelope.model_validate(payload)
