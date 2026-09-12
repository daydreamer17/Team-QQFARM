from datetime import date

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.rules.contracts import ProcurementRequirement, QuoteInput
from supplier_comparison.rules.specification import check_specification


def _requirement(*, allow_substitutes: bool = False) -> ProcurementRequirement:
    return ProcurementRequirement(
        manufacturer="QQ Demo Components",
        manufacturer_part_number="QW-MCU9-DEMO",
        package="QFN-32",
        revision="R1",
        condition="NEW",
        allow_substitutes=allow_substitutes,
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
    value: str | None,
    *,
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
        unit=None,
        validation_status=status,
        origin=None if missing else Origin.USER_INPUT,
        producer=CandidateProducer.DETERMINISTIC_PARSER,
    )


def _quote(
    *,
    manufacturer: str | None = "QQ Demo Components",
    part_number: str | None = "QW-MCU9-DEMO",
    package: str | None = "QFN-32",
    revision: str | None = "R1",
    condition: str | None = "NEW",
    package_status: ValidationStatus = ValidationStatus.VERIFIED,
    condition_status: ValidationStatus = ValidationStatus.VERIFIED,
) -> QuoteInput:
    return QuoteInput(
        quote_id="QUOTE-A",
        quote_version=1,
        candidates=(
            _candidate("manufacturer", manufacturer),
            _candidate("manufacturer_part_number", part_number),
            _candidate("package", package, status=package_status),
            _candidate("revision", revision),
            _candidate("condition", condition, status=condition_status),
        ),
    )


def test_exact_specification_is_confirmed_match() -> None:
    result = check_specification(_requirement(), _quote())

    assert result.is_confirmed_match
    assert result.failed_reasons == ()
    assert result.pending_reasons == ()


def test_part_number_mismatch_is_infeasible_when_substitutes_forbidden() -> None:
    result = check_specification(
        _requirement(allow_substitutes=False),
        _quote(part_number="QW-MCU9-OTHER"),
    )

    assert [issue.code for issue in result.failed_reasons] == [
        "MANUFACTURER_PART_NUMBER_MISMATCH"
    ]
    assert result.pending_reasons == ()


def test_package_mismatch_is_a_hard_failure() -> None:
    result = check_specification(
        _requirement(),
        _quote(package="QFN-48"),
    )

    assert [issue.code for issue in result.failed_reasons] == ["PACKAGE_MISMATCH"]


def test_missing_package_requires_confirmation() -> None:
    result = check_specification(
        _requirement(),
        _quote(
            package=None,
            package_status=ValidationStatus.MISSING,
        ),
    )

    assert result.failed_reasons == ()
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_MISSING"]
    assert result.pending_reasons[0].fields == ("package",)


def test_conflicting_condition_requires_confirmation() -> None:
    result = check_specification(
        _requirement(),
        _quote(
            condition="New / refurbished",
            condition_status=ValidationStatus.CONFLICT,
        ),
    )

    assert result.failed_reasons == ()
    assert [issue.code for issue in result.pending_reasons] == ["FIELD_CONFLICT"]
    assert result.pending_reasons[0].fields == ("condition",)


def test_allowed_substitute_still_requires_compatibility_review() -> None:
    result = check_specification(
        _requirement(allow_substitutes=True),
        _quote(
            manufacturer="Alternative Components",
            part_number="ALT-MCU-9",
        ),
    )

    assert result.failed_reasons == ()
    assert [issue.code for issue in result.pending_reasons] == [
        "SUBSTITUTE_COMPATIBILITY_REVIEW_REQUIRED"
    ]
    assert result.pending_reasons[0].fields == (
        "manufacturer",
        "manufacturer_part_number",
    )


def test_descriptive_item_and_category_are_not_used_for_exact_match() -> None:
    quote = _quote()
    extra_candidates = quote.candidates + (
        _candidate("item", "A different marketing description"),
        _candidate("category", "Components"),
    )

    result = check_specification(
        _requirement(),
        quote.model_copy(update={"candidates": extra_candidates}),
    )

    assert result.is_confirmed_match
