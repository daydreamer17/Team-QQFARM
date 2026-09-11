from datetime import date
from decimal import Decimal

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.rules.contracts import (
    ProcurementRequirement,
    QuantityBreakdown,
    QuoteInput,
)
from supplier_comparison.rules.cost import calculate_cost


def _requirement() -> ProcurementRequirement:
    return ProcurementRequirement(
        manufacturer="QQ Demo Components",
        manufacturer_part_number="QW-MCU9-DEMO",
        package="QFN-32",
        revision="R1",
        condition="NEW",
        allow_substitutes=False,
        base_unit="piece",
        required_quantity=1000,
        quantity_unit="piece",
        budget_amount="8000.00",
        currency="SGD",
        includes_shipping=True,
        tax_mode="NOT_APPLICABLE",
        other_fees_required=True,
        planned_order_date=date(2026, 9, 14),
        delivery_deadline=date(2026, 9, 19),
        delivery_location="SG-DEMO-01",
        ranking_preference="LOWEST_CONFIRMED_TOTAL_COST",
    )


def _candidate(
    field_name: str,
    value: str | int | bool | None,
    *,
    unit: str | None = None,
    status: ValidationStatus = ValidationStatus.VERIFIED,
) -> QuoteFieldCandidate:
    missing = status == ValidationStatus.MISSING
    conflict = status == ValidationStatus.CONFLICT
    return QuoteFieldCandidate(
        field_id=f"FIELD-{field_name}",
        quote_id="QUOTE-A",
        quote_version=1,
        field_name=field_name,
        raw_value=None if missing else str(value),
        normalized_value=None if missing or conflict else value,
        unit=None if missing else unit,
        validation_status=status,
        origin=None if missing else Origin.USER_INPUT,
        producer=CandidateProducer.DETERMINISTIC_PARSER,
    )


def _quote(
    *,
    price: str,
    basis: int,
    shipping_status: str | None,
    shipping_amount: str | None,
    shipping_validation: ValidationStatus = ValidationStatus.VERIFIED,
    other_status: str | None = "NOT_APPLICABLE",
    other_amount: str | None = "0.00",
    other_validation: ValidationStatus = ValidationStatus.VERIFIED,
) -> QuoteInput:
    return QuoteInput(
        quote_id="QUOTE-A",
        quote_version=1,
        candidates=(
            _candidate("currency", "SGD"),
            _candidate("unit_price", price, unit="SGD"),
            _candidate("price_basis_quantity", basis, unit="piece"),
            _candidate("price_basis_unit", "piece"),
            _candidate(
                "shipping_fee_status",
                shipping_status,
                status=shipping_validation,
            ),
            _candidate(
                "shipping_fee_amount",
                shipping_amount,
                unit="SGD",
                status=(
                    ValidationStatus.MISSING
                    if shipping_amount is None
                    else ValidationStatus.VERIFIED
                ),
            ),
            _candidate(
                "other_fees_status",
                other_status,
                status=other_validation,
            ),
            _candidate(
                "other_fees_amount",
                other_amount,
                unit="SGD",
                status=(
                    ValidationStatus.MISSING
                    if other_amount is None
                    else ValidationStatus.VERIFIED
                ),
            ),
            _candidate("tax_mode", "NOT_APPLICABLE"),
        ),
    )


def _quantity(actual: int) -> QuantityBreakdown:
    return QuantityBreakdown(
        base_unit="piece",
        required_quantity=1000,
        moq_quantity=1000,
        order_multiple=1,
        actual_purchase_quantity=actual,
    )


def test_supplier_a_goods_and_free_shipping_total() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="640.00",
            basis=100,
            shipping_status="FREE",
            shipping_amount="0.00",
        ),
        _quantity(2000),
    )

    assert result.goods_cost == Decimal("12800.00")
    assert result.shipping_cost == Decimal("0.00")
    assert result.known_cost_subtotal == Decimal("12800.00")
    assert result.total_cost == Decimal("12800.00")
    assert result.pending_reasons == ()


def test_model_string_price_basis_quantity_is_supported() -> None:
    quote = _quote(
        price="640.00",
        basis=100,
        shipping_status="FREE",
        shipping_amount="0.00",
    )
    candidates = tuple(
        candidate.model_copy(update={"normalized_value": "100"})
        if candidate.field_name == "price_basis_quantity"
        else candidate
        for candidate in quote.candidates
    )

    result = calculate_cost(
        _requirement(),
        quote.model_copy(update={"candidates": candidates}),
        _quantity(2000),
    )

    assert result.total_cost == Decimal("12800.00")


def test_missing_shipping_keeps_known_goods_subtotal_without_total() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="6.80",
            basis=1,
            shipping_status=None,
            shipping_amount=None,
            shipping_validation=ValidationStatus.MISSING,
        ),
        _quantity(1000),
    )

    assert result.goods_cost == Decimal("6800.00")
    assert result.known_cost_subtotal == Decimal("6800.00")
    assert result.total_cost is None
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_MISSING"]
    assert result.pending_reasons[0].fields == ("shipping_fee_status",)


def test_confirmed_shipping_produces_supplier_b_total() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="6.80",
            basis=1,
            shipping_status="KNOWN_AMOUNT",
            shipping_amount="200.00",
        ),
        _quantity(1000),
    )

    assert result.goods_cost == Decimal("6800.00")
    assert result.shipping_cost == Decimal("200.00")
    assert result.total_cost == Decimal("7000.00")


def test_supplier_c_total() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="660.00",
            basis=100,
            shipping_status="KNOWN_AMOUNT",
            shipping_amount="500.00",
        ),
        _quantity(1000),
    )

    assert result.goods_cost == Decimal("6600.00")
    assert result.total_cost == Decimal("7100.00")


def test_included_shipping_is_not_added_twice() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="7000.00",
            basis=1000,
            shipping_status="INCLUDED",
            shipping_amount=None,
        ),
        _quantity(1000),
    )

    assert result.shipping_cost == Decimal("0.00")
    assert result.total_cost == Decimal("7000.00")


def test_unknown_shipping_is_not_treated_as_zero() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="6.80",
            basis=1,
            shipping_status="UNKNOWN",
            shipping_amount=None,
        ),
        _quantity(1000),
    )

    assert result.shipping_cost is None
    assert result.known_cost_subtotal == Decimal("6800.00")
    assert result.total_cost is None
    assert [issue.code for issue in result.pending_reasons] == [
        "FEE_AMOUNT_UNKNOWN"
    ]


def test_known_other_fee_is_added_to_total() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="6.80",
            basis=1,
            shipping_status="FREE",
            shipping_amount="0.00",
            other_status="KNOWN_AMOUNT",
            other_amount="25.50",
        ),
        _quantity(1000),
    )

    assert result.other_fees_cost == Decimal("25.50")
    assert result.total_cost == Decimal("6825.50")


def test_free_shipping_with_nonzero_amount_is_a_conflict() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="6.80",
            basis=1,
            shipping_status="FREE",
            shipping_amount="10.00",
        ),
        _quantity(1000),
    )

    assert result.shipping_cost is None
    assert result.total_cost is None
    assert [issue.code for issue in result.pending_reasons] == [
        "ZERO_FEE_STATUS_AMOUNT_CONFLICT"
    ]


def test_goods_line_uses_half_up_rounding_to_sgd_cent() -> None:
    result = calculate_cost(
        _requirement(),
        _quote(
            price="1.005",
            basis=1,
            shipping_status="FREE",
            shipping_amount="0.00",
        ),
        _quantity(3),
    )

    assert result.goods_cost == Decimal("3.02")
    assert result.total_cost == Decimal("3.02")
