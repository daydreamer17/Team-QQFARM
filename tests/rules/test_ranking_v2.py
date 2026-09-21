from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from supplier_comparison.extraction.contracts import ValidationStatus
from supplier_comparison.rules import (
    ComparisonDisposition,
    ComparisonRequest,
    HistoryAvailabilityStatus,
    IdentityMatchStatus,
    ProcurementRequirement,
    RankingCriterion,
    RateMetric,
    SupplierHistoryDatasetContext,
    SupplierHistorySnapshot,
    compare_suppliers,
)
from tests.rules.test_engine import (
    EVALUATED_AT,
    _candidate,
    _requirement,
    _supplier_b,
    _supplier_c,
)


def _with_fields(quote, **fields):
    candidates = {item.field_name: item for item in quote.candidates}
    for name, value in fields.items():
        candidates[name] = _candidate(
            quote.quote_id,
            name,
            value,
            status=ValidationStatus.VERIFIED,
        )
    return quote.model_copy(update={"candidates": tuple(candidates.values())})


def _history_context() -> SupplierHistoryDatasetContext:
    return SupplierHistoryDatasetContext(
        dataset_id="synthetic-mcu9-supplier-performance",
        dataset_version="2026-08-06-v1",
        content_sha256="a" * 64,
        manifest_sha256="b" * 64,
        rating_method_version="mcu9-demo-rating/1.0.0",
        scope={"category": "Electronics", "item": "Microcontroller MCU-9"},
        as_of_date=date(2026, 8, 6),
        period_start=date(2023, 1, 2),
        period_end=date(2026, 6, 29),
        is_synthetic=True,
    )


def _snapshot(quote_id: str, supplier_id: str, name: str, grade: str, on_time, rejected):
    return SupplierHistorySnapshot(
        quote_id=quote_id,
        supplier_id=supplier_id,
        supplier_name=name,
        identity_match_status=IdentityMatchStatus.MATCHED,
        history_availability_status=HistoryAvailabilityStatus.AVAILABLE,
        overall_grade=grade,
        on_time=RateMetric(
            numerator=on_time[0],
            denominator=on_time[1],
            rate=Decimal(on_time[0]) / Decimal(on_time[1]),
        ),
        rejected_lines=RateMetric(
            numerator=rejected[0],
            denominator=rejected[1],
            rate=Decimal(rejected[0]) / Decimal(rejected[1]),
        ),
        evidence_refs=(f"HISTORY:{supplier_id}",),
    )


def _request(primary, secondary=None, *, tolerance=None, b=None, c=None):
    b = b or _supplier_b(shipping_status="KNOWN_AMOUNT", shipping_amount="200.00")
    c = c or _supplier_c()
    if not any(item.field_name == "payment_terms" for item in b.candidates):
        b = _with_fields(b, payment_terms="Net 45 after invoice")
    if not any(item.field_name == "payment_terms" for item in c.candidates):
        c = _with_fields(c, payment_terms="Net 30 after invoice")
    requirement = _requirement(
        ranking_preference=primary,
        secondary_preference=secondary,
    )
    return ComparisonRequest(
        requirement=requirement,
        quotes=(b, c),
        evaluated_at=EVALUATED_AT,
        cost_tolerance_amount=tolerance,
        history_dataset_context=_history_context(),
        supplier_history_snapshots=(
            _snapshot("QUOTE-B", "SUP-023", "Schwarzwald Circuits", "C", (11, 11), (1, 11)),
            _snapshot("QUOTE-C", "SUP-024", "Sterling Components", "A", (51, 55), (0, 55)),
        ),
    )


@pytest.mark.parametrize(
    ("criterion", "expected"),
    [
        (RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST, ("QUOTE-B",)),
        (RankingCriterion.FASTEST_CONFIRMED_DELIVERY, ("QUOTE-B", "QUOTE-C")),
        (RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM, ("QUOTE-B",)),
        (RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE, ("QUOTE-C",)),
        (RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE, ("QUOTE-B",)),
        (RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE, ("QUOTE-C",)),
    ],
)
def test_all_six_atomic_criteria_are_authoritative(criterion, expected) -> None:
    result = compare_suppliers(_request(criterion))
    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.recommended_quote_ids == expected
    assert result.ranking_trace is not None
    assert result.ranking_trace.ordered_criteria == (criterion,)


def test_secondary_only_runs_when_primary_best_group_is_tied() -> None:
    result = compare_suppliers(
        _request(
            RankingCriterion.FASTEST_CONFIRMED_DELIVERY,
            RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
        )
    )
    assert result.recommended_quote_ids == ("QUOTE-C",)
    assert result.ranking_trace is not None
    assert result.ranking_trace.secondary_applied is True
    assert len(result.ranking_trace.rounds) == 2


def test_cost_tolerance_does_not_invent_an_implicit_delivery_tiebreaker() -> None:
    result = compare_suppliers(
        _request(RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST, tolerance="100.00")
    )
    assert result.recommended_quote_ids == ("QUOTE-B", "QUOTE-C")
    assert result.ranking_trace is not None
    assert result.ranking_trace.cost_tolerance_applied is True
    assert result.ranking_trace.secondary_applied is False


def test_cost_tolerance_uses_the_selected_secondary_criterion() -> None:
    result = compare_suppliers(
        _request(
            RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST,
            RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
            tolerance="100.00",
        )
    )
    assert result.recommended_quote_ids == ("QUOTE-C",)


def test_bare_net_days_are_not_assumed_to_start_at_invoice_date() -> None:
    b = _with_fields(
        _supplier_b(shipping_status="KNOWN_AMOUNT", shipping_amount="200.00"),
        payment_terms="Net 45",
    )
    result = compare_suppliers(
        _request(RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM, b=b)
    )
    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.comparison_reasons[0].code == "RANKING_CRITERION_NOT_COMPARABLE"
    payment = next(
        item
        for item in result.supplier_results[0].criterion_evaluations
        if item.criterion == RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM
    )
    assert payment.reason_codes == ("PAYMENT_START_EVENT_MISSING",)


def test_audited_payment_information_makes_bare_net_days_comparable() -> None:
    b = _with_fields(
        _supplier_b(shipping_status="KNOWN_AMOUNT", shipping_amount="200.00"),
        payment_terms="Net 45",
    ).model_copy(update={
        "payment_start_event_override": "INVOICE_DATE",
        "payment_start_event_evidence_ref": "ISSUE:payment-1",
    })
    result = compare_suppliers(
        _request(RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM, b=b)
    )
    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.recommended_quote_ids == ("QUOTE-B",)
    assert result.supplier_results[0].payment_term.evidence_refs[-1] == "ISSUE:payment-1"


def test_history_criterion_without_binding_is_not_applicable() -> None:
    request = _request(RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE).model_copy(
        update={"history_dataset_context": None, "supplier_history_snapshots": ()}
    )
    result = compare_suppliers(request)
    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.comparison_reasons[0].code == "RANKING_CRITERION_NOT_APPLICABLE"


def test_explicit_exclusion_can_produce_distinct_empty_scope() -> None:
    request = _request(RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST).model_copy(
        update={"excluded_quote_ids": ("QUOTE-B", "QUOTE-C")}
    )
    result = compare_suppliers(request)
    assert result.disposition == ComparisonDisposition.EMPTY_SCOPE
    assert result.comparison_reasons[0].code == "ALL_CANDIDATES_EXCLUDED"
    assert result.ranking_trace is not None
    assert result.ranking_trace.original_scope_count == 2
