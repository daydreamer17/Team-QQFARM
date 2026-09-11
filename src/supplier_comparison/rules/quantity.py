"""Deterministic MOQ, packaging, and order-multiple conversion."""

from __future__ import annotations

from .contracts import (
    ProcurementRequirement,
    QuantityBreakdown,
    QuantityCalculation,
    QuoteInput,
    RuleIssue,
)
from .field_access import FieldAccess


def calculate_quantity(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
) -> QuantityCalculation:
    """Calculate purchase quantity in the requirement base unit.

    Formula: ceil(max(demand, MOQ) / order_multiple) * order_multiple.
    """

    issues: list[RuleIssue] = []
    if requirement.quantity_unit != requirement.base_unit:
        issues.append(
            RuleIssue(
                code="REQUIREMENT_UNIT_CONVERSION_UNSUPPORTED",
                fields=("quantity_unit", "base_unit"),
                message="Requirement quantity unit must match the comparison base unit.",
            )
        )

    fields = FieldAccess.from_quote(quote)
    moq_raw = _required(fields, "moq_quantity", issues)
    moq_unit_raw = _required(fields, "moq_unit", issues)
    multiple_raw = _required(fields, "order_multiple_units", issues)
    if issues:
        return QuantityCalculation(pending_reasons=tuple(issues))

    moq_quantity = _positive_integer(moq_raw, "moq_quantity", issues)
    order_multiple = _positive_integer(
        multiple_raw, "order_multiple_units", issues
    )
    moq_unit = _non_empty_string(moq_unit_raw, "moq_unit", issues)

    multiple_candidate = fields.candidate("order_multiple_units")
    if (
        multiple_candidate is not None
        and multiple_candidate.unit is not None
        and multiple_candidate.unit != requirement.base_unit
    ):
        issues.append(
            RuleIssue(
                code="ORDER_MULTIPLE_UNIT_MISMATCH",
                fields=("order_multiple_units", "base_unit"),
                message="Order multiple is not expressed in the comparison base unit.",
            )
        )

    moq_in_base_units: int | None = None
    if moq_unit is not None and moq_quantity is not None:
        if moq_unit == requirement.base_unit:
            moq_in_base_units = moq_quantity
        else:
            packaging_raw = _required(fields, "packaging_type", issues)
            units_per_pack_raw = _required(fields, "units_per_pack", issues)
            packaging_type = _non_empty_string(
                packaging_raw, "packaging_type", issues
            )
            units_per_pack = _positive_integer(
                units_per_pack_raw, "units_per_pack", issues
            )
            if packaging_type is not None and packaging_type != moq_unit:
                issues.append(
                    RuleIssue(
                        code="MOQ_PACKAGING_UNIT_MISMATCH",
                        fields=("moq_unit", "packaging_type"),
                        message="MOQ unit does not match the quoted packaging type.",
                    )
                )
            elif units_per_pack is not None:
                moq_in_base_units = moq_quantity * units_per_pack

    if issues or moq_in_base_units is None or order_multiple is None:
        return QuantityCalculation(pending_reasons=tuple(issues))

    target = max(requirement.required_quantity, moq_in_base_units)
    actual_quantity = ((target + order_multiple - 1) // order_multiple) * order_multiple
    return QuantityCalculation(
        breakdown=QuantityBreakdown(
            base_unit=requirement.base_unit,
            required_quantity=requirement.required_quantity,
            moq_quantity=moq_in_base_units,
            order_multiple=order_multiple,
            actual_purchase_quantity=actual_quantity,
        )
    )


def _required(
    fields: FieldAccess,
    field_name: str,
    issues: list[RuleIssue],
) -> object | None:
    value, issue = fields.require(field_name)
    if issue is not None:
        issues.append(issue)
    return value


def _positive_integer(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        issues.append(
            RuleIssue(
                code="FIELD_POSITIVE_INTEGER_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must be a positive normalized integer.",
            )
        )
        return None
    return value


def _non_empty_string(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        issues.append(
            RuleIssue(
                code="FIELD_STRING_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must be a non-empty normalized string.",
            )
        )
        return None
    return value
