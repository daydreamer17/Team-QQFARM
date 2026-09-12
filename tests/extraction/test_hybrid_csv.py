from __future__ import annotations

from pathlib import Path

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    DocumentContext,
    ExtractionBatch,
    Origin,
    QuoteFieldCandidate,
    SourceCitation,
    ValidationStatus,
)
from supplier_comparison.extraction.hybrid_csv import (
    RegisteredHybridCsvParser,
    merge_semantic_review,
)

from .conftest import DATA_ROOT


INPUTS_ROOT = DATA_ROOT / "generated" / "inputs"


def _context(case_id: str) -> DocumentContext:
    _, split, number = case_id.split("-")
    prefix = {"DEV": "D", "CAL": "C", "HOLD": "H"}[split]
    return DocumentContext(
        task_id=f"TASK-{case_id}",
        task_revision=6,
        scenario_id=case_id,
        quote_id=f"QUOTE-{case_id}",
        quote_version=1,
        document_id=f"DOC-{case_id}",
        document_version=1,
        supplier_id=f"V6-SUP-{prefix}{number}",
    )


def _path(split: str, filename: str) -> Path:
    return INPUTS_ROOT / split / "quote_V6" / filename


def test_clean_registered_csv_maps_all_fields_without_semantic_review(
    quote_dictionary,
) -> None:
    result = RegisteredHybridCsvParser(quote_dictionary).parse_row(
        _path("development", "dev_01_registered_clean.csv"),
        _context("V6-DEV-01"),
    )

    assert result.semantic_review_fields == ()
    assert len(result.batch.candidates) == 30
    assert result.batch.run is None
    assert all(
        candidate.producer == CandidateProducer.DETERMINISTIC_PARSER
        for candidate in result.batch.candidates
    )


def test_semantic_registered_csv_defers_only_detected_fields(quote_dictionary) -> None:
    result = RegisteredHybridCsvParser(quote_dictionary).parse_row(
        _path("development", "dev_02_registered_semantic.csv"),
        _context("V6-DEV-02"),
    )

    assert result.semantic_review_fields == ("payment_terms", "start_event")
    by_name = {candidate.field_name: candidate for candidate in result.batch.candidates}
    assert by_name["payment_terms"].validation_status == ValidationStatus.MISSING
    assert by_name["start_event"].validation_status == ValidationStatus.MISSING
    assert by_name["unit_price"].normalized_value == "6.49"


def test_holdout_semantics_are_detected_without_reading_reference(quote_dictionary) -> None:
    result = RegisteredHybridCsvParser(quote_dictionary).parse_row(
        _path("holdout", "hold_02_registered_semantic.csv"),
        _context("V6-HOLD-02"),
    )

    assert result.semantic_review_fields == (
        "lead_time_days",
        "other_fees_status",
        "payment_terms",
        "start_event",
    )


def test_merge_replaces_only_deferred_candidates(quote_dictionary) -> None:
    parsed = RegisteredHybridCsvParser(quote_dictionary).parse_row(
        _path("development", "dev_02_registered_semantic.csv"),
        _context("V6-DEV-02"),
    )
    source = next(
        item
        for item in parsed.batch.parsed_input.sources
        if item.column_name == "payment_terms"
    )
    replacements = []
    for candidate in parsed.batch.candidates:
        if candidate.field_name not in parsed.semantic_review_fields:
            continue
        values = candidate.model_dump(mode="python")
        values.update(
            raw_value=source.raw_text,
            normalized_value=(
                "Net 15" if candidate.field_name == "payment_terms" else None
            ),
            unit=None,
            validation_status=(
                ValidationStatus.EXTRACTED
                if candidate.field_name == "payment_terms"
                else ValidationStatus.CONFLICT
            ),
            origin=Origin.DOCUMENT,
            source_refs=(
                SourceCitation(source_id=source.source_id, quoted_text=source.raw_text),
            ),
            producer=CandidateProducer.MODEL_ADAPTER,
            adapter_version="fixed-test/1.0",
            prompt_version="fixed-test/1.0",
        )
        replacements.append(QuoteFieldCandidate.model_validate(values))
    semantic_batch = ExtractionBatch(
        dictionary_version=quote_dictionary.version,
        parsed_input=parsed.batch.parsed_input,
        candidates=tuple(replacements),
    )

    merged = merge_semantic_review(
        parsed.batch,
        semantic_batch,
        parsed.semantic_review_fields,
    )

    by_name = {candidate.field_name: candidate for candidate in merged.candidates}
    assert by_name["payment_terms"].producer == CandidateProducer.MODEL_ADAPTER
    assert by_name["start_event"].validation_status == ValidationStatus.CONFLICT
    assert by_name["unit_price"].producer == CandidateProducer.DETERMINISTIC_PARSER
