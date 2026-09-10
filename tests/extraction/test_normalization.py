from __future__ import annotations

import pytest

from supplier_comparison.extraction.model_payload import ModelExtractionPayload
from supplier_comparison.extraction.normalization import SGD_FEE_SCALE_RULE, normalize_model_payload


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
