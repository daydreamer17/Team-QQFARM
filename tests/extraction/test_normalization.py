from __future__ import annotations

import pytest

from supplier_comparison.extraction.model_payload import ModelExtractionPayload
from supplier_comparison.extraction.normalization import (
    CONFLICT_STATUS_PLACEHOLDER_RULE,
    INCLUDED_FEE_WITHOUT_SEPARATE_AMOUNT_RULE,
    PAYMENT_TERMS_NET_DAYS_RULE,
    SGD_FEE_SCALE_RULE,
    START_EVENT_PAYMENT_RECEIPT_RULE,
    normalize_model_payload,
)


def _payload(amount: str, *, currency: str = "SGD", field_name: str = "other_fees_amount"):
    return ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "currency",
                    "raw_value": currency,
                    "normalized_value": currency,
                    "unit": None,
                    "validation_status": "EXTRACTED",
                    "source_refs": [{"source_id": "SRC-CURRENCY", "quoted_text": currency}],
                },
                {
                    "field_name": field_name,
                    "raw_value": amount,
                    "normalized_value": amount,
                    "unit": currency,
                    "validation_status": "EXTRACTED",
                    "source_refs": [{"source_id": "SRC-AMOUNT", "quoted_text": amount}],
                },
            ]
        }
    )


@pytest.mark.parametrize("input_value", ("0", "0.0", "0.00"))
def test_legal_sgd_fee_strings_are_normalized_to_two_decimals(input_value: str) -> None:
    normalized, events = normalize_model_payload(_payload(input_value))
    amount = next(candidate for candidate in normalized.candidates if candidate.field_name == "other_fees_amount")
    assert amount.normalized_value == "0.00"
    if input_value == "0.00":
        assert events == ()
    else:
        assert len(events) == 1
        assert events[0].input_value == input_value
        assert events[0].output_value == "0.00"
        assert events[0].rule_id == SGD_FEE_SCALE_RULE


def test_fee_normalization_does_not_silently_round_extra_precision() -> None:
    payload = _payload("0.001")
    normalized, events = normalize_model_payload(payload)
    assert normalized == payload
    assert events == ()


def test_unit_price_precision_is_preserved() -> None:
    payload = _payload("6.8", field_name="unit_price")
    normalized, events = normalize_model_payload(payload)
    assert normalized == payload
    assert events == ()


def test_non_sgd_fee_amount_is_not_reformatted() -> None:
    payload = _payload("0", currency="USD")
    normalized, events = normalize_model_payload(payload)
    assert normalized == payload
    assert events == ()


def test_conflict_status_placeholder_is_normalized_to_null() -> None:
    payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "start_event",
                    "raw_value": "after confirmed PO date",
                    "normalized_value": "CONFLICT",
                    "unit": None,
                    "validation_status": "CONFLICT",
                    "source_refs": [
                        {"source_id": "SRC-START", "quoted_text": "after confirmed PO date"}
                    ],
                }
            ]
        }
    )

    normalized, events = normalize_model_payload(payload)

    assert normalized.candidates[0].normalized_value is None
    assert len(events) == 1
    assert events[0].input_value == "CONFLICT"
    assert events[0].output_value is None
    assert events[0].rule_id == CONFLICT_STATUS_PLACEHOLDER_RULE


@pytest.mark.parametrize(
    ("input_value", "expected"),
    (
        ("N30 - payment due 30 days after invoice", "Net 30"),
        ("Net30", "Net 30"),
        ("NET 45", "Net 45"),
    ),
)
def test_net_day_payment_terms_are_normalized(input_value: str, expected: str) -> None:
    payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "payment_terms",
                    "raw_value": input_value,
                    "normalized_value": input_value,
                    "unit": None,
                    "validation_status": "EXTRACTED",
                    "source_refs": [{"source_id": "SRC-PAYMENT", "quoted_text": input_value}],
                }
            ]
        }
    )

    normalized, events = normalize_model_payload(payload)

    assert normalized.candidates[0].normalized_value == expected
    assert len(events) == 1
    assert events[0].rule_id == PAYMENT_TERMS_NET_DAYS_RULE


def test_non_net_payment_terms_are_preserved() -> None:
    payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "payment_terms",
                    "raw_value": "100% before dispatch",
                    "normalized_value": "100% before dispatch",
                    "unit": None,
                    "validation_status": "EXTRACTED",
                    "source_refs": [
                        {"source_id": "SRC-PAYMENT", "quoted_text": "100% before dispatch"}
                    ],
                }
            ]
        }
    )

    normalized, events = normalize_model_payload(payload)

    assert normalized == payload
    assert events == ()


def test_included_fee_without_separate_amount_becomes_missing() -> None:
    payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "other_fees_status",
                    "raw_value": "Included in the quoted line rate",
                    "normalized_value": "INCLUDED",
                    "unit": None,
                    "validation_status": "EXTRACTED",
                    "source_refs": [
                        {"source_id": "SRC-FEE-STATUS", "quoted_text": "Included"}
                    ],
                },
                {
                    "field_name": "other_fees_amount",
                    "raw_value": "No separately stated amount",
                    "normalized_value": None,
                    "unit": None,
                    "validation_status": "CONFLICT",
                    "source_refs": [
                        {
                            "source_id": "SRC-FEE-AMOUNT",
                            "quoted_text": "No separately stated amount",
                        }
                    ],
                },
            ]
        }
    )

    normalized, events = normalize_model_payload(payload)

    amount = next(item for item in normalized.candidates if item.field_name == "other_fees_amount")
    assert amount.validation_status == "MISSING"
    assert amount.raw_value is None
    assert amount.normalized_value is None
    assert amount.source_refs == ()
    assert len(events) == 1
    assert events[0].rule_id == INCLUDED_FEE_WITHOUT_SEPARATE_AMOUNT_RULE


def test_cleared_payment_received_event_uses_contract_value() -> None:
    payload = ModelExtractionPayload.model_validate(
        {
            "candidates": [
                {
                    "field_name": "start_event",
                    "raw_value": "Clock starts after cleared payment is received",
                    "normalized_value": "PAYMENT_RECEIVED",
                    "unit": None,
                    "validation_status": "EXTRACTED",
                    "source_refs": [
                        {
                            "source_id": "SRC-START",
                            "quoted_text": "cleared payment is received",
                        }
                    ],
                }
            ]
        }
    )

    normalized, events = normalize_model_payload(payload)

    assert normalized.candidates[0].normalized_value == "PAYMENT_RECEIPT"
    assert len(events) == 1
    assert events[0].rule_id == START_EVENT_PAYMENT_RECEIPT_RULE
