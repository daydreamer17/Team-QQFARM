from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

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
    DecisionImpactRequest,
    ImpactStatus,
    analyze_decision_impact,
)


EVALUATED_AT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def _requirement(
    *,
    ranking_preference: str = "LOWEST_CONFIRMED_TOTAL_COST",
    secondary_preference: str | None = None,
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
        secondary_preference=secondary_preference,
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


def test_cost_then_fastest_breaks_only_an_exact_cost_tie() -> None:
    requirement = _requirement(secondary_preference="FASTEST_CONFIRMED_DELIVERY")
    slow = _supplier_b(
        shipping_status="KNOWN_AMOUNT", shipping_amount="300.00"
    )
    fast = _supplier_c()
    slow = slow.model_copy(update={"candidates": tuple(
        candidate.model_copy(update={"normalized_value": 4, "raw_value": "4"})
        if candidate.field_name == "lead_time_days" else candidate
        for candidate in slow.candidates
    )})

    result = compare_suppliers(_request(slow, fast, requirement=requirement))

    assert result.ranked_quote_ids == (("QUOTE-C",), ("QUOTE-B",))
    assert result.recommended_quote_ids == ("QUOTE-C",)


def test_fastest_and_fastest_then_cost_are_deterministic() -> None:
    cheap_slow = _supplier_b(
        shipping_status="KNOWN_AMOUNT", shipping_amount="100.00", unit_price="6.00"
    )
    cheap_slow = cheap_slow.model_copy(update={"candidates": tuple(
        candidate.model_copy(update={"normalized_value": 4, "raw_value": "4"})
        if candidate.field_name == "lead_time_days" else candidate
        for candidate in cheap_slow.candidates
    )})
    expensive_fast = _supplier_c()
    fastest = compare_suppliers(_request(
        cheap_slow,
        expensive_fast,
        requirement=_requirement(ranking_preference="FASTEST_CONFIRMED_DELIVERY"),
    ))
    assert fastest.recommended_quote_ids == ("QUOTE-C",)

    same_arrival = cheap_slow.model_copy(update={"candidates": tuple(
        candidate.model_copy(update={"normalized_value": 3, "raw_value": "3"})
        if candidate.field_name == "lead_time_days" else candidate
        for candidate in cheap_slow.candidates
    )})
    fastest_then_cost = compare_suppliers(_request(
        same_arrival,
        expensive_fast,
        requirement=_requirement(
            ranking_preference="FASTEST_CONFIRMED_DELIVERY",
            secondary_preference="LOWEST_CONFIRMED_TOTAL_COST",
        ),
    ))
    assert fastest_then_cost.recommended_quote_ids == ("QUOTE-B",)


def test_cost_tolerance_pool_chooses_fastest_then_cost() -> None:
    cheap_slow = _supplier_b(
        shipping_status="KNOWN_AMOUNT", shipping_amount="100.00", unit_price="6.00"
    )
    cheap_slow = cheap_slow.model_copy(update={"candidates": tuple(
        candidate.model_copy(update={"normalized_value": 4, "raw_value": "4"})
        if candidate.field_name == "lead_time_days" else candidate
        for candidate in cheap_slow.candidates
    )})
    expensive_fast = _supplier_c()
    requirement = _requirement(secondary_preference="FASTEST_CONFIRMED_DELIVERY")

    outside = compare_suppliers(ComparisonRequest(
        requirement=requirement,
        quotes=(cheap_slow, expensive_fast),
        evaluated_at=EVALUATED_AT,
        cost_tolerance_amount="50.00",
    ))
    inside = compare_suppliers(ComparisonRequest(
        requirement=requirement,
        quotes=(cheap_slow, expensive_fast),
        evaluated_at=EVALUATED_AT,
        cost_tolerance_amount="1000.00",
    ))

    assert outside.recommended_quote_ids == ("QUOTE-B",)
    assert inside.recommended_quote_ids == ("QUOTE-C",)


def test_delivery_first_keeps_unresolved_quote_blocking() -> None:
    result = compare_suppliers(_request(
        _supplier_b(),
        _supplier_c(),
        requirement=_requirement(ranking_preference="FASTEST_CONFIRMED_DELIVERY"),
    ))

    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.blocking_pending_quote_ids == ("QUOTE-B",)
    assert not result.final_recommendation_allowed


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


def _impact_example(*, pending_price: str = "9.80", task_revision: int = 1):
    requirement = _requirement().model_copy(update={"budget_amount": Decimal("20000.00")})
    winner = _supplier_c()
    winner = winner.model_copy(update={"candidates": tuple(
        _candidate(winner.quote_id, c.field_name, "3400.00", unit="SGD")
        if c.field_name == "shipping_fee_amount" else c for c in winner.candidates
    )})
    return DecisionImpactRequest(
        task_id="TASK-IMPACT", task_revision=task_revision,
        comparison=_request(winner, _supplier_b(unit_price=pending_price), requirement=requirement),
    )


@pytest.mark.parametrize("price,status,threshold", [
    ("9.80", ImpactStatus.REQUIRES_INVESTIGATION, Decimal("200.00")),
    ("10.00", ImpactStatus.REQUIRES_INVESTIGATION, Decimal("0.00")),
    ("15.00", ImpactStatus.NON_BLOCKING, None),
])
def test_decision_impact_reports_fee_threshold_without_filling_unknown(price, status, threshold):
    report = analyze_decision_impact(_impact_example(pending_price=price))
    impact = next(i for i in report.quote_impacts if i.quote_id == "QUOTE-B")
    supplier = next(i for i in report.comparison.supplier_results if i.quote_id == "QUOTE-B")
    assert impact.status == status
    assert impact.additional_cost_to_tie == threshold
    assert impact.best_confirmed_cost == Decimal("10000.00")
    assert impact.cost_lower_bound == Decimal(price) * 1000
    assert supplier.total_cost is None
    assert supplier.status == FeasibilityStatus.PENDING
    assert bool(report.blocking_quote_ids) == (status != ImpactStatus.NON_BLOCKING)
    assert report.comparison.final_recommendation_allowed == (status == ImpactStatus.NON_BLOCKING)


@pytest.mark.parametrize("field,value", [
    ("currency", "USD"), ("price_basis_unit", "tray"),
    ("tax_mode", "EXCLUSIVE"), ("package", None),
])
def test_high_subtotal_does_not_hide_unproven_cost_or_nonfee_unknown(field, value):
    quote = _supplier_b(unit_price="15.00")
    quote = quote.model_copy(update={"candidates": tuple(
        _candidate(quote.quote_id, field, value,
                   status=ValidationStatus.MISSING if value is None else ValidationStatus.VERIFIED)
        if c.field_name == field else c for c in quote.candidates
    )})
    requirement = _requirement().model_copy(update={"budget_amount": Decimal("20000.00")})
    report = analyze_decision_impact(DecisionImpactRequest(
        task_id="TASK-IMPACT", task_revision=1,
        comparison=_request(_supplier_c(), quote, requirement=requirement),
    ))
    impact = next(i for i in report.quote_impacts if i.quote_id == quote.quote_id)
    assert impact.status == ImpactStatus.UNDETERMINED
    assert impact.cost_lower_bound is None
    assert report.comparison.blocking_pending_quote_ids == (quote.quote_id,)
    assert not report.comparison.final_recommendation_allowed


def test_expensive_unknown_without_feasible_baseline_still_needs_investigation():
    request = _impact_example(pending_price="15.00")
    request = request.model_copy(update={"comparison": request.comparison.model_copy(
        update={"quotes": (request.comparison.quotes[1],)}
    )})
    report = analyze_decision_impact(request)
    assert report.quote_impacts[0].status == ImpactStatus.REQUIRES_INVESTIGATION
    assert report.quote_impacts[0].reason_code == "NO_CONFIRMED_FEASIBLE_BASELINE"
    assert not report.comparison.final_recommendation_allowed


def test_impact_proof_is_bound_to_revision_requirement_quote_and_policy():
    original = _impact_example()
    digest = analyze_decision_impact(original).input_sha256
    variants = (
        original.model_copy(update={"task_revision": 2}),
        _impact_example(pending_price="15.00"),
        original.model_copy(update={"policy_binding": {"policy_set_version": "next"}}),
        original.model_copy(update={"comparison": original.comparison.model_copy(update={
            "requirement": original.comparison.requirement.model_copy(update={
                "ranking_preference": "FASTEST_DELIVERY"
            })
        })}),
    )
    assert all(analyze_decision_impact(v).input_sha256 != digest for v in variants)
