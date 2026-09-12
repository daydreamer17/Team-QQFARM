from __future__ import annotations

import json

import pytest

from supplier_comparison.extraction.adapters import (
    FixedOutputAdapter,
    ModelCallBudget,
    _build_prompt,
    _source_handle_map,
)
from supplier_comparison.extraction.contracts import (
    DocumentContext,
    EvidenceContextPurpose,
    SourceKind,
)
from supplier_comparison.extraction.errors import InputLimitError
from supplier_comparison.extraction.evidence import source_semantic_contexts
from supplier_comparison.extraction.pdf_layout import PdfLayoutConfig
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import DEVELOPMENT_ROOT, context_for, quote_path


def _v5_path(alias: str):
    return DEVELOPMENT_ROOT / "quote_V5" / f"supplier_{alias.lower()}_quote_v5.pdf"


def _v5_context(alias: str) -> DocumentContext:
    normalized = alias.upper()
    return DocumentContext(
        task_id="TASK-V5-LAYOUT-REGRESSION",
        task_revision=5,
        scenario_id="QUOTE-V5-GENERALIZATION",
        quote_id=f"QUOTE-V5-{normalized}",
        quote_version=1,
        document_id=f"DOC-V5-{normalized}-PDF",
        document_version=1,
        supplier_id=f"V5-SUP-{normalized}",
    )


def _group_texts(parsed, purpose: EvidenceContextPurpose) -> list[list[str]]:
    source_by_id = {source.source_id: source for source in parsed.sources}
    return [
        [source_by_id[source_id].raw_text for source_id in group.source_ids]
        for group in parsed.context_groups
        if group.purpose == purpose
    ]


@pytest.mark.parametrize(
    ("alias", "label", "value"),
    (
        ("a", "FREIGHT CHARGE", "SGD 120.00 per order"),
        ("e", "DELIVERY CHARGE", "SGD 250.00 per order"),
        ("f", "FREIGHT", "No charge; SGD 0.00"),
        ("h", "SHIPPING AMOUNT", "SGD 85.00 per order"),
    ),
)
def test_four_v5_failures_have_atomic_fee_cells_and_field_value_context(
    alias: str,
    label: str,
    value: str,
) -> None:
    parsed = PdfQuoteParser().parse(_v5_path(alias), _v5_context(alias))
    source_by_text = {source.raw_text: source for source in parsed.sources}

    label_source = source_by_text[label]
    value_source = source_by_text[value]
    groups = _group_texts(parsed, EvidenceContextPurpose.FIELD_AND_VALUE)

    assert label_source.kind == value_source.kind == SourceKind.PDF_TABLE_CELL
    assert label_source.table_id == value_source.table_id
    assert label_source.row_index == value_source.row_index
    assert (label_source.column_index, value_source.column_index) == (0, 1)
    assert label_source.bbox is not None and value_source.bbox is not None
    assert [label, value] in groups
    assert label not in value_source.raw_text


def test_same_visual_row_fields_remain_independent_atomic_cells() -> None:
    parsed = PdfQuoteParser().parse(_v5_path("a"), _v5_context("a"))
    commodity = next(source for source in parsed.sources if source.raw_text == "COMMODITY")
    freight = next(source for source in parsed.sources if source.raw_text == "FREIGHT CHARGE")

    assert commodity.kind == freight.kind == SourceKind.PDF_TABLE_CELL
    assert commodity.bbox is not None and freight.bbox is not None
    assert abs(commodity.bbox.top - freight.bbox.top) < 0.1
    assert commodity.bbox.x1 < freight.bbox.x0
    assert all(
        source.raw_text != "COMMODITY FREIGHT CHARGE" for source in parsed.sources
    )


def test_wrapped_table_value_is_one_locatable_atomic_region() -> None:
    parsed = PdfQuoteParser().parse(_v5_path("a"), _v5_context("a"))
    delivery = next(
        source
        for source in parsed.sources
        if source.raw_text
        == "Goods arrive at the buyer site 4 calendar days after the confirmed purchase-order date"
    )

    assert delivery.kind == SourceKind.PDF_TABLE_CELL
    assert delivery.bbox is not None
    assert delivery.bbox.bottom - delivery.bbox.top > 15
    assert [
        "DELIVERY PROMISE",
        delivery.raw_text,
    ] in _group_texts(parsed, EvidenceContextPurpose.FIELD_AND_VALUE)


def test_ruled_key_value_table_links_header_without_fabricating_source_text() -> None:
    parsed = PdfQuoteParser().parse(quote_path("c"), context_for("c"))
    value_source = next(source for source in parsed.sources if source.raw_text == "S$500.00")
    semantic_context = source_semantic_contexts(parsed)[value_source.source_id]

    assert value_source.kind == SourceKind.PDF_TABLE_CELL
    assert value_source.raw_text == "S$500.00"
    assert "Shipping fee" in semantic_context
    assert ["Shipping fee", "S$500.00"] in _group_texts(
        parsed,
        EvidenceContextPurpose.FIELD_AND_VALUE,
    )


