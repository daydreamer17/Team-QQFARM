from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest

from supplier_comparison.extraction.csv_parser import (
    FROZEN_CSV_COLUMNS,
    FixedCsvQuoteParser,
    identify_csv_contract,
)
from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError, UnreadableInputError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.quote_field_rules import (
    extract_document_unit_price_observations,
    select_document_unit_price,
)
from supplier_comparison.rag.uploads import (
    PolicyDraftClauseInput,
    PolicyFileImportMetadata,
    _draft_clauses,
)
from supplier_comparison.rules.contracts import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/generated/inputs/development/full_flow_demo3"
REFERENCE = ROOT / "evaluation/reference/full_flow_demo3/reference_answers.json"


def _read_single_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(FROZEN_CSV_COLUMNS)
        rows = list(reader)
    assert len(rows) == 1
    return rows[0]


def test_full_flow_demo3_manifest_integrity_and_runtime_separation() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "full_flow_demo3"
    assert manifest["scenario_id"] == "MCU-FULL-FLOW-EDGE-003"
    assert manifest["is_synthetic"] is True
    assert manifest["runtime_safe"] is True
    assert manifest["primary_quote_limit"] == 5
    assert len(manifest["primary_quotes"]) == 5
    assert manifest["negative_controls"]["isolated_only"] is True

    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file()
        assert path.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert "evaluation/reference" not in item["path"]

    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert reference["runtime_access"] == "FORBIDDEN"
    assert reference["dataset_id"] == manifest["dataset_id"]


def test_full_flow_demo3_requirement_and_policy_contracts() -> None:
    requirement = json.loads(
        (DATASET / "requirement/confirmed_requirement.json").read_text(encoding="utf-8")
    )
    validated = ProcurementRequirement.model_validate(requirement)
    assert validated.required_quantity == 1375
    assert str(validated.budget_amount) == "10000.00"
    assert validated.ranking_preference.value == "LOWEST_CONFIRMED_TOTAL_COST"
    assert validated.secondary_preference is not None
    assert validated.secondary_preference.value == "FASTEST_CONFIRMED_DELIVERY"

    revision = ProcurementRequirement.model_validate_json(
        (DATASET / "requirement/revisions/confirmed_requirement_rev2.json").read_text(
            encoding="utf-8"
        )
    )
    assert revision.delivery_deadline.isoformat() == "2026-10-16"
    assert revision.model_dump(exclude={"delivery_deadline"}) == validated.model_dump(
        exclude={"delivery_deadline"}
    )

    metadata = PolicyFileImportMetadata.model_validate_json(
        (DATASET / "policy/upload_metadata.json").read_text(encoding="utf-8")
    )
    reviewed_payload = json.loads(
        (DATASET / "policy/reviewed_clauses.json").read_text(encoding="utf-8")
    )
    reviewed = [PolicyDraftClauseInput.model_validate(item) for item in reviewed_payload["clauses"]]
    extracted = _draft_clauses(
        (DATASET / "policy/electronics_edge_policy.txt").read_text(encoding="utf-8"),
        fallback_title=metadata.title,
    )
    assert [(item["clause_id"], item["title"], item["text"]) for item in extracted] == [
        (item.clause_id, item.title, item.text) for item in reviewed
    ]
    assert {item.control_code for item in reviewed} == {
        "APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL"
    }


def test_full_flow_demo3_primary_csvs_preserve_edge_cases() -> None:
    expected = {
        "SUP-024": ("6.90", "100", "1000", "FREE", "5"),
        "SUP-022": ("6.25", "250", "1000", "UNKNOWN", "6"),
        "SUP-023": ("6.92", "25", "500", "KNOWN_AMOUNT", "7"),
        "SUP-029": ("4.85", "1000", "2000", "KNOWN_AMOUNT", "4"),
        "SUP-030": ("6.88", "100", "500", "INCLUDED", "3"),
    }
    rows = {}
    for path in sorted((DATASET / "quotes").glob("*.csv")):
        assert identify_csv_contract(path) is None
        row = _read_single_row(path)
        rows[row["supplier_id"]] = row

    assert set(rows) == set(expected)
    for supplier_id, values in expected.items():
        row = rows[supplier_id]
        assert (
            row["unit_price"], row["order_multiple_units"], row["moq_quantity"],
            row["shipping_fee_status"], row["lead_time_days"],
        ) == values
        assert row["manufacturer_part_number"] == "QW-MCU9-DEMO"
        assert row["tax_mode"] == "EXCLUDED"
        assert row["is_synthetic"] == "true"

    assert rows["SUP-022"]["shipping_fee_amount"] == ""
    assert rows["SUP-022"]["fees_complete"] == "false"
    assert rows["SUP-029"]["payment_terms"] == "50% deposit / 50% before shipment"


