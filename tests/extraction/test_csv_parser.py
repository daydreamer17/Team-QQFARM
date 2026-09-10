from __future__ import annotations

import pytest

from supplier_comparison.extraction.contracts import ValidationStatus
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.errors import ContractError

from .conftest import DATA_ROOT, context_for


QUOTES_CSV = DATA_ROOT / "generated" / "inputs" / "development" / "quotes.csv"


def _candidate(batch, field_name: str):
    return next(candidate for candidate in batch.candidates if candidate.field_name == field_name)


def test_fixed_csv_rows_enter_the_same_candidate_contract(quote_dictionary) -> None:
    parser = FixedCsvQuoteParser(quote_dictionary)
    for row_number, alias in enumerate(("a", "b", "c"), start=2):
        batch = parser.parse_row(QUOTES_CSV, context_for(alias), row_number)
        assert batch.dictionary_version == "1.2.0"
        assert len(batch.candidates) == 30
        assert all(candidate.field_name != "supplier_id" for candidate in batch.candidates)
        assert all(candidate.field_name != "fees_complete" for candidate in batch.candidates)


def test_supplier_b_unknown_shipping_is_null_and_has_no_source(quote_dictionary) -> None:
    batch = FixedCsvQuoteParser(quote_dictionary).parse_row(QUOTES_CSV, context_for("b"), 3)
    for field_name in ("shipping_fee_status", "shipping_fee_amount"):
        candidate = _candidate(batch, field_name)
        assert candidate.validation_status == ValidationStatus.MISSING
        assert candidate.raw_value is None
        assert candidate.normalized_value is None
        assert candidate.source_refs == ()


def test_csv_identity_is_checked_against_authority_context(quote_dictionary) -> None:
    parser = FixedCsvQuoteParser(quote_dictionary)
    with pytest.raises(ContractError) as raised:
        parser.parse_row(QUOTES_CSV, context_for("a"), 3)
    assert raised.value.code == "csv_authority_mismatch"


def test_supplier_variant_is_not_silently_accepted_as_fixed_template(quote_dictionary) -> None:
    variant = DATA_ROOT / "generated" / "inputs" / "development" / "csv_variants" / "supplier_a_quote_v1.csv"
    with pytest.raises(ContractError) as raised:
        FixedCsvQuoteParser(quote_dictionary).parse_row(variant, context_for("a"), 2)
    assert raised.value.code == "csv_header_mismatch"
