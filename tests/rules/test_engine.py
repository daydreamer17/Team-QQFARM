from datetime import date, datetime, timezone

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.rules import (
    ComparisonDisposition,
    ComparisonRequest,
    FeasibilityStatus,
    ProcurementRequirement,
    QuoteInput,
    compare_suppliers,
    evaluate_supplier,
)


EVALUATED_AT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def _requirement(
    *,
    ranking_preference: str = "LOWEST_CONFIRMED_TOTAL_COST",
    includes_shipping: bool = True,
) -> ProcurementRequirement:
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
        includes_shipping=includes_shipping,
        tax_mode="NOT_APPLICABLE",
        other_fees_required=True,
        planned_order_date=date(2026, 9, 14),
        delivery_deadline=date(2026, 9, 19),
        delivery_location="SG-DEMO-01",
        ranking_preference=ranking_preference,
    )


def _candidate(
    quote_id: str,
    field_name: str,
    value: str | int | bool | None,
    *,
    unit: str | None = None,
    status: ValidationStatus = ValidationStatus.VERIFIED,
) -> QuoteFieldCandidate:
    missing = status == ValidationStatus.MISSING
    conflict = status == ValidationStatus.CONFLICT
    return QuoteFieldCandidate(
        field_id=f"{quote_id}-{field_name}",
        quote_id=quote_id,
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
    quote_id: str,
    supplier_name: str,
    *,
    moq_quantity: int,
    moq_unit: str,
    units_per_pack: int,
    order_multiple: int,
    unit_price: str,
    price_basis_quantity: int,
    shipping_status: str | None,
    shipping_amount: str | None,
    lead_days: int,
) -> QuoteInput:
    shipping_validation = (
        ValidationStatus.MISSING
        if shipping_status is None
        else ValidationStatus.VERIFIED
    )
    amount_validation = (
        ValidationStatus.MISSING
        if shipping_amount is None
        else ValidationStatus.VERIFIED
    )
    return QuoteInput(
        quote_id=quote_id,
        quote_version=1,
        candidates=(
            _candidate(quote_id, "supplier_name", supplier_name),
            _candidate(quote_id, "manufacturer", "QQ Demo Components"),
            _candidate(
                quote_id,
                "manufacturer_part_number",
                "QW-MCU9-DEMO",
            ),
            _candidate(quote_id, "package", "QFN-32"),
            _candidate(quote_id, "revision", "R1"),
            _candidate(quote_id, "condition", "NEW"),
            _candidate(
                quote_id,
                "moq_quantity",
                moq_quantity,
                unit=moq_unit,
            ),
            _candidate(quote_id, "moq_unit", moq_unit),
            _candidate(quote_id, "packaging_type", moq_unit),
            _candidate(
                quote_id,
                "units_per_pack",
                units_per_pack,
                unit="piece",
            ),
            _candidate(
                quote_id,
                "order_multiple_units",
                order_multiple,
                unit="piece",
            ),
            _candidate(quote_id, "currency", "SGD"),
            _candidate(
                quote_id,
                "unit_price",
                unit_price,
                unit="SGD",
            ),
            _candidate(
                quote_id,
                "price_basis_quantity",
                price_basis_quantity,
                unit="piece",
            ),
            _candidate(quote_id, "price_basis_unit", "piece"),
            _candidate(
                quote_id,
                "shipping_fee_status",
                shipping_status,
                status=shipping_validation,
            ),
            _candidate(
                quote_id,
                "shipping_fee_amount",
                shipping_amount,
                unit="SGD",
                status=amount_validation,
            ),
            _candidate(quote_id, "other_fees_status", "NOT_APPLICABLE"),
            _candidate(
                quote_id,
                "other_fees_amount",
                "0.00",
                unit="SGD",
            ),
            _candidate(quote_id, "tax_mode", "NOT_APPLICABLE"),
            _candidate(
                quote_id,
                "lead_time_days",
                lead_days,
                unit="calendar_day",
            ),
            _candidate(quote_id, "day_basis", "CALENDAR_DAYS"),
            _candidate(quote_id, "delivery_semantics", "ARRIVAL"),
            _candidate(quote_id, "start_event", "ORDER_DATE"),
            _candidate(quote_id, "valid_until", "2026-09-20"),
        ),
    )


def _supplier_a() -> QuoteInput:
    return _quote(
        "QUOTE-A",
        "Redwood Components",
        moq_quantity=20,
        moq_unit="tray",
        units_per_pack=100,
        order_multiple=100,
        unit_price="640.00",
        price_basis_quantity=100,
        shipping_status="FREE",
        shipping_amount="0.00",
        lead_days=7,
    )


def _supplier_b(
    *,
    shipping_status: str | None = None,
    shipping_amount: str | None = None,
    unit_price: str = "6.80",
) -> QuoteInput:
    return _quote(
        "QUOTE-B",
        "Schwarzwald Circuits",
        moq_quantity=1000,
        moq_unit="piece",
        units_per_pack=1,
        order_multiple=1,
        unit_price=unit_price,
        price_basis_quantity=1,
        shipping_status=shipping_status,
        shipping_amount=shipping_amount,
        lead_days=3,
    )


