from __future__ import annotations

from decimal import Decimal
import pytest

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.quote_field_rules import (
    FeeStatus,
    UnitPriceObservation,
    UnitPriceVersionStatus,
    select_current_unit_price,
    select_document_unit_price,
    validate_fee_status_amount,
)


@pytest.mark.parametrize(
    ("status", "amount", "expected_valid", "expected_code"),
    [
        ("KNOWN_AMOUNT", "0", True, None),
        ("KNOWN_AMOUNT", "12.34", True, None),
        ("KNOWN_AMOUNT", None, False, "FEE_AMOUNT_REQUIRED"),
        ("KNOWN_AMOUNT", "-0.01", False, "FEE_AMOUNT_INVALID"),
        ("KNOWN_AMOUNT", "NaN", False, "FEE_AMOUNT_INVALID"),
        ("KNOWN_AMOUNT", 12, False, "FEE_AMOUNT_INVALID"),
        ("FREE", None, True, None),
        ("FREE", "0", True, None),
        ("FREE", "0.00", True, None),
        ("FREE", "1.00", False, "FEE_AMOUNT_MUST_BE_ZERO_OR_NULL"),
        ("FREE", "-1.00", False, "FEE_AMOUNT_INVALID"),
        ("NOT_APPLICABLE", None, True, None),
        ("NOT_APPLICABLE", "0.00", True, None),
        (
            "NOT_APPLICABLE",
            "0.01",
            False,
            "FEE_AMOUNT_MUST_BE_ZERO_OR_NULL",
        ),
        ("INCLUDED", None, True, None),
        ("INCLUDED", "0.00", False, "FEE_AMOUNT_MUST_BE_NULL"),
        ("INCLUDED", "3.00", False, "FEE_AMOUNT_MUST_BE_NULL"),
        ("UNKNOWN", None, True, None),
        ("UNKNOWN", "0.00", False, "FEE_AMOUNT_MUST_BE_NULL"),
        ("UNKNOWN", "3.00", False, "FEE_AMOUNT_MUST_BE_NULL"),
        ("NOT_A_STATUS", None, False, "FEE_STATUS_INVALID"),
        (None, None, False, "FEE_STATUS_INVALID"),
    ],
)
def test_fee_status_amount_truth_table(
    status: str | None,
    amount: object | None,
    expected_valid: bool,
    expected_code: str | None,
) -> None:
    result = validate_fee_status_amount(status, amount)

    assert result.valid is expected_valid
    assert result.code == expected_code
    if expected_valid and amount is not None:
        assert result.amount == Decimal(str(amount))


def test_fee_status_enum_is_accepted() -> None:
    result = validate_fee_status_amount(FeeStatus.FREE, None)

    assert result.valid is True
    assert result.status == FeeStatus.FREE


def _price(
    amount: str,
    status: UnitPriceVersionStatus,
    source_id: str,
) -> UnitPriceObservation:
    return UnitPriceObservation(
        amount=Decimal(amount),
        version_status=status,
        source_ids=(source_id,),
    )


def test_unique_current_price_wins_and_superseded_price_is_audit_only() -> None:
    selection = select_current_unit_price(
        (
            _price("7.40", UnitPriceVersionStatus.SUPERSEDED, "src-old"),
            _price("7.32", UnitPriceVersionStatus.CURRENT, "src-current"),
            _price("8.00", UnitPriceVersionStatus.UNVERSIONED, "src-other"),
        )
    )

    assert selection.has_conflict is False
    assert selection.selected_value == Decimal("7.32")
    assert selection.selected_source_ids == ("src-current",)
    assert [(item.amount, item.source_ids) for item in selection.audit_observations] == [
        (Decimal("7.40"), ("src-old",))
    ]


def test_repeated_current_evidence_for_same_value_is_not_a_conflict() -> None:
    selection = select_current_unit_price(
        (
            _price("7.32", UnitPriceVersionStatus.CURRENT, "src-current-a"),
            _price("7.320", UnitPriceVersionStatus.CURRENT, "src-current-b"),
        )
    )

    assert selection.has_conflict is False
    assert selection.selected_value == Decimal("7.32")
    assert selection.selected_source_ids == ("src-current-a", "src-current-b")


