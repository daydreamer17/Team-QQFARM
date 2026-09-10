from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.csv_parser import (
    CsvSourceProfile,
    ProfiledCsvQuoteParser,
)
from supplier_comparison.extraction.errors import (
    ContractError,
    EvidenceValidationError,
    ExtractionError,
    ModelCallBudgetExceeded,
)
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import DATA_ROOT, context_for


V4_INPUT_ROOT = DATA_ROOT / "generated/inputs/development/quote_V4"
V4_FIXTURE_ROOT = DATA_ROOT / "generated/fixtures/quote_V4"
V4_REFERENCE = Path("evaluation/reference/quote_V4/reference_answers.json")


def _all_missing_payload(quote_dictionary) -> dict:
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


def _payload_with_unit_price(quote_dictionary, source_id: str, quoted_text: str, value: str) -> dict:
    payload = _all_missing_payload(quote_dictionary)
    candidate = next(
        item for item in payload["candidates"] if item["field_name"] == "unit_price"
    )
    candidate.update(
        {
            "raw_value": quoted_text,
            "normalized_value": value,
            "unit": "SGD",
            "validation_status": "EXTRACTED",
            "source_refs": [{"source_id": source_id, "quoted_text": quoted_text}],
        }
    )
    return payload


def test_v4_reference_hashes_bind_every_fixture() -> None:
    reference = json.loads(V4_REFERENCE.read_text(encoding="utf-8"))

    for file_record in reference["files"]:
        path = Path(file_record["relative_path"])
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == file_record["sha256"]


@pytest.mark.parametrize(
    ("filename", "expected_code"),
    (
        ("corrupted_quote_v4.pdf", "corrupted_pdf"),
        ("blank_quote_v4.pdf", "blank_pdf"),
        ("encrypted_quote_v4.pdf", "encrypted_pdf_unsupported"),
        ("over_page_limit_quote_v4.pdf", "pdf_page_limit_exceeded"),
        ("over_size_limit_quote_v4.pdf", "pdf_size_limit_exceeded"),
    ),
)
def test_v4_invalid_pdfs_are_rejected_before_model(filename: str, expected_code: str) -> None:
    with pytest.raises(ExtractionError) as raised:
        PdfQuoteParser().parse(V4_INPUT_ROOT / filename, context_for("a", version=4))

    assert raised.value.code == expected_code
    assert filename in str(raised.value) or filename in raised.value.details.get("path", "")


def test_v4_invalid_csv_header_reports_duplicate_and_missing_columns(quote_dictionary) -> None:
    profile = CsvSourceProfile(
        profile_id="v4_canonical_quote",
        version="1.0.0",
        columns=tuple(quote_dictionary.fields),
    )
    parser = ProfiledCsvQuoteParser({profile.profile_id: profile})

    with pytest.raises(ContractError) as raised:
        parser.parse_row(
            V4_INPUT_ROOT / "invalid_header_quote_v4.csv",
            context_for("a", version=4),
            2,
            profile_id=profile.profile_id,
        )

    assert raised.value.code == "csv_profile_header_mismatch"
    assert raised.value.details["duplicate_headers"] == ["supplier_name"]
    assert raised.value.details["missing_headers"] == ["supplier_country"]


def test_v4_source_ids_are_stable_for_repeat_parse() -> None:
    fixture = json.loads(
        (V4_FIXTURE_ROOT / "source_id_stability_case_v4.json").read_text(encoding="utf-8")
    )
    path = Path(fixture["registered_document"]["relative_path"])
    parser = PdfQuoteParser()
    first = parser.parse(path, context_for("a", version=4))
    second = parser.parse(path, context_for("a", version=4))

    assert [source.source_id for source in first.sources] == [
        source.source_id for source in second.sources
    ]
    assert [source.block_id for source in first.sources] == [
        source.block_id for source in second.sources
    ]
    assert [source.raw_text for source in first.sources] == [
        source.raw_text for source in second.sources
    ]


def test_v4_valid_control_citation_is_accepted(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(
        V4_INPUT_ROOT / "source_supplier_a_quote_v4.pdf",
        context_for("a", version=4),
    )
    source = next(source for source in parsed.sources if "Unit price:" in source.raw_text)
    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter(
            {
                parsed.context.document_id: _payload_with_unit_price(
                    quote_dictionary,
                    source.source_id,
                    "SGD 6.72 per piece",
                    "6.72",
                )
            }
        ),
        ModelCallBudget(graph_run_id="GRAPH-V4-CITATION-CONTROL"),
        "EXTRACT-V4-CITATION-CONTROL",
    )

    assert next(item for item in batch.candidates if item.field_name == "unit_price").normalized_value == "6.72"


@pytest.mark.parametrize("citation_kind", ("forged", "cross_supplier"))
def test_v4_untrusted_citation_is_rejected(quote_dictionary, citation_kind: str) -> None:
    parsed_a = PdfQuoteParser().parse(
        V4_INPUT_ROOT / "source_supplier_a_quote_v4.pdf",
        context_for("a", version=4),
    )
    if citation_kind == "forged":
        source_id = "src_ffffffffffffffffffffffff"
        quoted_text = "SGD 1.00 per piece"
        value = "1.00"
    else:
        parsed_b = PdfQuoteParser().parse(
            V4_INPUT_ROOT / "source_supplier_b_quote_v4.pdf",
            context_for("b", version=4),
        )
        foreign_source = next(
            source for source in parsed_b.sources if "Unit price:" in source.raw_text
        )
        source_id = foreign_source.source_id
        quoted_text = "SGD 6.60 per piece"
        value = "6.60"

    with pytest.raises(EvidenceValidationError) as raised:
        extract_quote_candidates(
            parsed_a,
            quote_dictionary,
            FixedOutputAdapter(
                {
                    parsed_a.context.document_id: _payload_with_unit_price(
                        quote_dictionary,
                        source_id,
                        quoted_text,
                        value,
                    )
                }
            ),
            ModelCallBudget(graph_run_id=f"GRAPH-V4-{citation_kind.upper()}"),
            f"EXTRACT-V4-{citation_kind.upper()}",
        )

    assert raised.value.code == "source_ref_unknown"


def test_v4_call_budget_persists_across_resume_and_new_job_labels() -> None:
    fixture = json.loads(
        (V4_FIXTURE_ROOT / "execution_limit_cases_v4.json").read_text(encoding="utf-8")
    )
    max_calls = fixture["configuration"]["max_model_calls_per_graph_run"]
    budget = ModelCallBudget(
        graph_run_id="GR-V4-RESUME",
        calls_used=7,
        max_calls=max_calls,
    )

    budget.consume()
    with pytest.raises(ModelCallBudgetExceeded):
        budget.consume()

    assert budget.calls_used == 8
