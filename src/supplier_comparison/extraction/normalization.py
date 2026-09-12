"""Small deterministic normalizations that do not change document provenance."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .contracts import NormalizationEvent
from .model_payload import (
    MissingModelFieldCandidate,
    ModelExtractionPayload,
    ModelFieldCandidate,
)


SGD_FEE_SCALE_RULE = "sgd-fee-amount-2dp/1.0.0"
CONFLICT_STATUS_PLACEHOLDER_RULE = "conflict-status-placeholder-null/1.0.0"
PAYMENT_TERMS_NET_DAYS_RULE = "payment-terms-net-days/1.0.0"
INCLUDED_FEE_WITHOUT_SEPARATE_AMOUNT_RULE = "included-fee-no-separate-amount/1.0.0"
FEE_AMOUNT_FIELDS = frozenset({"shipping_fee_amount", "other_fees_amount"})
FEE_STATUS_BY_AMOUNT_FIELD = {
    "shipping_fee_amount": "shipping_fee_status",
    "other_fees_amount": "other_fees_status",
}
STATUS_LABELS = frozenset({"EXTRACTED", "VERIFIED", "MISSING", "CONFLICT"})
NET_DAYS_PATTERN = re.compile(
    r"^(?:N|NET\s*)(?P<days>\d{1,3})(?:\s*[-:–—]\s*.*)?$",
    re.IGNORECASE,
)
NO_SEPARATE_AMOUNT_PATTERN = re.compile(
    r"\bno\s+(?:separate\s+charge|separately\s+stated\s+amount)\b",
    re.IGNORECASE,
)


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
    normalized_by_field = {
        candidate.field_name: candidate.normalized_value for candidate in payload.candidates
    }
    normalized_candidates: list[ModelFieldCandidate] = []
    events: list[NormalizationEvent] = []
    for candidate in payload.candidates:
        value = candidate.normalized_value
        fee_status_field = FEE_STATUS_BY_AMOUNT_FIELD.get(candidate.field_name)
        if (
            fee_status_field is not None
            and normalized_by_field.get(fee_status_field) == "INCLUDED"
            and candidate.validation_status == "CONFLICT"
            and value is None
            and NO_SEPARATE_AMOUNT_PATTERN.search(candidate.raw_value)
        ):
            normalized_candidates.append(
                MissingModelFieldCandidate(
                    field_name=candidate.field_name,
                    unit=None,
                    raw_value=None,
                    normalized_value=None,
                    validation_status="MISSING",
                    source_refs=(),
                )
            )
            events.append(
                NormalizationEvent(
                    field_name=candidate.field_name,
                    input_value=candidate.raw_value,
                    output_value=None,
                    rule_id=INCLUDED_FEE_WITHOUT_SEPARATE_AMOUNT_RULE,
                )
            )
            continue
        if (
            candidate.field_name == "payment_terms"
            and candidate.validation_status == "EXTRACTED"
            and isinstance(value, str)
        ):
            match = NET_DAYS_PATTERN.fullmatch(value.strip())
            if match is not None:
                formatted = f"Net {int(match.group('days'))}"
                normalized_candidates.append(
                    candidate.model_copy(update={"normalized_value": formatted})
                )
                if formatted != value:
                    events.append(
                        NormalizationEvent(
                            field_name=candidate.field_name,
                            input_value=value,
                            output_value=formatted,
                            rule_id=PAYMENT_TERMS_NET_DAYS_RULE,
                        )
                    )
                continue
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
