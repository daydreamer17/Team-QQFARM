from __future__ import annotations

import pytest

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.contracts import AdapterOutputMode, CandidateProducer, ValidationStatus
from supplier_comparison.extraction.errors import EvidenceValidationError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import DATA_ROOT, context_for


def _all_missing_payload(quote_dictionary):
    return {
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


def test_fixed_adapter_is_explicit_and_does_not_consume_real_call_budget(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT / "generated" / "inputs" / "development" / "supplier_b_quote_v1.pdf",
        context_for("b"),
    )
    payload = _all_missing_payload(quote_dictionary)
    first_source = parsed.sources[0]
    payload["candidates"][0] = {
        "field_name": quote_dictionary.extractable_fields[0].field_name,
        "raw_value": first_source.raw_text,
        "normalized_value": first_source.raw_text,
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": first_source.source_id, "quoted_text": first_source.raw_text}],
    }
    budget = ModelCallBudget(graph_run_id="GRAPH-1", calls_used=2)
    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        budget,
        "EXTRACT-1",
    )
    assert batch.run is not None
    assert batch.run.output_mode == AdapterOutputMode.FIXED
    assert batch.run.calls_before == batch.run.calls_after == 2
    assert budget.calls_used == 2
    assert all(candidate.producer == CandidateProducer.MODEL_ADAPTER for candidate in batch.candidates)


@pytest.mark.parametrize("alias", ("a", "b", "c"))
def test_all_three_pdfs_cross_the_fixed_adapter_boundary(quote_dictionary, alias) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT / "generated" / "inputs" / "development" / f"supplier_{alias}_quote_v1.pdf",
        context_for(alias),
    )
    budget = ModelCallBudget(graph_run_id=f"GRAPH-{alias.upper()}")
    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: _all_missing_payload(quote_dictionary)}),
        budget,
        f"EXTRACT-{alias.upper()}",
    )
    assert len(batch.candidates) == 30
    assert {candidate.field_name for candidate in batch.candidates} == {
        definition.field_name for definition in quote_dictionary.extractable_fields
    }
    assert batch.run is not None and batch.run.output_mode == AdapterOutputMode.FIXED
    assert budget.calls_used == 0
    if alias == "b":
        shipping = {
            candidate.field_name: candidate
            for candidate in batch.candidates
            if candidate.field_name in {"shipping_fee_status", "shipping_fee_amount"}
        }
        assert all(candidate.validation_status == ValidationStatus.MISSING for candidate in shipping.values())
        assert all(candidate.normalized_value is None for candidate in shipping.values())
        assert all(candidate.source_refs == () for candidate in shipping.values())


@pytest.mark.parametrize("alias", ("a", "b", "c"))
def test_all_three_v2_pdfs_cross_the_fixed_adapter_boundary(quote_dictionary, alias) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT
        / "generated"
        / "inputs"
        / "development"
        / "quote_V2"
        / f"supplier_{alias}_quote_v2.pdf",
        context_for(alias, version=2),
    )
    budget = ModelCallBudget(graph_run_id=f"GRAPH-V2-{alias.upper()}")
    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: _all_missing_payload(quote_dictionary)}),
        budget,
        f"EXTRACT-V2-{alias.upper()}",
    )

    assert len(batch.candidates) == 30
    assert batch.parsed_input.context.quote_version == 2
    assert batch.parsed_input.context.document_version == 2
    assert batch.run is not None and batch.run.output_mode == AdapterOutputMode.FIXED
    assert budget.calls_used == 0


def test_unknown_model_source_id_is_rejected(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT / "generated" / "inputs" / "development" / "supplier_a_quote_v1.pdf",
        context_for("a"),
    )
    payload = _all_missing_payload(quote_dictionary)
    payload["candidates"][0] = {
        "field_name": quote_dictionary.extractable_fields[0].field_name,
        "raw_value": "QUOTATION",
        "normalized_value": "QUOTATION",
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": "src_from_another_file", "quoted_text": "QUOTATION"}],
    }
    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed,
            quote_dictionary,
            FixedOutputAdapter({parsed.context.document_id: payload}),
            ModelCallBudget(graph_run_id="GRAPH-1"),
            "EXTRACT-1",
        )
    assert raised.value.code == "source_ref_unknown"
    assert raised.value.details["adapter_run"]["output_mode"] == "FIXED"
    assert raised.value.details["adapter_run"]["calls_before"] == 0


def test_altered_display_quote_is_rejected(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT / "generated" / "inputs" / "development" / "supplier_a_quote_v1.pdf",
        context_for("a"),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = parsed.sources[0]
    payload["candidates"][0] = {
        "field_name": quote_dictionary.extractable_fields[0].field_name,
        "raw_value": source.raw_text,
        "normalized_value": source.raw_text,
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": source.source_id, "quoted_text": "fabricated snippet"}],
    }
    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed,
            quote_dictionary,
            FixedOutputAdapter({parsed.context.document_id: payload}),
            ModelCallBudget(graph_run_id="GRAPH-1"),
            "EXTRACT-1",
        )
    assert raised.value.code == "source_quote_mismatch"
    assert raised.value.details["adapter_run"]["output_mode"] == "FIXED"


def test_fee_status_outside_contract_is_rejected(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        DATA_ROOT / "generated" / "inputs" / "development" / "supplier_c_quote_v1.pdf",
        context_for("c"),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = next(source for source in parsed.sources if source.raw_text == "Shipping fee S$500.00")
    field_index = next(
        index
        for index, field in enumerate(quote_dictionary.extractable_fields)
        if field.field_name == "shipping_fee_status"
    )
    payload["candidates"][field_index] = {
        "field_name": "shipping_fee_status",
        "raw_value": source.raw_text,
        "normalized_value": "PAID",
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": source.source_id, "quoted_text": source.raw_text}],
    }

    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed,
            quote_dictionary,
            FixedOutputAdapter({parsed.context.document_id: payload}),
            ModelCallBudget(graph_run_id="GRAPH-INVALID-ENUM"),
            "EXTRACT-INVALID-ENUM",
        )
    assert raised.value.code == "candidate_enum_invalid"
    assert raised.value.details["normalized_value"] == "PAID"
