import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from supplier_comparison.extraction.contracts import (
    DocumentContext,
    ExtractionBatch,
    Origin,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.rules import (
    ComparisonDisposition,
    FeasibilityStatus,
    ProcurementRequirement,
    compare_extraction_batches,
)


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "data/contracts/quote_data_field.csv"
REQUIREMENT_PATH = (
    ROOT
    / "data/generated/inputs/development/quote_V2/procurement_requirement_v2.csv"
)
QUOTES_PATH = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"
CORRECTED_RESULTS_PATH = ROOT / "evaluation/results/local/2026-09-10"


def _load_requirement() -> ProcurementRequirement:
    with REQUIREMENT_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    row["secondary_preference"] = row["secondary_preference"] or None
    return ProcurementRequirement.model_validate(row)


def _context(alias: str) -> DocumentContext:
    supplier_numbers = {"A": "022", "B": "023", "C": "024"}
    return DocumentContext(
        task_id="TASK-MCU-DEMO-001",
        task_revision=1,
        scenario_id="MCU-DEMO-001",
        quote_id=f"QUOTE-MCU-DEMO-001-{alias}",
        quote_version=1,
        document_id=f"DOC-MCU-DEMO-001-{alias}-V1",
        document_version=1,
        supplier_id=f"SUP-{supplier_numbers[alias]}",
    )


def _load_corrected_batch(alias: str) -> ExtractionBatch:
    path = CORRECTED_RESULTS_PATH / (
        f"deepseek_v4_flash_supplier_{alias.lower()}_post_correction.json"
    )
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return ExtractionBatch.model_validate(payload["batch"])


def _answer_supplier_b_shipping(batch: ExtractionBatch) -> ExtractionBatch:
    updates = {
        "shipping_fee_status": {
            "raw_value": "KNOWN_AMOUNT",
            "normalized_value": "KNOWN_AMOUNT",
            "unit": None,
        },
        "shipping_fee_amount": {
            "raw_value": "S$200.00",
            "normalized_value": "200.00",
            "unit": "SGD",
        },
    }
    candidates = []
    for candidate in batch.candidates:
        values = candidate.model_dump(mode="json")
        if candidate.field_name in updates:
            values.update(updates[candidate.field_name])
            values.update(
                validation_status=ValidationStatus.VERIFIED,
                origin=Origin.USER_INPUT,
                source_refs=[],
            )
        candidates.append(QuoteFieldCandidate.model_validate(values))

    values = batch.model_dump(mode="json")
    values["candidates"] = [
        candidate.model_dump(mode="json") for candidate in candidates
    ]
    return ExtractionBatch.model_validate(values)


def test_actual_canonical_csv_flows_from_b_parser_into_c_rules() -> None:
    dictionary = QuoteDictionary.load(CONTRACT_PATH)
    parser = FixedCsvQuoteParser(dictionary)
    batches = tuple(
        parser.parse_row(QUOTES_PATH, _context(alias), row_number)
        for alias, row_number in (("A", 2), ("B", 3), ("C", 4))
    )

    result = compare_extraction_batches(
        _load_requirement(),
        batches,
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    )

    by_quote = {item.quote_id: item for item in result.supplier_results}
    supplier_a = by_quote["QUOTE-MCU-DEMO-001-A"]
    supplier_b = by_quote["QUOTE-MCU-DEMO-001-B"]
    supplier_c = by_quote["QUOTE-MCU-DEMO-001-C"]

    assert supplier_a.status == FeasibilityStatus.INFEASIBLE
    assert supplier_a.actual_quantity == 2000
    assert str(supplier_a.total_cost) == "12800.00"
    assert {issue.code for issue in supplier_a.failed_reasons} == {
        "BUDGET_EXCEEDED",
        "DELIVERY_DEADLINE_EXCEEDED",
    }

    assert supplier_b.status == FeasibilityStatus.PENDING
    assert str(supplier_b.known_cost_subtotal) == "6800.00"
    assert supplier_b.total_cost is None

    assert supplier_c.status == FeasibilityStatus.FEASIBLE
    assert str(supplier_c.total_cost) == "7100.00"

    assert result.disposition == ComparisonDisposition.PENDING_INPUT
    assert result.recommended_quote_ids == ()
    assert result.blocking_pending_quote_ids == ("QUOTE-MCU-DEMO-001-B",)


def test_corrected_model_extractions_recommend_b_after_shipping_answer() -> None:
    supplier_a = _load_corrected_batch("A")
    supplier_b = _answer_supplier_b_shipping(_load_corrected_batch("B"))
    supplier_c = _load_corrected_batch("C")

    result = compare_extraction_batches(
        _load_requirement(),
        (supplier_a, supplier_b, supplier_c),
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    )

    by_quote = {item.quote_id: item for item in result.supplier_results}
    assert by_quote["QUOTE-MCU-DEMO-001-B"].status == FeasibilityStatus.FEASIBLE
    assert str(by_quote["QUOTE-MCU-DEMO-001-B"].total_cost) == "7000.00"
    assert str(by_quote["QUOTE-MCU-DEMO-001-C"].total_cost) == "7100.00"
    assert result.disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
    assert result.recommended_quote_ids == ("QUOTE-MCU-DEMO-001-B",)
    assert result.blocking_pending_quote_ids == ()
