from __future__ import annotations

import pytest

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.contracts import AdapterOutputMode, CandidateProducer, ValidationStatus
from supplier_comparison.extraction.errors import EvidenceValidationError
from supplier_comparison.extraction.normalization import CONFLICT_STATUS_PLACEHOLDER_RULE
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import context_for, quote_path


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
        quote_path("b"),
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
        quote_path(alias),
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
        quote_path(alias, version=2),
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


@pytest.mark.parametrize("alias", ("a", "b", "c", "d", "e"))
def test_all_five_v3_pdfs_cross_the_fixed_adapter_boundary(quote_dictionary, alias) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path(alias, version=3),
        context_for(alias, version=3),
    )
    budget = ModelCallBudget(graph_run_id=f"GRAPH-V3-{alias.upper()}")
    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: _all_missing_payload(quote_dictionary)}),
        budget,
        f"EXTRACT-V3-{alias.upper()}",
    )

    assert len(batch.candidates) == 30
    assert batch.parsed_input.context.quote_version == 3
    assert batch.parsed_input.context.document_version == 3
    assert batch.run is not None and batch.run.output_mode == AdapterOutputMode.FIXED
    assert budget.calls_used == 0


def test_unknown_model_source_id_is_rejected(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("a"),
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
        quote_path("a"),
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
    assert raised.value.details["adapter_run"]["status"] == "FAILED"
    assert raised.value.details["adapter_run"]["failure_category"] == "EVIDENCE"
    assert "rejected_model_payload" not in raised.value.details
    assert len(raised.value.details["rejected_model_payload_sha256"]) == 64


def test_fee_status_outside_contract_is_rejected(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("c"),
        context_for("c"),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = next(source for source in parsed.sources if source.raw_text == "S$500.00")
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
    assert "normalized_value" in raised.value.details["evidence_detail_keys"]
    assert "normalized_value" not in raised.value.details


def test_other_fees_evidence_cannot_support_shipping(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("b", version=2),
        context_for("b", version=2),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = next(source for source in parsed.sources if source.raw_text == "ADDITIONAL FEES None")
    field_index = next(
        index
        for index, field in enumerate(quote_dictionary.extractable_fields)
        if field.field_name == "shipping_fee_status"
    )
    payload["candidates"][field_index] = {
        "field_name": "shipping_fee_status",
        "raw_value": "None",
        "normalized_value": "NOT_APPLICABLE",
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": source.source_id, "quoted_text": "None"}],
    }

    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed,
            quote_dictionary,
            FixedOutputAdapter({parsed.context.document_id: payload}),
            ModelCallBudget(graph_run_id="GRAPH-SHIPPING-SEMANTICS"),
            "EXTRACT-SHIPPING-SEMANTICS",
        )

    assert raised.value.code == "source_semantic_mismatch"
    assert "field_name" in raised.value.details["evidence_detail_keys"]
    assert "field_name" not in raised.value.details


def test_delivery_fee_evidence_can_support_shipping_amount(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("d", version=3),
        context_for("d", version=3),
    )
    payload = _all_missing_payload(quote_dictionary)
    semantic_source = next(
        source for source in parsed.sources if "No delivery fee is charged" in source.raw_text
    )
    amount_source = next(source for source in parsed.sources if source.raw_text == "SGD 0.00")
    field_index = next(
        index
        for index, field in enumerate(quote_dictionary.extractable_fields)
        if field.field_name == "shipping_fee_amount"
    )
    payload["candidates"][field_index] = {
        "field_name": "shipping_fee_amount",
        "raw_value": "SGD 0.00",
        "normalized_value": "0.00",
        "unit": "SGD",
        "validation_status": "EXTRACTED",
        "source_refs": [
            {
                "source_id": semantic_source.source_id,
                "quoted_text": "delivery fee",
            },
            {
                "source_id": amount_source.source_id,
                "quoted_text": "SGD 0.00",
            },
        ],
    }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id="GRAPH-DELIVERY-FEE-SEMANTICS"),
        "EXTRACT-DELIVERY-FEE-SEMANTICS",
    )

    amount = next(item for item in batch.candidates if item.field_name == "shipping_fee_amount")
    assert amount.normalized_value == "0.00"