def _supplier_c() -> QuoteInput:
    return _quote(
        "QUOTE-C",
        "Sterling Components",
        moq_quantity=10,
        moq_unit="tray",
        units_per_pack=100,
        order_multiple=100,
        unit_price="660.00",
        price_basis_quantity=100,
        shipping_status="KNOWN_AMOUNT",
        shipping_amount="500.00",
        lead_days=3,
    )


def _request(*quotes: QuoteInput, requirement=None) -> ComparisonRequest:
    return ComparisonRequest(
        requirement=requirement or _requirement(),
        quotes=quotes,
        evaluated_at=EVALUATED_AT,
    )


def test_supplier_a_is_infeasible_for_budget_and_delivery() -> None:
    result = evaluate_supplier(
        _requirement(),
        _supplier_a(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.actual_quantity == 2000
    assert str(result.total_cost) == "12800.00"
    assert result.estimated_arrival_date == date(2026, 9, 21)
    assert {issue.code for issue in result.failed_reasons} == {
        "BUDGET_EXCEEDED",
        "DELIVERY_DEADLINE_EXCEEDED",
    }


def test_supplier_b_with_missing_shipping_is_pending() -> None:
    result = evaluate_supplier(
        _requirement(),
        _supplier_b(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.status == FeasibilityStatus.PENDING
    assert str(result.known_cost_subtotal) == "6800.00"
    assert result.total_cost is None
    assert any(
        issue.fields == ("shipping_fee_status",)
        for issue in result.pending_reasons
    )


def test_pending_b_blocks_final_recommendation_of_c() -> None:
    result = compare_suppliers(
        _request(_supplier_a(), _supplier_b(), _supplier_c())
    )

    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.ranked_quote_ids == (("QUOTE-C",),)
    assert result.recommended_quote_ids == ()
    assert result.blocking_pending_quote_ids == ("QUOTE-B",)
    assert not result.final_recommendation_allowed


def test_confirmed_b_shipping_makes_b_the_recommendation() -> None:
    result = compare_suppliers(
        _request(
            _supplier_a(),
            _supplier_b(
                shipping_status="KNOWN_AMOUNT",
                shipping_amount="200.00",
            ),
            _supplier_c(),
        )
    )

    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.ranked_quote_ids == (("QUOTE-B",), ("QUOTE-C",))
    assert result.recommended_quote_ids == ("QUOTE-B",)
    assert result.final_recommendation_allowed


def test_equal_primary_cost_returns_a_tie() -> None:
    result = compare_suppliers(
        _request(
            _supplier_b(
                shipping_status="KNOWN_AMOUNT",
                shipping_amount="300.00",
            ),
            _supplier_c(),
        )
    )

    assert result.ranked_quote_ids == (("QUOTE-B", "QUOTE-C"),)
    assert result.recommended_quote_ids == ("QUOTE-B", "QUOTE-C")


def test_nonblocking_pending_quote_does_not_hide_cheaper_confirmed_winner() -> None:
    result = compare_suppliers(
        _request(
            _supplier_b(unit_price="8.00"),
            _supplier_c(),
        )
    )

    assert result.pending_quote_ids == ("QUOTE-B",)
    assert result.blocking_pending_quote_ids == ()
    assert result.recommended_quote_ids == ("QUOTE-C",)
    assert result.final_recommendation_allowed


def test_all_confirmed_infeasible_is_distinct_from_pending() -> None:
    result = compare_suppliers(_request(_supplier_a()))

    assert result.disposition == ComparisonDisposition.NO_FEASIBLE_QUOTES
    assert result.recommended_quote_ids == ()


def test_budget_can_explicitly_exclude_shipping() -> None:
    quote = _quote(
        "QUOTE-X",
        "Budget Scope Supplier",
        moq_quantity=1000,
        moq_unit="piece",
        units_per_pack=1,
        order_multiple=1,
        unit_price="7.90",
        price_basis_quantity=1,
        shipping_status="KNOWN_AMOUNT",
        shipping_amount="500.00",
        lead_days=3,
    )

    result = evaluate_supplier(
        _requirement(includes_shipping=False),
        quote,
        evaluated_at=EVALUATED_AT,
    )

    assert str(result.total_cost) == "8400.00"
    assert result.status == FeasibilityStatus.FEASIBLE
    assert "BUDGET_EXCEEDED" not in {
        issue.code for issue in result.failed_reasons
    }


def test_empty_comparison_scope_is_explicit() -> None:
    result = compare_suppliers(_request())

    assert result.disposition == ComparisonDisposition.EMPTY_SCOPE
    assert result.supplier_results == ()


def test_unsupported_ranking_preference_requires_input() -> None:
    result = compare_suppliers(
        _request(
            _supplier_c(),
            requirement=_requirement(ranking_preference="HIGHEST_RATING"),
        )
    )

    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert [issue.code for issue in result.comparison_reasons] == [
        "RANKING_PREFERENCE_UNSUPPORTED"
    ]
