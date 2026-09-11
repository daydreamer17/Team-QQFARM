from datetime import date

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.rules.contracts import ProcurementRequirement, QuoteInput
from supplier_comparison.rules.quantity import calculate_quantity


def _requirement(required_quantity: int = 1000) -> ProcurementRequirement:
    return ProcurementRequirement(
        manufacturer="QQ Demo Components",
        manufacturer_part_number="QW-MCU9-DEMO",
        package="QFN-32",
        revision="R1",
        condition="NEW",
        allow_substitutes=False,
        base_unit="piece",
        required_quantity=required_quantity,
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


def _quote(*candidates: QuoteFieldCandidate) -> QuoteInput:
    return QuoteInput(
        quote_id="QUOTE-A",
        quote_version=1,
        candidates=candidates,
    )


def test_required_quantity_is_rounded_up_to_order_multiple() -> None:
    result = calculate_quantity(
        _requirement(required_quantity=1050),
        _quote(
            _candidate("moq_quantity", 1000, unit="piece"),
            _candidate("moq_unit", "piece"),
            _candidate("order_multiple_units", 100, unit="piece"),
        ),
    )

    assert result.pending_reasons == ()
    assert result.breakdown is not None
    assert result.breakdown.actual_purchase_quantity == 1100


def test_normalized_integer_strings_from_model_are_supported() -> None:
    result = calculate_quantity(
        _requirement(required_quantity=1050),
        _quote(
            _candidate("moq_quantity", "1000", unit="piece"),
            _candidate("moq_unit", "piece"),
            _candidate("order_multiple_units", "100", unit="piece"),
        ),
    )

    assert result.breakdown is not None
    assert result.breakdown.actual_purchase_quantity == 1100


def test_tray_moq_is_converted_to_base_units() -> None:
    result = calculate_quantity(
        _requirement(),
        _quote(
            _candidate("moq_quantity", 20, unit="tray"),
            _candidate("moq_unit", "tray"),
            _candidate("packaging_type", "tray"),
            _candidate("units_per_pack", 100, unit="piece"),
            _candidate("order_multiple_units", 100, unit="piece"),
        ),
    )

    assert result.breakdown is not None
    assert result.breakdown.moq_quantity == 2000
    assert result.breakdown.actual_purchase_quantity == 2000


def test_piece_moq_and_single_piece_ordering_need_no_pack_conversion() -> None:
    result = calculate_quantity(
        _requirement(),
        _quote(
            _candidate("moq_quantity", 1000, unit="piece"),
            _candidate("moq_unit", "piece"),
            _candidate("order_multiple_units", 1, unit="piece"),
        ),
    )

    assert result.breakdown is not None
    assert result.breakdown.actual_purchase_quantity == 1000


def test_missing_order_multiple_returns_pending_issue() -> None:
    result = calculate_quantity(
        _requirement(),
        _quote(
            _candidate("moq_quantity", 1000, unit="piece"),
            _candidate("moq_unit", "piece"),
            _candidate(
                "order_multiple_units",
                None,
                status=ValidationStatus.MISSING,
            ),
        ),
    )

    assert result.breakdown is None
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_MISSING"]
    assert result.pending_reasons[0].fields == ("order_multiple_units",)


def test_conflicting_pack_size_blocks_tray_conversion() -> None:
    result = calculate_quantity(
        _requirement(),
        _quote(
            _candidate("moq_quantity", 10, unit="tray"),
            _candidate("moq_unit", "tray"),
            _candidate("packaging_type", "tray"),
            _candidate(
                "units_per_pack",
                100,
                unit="piece",
                status=ValidationStatus.CONFLICT,
            ),
            _candidate("order_multiple_units", 100, unit="piece"),
        ),
    )

    assert result.breakdown is None
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_CONFLICT"]
    assert result.pending_reasons[0].fields == ("units_per_pack",)


def test_moq_unit_must_match_packaging_type() -> None:
    result = calculate_quantity(
        _requirement(),
        _quote(
            _candidate("moq_quantity", 10, unit="tray"),
            _candidate("moq_unit", "tray"),
            _candidate("packaging_type", "reel"),
            _candidate("units_per_pack", 100, unit="piece"),
            _candidate("order_multiple_units", 100, unit="piece"),
        ),
    )

    assert result.breakdown is None
    assert [issue.code for issue in result.pending_reasons] == [
        "MOQ_PACKAGING_UNIT_MISMATCH"
    ]