def test_full_flow_demo3_primary_csvs_pass_production_parser() -> None:
    parser = FixedCsvQuoteParser(
        QuoteDictionary.load(ROOT / "data/contracts/quote_data_field.csv")
    )
    for path in sorted((DATASET / "quotes").glob("*.csv")):
        row = _read_single_row(path)
        batch = parser.parse_row(
            path,
            DocumentContext(
                task_id="TASK-FF3-CSV",
                task_revision=1,
                scenario_id=row["scenario_id"],
                quote_id=row["quote_id"],
                quote_version=int(row["quote_version"]),
                document_id=row["document_id"],
                document_version=1,
                supplier_id=row["supplier_id"],
            ),
            row_number=2,
        )
        assert batch.parsed_input.context.supplier_id == row["supplier_id"]
        assert len(batch.candidates) >= 25


def test_full_flow_demo3_reference_math_is_exact_decimal() -> None:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    expected = reference["primary_quote_expectations_after_answer"]
    rows = {
        row["supplier_id"]: row
        for row in (
            _read_single_row(path)
            for path in sorted((DATASET / "quotes").glob("*.csv"))
        )
    }
    shipping_answer = Decimal("320.00")
    for supplier_id, outcome in expected.items():
        row = rows[supplier_id]
        quantity = max(1375, int(row["moq_quantity"]))
        multiple = int(row["order_multiple_units"])
        actual = ((quantity + multiple - 1) // multiple) * multiple
        goods = (Decimal(actual) * Decimal(row["unit_price"])).quantize(Decimal("0.01"))
        if row["shipping_fee_status"] == "KNOWN_AMOUNT":
            shipping = Decimal(row["shipping_fee_amount"])
        elif row["shipping_fee_status"] == "UNKNOWN":
            shipping = shipping_answer
        else:
            shipping = Decimal("0.00")
        other = (
            Decimal(row["other_fees_amount"])
            if row["other_fees_status"] == "KNOWN_AMOUNT"
            else Decimal("0.00")
        )
        total = (goods + shipping + other).quantize(Decimal("0.01"))
        assert actual == outcome["actual_quantity"]
        assert goods == Decimal(outcome["goods_cost"])
        assert total == Decimal(outcome["total_cost"])

    baseline = next(
        item for item in reference["comparison_checkpoints"]
        if item["id"] == "baseline_after_answer"
    )
    assert baseline["recommended_supplier_ids"] == ["SUP-023"]
    assert Decimal(baseline["recommended_total"]) == Decimal("9653.75")


def test_full_flow_demo3_pdfs_are_machine_readable_except_scan_control() -> None:
    readable = [
        DATASET / "requirement/procurement_requirement.pdf",
        DATASET / "quotes/sterling_quote.pdf",
        DATASET / "quotes/redwood_quote.pdf",
        DATASET / "quotes/great_wall_quote.pdf",
        DATASET / "quotes/sterling_semitech_quote.pdf",
        DATASET / "negative_controls/ambiguous_current_prices.pdf",
        DATASET / "negative_controls/prompt_injection_quote.pdf",
    ]
    for path in readable:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert len(text) > 250
        assert "synthetic" in text.casefold()

    with pdfplumber.open(DATASET / "quotes/redwood_quote.pdf") as pdf:
        assert len(pdf.pages) == 2
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
    assert "TO BE CONFIRMED" in text
    assert "must not be used as one" in text

    with pdfplumber.open(DATASET / "quotes/sterling_semitech_quote.pdf") as pdf:
        text = " ".join(page.extract_text() or "" for page in pdf.pages)
    assert "Revision 2 - CURRENT" in text
    assert "Revision 1 SUPERSEDED SGD 7.20 per piece" in text

    with pdfplumber.open(DATASET / "negative_controls/scan_only_quote.pdf") as pdf:
        assert all(not (page.extract_text() or "").strip() for page in pdf.pages)


def test_full_flow_demo3_primary_pdfs_pass_production_parser_and_scan_fails() -> None:
    parser = PdfQuoteParser()
    supplier_ids = {
        "sterling": "SUP-024",
        "redwood": "SUP-022",
        "great_wall": "SUP-029",
        "sterling_semitech": "SUP-030",
    }
    for name, supplier_id in supplier_ids.items():
        parsed = parser.parse(
            DATASET / f"quotes/{name}_quote.pdf",
            DocumentContext(
                task_id="TASK-FF3-PARSER",
                task_revision=1,
                scenario_id="MCU-FULL-FLOW-EDGE-003",
                quote_id=f"FF3-PARSER-{name}",
                quote_version=1,
                document_id=f"FF3-PARSER-DOC-{name}",
                document_version=1,
                supplier_id=supplier_id,
            ),
        )
        assert len(parsed.sources) >= 30
        assert parsed.context_groups

    with pytest.raises(UnreadableInputError) as exc_info:
        parser.parse(
            DATASET / "negative_controls/scan_only_quote.pdf",
            DocumentContext(
                task_id="TASK-FF3-PARSER",
                task_revision=1,
                scenario_id="MCU-FULL-FLOW-EDGE-003",
                quote_id="FF3-PARSER-SCAN",
                quote_version=1,
                document_id="FF3-PARSER-DOC-SCAN",
                document_version=1,
                supplier_id="SUP-NC-SCAN",
            ),
        )
    assert exc_info.value.code == "blank_pdf"


def test_full_flow_demo3_ambiguous_price_control_is_deterministically_conflicting() -> None:
    parsed = PdfQuoteParser().parse(
        DATASET / "negative_controls/ambiguous_current_prices.pdf",
        DocumentContext(
            task_id="TASK-FF3-CONFLICT",
            task_revision=1,
            scenario_id="MCU-FULL-FLOW-EDGE-003",
            quote_id="FF3-NC-PRICE",
            quote_version=1,
            document_id="FF3-NC-DOC-PRICE",
            document_version=1,
            supplier_id="SUP-NC-PRICE",
        ),
    )
    observations = extract_document_unit_price_observations(parsed)
    selection = select_document_unit_price(parsed)
    assert {(str(item.amount), item.version_status.value) for item in observations} == {
        ("6.70", "CURRENT"), ("6.95", "CURRENT")
    }
    assert selection.selected_value is None
    assert selection.conflict_code == "MULTIPLE_CURRENT_UNIT_PRICES"


def test_full_flow_demo3_negative_csv_controls_are_isolated() -> None:
    with pytest.raises(ContractError) as exc_info:
        identify_csv_contract(DATASET / "negative_controls/invalid_header_quote.csv")
    assert exc_info.value.code == "csv_header_unregistered"

    business_days = _read_single_row(
        DATASET / "negative_controls/unsupported_business_days.csv"
    )
    wrong_part = _read_single_row(DATASET / "negative_controls/wrong_part_quote.csv")
    assert business_days["day_basis"] == "BUSINESS_DAYS"
    assert wrong_part["manufacturer_part_number"] == "QW-MCU8-DEMO"


def test_full_flow_demo3_update_and_prompt_catalog_contracts() -> None:
    revised = _read_single_row(DATASET / "staged_updates/sup-023_quote_revision_2.csv")
    assert revised["supplier_id"] == "SUP-023"
    assert revised["quote_version"] == "2"
    assert revised["unit_price"] == "7.05"
    assert revised["lead_time_days"] == "4"

    catalog = json.loads(
        (DATASET / "conversation_prompts/prompts.json").read_text(encoding="utf-8")
    )
    prompt_ids = {item["id"] for item in catalog["prompts"]}
    assert len(prompt_ids) == 6
    assert {
        "tolerance_then_delivery", "exclude_similar_name_and_history",
        "unsupported_weighting", "prompt_injection_resistance",
    } <= prompt_ids
