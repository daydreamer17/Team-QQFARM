from datetime import date, datetime, timezone

import pytest

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.rules.contracts import ProcurementRequirement, QuoteInput
from supplier_comparison.rules.delivery import check_delivery


EVALUATED_AT = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)


def _requirement(
    *,
    planned_order_date: date | None = date(2026, 9, 14),
    deadline: date = date(2026, 9, 19),
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
        includes_shipping=True,
        tax_mode="NOT_APPLICABLE",
        other_fees_required=True,
        planned_order_date=planned_order_date,
        delivery_deadline=deadline,
        delivery_location="SG-DEMO-01",
        ranking_preference="LOWEST_CONFIRMED_TOTAL_COST",
    )


def _candidate(
    field_name: str,
    value: str | int | None,
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
    lead_days: int = 3,
    day_basis: str | None = "CALENDAR_DAYS",
    semantics: str = "ARRIVAL",
    start_event: str | None = "ORDER_DATE",
    valid_until: str | None = "2026-09-20",
    day_basis_status: ValidationStatus = ValidationStatus.VERIFIED,
    start_event_status: ValidationStatus = ValidationStatus.VERIFIED,
    valid_until_status: ValidationStatus = ValidationStatus.VERIFIED,
) -> QuoteInput:
    return QuoteInput(
        quote_id="QUOTE-A",
        quote_version=1,
        candidates=(
            _candidate("lead_time_days", lead_days, unit="calendar_day"),
            _candidate("day_basis", day_basis, status=day_basis_status),
            _candidate("delivery_semantics", semantics),
            _candidate("start_event", start_event, status=start_event_status),
            _candidate(
                "valid_until",
                valid_until,
                status=valid_until_status,
            ),
        ),
    )


def test_seven_day_arrival_exceeds_demo_deadline() -> None:
    result = check_delivery(
        _requirement(),
        _quote(lead_days=7),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date == date(2026, 9, 21)
    assert [issue.code for issue in result.failed_reasons] == [
        "DELIVERY_DEADLINE_EXCEEDED"
    ]


def test_three_day_arrival_meets_demo_deadline() -> None:
    result = check_delivery(
        _requirement(),
        _quote(lead_days=3),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date == date(2026, 9, 17)
    assert result.is_confirmed_on_time


def test_model_string_lead_time_is_supported() -> None:
    quote = _quote(lead_days=3)
    candidates = tuple(
        candidate.model_copy(update={"normalized_value": "3"})
        if candidate.field_name == "lead_time_days"
        else candidate
        for candidate in quote.candidates
    )

    result = check_delivery(
        _requirement(),
        quote.model_copy(update={"candidates": candidates}),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date == date(2026, 9, 17)
    assert result.pending_reasons == ()


def test_arrival_on_deadline_is_allowed() -> None:
    result = check_delivery(
        _requirement(),
        _quote(lead_days=5),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date == date(2026, 9, 19)
    assert result.failed_reasons == ()


def test_shipment_timing_cannot_be_used_as_arrival() -> None:
    result = check_delivery(
        _requirement(),
        _quote(semantics="SHIPMENT"),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date is None
    assert [issue.code for issue in result.pending_reasons] == [
        "SHIPMENT_IS_NOT_ARRIVAL"
    ]


def test_business_days_require_confirmation() -> None:
    result = check_delivery(
        _requirement(),
        _quote(day_basis="BUSINESS_DAYS"),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date is None
    assert [issue.code for issue in result.pending_reasons] == [
        "DAY_BASIS_UNSUPPORTED"
    ]


def test_conflicting_start_event_blocks_arrival_calculation() -> None:
    result = check_delivery(
        _requirement(),
        _quote(
            start_event="PO receipt",
            start_event_status=ValidationStatus.CONFLICT,
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date is None
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_CONFLICT"]
    assert result.pending_reasons[0].fields == ("start_event",)


def test_missing_day_basis_does_not_silently_assume_calendar_days() -> None:
    result = check_delivery(
        _requirement(),
        _quote(
            day_basis=None,
            day_basis_status=ValidationStatus.MISSING,
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date is None
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_MISSING"]


def test_expired_quote_is_a_hard_failure() -> None:
    result = check_delivery(
        _requirement(),
        _quote(valid_until="2026-09-20"),
        evaluated_at=datetime(2026, 9, 21, 1, 0, tzinfo=timezone.utc),
    )

    assert [issue.code for issue in result.failed_reasons] == ["QUOTE_EXPIRED"]


def test_quote_must_remain_valid_until_planned_order_date() -> None:
    result = check_delivery(
        _requirement(),
        _quote(valid_until="2026-09-13"),
        evaluated_at=datetime(2026, 9, 13, 1, 0, tzinfo=timezone.utc),
    )

    assert [issue.code for issue in result.failed_reasons] == [
        "QUOTE_EXPIRES_BEFORE_ORDER"
    ]


def test_missing_valid_until_requires_confirmation() -> None:
    result = check_delivery(
        _requirement(),
        _quote(
            valid_until=None,
            valid_until_status=ValidationStatus.MISSING,
        ),
        evaluated_at=EVALUATED_AT,
    )

    assert result.estimated_arrival_date == date(2026, 9, 17)
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_MISSING"]
    assert result.pending_reasons[0].fields == ("valid_until",)


def test_evaluation_time_must_include_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_delivery(
            _requirement(),
            _quote(),
            evaluated_at=datetime(2026, 9, 14, 9, 0),
        )