def test_repeated_headers_keep_cross_page_table_identity() -> None:
    path = DEVELOPMENT_ROOT / "quote_V6" / "dev_03_multipage_repeated.pdf"
    parsed = PdfQuoteParser().parse(path, context_for("a", version=6))
    headers = [source for source in parsed.sources if source.raw_text == "Field"]

    assert [source.page_number for source in headers] == [1, 2, 3]
    assert len({source.table_id for source in headers}) == 1
    assert all(
        any(group.page_number == page_number for group in parsed.context_groups)
        for page_number in (1, 2, 3)
    )


def test_conflicting_prices_on_different_pages_are_both_preserved() -> None:
    path = DEVELOPMENT_ROOT / "quote_V6" / "dev_04_internal_price_conflict.pdf"
    parsed = PdfQuoteParser().parse(path, context_for("a", version=6))
    price_sources = [
        source
        for source in parsed.sources
        if source.raw_text in {"SGD 6.55 per piece", "SGD 6.85 per piece"}
    ]

    assert [(source.page_number, source.raw_text) for source in price_sources] == [
        (1, "SGD 6.55 per piece"),
        (2, "SGD 6.85 per piece"),
    ]
    groups = _group_texts(parsed, EvidenceContextPurpose.FIELD_AND_VALUE)
    assert ["Unit price", "SGD 6.55 per piece"] in groups
    assert ["Unit price", "SGD 6.85 per piece"] in groups


def test_v7_unruled_two_column_rows_have_atomic_cells_and_contexts() -> None:
    path = DEVELOPMENT_ROOT / "quote_V7" / "dev_01.pdf"
    parsed = PdfQuoteParser().parse(path, context_for("a", version=7))
    groups = _group_texts(parsed, EvidenceContextPurpose.FIELD_AND_VALUE)
    source_by_text = {source.raw_text: source for source in parsed.sources}

    assert ["Quoted by", "Synthetic Meridian Supplier 01 Ltd."] in groups
    assert ["Price for stated basis", "S$ 5.67 for each individual piece"] in groups
    assert ["Shipping fee value", "Total freight charge: SGD 120.00"] in groups
    assert source_by_text["Quoted by"].kind == SourceKind.PDF_TABLE_CELL
    assert source_by_text["Synthetic Meridian Supplier 01 Ltd."].kind == SourceKind.PDF_TABLE_CELL


def test_prompt_exposes_context_members_but_context_id_is_not_citable(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(_v5_path("a"), _v5_context("a"))
    handles = _source_handle_map(parsed)
    prompt = json.loads(_build_prompt(parsed, quote_dictionary, handles))
    freight_group = next(
        group
        for group in prompt["context_groups"]
        if [member["text"] for member in group["members"]]
        == ["FREIGHT CHARGE", "SGD 120.00 per order"]
    )

    assert freight_group["purpose"] == "FIELD_AND_VALUE"
    assert all(member["source_id"] in handles for member in freight_group["members"])
    assert freight_group["context_group_id"] not in handles
    assert "reading context only" in freight_group["citation_rule"]


def test_value_cell_can_use_its_structural_header_without_weakening_evidence(
    quote_dictionary,
) -> None:
    parsed = PdfQuoteParser().parse(_v5_path("a"), _v5_context("a"))
    amount_source = next(
        source for source in parsed.sources if source.raw_text == "SGD 120.00 per order"
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
    shipping_index = next(
        index
        for index, candidate in enumerate(payload["candidates"])
        if candidate["field_name"] == "shipping_fee_status"
    )
    payload["candidates"][shipping_index] = {
        "field_name": "shipping_fee_status",
        "raw_value": amount_source.raw_text,
        "normalized_value": "KNOWN_AMOUNT",
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [
            {
                "source_id": amount_source.source_id,
                "quoted_text": amount_source.raw_text,
            }
        ],
    }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id="GRAPH-STAGE2-CONTEXT"),
        "EXTRACT-STAGE2-CONTEXT",
    )

    shipping = next(
        candidate for candidate in batch.candidates if candidate.field_name == "shipping_fee_status"
    )
    assert shipping.normalized_value == "KNOWN_AMOUNT"
    assert shipping.source_refs[0].source_id == amount_source.source_id


@pytest.mark.parametrize(
    ("config", "expected_code"),
    (
        (PdfLayoutConfig(max_sources=1), "pdf_source_limit_exceeded"),
        (
            PdfLayoutConfig(max_source_characters=1),
            "pdf_source_character_limit_exceeded",
        ),
    ),
)
def test_layout_limits_fail_explicitly_without_truncation(config, expected_code) -> None:
    with pytest.raises(InputLimitError) as raised:
        PdfQuoteParser(layout_config=config).parse(
            _v5_path("a"),
            _v5_context("a"),
        )

    assert raised.value.code == expected_code
