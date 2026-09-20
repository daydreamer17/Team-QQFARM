"""Offline source-PDF contract checks for the synthetic full-flow demo.

These tests deliberately assert only facts visible in the five uploaded quote
PDFs.  They do not read the evaluation reference answers and they do not
pretend to measure model extraction accuracy.  The production PDF parser is
used so the checks also protect the runtime-visible evidence text and context
groups.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from supplier_comparison.extraction.contracts import (
    DocumentContext,
    EvidenceContextPurpose,
    ParsedInput,
)
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.quote_field_rules import (
    UnitPriceVersionStatus,
    extract_document_unit_price_observations,
    select_document_unit_price,
)


ROOT = Path(__file__).resolve().parents[2]
QUOTE_ROOT = ROOT / "data/generated/inputs/development/full_flow_demo2/quotes"


VISIBLE_FACTS_BY_SUPPLIER = {
    "B": (
        "Supplier ID: V9-SUP-B",
        "new QQ Demo Components QW-MCU9-DEMO devices in QFN-32, revision R1",
        "trays of 100 pieces",
        "minimum order is 500 pieces",
        "SGD 7.10 per piece",
        "supply 1,300 pieces",
        "4 calendar days after the order date",
        "Freight is a confirmed SGD 40.00",
        "no other mandatory fees apply",
        "Payment terms are Net 30",
        "valid through 10 October 2026",
    ),
    "C": (
        "Supplier ID V9-SUP-C",
        "QQ Demo Components / QFN-32 / R1 / NEW",
        "SGD 6.10 per piece",
        "500 pcs per tray",
        "Minimum order is 1,000 pieces",
        "fulfilled as 1,500 pieces",
        "confirmed goods subtotal is SGD 9,150.00",
        "10 calendar days after order date",
        "TO BE CONFIRMED by buyer before award",
        "Do not treat an omitted charge as zero",
        "final landed total cannot be confirmed until the buyer records the freight amount",
    ),
    "D": (
        "Synthetic Delta Global Components Inc. (V9-SUP-D)",
        "Currency: USD",
        "QQ Demo Components QW-MCU9-DEMO QFN-32 / Rev R1 / New",
        "1,300 pcs",
        "USD 4.39",
        "100 pieces per tray; order multiple 100 pieces; MOQ 500 pieces",
        "USD 15.00",
        "USD 5,722.00",
        "5 calendar days after order date, arrival basis",
        "Net 45",
        "does not provide an SGD conversion",
        "separately approved exchange-rate snapshot",
    ),
    "E": (
        "Supplier ID: V9-SUP-E",
        "Revision 2 - CURRENT",
        "supersedes the indicative Revision 1 price",
        "QW-MCU9-DEMO QFN-32 / R1 / NEW",
        "1,250 pcs",
        "SGD 7.32",
        "MOQ 500 pieces; order multiple 50 pieces; individual-piece price basis",
        "SGD 50.00",
        "SGD 9,200.00",
        "7 calendar days after order date; arrival 27 September 2026",
        "Revision 1 SUPERSEDED SGD 7.40",
        "Indicative price; not valid for award",
    ),
    "G": (
        "Supplier ID V9-SUP-G",
        "STOCK RESERVED FOR 1,250 PIECES",
        "QFN-32 / Revision R1 / NEW",
        "SGD 7.40",
        "Individual-piece basis; order multiple 50 pieces; MOQ 500 pieces",
        "FREE - included at SGD 0.00",
        "SGD 9,250.00",
        "Arrival in 3 calendar days after order date (23 September 2026)",
        "Net 60",
        "valid until 10 October 2026",
        "does not instruct the buyer which ranking preference to use",
    ),
}


@pytest.fixture(scope="module")
def parsed_quotes() -> dict[str, ParsedInput]:
    parser = PdfQuoteParser()
    parsed: dict[str, ParsedInput] = {}
    for alias in VISIBLE_FACTS_BY_SUPPLIER:
        parsed[alias] = parser.parse(
            QUOTE_ROOT / f"supplier_{alias.lower()}_quote.pdf",
            DocumentContext(
                task_id="task-full-flow-demo2-pdf-contract",
                task_revision=1,
                scenario_id="MCU-V9-PERSONALIZED",
                quote_id=f"quote-v9-{alias.lower()}",
                quote_version=1,
                document_id=f"doc-v9-{alias.lower()}",
                document_version=1,
                supplier_id=f"V9-SUP-{alias}",
            ),
        )
    return parsed


@pytest.mark.parametrize("alias", tuple(VISIBLE_FACTS_BY_SUPPLIER))
def test_raw_quote_pdf_preserves_visible_business_facts(
    alias: str,
    parsed_quotes: dict[str, ParsedInput],
) -> None:
    """Lock facts visible to runtime without consulting hidden references."""

    visible_text = " ".join(
        source.raw_text for source in parsed_quotes[alias].sources
    )
    for expected_fact in VISIBLE_FACTS_BY_SUPPLIER[alias]:
        assert expected_fact in visible_text


def test_supplier_c_freight_is_explicitly_unknown_and_has_no_amount(
    parsed_quotes: dict[str, ParsedInput],
) -> None:
    """The freight field itself says unknown; the goods subtotal is not freight."""

    parsed = parsed_quotes["C"]
    source_by_id = {source.source_id: source for source in parsed.sources}
    freight_groups = [
        tuple(source_by_id[source_id].raw_text for source_id in group.source_ids)
        for group in parsed.context_groups
        if group.purpose == EvidenceContextPurpose.FIELD_AND_VALUE
        and source_by_id[group.source_ids[0]].raw_text == "Freight"
    ]

    assert freight_groups == [("Freight", "TO BE CONFIRMED by buyer before award")]
    freight_value = freight_groups[0][1]
    assert re.search(
        r"(?:S\$|SGD|USD|EUR|GBP|MYR)\s*[0-9]",
        freight_value,
        flags=re.IGNORECASE,
    ) is None

    visible_text = " ".join(source.raw_text for source in parsed.sources)
    assert (
        "Freight is intentionally not included in the SGD 9,150.00 goods subtotal"
        in visible_text
    )
    assert "The final landed total cannot be confirmed" in visible_text


def test_pdf_contract_keeps_intentional_human_confirmation_gaps(
    parsed_quotes: dict[str, ParsedInput],
) -> None:
    """Missing quote facts stay missing instead of leaking from other inputs."""

    text = {
        alias: " ".join(source.raw_text for source in parsed.sources)
        for alias, parsed in parsed_quotes.items()
    }

    # None of the five quotes states a tax treatment.  The buyer's requirement
    # must not be copied into the supplier quote as if it were document fact.
    assert all(
        re.search(r"\b(?:tax|GST|VAT)\b", value, flags=re.IGNORECASE) is None
        for value in text.values()
    )

    # B and C explicitly resolve other fees; D, E and G do not, so those three
    # require a human-sourced status rather than an inferred zero.
    assert "no other mandatory fees apply" in text["B"]
    assert "Other mandatory fees None" in text["C"]
    assert all(
        re.search(
            r"\b(?:other|additional|mandatory)\s+(?:fee|fees|charge|charges)\b",
            text[alias],
            flags=re.IGNORECASE,
        )
        is None
        for alias in ("D", "E", "G")
    )

    # E and G state the part and specification but not the manufacturer.  It
    # must be supplied by a person, not copied from the procurement request.
    assert "QQ Demo Components" in text["B"]
    assert "QQ Demo Components" in text["C"]
    assert "QQ Demo Components" in text["D"]
    assert "QQ Demo Components" not in text["E"]
    assert "QQ Demo Components" not in text["G"]


def test_supplier_e_current_price_is_selected_and_old_price_is_audit_only(
    parsed_quotes: dict[str, ParsedInput],
) -> None:
    """Exercise the version rule against the real E PDF, not a hand-built fixture."""

    parsed = parsed_quotes["E"]
    observations = extract_document_unit_price_observations(parsed)
    selection = select_document_unit_price(parsed)

    assert {(item.amount, item.version_status) for item in observations} >= {
        (Decimal("7.32"), UnitPriceVersionStatus.CURRENT),
        (Decimal("7.40"), UnitPriceVersionStatus.SUPERSEDED),
    }
    assert selection.has_conflict is False
    assert selection.selected_value == Decimal("7.32")
    assert {item.amount for item in selection.audit_observations} == {
        Decimal("7.40")
    }
