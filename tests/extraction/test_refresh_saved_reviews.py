from __future__ import annotations

from scripts.refresh_saved_reviews import _renormalize_batch
from supplier_comparison.extraction.contracts import ExtractionBatch, QuoteFieldCandidate
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser

from .conftest import context_for, quotes_csv_path


def test_saved_real_batch_can_receive_new_deterministic_normalization(
    quote_dictionary,
) -> None:
    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for("a"), 2
    )
    candidates = []
    for candidate in batch.candidates:
        if candidate.field_name != "start_event":
            candidates.append(candidate)
            continue
        candidates.append(
            QuoteFieldCandidate.model_validate(
                {
                    **candidate.model_dump(mode="python"),
                    "raw_value": "Clock starts after cleared payment is received",
                    "normalized_value": "PAYMENT_RECEIVED",
                }
            )
        )
    batch = ExtractionBatch.model_validate(
        {**batch.model_dump(mode="python"), "candidates": tuple(candidates)}
    )

    replayed = _renormalize_batch(batch)

    start_event = next(
        item for item in replayed.candidates if item.field_name == "start_event"
    )
    assert start_event.normalized_value == "PAYMENT_RECEIPT"
    assert any(
        event.rule_id == "start-event-payment-receipt/1.0.0"
        for event in replayed.normalization_events
    )
