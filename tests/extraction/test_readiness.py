from __future__ import annotations

from datetime import datetime, timezone

import pytest

from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.errors import DownstreamNotReadyError
from supplier_comparison.extraction.readiness import require_downstream_ready
from supplier_comparison.extraction.review import review_extraction_batch

from .conftest import context_for, quotes_csv_path


def test_readiness_returns_the_unchanged_compatible_batch(quote_dictionary) -> None:
    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("a"), 2
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=datetime(2026, 9, 11, 16, 30, tzinfo=timezone.utc),
    )

    assert require_downstream_ready(envelope) is batch


def test_readiness_exposes_blocking_fields(quote_dictionary) -> None:
    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("b"), 3
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=datetime(2026, 9, 11, 16, 30, tzinfo=timezone.utc),
    )

    with pytest.raises(DownstreamNotReadyError) as raised:
        require_downstream_ready(envelope)
    assert "shipping_fee_status" in raised.value.details["blocking_fields"]
