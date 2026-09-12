"""Deterministic Decimal cost and fee calculation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from supplier_comparison.extraction.contracts import ValidationStatus

from .contracts import (
    CostCalculation,
    ProcurementRequirement,
    QuantityBreakdown,
    QuoteInput,
    RuleIssue,
)
from .field_access import FieldAccess


MONEY_QUANTUM = Decimal("0.01")
ZERO = Decimal("0.00")
KNOWN_FEE_STATUSES = frozenset(
    {"KNOWN_AMOUNT", "FREE", "INCLUDED", "NOT_APPLICABLE", "UNKNOWN"}
)


def calculate_cost(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
    quantity: QuantityBreakdown,
) -> CostCalculation:
    """Calculate confirmed costs without treating unknown fees as zero."""

    fields = FieldAccess.from_quote(quote)
    issues: list[RuleIssue] = []

    quote_currency_raw = _required(fields, "currency", issues)
    quote_currency = _string(quote_currency_raw, "currency", issues)
    if quote_currency is not None and quote_currency != requirement.currency:
        issues.append(
            RuleIssue(
                code="CURRENCY_MISMATCH",
                fields=("currency",),
                message="Quote currency does not match the comparison currency.",
            )
        )

    price_raw = _required(fields, "unit_price", issues)
    basis_quantity_raw = _required(fields, "price_basis_quantity", issues)
    basis_unit_raw = _required(fields, "price_basis_unit", issues)
    price = _money(price_raw, "unit_price", issues, positive=True)
    basis_quantity = _positive_integer(
        basis_quantity_raw, "price_basis_quantity", issues
    )
    basis_unit = _string(basis_unit_raw, "price_basis_unit", issues)
    if basis_unit is not None and basis_unit != quantity.base_unit:
        issues.append(
            RuleIssue(
                code="PRICE_BASIS_UNIT_MISMATCH",
                fields=("price_basis_unit",),
                message="Price basis unit does not match the quantity base unit.",
            )
        )

    price_candidate = fields.candidate("unit_price")
    if (
        price_candidate is not None
        and price_candidate.unit is not None
        and quote_currency is not None
        and price_candidate.unit != quote_currency
    ):
        issues.append(
            RuleIssue(
                code="UNIT_PRICE_CURRENCY_MISMATCH",
                fields=("unit_price", "currency"),
                message="Unit price currency does not match the quoted currency.",
            )
        )

    goods_cost: Decimal | None = None
    if (
        price is not None
        and basis_quantity is not None
        and basis_unit == quantity.base_unit
    ):
        goods_cost = _round_money(
            Decimal(quantity.actual_purchase_quantity)
            / Decimal(basis_quantity)
            * price
        )

    shipping_cost, shipping_complete = _fee_contribution(
        fields,
        status_field="shipping_fee_status",
        amount_field="shipping_fee_amount",
        currency=quote_currency,
        required=True,
        issues=issues,
    )
    other_fees_cost, other_fees_complete = _fee_contribution(
        fields,
        status_field="other_fees_status",
        amount_field="other_fees_amount",
        currency=quote_currency,
        required=requirement.other_fees_required,
        issues=issues,
    )
    _validate_tax_mode(requirement, fields, issues)

    known_subtotal: Decimal | None = None
    if goods_cost is not None:
        known_subtotal = goods_cost
        if shipping_cost is not None:
            known_subtotal += shipping_cost
        if other_fees_cost is not None:
            known_subtotal += other_fees_cost
        known_subtotal = _round_money(known_subtotal)

    total_cost: Decimal | None = None
    if (
        not issues
        and goods_cost is not None
        and shipping_complete
        and other_fees_complete
        and known_subtotal is not None
    ):
        total_cost = known_subtotal

    return CostCalculation(
        currency=quote_currency or requirement.currency,
        goods_cost=goods_cost,
        shipping_cost=shipping_cost,
        other_fees_cost=other_fees_cost,
        known_cost_subtotal=known_subtotal,
        total_cost=total_cost,
        pending_reasons=tuple(issues),
    )


def _fee_contribution(
    fields: FieldAccess,
    *,
    status_field: str,
    amount_field: str,
    currency: str | None,
    required: bool,
    issues: list[RuleIssue],
) -> tuple[Decimal | None, bool]:
    status_raw, status_issue = fields.require(status_field)
    if status_issue is not None:
        if required:
            issues.append(status_issue)
            return None, False
        return ZERO, True

    status = _string(status_raw, status_field, issues)
    if status is None:
        return None, False
    if status not in KNOWN_FEE_STATUSES:
        issues.append(
            RuleIssue(
                code="FEE_STATUS_UNSUPPORTED",
                fields=(status_field,),
                message=f"Field {status_field} has an unsupported fee status.",
            )
        )
        return None, False
    if status == "UNKNOWN":
        if required:
            issues.append(
                RuleIssue(
                    code="FEE_AMOUNT_UNKNOWN",
                    fields=(status_field, amount_field),
                    message=f"Fee represented by {status_field} is unknown.",
                )
            )
            return None, False
        return ZERO, True
    if status in {"INCLUDED", "FREE", "NOT_APPLICABLE"}:
        if status in {"FREE", "NOT_APPLICABLE"}:
            amount_candidate = fields.candidate(amount_field)
            if (
                amount_candidate is not None
                and amount_candidate.validation_status == ValidationStatus.CONFLICT
            ):
                issues.append(
                    RuleIssue(
                        code="FIELD_CONFLICT",
                        fields=(amount_field,),
                        message=f"Required field {amount_field} is ambiguous or contradictory.",
                    )
                )
                return None, False
            if (
                amount_candidate is not None
                and amount_candidate.validation_status
                in {ValidationStatus.EXTRACTED, ValidationStatus.VERIFIED}
            ):
                amount = _money(
                    amount_candidate.normalized_value,
                    amount_field,
                    issues,
                    positive=False,
                )
                if amount is not None and amount != ZERO:
                    issues.append(
                        RuleIssue(
                            code="ZERO_FEE_STATUS_AMOUNT_CONFLICT",
                            fields=(status_field, amount_field),
                            message="Free or not-applicable fee has a non-zero amount.",
                        )
                    )
                    return None, False
        return ZERO, True

    amount_raw, amount_issue = fields.require(amount_field)
    if amount_issue is not None:
        issues.append(amount_issue)
        return None, False
    amount = _money(amount_raw, amount_field, issues, positive=False)
    if amount is None:
        return None, False
    amount_candidate = fields.candidate(amount_field)
    if (
        amount_candidate is not None
        and amount_candidate.unit is not None
        and currency is not None
        and amount_candidate.unit != currency
    ):
        issues.append(
            RuleIssue(
                code="FEE_CURRENCY_MISMATCH",
                fields=(amount_field,),
                message=f"Field {amount_field} currency does not match the quote.",
            )
        )
        return None, False
    return _round_money(amount), True


def _validate_tax_mode(
    requirement: ProcurementRequirement,
    fields: FieldAccess,
    issues: list[RuleIssue],
) -> None:
    tax_raw = _required(fields, "tax_mode", issues)
    tax_mode = _string(tax_raw, "tax_mode", issues)
    if tax_mode is None:
        return
    if tax_mode != requirement.tax_mode:
        issues.append(
            RuleIssue(
                code="TAX_MODE_MISMATCH",
                fields=("tax_mode",),
                message="Quote tax treatment does not match the requirement.",
            )
        )
    elif tax_mode != "NOT_APPLICABLE":
        issues.append(
            RuleIssue(
                code="TAX_CALCULATION_UNSUPPORTED",
                fields=("tax_mode",),
                message="MVP cost calculation only supports explicitly non-applicable tax.",
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


def _money(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
    *,
    positive: bool,
) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        issues.append(
            RuleIssue(
                code="DECIMAL_STRING_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must be a decimal string.",
            )
        )
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite() or amount < ZERO or (positive and amount == ZERO):
        issues.append(
            RuleIssue(
                code="MONEY_VALUE_INVALID",
                fields=(field_name,),
                message=f"Field {field_name} must contain a valid monetary amount.",
            )
        )
        return None
    return amount


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


def _string(
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


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
