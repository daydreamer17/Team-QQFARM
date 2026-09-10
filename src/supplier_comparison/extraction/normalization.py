"""Small deterministic normalizations that do not change document provenance."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from .contracts import NormalizationEvent
from .model_payload import ModelExtractionPayload, ModelFieldCandidate


SGD_FEE_SCALE_RULE = "sgd-fee-amount-2dp/1.0.0"
CONFLICT_STATUS_PLACEHOLDER_RULE = "conflict-status-placeholder-null/1.0.0"
FEE_AMOUNT_FIELDS = frozenset({"shipping_fee_amount", "other_fees_amount"})
STATUS_LABELS = frozenset({"EXTRACTED", "VERIFIED", "MISSING", "CONFLICT"})


def normalize_model_payload(
    payload: ModelExtractionPayload,
) -> tuple[ModelExtractionPayload, tuple[NormalizationEvent, ...]]:
    """Apply small auditable cleanups without changing document provenance."""

    currency = next(
        (
            candidate.normalized_value
            for candidate in payload.candidates
            if candidate.field_name == "currency" and candidate.validation_status == "EXTRACTED"
        ),
        None,
    )
    normalized_candidates: list[ModelFieldCandidate] = []
    events: list[NormalizationEvent] = []
    for candidate in payload.candidates:
        value = candidate.normalized_value
        if (
            candidate.validation_status == "CONFLICT"
            and isinstance(value, str)
            and value.upper() in STATUS_LABELS
        ):
            normalized_candidates.append(candidate.model_copy(update={"normalized_value": None}))
            events.append(
                NormalizationEvent(
                    field_name=candidate.field_name,
                    input_value=value,
                    output_value=None,
                    rule_id=CONFLICT_STATUS_PLACEHOLDER_RULE,
                )
            )
            continue
        if (
            currency != "SGD"
            or candidate.validation_status != "EXTRACTED"
            or candidate.field_name not in FEE_AMOUNT_FIELDS
            or not isinstance(value, str)
        ):
            normalized_candidates.append(candidate)
            continue

        formatted = _format_exact_two_decimal_amount(value)
        if formatted is None or formatted == value:
            normalized_candidates.append(candidate)
            continue

        normalized_candidates.append(candidate.model_copy(update={"normalized_value": formatted}))
        events.append(
            NormalizationEvent(
                field_name=candidate.field_name,
                input_value=value,
                output_value=formatted,
                rule_id=SGD_FEE_SCALE_RULE,
            )
        )

    return (
        payload.model_copy(update={"candidates": tuple(normalized_candidates)}),
        tuple(events),
    )


def _format_exact_two_decimal_amount(value: str) -> str | None:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount.as_tuple().exponent < -2:
        return None
    return format(amount.quantize(Decimal("0.01")), "f")