def test_order_increment_evidence_cannot_support_price_basis(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("b", version=2),
        context_for("b", version=2),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = next(source for source in parsed.sources if source.raw_text == "ORDER INCREMENT 1 piece")
    field_index = next(
        index
        for index, field in enumerate(quote_dictionary.extractable_fields)
        if field.field_name == "price_basis_quantity"
    )
    payload["candidates"][field_index] = {
        "field_name": "price_basis_quantity",
        "raw_value": "1 piece",
        "normalized_value": "1",
        "unit": "piece",
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": source.source_id, "quoted_text": "1 piece"}],
    }

    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed,
            quote_dictionary,
            FixedOutputAdapter({parsed.context.document_id: payload}),
            ModelCallBudget(graph_run_id="GRAPH-PRICE-BASIS-SEMANTICS"),
            "EXTRACT-PRICE-BASIS-SEMANTICS",
        )

    assert raised.value.code == "source_semantic_mismatch"
    assert "field_name" in raised.value.details["evidence_detail_keys"]
    assert "field_name" not in raised.value.details


def test_field_specific_sources_pass_semantic_guards(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("a", version=2),
        context_for("a", version=2),
    )
    payload = _all_missing_payload(quote_dictionary)
    replacements = {
        "shipping_fee_status": {
            "raw_value": "Free",
            "normalized_value": "FREE",
            "unit": None,
            "source": next(source for source in parsed.sources if source.raw_text == "FREIGHT Free"),
            "quoted_text": "Free",
        },
        "price_basis_quantity": {
            "raw_value": "100 pieces per tray",
            "normalized_value": "100",
            "unit": "piece",
            "source": next(source for source in parsed.sources if "100 pieces per tray" in source.raw_text),
            "quoted_text": "100 pieces per tray",
        },
    }
    for field_name, replacement in replacements.items():
        field_index = next(
            index
            for index, field in enumerate(quote_dictionary.extractable_fields)
            if field.field_name == field_name
        )
        payload["candidates"][field_index] = {
            "field_name": field_name,
            "raw_value": replacement["raw_value"],
            "normalized_value": replacement["normalized_value"],
            "unit": replacement["unit"],
            "validation_status": "EXTRACTED",
            "source_refs": [
                {
                    "source_id": replacement["source"].source_id,
                    "quoted_text": replacement["quoted_text"],
                }
            ],
        }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id="GRAPH-VALID-SEMANTICS"),
        "EXTRACT-VALID-SEMANTICS",
    )

    assert len(batch.candidates) == 30


def test_conflict_status_label_is_normalized_before_evidence_validation(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("a", version=2),
        context_for("a", version=2),
    )
    payload = _all_missing_payload(quote_dictionary)
    source = next(source for source in parsed.sources if "confirmed purchase order date" in source.raw_text)
    field_index = next(
        index
        for index, field in enumerate(quote_dictionary.extractable_fields)
        if field.field_name == "start_event"
    )
    payload["candidates"][field_index] = {
        "field_name": "start_event",
        "raw_value": "confirmed purchase order date",
        "normalized_value": "CONFLICT",
        "unit": None,
        "validation_status": "CONFLICT",
        "source_refs": [
            {"source_id": source.source_id, "quoted_text": "confirmed purchase order date"}
        ],
    }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id="GRAPH-CONFLICT-PLACEHOLDER"),
        "EXTRACT-CONFLICT-PLACEHOLDER",
    )

    start_event = next(
        candidate for candidate in batch.candidates if candidate.field_name == "start_event"
    )
    assert start_event.validation_status == ValidationStatus.CONFLICT
    assert start_event.normalized_value is None
    assert len(batch.normalization_events) == 1
    assert batch.normalization_events[0].rule_id == CONFLICT_STATUS_PLACEHOLDER_RULE
