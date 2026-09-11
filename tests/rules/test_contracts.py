from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplier_comparison.rules.contracts import (
    ComparisonDisposition,
    ComparisonResult,
    FeasibilityStatus,
    ProcurementRequirement,
    RuleIssue,
    SupplierEvaluation,
)


def _requirement(**updates: object) -> ProcurementRequirement:
    values = {
        "manufacturer": "QQ Demo Components",
        "manufacturer_part_number": "QW-MCU9-DEMO",
        "package": "QFN-32",
        "revision": "R1",
        "condition": "NEW",
        "allow_substitutes": False,
        "base_unit": "piece",
        "required_quantity": 1000,
        "quantity_unit": "piece",
        "budget_amount": "8000.00",
        "currency": "SGD",
        "includes_shipping": True,
        "tax_mode": "NOT_APPLICABLE",
        "other_fees_required": True,
        "planned_order_date": date(2026, 9, 14),
        "delivery_deadline": date(2026, 9, 19),
        "delivery_location": "SG-DEMO-01",
        "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
        "secondary_preference": None,
    }
    values.update(updates)
    return ProcurementRequirement.model_validate(values)


def test_requirement_accepts_decimal_string_and_serializes_money_as_string() -> None:
    requirement = _requirement()

    assert requirement.budget_amount == Decimal("8000.00")
    assert '"budget_amount":"8000.00"' in requirement.model_dump_json()


def test_requirement_rejects_binary_float_money() -> None:
    with pytest.raises(ValidationError, match="decimal string or Decimal"):
        _requirement(budget_amount=8000.0)


def test_requirement_rejects_deadline_before_order_date() -> None:
    with pytest.raises(ValidationError, match="cannot precede"):
        _requirement(delivery_deadline=date(2026, 9, 13))


def test_pending_supplier_requires_pending_reason() -> None:
    with pytest.raises(ValidationError, match="requires at least one pending reason"):
        SupplierEvaluation(
            quote_id="Q-B",
            quote_version=1,
            status=FeasibilityStatus.PENDING,
            known_cost_subtotal="6800.00",
        )


def test_feasible_supplier_requires_confirmed_total() -> None:
    with pytest.raises(ValidationError, match="requires a confirmed total_cost"):
        SupplierEvaluation(
            quote_id="Q-C",
            quote_version=1,
            status=FeasibilityStatus.FEASIBLE,
        )


def test_pending_comparison_cannot_publish_recommendation() -> None:
    result = SupplierEvaluation(
        quote_id="Q-B",
        quote_version=1,
        status=FeasibilityStatus.PENDING,
        known_cost_subtotal="6800.00",
        pending_reasons=(
            RuleIssue(
                code="SHIPPING_FEE_UNKNOWN",
                fields=("shipping_fee_status", "shipping_fee_amount"),
                message="Shipping fee must be confirmed.",
            ),
        ),
    )

    comparison = ComparisonResult(
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
        disposition=ComparisonDisposition.PENDING_INPUT,
        supplier_results=(result,),
        pending_quote_ids=("Q-B",),
        blocking_pending_quote_ids=("Q-B",),
        final_recommendation_allowed=False,
    )

    assert comparison.recommended_quote_ids == ()


def test_empty_scope_cannot_contain_supplier_results() -> None:
    pending = SupplierEvaluation(
        quote_id="Q-B",
        quote_version=1,
        status=FeasibilityStatus.PENDING,
        pending_reasons=(RuleIssue(code="MISSING", message="Missing field."),),
    )
    with pytest.raises(ValidationError, match="EMPTY_SCOPE"):
        ComparisonResult(
            evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
            disposition=ComparisonDisposition.EMPTY_SCOPE,
            supplier_results=(pending,),
            final_recommendation_allowed=False,
        )
