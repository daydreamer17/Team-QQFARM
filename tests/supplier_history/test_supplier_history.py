from __future__ import annotations

import csv
import hashlib
import random
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from supplier_comparison.backend.supplier_history import history_inputs
from supplier_comparison.extraction import CriticalityContext, DocumentContext, ExtractionBatch
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.supplier_history import (
    SupplierHistoryLoader,
    SupplierHistoryLoadError,
    generate_supplier_history,
)
from supplier_comparison.supplier_history.generator import (
    SupplierHistoryGenerationError,
    aggregate_rows,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "purchase_orders.csv"
QUOTE_DICTIONARY = ROOT / "data" / "contracts" / "quote_data_field.csv"
QUOTES = ROOT / "data" / "generated" / "inputs" / "development" / "quote_V1" / "quotes.csv"
VERSION = "test-v1"
GENERATED_AT = datetime(2026, 8, 7, tzinfo=timezone.utc)
AS_OF = date(2026, 8, 6)


def _rows() -> list[dict[str, str]]:
    with SOURCE.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_bundled_mcu9_history_matches_reviewed_aggregates(tmp_path: Path) -> None:
    content, _manifest, content_hash, manifest_hash = generate_supplier_history(
        SOURCE,
        tmp_path,
        dataset_version=VERSION,
        generated_at=GENERATED_AT,
        as_of_date=AS_OF,
    )
    dataset, manifest, loaded_manifest_hash = SupplierHistoryLoader(tmp_path).load(
        dataset_version=VERSION,
        expected_content_sha256=content_hash,
        expected_manifest_sha256=manifest_hash,
    )
    assert hashlib.sha256(content.read_bytes()).hexdigest() == content_hash
    assert loaded_manifest_hash == manifest_hash
    assert manifest.filtered_record_count == 1185
    by_id = {item.supplier_id: item for item in dataset.suppliers}
    assert (
        by_id["SUP-024"].order_line_count,
        by_id["SUP-024"].on_time.numerator,
        by_id["SUP-024"].rejected_lines.numerator,
        by_id["SUP-024"].overall_grade,
    ) == (55, 51, 0, "A")
    assert (
        by_id["SUP-022"].order_line_count,
        by_id["SUP-022"].on_time.numerator,
        by_id["SUP-022"].rejected_lines.numerator,
        by_id["SUP-022"].overall_grade,
    ) == (86, 74, 2, "B")
    assert (
        by_id["SUP-023"].order_line_count,
        by_id["SUP-023"].on_time.numerator,
        by_id["SUP-023"].rejected_lines.numerator,
        by_id["SUP-023"].overall_grade,
    ) == (11, 11, 1, "C")
    assert (
        by_id["SUP-029"].order_line_count,
        by_id["SUP-029"].on_time.numerator,
        by_id["SUP-029"].rejected_lines.numerator,
        by_id["SUP-029"].overall_grade,
    ) == (71, 4, 9, "D")


def test_canonical_dataset_is_independent_of_input_order(tmp_path: Path) -> None:
    rows = _rows()
    shuffled = list(rows)
    random.Random(42).shuffle(shuffled)
    first = aggregate_rows(rows, as_of_date=AS_OF)[0]
    second = aggregate_rows(shuffled, as_of_date=AS_OF)[0]
    assert first == second


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("promised_delivery_date", "", "valid ISO date"),
        ("actual_delivery_date", "not-a-date", "valid ISO date"),
        ("quality_rejected", "yes", "True or False"),
    ],
)
def test_invalid_scoped_rows_fail_explicitly(field: str, value: str, message: str) -> None:
    row = next(
        item
        for item in _rows()
        if item["category"] == "Electronics" and item["item"] == "Microcontroller MCU-9"
    )
    row[field] = value
    with pytest.raises(SupplierHistoryGenerationError, match=message):
        aggregate_rows([row], as_of_date=AS_OF)


def test_duplicate_po_fails_instead_of_being_deduplicated() -> None:
    row = next(
        item
        for item in _rows()
        if item["category"] == "Electronics" and item["item"] == "Microcontroller MCU-9"
    )
    with pytest.raises(SupplierHistoryGenerationError, match="duplicated"):
        aggregate_rows([row, dict(row)], as_of_date=AS_OF)


def test_loader_rejects_tampered_content(tmp_path: Path) -> None:
    content, _manifest, content_hash, manifest_hash = generate_supplier_history(
        SOURCE,
        tmp_path,
        dataset_version=VERSION,
        generated_at=GENERATED_AT,
        as_of_date=AS_OF,
    )
    content.write_text(content.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(SupplierHistoryLoadError) as raised:
        SupplierHistoryLoader(tmp_path).load(
            dataset_version=VERSION,
            expected_content_sha256=content_hash,
            expected_manifest_sha256=manifest_hash,
        )
    assert raised.value.code == "content_hash_mismatch"


def test_loader_rejects_path_escape(tmp_path: Path) -> None:
    with pytest.raises(SupplierHistoryLoadError) as raised:
        SupplierHistoryLoader(tmp_path).load(dataset_version="../outside")
    assert raised.value.code == "invalid_dataset_version"


def test_history_identity_accepts_reviewed_name_and_case_insensitive_id(
    tmp_path: Path,
) -> None:
    _content, _manifest_path, content_hash, manifest_hash = generate_supplier_history(
        SOURCE,
        tmp_path,
        dataset_version=VERSION,
        generated_at=GENERATED_AT,
        as_of_date=AS_OF,
    )
    dataset, manifest, _loaded_hash = SupplierHistoryLoader(tmp_path).load(
        dataset_version=VERSION,
        expected_content_sha256=content_hash,
        expected_manifest_sha256=manifest_hash,
    )
    dictionary = QuoteDictionary.load(QUOTE_DICTIONARY)
    batch = FixedCsvQuoteParser(dictionary).parse_row(
        QUOTES,
        DocumentContext(
            task_id="task-history",
            task_revision=1,
            scenario_id="MCU-DEMO-001",
            quote_id="QUOTE-MCU-DEMO-001-C",
            quote_version=1,
            document_id="DOC-MCU-DEMO-001-C-V1",
            document_version=1,
            supplier_id="SUP-024",
        ),
        4,
    )
    payload = batch.model_dump(mode="python")
    payload["parsed_input"]["context"]["supplier_id"] = "sup-024"
    envelope = review_extraction_batch(
        ExtractionBatch.model_validate(payload),
        dictionary,
        CriticalityContext(required_revision="R1", base_unit="piece"),
        input_is_synthetic=True,
        reviewed_at=GENERATED_AT,
    )
    name = next(item for item in envelope.batch.candidates if item.field_name == "supplier_name")
    assert name.validation_status.value == "EXTRACTED"

    _context, snapshots = history_inputs(
        {
            "supplier_history_binding": {
                "binding_status": "AVAILABLE",
                "dataset_id": manifest.dataset_id,
                "dataset_version": manifest.dataset_version,
                "content_sha256": content_hash,
                "manifest_sha256": manifest_hash,
                "rating_method_version": dataset.context.rating_method_version,
                "scope": dataset.context.scope.model_dump(mode="json"),
            },
            "supplier_history_dataset": dataset.model_dump(mode="json"),
        },
        (envelope,),
    )

    assert snapshots[0].identity_match_status.value == "MATCHED"
    assert snapshots[0].supplier_id == "SUP-024"
    assert snapshots[0].supplier_name == "Sterling Components"
    assert snapshots[0].on_time is not None
