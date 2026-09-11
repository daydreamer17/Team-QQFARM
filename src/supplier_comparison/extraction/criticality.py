"""Versioned field-criticality policy supplied by C's comparison boundary."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .contracts import ExtractionBatch, QuoteFieldCandidate, ValidationStatus
from .errors import ContractError
from .review_contracts import (
    CRITICALITY_POLICY_VERSION,
    CriticalityAssessment,
    EffectiveCriticality,
)


# These hashes freeze the two human-owned inputs used to define this policy.
# If either source changes, update the policy version, review the rules below,
# and deliberately update the corresponding hash assertion in the tests.
CRITICALITY_SOURCE_SHA256 = (
    "f3d62b13be975d076858600be116f325bd1535b2ec04271457b062adc07ee706"
)
QUOTE_DICTIONARY_SOURCE_SHA256 = (
    "2d011d64c821ae4d124d4dcf046732b7f3f85449b82312d78f5dadc1b878fb55"
)


ALWAYS_CRITICAL_FIELDS = frozenset(
    {
        "supplier_name",
        "manufacturer",
        "manufacturer_part_number",
        "package",
        "condition",
        "currency",
        "unit_price",
        "price_basis_quantity",
        "price_basis_unit",
        "order_multiple_units",
        "moq_quantity",
        "moq_unit",
        "shipping_fee_status",
        "other_fees_status",
        "tax_mode",
        "valid_until",
    }
)

CONDITIONAL_CRITICAL_FIELDS = frozenset(
    {
        "revision",
        "packaging_type",
        "units_per_pack",
        "shipping_fee_amount",
        "other_fees_amount",
        "lead_time_days",
        "day_basis",
        "delivery_semantics",
        "start_event",
        "quote_date",
    }
)

NON_CRITICAL_FIELDS = frozenset(
    {"supplier_country", "category", "item", "payment_terms"}
)

POLICY_FIELDS = ALWAYS_CRITICAL_FIELDS | CONDITIONAL_CRITICAL_FIELDS | NON_CRITICAL_FIELDS

RELATIVE_DELIVERY_PATTERN = re.compile(
    r"\b(?:lead[ -]?time|calendar\s+days?|business\s+days?|working\s+days?)\b"
    r"|\b(?:arriv\w*|deliver\w*|ship\w*)\b.{0,40}\b\d+\s*(?:day|days)\b",
    re.IGNORECASE,
)
RELATIVE_VALIDITY_PATTERN = re.compile(
    r"\bvalid(?:ity)?\s+(?:for\s+)?\d+\s*(?:day|days)\b"
    r"|\b(?:expire|expires|expiry)\b.{0,30}\bafter\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CriticalityContext:
    """Buyer-owned facts needed to resolve conditional criticality.

    Optional booleans let callers provide a stronger upstream determination.
    ``None`` means use deterministic document/candidate detection.
    """

    required_revision: str | None
    base_unit: str
    relative_delivery_required: bool | None = None
    relative_validity_requires_quote_date: bool | None = None

    def __post_init__(self) -> None:
        if not self.base_unit:
            raise ValueError("base_unit cannot be blank")


def validate_policy_fields(field_names: Iterable[str]) -> None:
    """Fail when C's versioned 16+10+4 policy drifts from A's dictionary."""

    actual = frozenset(field_names)
    missing = sorted(POLICY_FIELDS - actual)
    extra = sorted(actual - POLICY_FIELDS)
    if missing or extra or len(actual) != 30:
        raise ContractError(
            "criticality_policy_field_mismatch",
            "criticality policy must partition the 30 extractable quote fields",
            policy_version=CRITICALITY_POLICY_VERSION,
            missing=missing,
            extra=extra,
            actual_count=len(actual),
        )


def resolve_criticalities(
    batch: ExtractionBatch,
    context: CriticalityContext,
) -> tuple[CriticalityAssessment, ...]:
    """Return one deterministic criticality assessment per candidate field."""

    by_name = {candidate.field_name: candidate for candidate in batch.candidates}
    validate_policy_fields(by_name)
    packaging_required = _packaging_conversion_required(by_name, context.base_unit)
    relative_delivery_required = (
        context.relative_delivery_required
        if context.relative_delivery_required is not None
        else _relative_delivery_is_present(batch, by_name)
    )
    quote_date_required = (
        context.relative_validity_requires_quote_date
        if context.relative_validity_requires_quote_date is not None
        else _relative_validity_is_present(batch, by_name)
    )
    shipping_amount_required = _value(by_name.get("shipping_fee_status")) == "KNOWN_AMOUNT"
    other_amount_required = (
        _value(by_name.get("other_fees_status")) == "KNOWN_AMOUNT"
        or _non_zero_decimal(_value(by_name.get("other_fees_amount")))
    )

    assessments: list[CriticalityAssessment] = []
    for candidate in batch.candidates:
        field_name = candidate.field_name
        if field_name in ALWAYS_CRITICAL_FIELDS:
            assessments.append(
                CriticalityAssessment(
                    field_name=field_name,
                    criticality=EffectiveCriticality.ALWAYS,
                    applicable=True,
                    applicability_basis="C policy marks this field as always critical",
                )
            )
            continue
        if field_name in NON_CRITICAL_FIELDS:
            assessments.append(
                CriticalityAssessment(
                    field_name=field_name,
                    criticality=EffectiveCriticality.NON_CRITICAL,
                    applicable=False,
                    applicability_basis="C policy excludes this field from current comparison rules",
                )
            )
            continue

        applicable, basis = _conditional_rule(
            field_name,
            required_revision=context.required_revision,
            packaging_required=packaging_required,
            shipping_amount_required=shipping_amount_required,
            other_amount_required=other_amount_required,
            relative_delivery_required=relative_delivery_required,
            quote_date_required=quote_date_required,
        )
        assessments.append(
            CriticalityAssessment(
                field_name=field_name,
                criticality=(
                    EffectiveCriticality.CONDITIONAL_APPLICABLE
                    if applicable
                    else EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
                ),
                applicable=applicable,
                applicability_basis=basis,
            )
        )
    return tuple(assessments)


def _conditional_rule(
    field_name: str,
    *,
    required_revision: str | None,
    packaging_required: bool,
    shipping_amount_required: bool,
    other_amount_required: bool,
    relative_delivery_required: bool,
    quote_date_required: bool,
) -> tuple[bool, str]:
    if field_name == "revision":
        return bool(required_revision), "procurement requirement specifies revision" if required_revision else "no revision required"
    if field_name in {"packaging_type", "units_per_pack"}:
        return packaging_required, (
            "MOQ or price basis requires package-to-base-unit conversion"
            if packaging_required
            else "MOQ and price basis already use the comparison base unit"
        )
    if field_name == "shipping_fee_amount":
        return shipping_amount_required, (
            "shipping_fee_status is KNOWN_AMOUNT"
            if shipping_amount_required
            else "shipping_fee_status does not require a separate amount"
        )
    if field_name == "other_fees_amount":
        return other_amount_required, (
            "other fees state or non-zero amount requires an amount review"
            if other_amount_required
            else "other fees state does not require a separate amount"
        )
    if field_name in {"lead_time_days", "day_basis", "delivery_semantics", "start_event"}:
        return relative_delivery_required, (
            "quote uses a relative delivery expression"
            if relative_delivery_required
            else "no relative delivery expression detected"
        )
    if field_name == "quote_date":
        return quote_date_required, (
            "quote validity is expressed relative to quote date"
            if quote_date_required
            else "an explicit valid_until date is available"
        )
    raise ContractError(
        "criticality_rule_missing",
        f"conditional field has no criticality rule: {field_name}",
        field_name=field_name,
    )


def _value(candidate: QuoteFieldCandidate | None) -> object | None:
    if candidate is None or candidate.validation_status not in {
        ValidationStatus.EXTRACTED,
        ValidationStatus.VERIFIED,
    }:
        return None
    return candidate.normalized_value


def _packaging_conversion_required(
    by_name: dict[str, QuoteFieldCandidate],
    base_unit: str,
) -> bool:
    moq_unit = _value(by_name.get("moq_unit"))
    price_basis_unit = _value(by_name.get("price_basis_unit"))
    return any(
        isinstance(value, str) and value != base_unit
        for value in (moq_unit, price_basis_unit)
    )


def _relative_delivery_is_present(
    batch: ExtractionBatch,
    by_name: dict[str, QuoteFieldCandidate],
) -> bool:
    if any(
        _value(by_name.get(field_name)) is not None
        for field_name in ("lead_time_days", "day_basis", "delivery_semantics", "start_event")
    ):
        return True
    return bool(RELATIVE_DELIVERY_PATTERN.search(_document_text(batch)))


def _relative_validity_is_present(
    batch: ExtractionBatch,
    by_name: dict[str, QuoteFieldCandidate],
) -> bool:
    if _value(by_name.get("valid_until")) is not None:
        return False
    return bool(RELATIVE_VALIDITY_PATTERN.search(_document_text(batch)))


def _document_text(batch: ExtractionBatch) -> str:
    return "\n".join(source.raw_text for source in batch.parsed_input.sources)


def _non_zero_decimal(value: object | None) -> bool:
    if not isinstance(value, str):
        return False
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return False
    return amount.is_finite() and amount != 0