def test_distinct_current_prices_conflict() -> None:
    selection = select_current_unit_price(
        (
            _price("7.32", UnitPriceVersionStatus.CURRENT, "src-current-a"),
            _price("7.40", UnitPriceVersionStatus.CURRENT, "src-current-b"),
        )
    )

    assert selection.has_conflict is True
    assert selection.selected_value is None
    assert selection.conflict_code == "MULTIPLE_CURRENT_UNIT_PRICES"


def test_one_unversioned_price_is_selected_when_no_current_marker_exists() -> None:
    selection = select_current_unit_price(
        (_price("6.80", UnitPriceVersionStatus.UNVERSIONED, "src-price"),)
    )

    assert selection.has_conflict is False
    assert selection.selected_value == Decimal("6.80")


def test_distinct_unversioned_prices_conflict() -> None:
    selection = select_current_unit_price(
        (
            _price("6.80", UnitPriceVersionStatus.UNVERSIONED, "src-price-a"),
            _price("6.90", UnitPriceVersionStatus.UNVERSIONED, "src-price-b"),
        )
    )

    assert selection.has_conflict is True
    assert selection.conflict_code == "MULTIPLE_UNVERSIONED_UNIT_PRICES"


def test_only_superseded_prices_cannot_supply_a_current_value() -> None:
    selection = select_current_unit_price(
        (_price("7.40", UnitPriceVersionStatus.SUPERSEDED, "src-old"),)
    )

    assert selection.has_conflict is True
    assert selection.selected_value is None
    assert selection.conflict_code == "CURRENT_UNIT_PRICE_NOT_IDENTIFIED"


def test_no_price_observations_is_left_for_required_field_review() -> None:
    selection = select_current_unit_price(())

    assert selection.has_conflict is False
    assert selection.selected_value is None
    assert selection.observations == ()


def test_negative_unit_price_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        UnitPriceObservation(
            amount=Decimal("-0.01"),
            version_status=UnitPriceVersionStatus.CURRENT,
            source_ids=("src-negative",),
        )


@pytest.mark.parametrize("second,currency,expected_conflict", [
    ("680.00", "SGD", False),
    ("6.80", "SGD", True),
    ("680.00", "USD", True),
])
def test_native_pdf_price_conflicts_compare_currency_and_basis(tmp_path, second, currency, expected_conflict):
    from reportlab.pdfgen.canvas import Canvas
    path = tmp_path / "basis.pdf"
    canvas = Canvas(str(path))
    lines = ["Supplier: Synthetic Components", "Manufacturer part number: MCU-TEST-123",
             "Package: QFN-32; revision R1; condition NEW",
             "CURRENT unit price SGD 6.80 per 1 piece",
             f"CURRENT unit price {currency} {second} per 100 pieces",
             "Minimum order quantity: 100 pieces", "Freight: SGD 200.00 per order",
             "Delivery: 10 calendar days after order date", "Payment: Net 30 from invoice",
             "Quote valid until 2026-12-31"]
    for index, line in enumerate(lines):
        canvas.drawString(50, 750 - 25 * index, line)
    canvas.save()
    parsed = PdfQuoteParser().parse(path, DocumentContext(task_id="T", task_revision=1,
        quote_id="Q", quote_version=1, document_id="D", document_version=1))
    selected = select_document_unit_price(parsed)
    assert selected.has_conflict == expected_conflict
    if not expected_conflict:
        assert selected.selected_value == Decimal("6.80")
        assert selected.matches(Decimal("680"), "SGD", Decimal("100"), "piece")
        assert selected.matches(Decimal("6.8"), "SGD", Decimal("1"), "pcs")
        assert not selected.matches(Decimal("6.8"), "SGD", Decimal("100"), "piece")
        assert not selected.matches(Decimal("680"), "USD", Decimal("100"), "piece")


def test_equal_price_amounts_with_incompatible_units_are_not_equivalent():
    rows = [UnitPriceObservation(Decimal("10"), UnitPriceVersionStatus.CURRENT, (unit,),
                                 "SGD", Decimal("1"), unit) for unit in ("piece", "pack")]
    assert select_current_unit_price(rows).has_conflict


def test_shared_table_header_does_not_make_superseded_price_match_current():
    selected = select_current_unit_price([
        UnitPriceObservation(Decimal("10"), UnitPriceVersionStatus.SUPERSEDED, ("header", "old")),
        UnitPriceObservation(Decimal("12"), UnitPriceVersionStatus.CURRENT, ("header", "new")),
    ])
    assert selected.matches(Decimal("12"), None, None, None)
    assert not selected.matches(Decimal("10"), None, None, None)
