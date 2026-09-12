from __future__ import annotations

from hashlib import sha256

from supplier_comparison.extraction.criticality import (
    ALWAYS_CRITICAL_FIELDS,
    CONDITIONAL_CRITICAL_FIELDS,
    CRITICALITY_SOURCE_SHA256,
    NON_CRITICAL_FIELDS,
    POLICY_FIELDS,
    QUOTE_DICTIONARY_SOURCE_SHA256,
    CriticalityContext,
    resolve_criticalities,
    validate_policy_fields,
)
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.review_contracts import EffectiveCriticality

from .conftest import DATA_ROOT, context_for, quotes_csv_path


def _batch(quote_dictionary, alias: str, row_number: int):
    return FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for(alias), row_number
    )


def test_c_policy_partitions_the_30_extractable_fields(quote_dictionary) -> None:
    assert len(ALWAYS_CRITICAL_FIELDS) == 16
    assert len(CONDITIONAL_CRITICAL_FIELDS) == 10
    assert len(NON_CRITICAL_FIELDS) == 4
    assert len(POLICY_FIELDS) == 30
    assert ALWAYS_CRITICAL_FIELDS.isdisjoint(CONDITIONAL_CRITICAL_FIELDS)
    assert ALWAYS_CRITICAL_FIELDS.isdisjoint(NON_CRITICAL_FIELDS)
    assert CONDITIONAL_CRITICAL_FIELDS.isdisjoint(NON_CRITICAL_FIELDS)
    validate_policy_fields(
        definition.field_name for definition in quote_dictionary.extractable_fields
    )


def test_criticality_policy_sources_have_not_drifted() -> None:
    criticality_source = DATA_ROOT / "EXTRACTION_REVIEW_FIELD_CRITICALITY.md"
    quote_dictionary_source = DATA_ROOT / "contracts" / "quote_data_field.csv"

    assert sha256(criticality_source.read_bytes()).hexdigest() == CRITICALITY_SOURCE_SHA256
    assert (
        sha256(quote_dictionary_source.read_bytes()).hexdigest()
        == QUOTE_DICTIONARY_SOURCE_SHA256
    )


def test_mcu_context_resolves_packaging_and_fee_conditions(quote_dictionary) -> None:
    context = CriticalityContext(required_revision="R1", base_unit="piece")
    supplier_a = {
        item.field_name: item
        for item in resolve_criticalities(_batch(quote_dictionary, "a", 2), context)
    }
    supplier_b = {
        item.field_name: item
        for item in resolve_criticalities(_batch(quote_dictionary, "b", 3), context)
    }
    supplier_c = {
        item.field_name: item
        for item in resolve_criticalities(_batch(quote_dictionary, "c", 4), context)
    }

    assert supplier_a["packaging_type"].criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
    assert supplier_b["packaging_type"].criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
    assert supplier_b["units_per_pack"].criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
    assert supplier_c["shipping_fee_amount"].criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
    assert supplier_a["shipping_fee_amount"].criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
    assert supplier_a["revision"].criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
    assert supplier_a["lead_time_days"].criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
    assert supplier_a["quote_date"].criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
    assert supplier_a["category"].criticality == EffectiveCriticality.NON_CRITICAL


def test_revision_is_not_critical_when_requirement_does_not_specify_it(quote_dictionary) -> None:
    assessments = {
        item.field_name: item
        for item in resolve_criticalities(
            _batch(quote_dictionary, "a", 2),
            CriticalityContext(required_revision=None, base_unit="piece"),
        )
    }

    assert assessments["revision"].criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
