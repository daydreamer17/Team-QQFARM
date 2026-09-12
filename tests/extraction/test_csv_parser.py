from __future__ import annotations

import pytest

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.contracts import AdapterOutputMode, SourceKind, ValidationStatus
from supplier_comparison.extraction.csv_parser import (
    V2_CSV_PROFILES,
    V3_CSV_PROFILES,
    FixedCsvQuoteParser,
    ProfiledCsvQuoteParser,
)
from supplier_comparison.extraction.errors import ContractError
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import context_for, quote_path, quotes_csv_path


QUOTES_CSV = quotes_csv_path()


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
        assert candidate.origin is None
        assert candidate.source_refs == ()


def test_csv_identity_is_checked_against_authority_context(quote_dictionary) -> None:
    parser = FixedCsvQuoteParser(quote_dictionary)
    with pytest.raises(ContractError) as raised:
        parser.parse_row(QUOTES_CSV, context_for("a"), 3)
    assert raised.value.code == "csv_authority_mismatch"


def test_supplier_variant_is_not_silently_accepted_as_fixed_template(quote_dictionary) -> None:
    variant = quote_path("a", extension="csv")
    with pytest.raises(ContractError) as raised:
        FixedCsvQuoteParser(quote_dictionary).parse_row(variant, context_for("a"), 2)
    assert raised.value.code == "csv_header_mismatch"


@pytest.mark.parametrize("alias", ("a", "b", "c"))
def test_v2_profiled_csv_rows_produce_stable_cell_sources(alias: str) -> None:
    profile_id = f"v2_supplier_{alias}"
    path = quote_path(alias, version=2, extension="csv")
    parser = ProfiledCsvQuoteParser()

    first = parser.parse_row(path, context_for(alias, version=2), 2, profile_id=profile_id)
    second = parser.parse_row(path, context_for(alias, version=2), 2, profile_id=profile_id)

    assert first.document_sha256 == second.document_sha256
    assert [source.source_id for source in first.sources] == [source.source_id for source in second.sources]
    assert first.parser_version == V2_CSV_PROFILES[profile_id].parser_version
    assert first.sources
    assert all(source.kind == SourceKind.CSV_CELL for source in first.sources)
    assert all(source.row_number == 2 and source.column_name for source in first.sources)


def test_v2_profile_must_match_the_exact_registered_header() -> None:
    with pytest.raises(ContractError) as raised:
        ProfiledCsvQuoteParser().parse_row(
            quote_path("a", version=2, extension="csv"),
            context_for("a", version=2),
            2,
            profile_id="v2_supplier_b",
        )

    assert raised.value.code == "csv_profile_header_mismatch"
    assert raised.value.details["profile_id"] == "v2_supplier_b"


def test_unknown_v2_profile_is_explicitly_rejected() -> None:
    with pytest.raises(ContractError) as raised:
        ProfiledCsvQuoteParser().parse_row(
            quote_path("a", version=2, extension="csv"),
            context_for("a", version=2),
            2,
            profile_id="auto_detect",
        )

    assert raised.value.code == "csv_profile_unknown"


def test_v2_profiled_csv_crosses_the_model_adapter_boundary(quote_dictionary) -> None:
    parsed = ProfiledCsvQuoteParser().parse_row(
        quote_path("b", version=2, extension="csv"),
        context_for("b", version=2),
        2,
        profile_id="v2_supplier_b",
    )
    payload = {
        "candidates": [
            {
                "field_name": definition.field_name,
                "raw_value": None,
                "normalized_value": None,
                "unit": None,
                "validation_status": "MISSING",
                "source_refs": [],
            }
            for definition in quote_dictionary.extractable_fields
        ]
    }
    budget = ModelCallBudget(graph_run_id="GRAPH-V2-CSV-B")

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        budget,
        "EXTRACT-V2-CSV-B",
    )

    assert len(batch.candidates) == 30
    assert batch.run is not None and batch.run.output_mode == AdapterOutputMode.FIXED
    assert budget.calls_used == 0


@pytest.mark.parametrize("alias", ("a", "b", "c", "d", "e"))
def test_v3_profiled_csv_rows_produce_stable_cell_sources(alias: str) -> None:
    profile_id = f"v3_supplier_{alias}"
    path = quote_path(alias, version=3, extension="csv")
    parser = ProfiledCsvQuoteParser()

    first = parser.parse_row(path, context_for(alias, version=3), 2, profile_id=profile_id)
    second = parser.parse_row(path, context_for(alias, version=3), 2, profile_id=profile_id)

    assert first.document_sha256 == second.document_sha256
    assert [source.source_id for source in first.sources] == [
        source.source_id for source in second.sources
    ]
    assert first.parser_version == V3_CSV_PROFILES[profile_id].parser_version
    assert len(first.sources) == 31
    assert all(source.kind == SourceKind.CSV_CELL for source in first.sources)


@pytest.mark.parametrize("alias", ("a", "b", "c", "d", "e"))
def test_v3_csv_crosses_the_fixed_adapter_boundary(quote_dictionary, alias: str) -> None:
    parsed = ProfiledCsvQuoteParser().parse_row(
        quote_path(alias, version=3, extension="csv"),
        context_for(alias, version=3),
        2,
        profile_id=f"v3_supplier_{alias}",
    )
    payload = {
        "candidates": [
            {
                "field_name": definition.field_name,
                "raw_value": None,
                "normalized_value": None,
                "unit": None,
                "validation_status": "MISSING",
                "source_refs": [],
            }
            for definition in quote_dictionary.extractable_fields
        ]
    }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id=f"GRAPH-V3-CSV-{alias.upper()}"),
        f"EXTRACT-V3-CSV-{alias.upper()}",
    )

    assert len(batch.candidates) == 30
    assert batch.run is not None and batch.run.output_mode == AdapterOutputMode.FIXED
