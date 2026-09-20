from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.quote_field_rules import (
    FeeStatus,
    UnitPriceObservation,
    UnitPriceVersionStatus,
    extract_document_unit_price_observations,
    select_current_unit_price,
    select_document_unit_price,
    validate_fee_status_amount,
)


ROOT = Path(__file__).resolve().parents[2]
FULL_FLOW_DEMO2_QUOTE_E = (
    ROOT
    / "data/generated/inputs/development/full_flow_demo2/quotes/supplier_e_quote.pdf"
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


def test_full_flow_demo2_supplier_e_selects_current_price_and_retains_history() -> None:
    parsed = PdfQuoteParser().parse(
        FULL_FLOW_DEMO2_QUOTE_E,
        DocumentContext(
            task_id="task-full-flow-demo2",
            task_revision=1,
            scenario_id="MCU-V9-PERSONALIZED",
            quote_id="quote-v9-e",
            quote_version=1,
            document_id="doc-v9-e",
            document_version=1,
            supplier_id="V9-SUP-E",
        ),
    )

    observations = extract_document_unit_price_observations(parsed)
    selection = select_document_unit_price(parsed)

    assert any(
        item.amount == Decimal("7.32")
        and item.version_status == UnitPriceVersionStatus.CURRENT
        for item in observations
    )
    assert any(
        item.amount == Decimal("7.40")
        and item.version_status == UnitPriceVersionStatus.SUPERSEDED
        for item in observations
    )
    assert selection.has_conflict is False
    assert selection.selected_value == Decimal("7.32")
    assert {item.amount for item in selection.audit_observations} == {Decimal("7.40")}
    source_by_id = {source.source_id: source for source in parsed.sources}
    selected_text = " ".join(
        source_by_id[source_id].raw_text for source_id in selection.selected_source_ids
    )
    assert "7.32" in selected_text
    assert "7.40" not in selected_text


def test_negative_unit_price_observation_is_rejected() -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        UnitPriceObservation(
            amount=Decimal("-0.01"),
            version_status=UnitPriceVersionStatus.CURRENT,
            source_ids=("src-negative",),
        )
