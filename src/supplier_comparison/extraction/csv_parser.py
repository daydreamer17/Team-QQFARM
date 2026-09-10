"""Strict parser for A's frozen Week 1 canonical CSV template."""

from __future__ import annotations

import csv
from pathlib import Path

from .contracts import (
    CandidateProducer,
    DocumentContext,
    EvidenceSource,
    ExtractionBatch,
    Origin,
    ParsedInput,
    QuoteFieldCandidate,
    SourceCitation,
    SourceKind,
    ValidationStatus,
)
from .dictionary import QuoteDictionary, QuoteFieldDefinition
from .errors import ContractError, UnreadableInputError
from .files import FileLimits, require_csv_shape, stable_id, validate_regular_file


PARSER_VERSION = "fixed-csv/1.0.0"

FROZEN_CSV_COLUMNS = (
    "scenario_id", "quote_id", "quote_version", "document_id", "supplier_alias", "supplier_id",
    "supplier_name", "supplier_country", "category", "item", "manufacturer", "manufacturer_part_number",
    "package", "revision", "condition", "currency", "unit_price", "price_basis_quantity",
    "price_basis_unit", "packaging_type", "units_per_pack", "order_multiple_units", "moq_quantity",
    "moq_unit", "shipping_fee_status", "shipping_fee_amount", "other_fees_status", "other_fees_amount",
    "fees_complete", "tax_mode", "lead_time_days", "day_basis", "delivery_semantics", "start_event",
    "start_date", "delivery_location", "payment_terms", "quote_date", "valid_until", "source_po_id",
    "is_synthetic",
)


def _normalize(raw_value: str, definition: QuoteFieldDefinition) -> str | int:
    value = raw_value.strip()
    if definition.value_type.startswith("integer"):
        try:
            return int(value)
        except ValueError as exc:
            raise ContractError(
                "csv_integer_invalid",
                f"{definition.field_name} must be an integer",
                field_name=definition.field_name,
                raw_value=raw_value,
            ) from exc
    return value


def _candidate_unit(field_name: str, row: dict[str, str]) -> str | None:
    if field_name in {"unit_price", "shipping_fee_amount", "other_fees_amount"}:
        return (row.get("currency") or "").strip() or None
    if field_name in {"price_basis_quantity", "order_multiple_units", "units_per_pack"}:
        return "piece"
    if field_name == "moq_quantity":
        return (row.get("moq_unit") or "").strip() or None
    if field_name == "lead_time_days":
        return (row.get("day_basis") or "").strip() or None
    return None


class FixedCsvQuoteParser:
    def __init__(self, dictionary: QuoteDictionary, limits: FileLimits | None = None) -> None:
        self.dictionary = dictionary
        self.limits = limits or FileLimits()

    def parse_row(self, path: str | Path, context: DocumentContext, row_number: int) -> ExtractionBatch:
        if row_number < 2:
            raise ContractError("csv_row_invalid", "CSV data row numbers start at 2", row_number=row_number)
        csv_path, size, file_hash = validate_regular_file(path, self.limits)
        require_csv_shape(csv_path)
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_columns = tuple(reader.fieldnames or ())
                if actual_columns != FROZEN_CSV_COLUMNS:
                    raise ContractError(
                        "csv_header_mismatch",
                        "CSV header does not match the frozen Week 1 template",
                        expected=list(FROZEN_CSV_COLUMNS),
                        actual=list(actual_columns),
                    )
                selected: dict[str, str] | None = None
                for current_row_number, row in enumerate(reader, start=2):
                    if current_row_number == row_number:
                        selected = {key: value or "" for key, value in row.items() if key is not None}
                        break
        except UnicodeDecodeError as exc:
            raise UnreadableInputError("csv_not_utf8", "CSV input must be UTF-8", path=str(csv_path)) from exc
        except csv.Error as exc:
            raise UnreadableInputError("csv_parse_failed", "CSV input is malformed", path=str(csv_path)) from exc

        if selected is None:
            raise ContractError("csv_row_missing", "requested CSV row does not exist", row_number=row_number)
        self._validate_authority_columns(selected, context, row_number)

        sources: list[EvidenceSource] = []
        candidates: list[QuoteFieldCandidate] = []
        for definition in self.dictionary.extractable_fields:
            raw_value = selected.get(definition.field_name, "").strip()
            field_id = stable_id(
                "fld",
                {
                    "quote_id": context.quote_id,
                    "quote_version": context.quote_version,
                    "field_name": definition.field_name,
                    "field_version": 1,
                },
            )
            if not raw_value:
                candidates.append(
                    QuoteFieldCandidate(
                        field_id=field_id,
                        quote_id=context.quote_id,
                        quote_version=context.quote_version,
                        field_name=definition.field_name,
                        validation_status=ValidationStatus.MISSING,
                        origin=Origin.DOCUMENT,
                        producer=CandidateProducer.DETERMINISTIC_PARSER,
                    )
                )
                continue

            source_id = stable_id(
                "src",
                {
                    "kind": SourceKind.CSV_CELL,
                    "document_id": context.document_id,
                    "document_version": context.document_version,
                    "sha256": file_hash,
                    "row": row_number,
                    "column": definition.field_name,
                    "raw_text": raw_value,
                },
            )
            source = EvidenceSource(
                source_id=source_id,
                kind=SourceKind.CSV_CELL,
                document_id=context.document_id,
                document_version=context.document_version,
                document_sha256=file_hash,
                parser_version=PARSER_VERSION,
                raw_text=raw_value,
                row_number=row_number,
                column_name=definition.field_name,
            )
            sources.append(source)
            candidates.append(
                QuoteFieldCandidate(
                    field_id=field_id,
                    quote_id=context.quote_id,
                    quote_version=context.quote_version,
                    field_name=definition.field_name,
                    raw_value=raw_value,
                    normalized_value=_normalize(raw_value, definition),
                    unit=_candidate_unit(definition.field_name, selected),
                    validation_status=ValidationStatus.EXTRACTED,
                    origin=Origin.DOCUMENT,
                    source_refs=(SourceCitation(source_id=source_id, quoted_text=raw_value),),
                    producer=CandidateProducer.DETERMINISTIC_PARSER,
                )
            )

        parsed = ParsedInput(
            context=context,
            original_filename=csv_path.name,
            media_type="text/csv",
            file_size_bytes=size,
            document_sha256=file_hash,
            parser_version=PARSER_VERSION,
            sources=tuple(sources),
        )
        return ExtractionBatch(
            dictionary_version=self.dictionary.version,
            parsed_input=parsed,
            candidates=tuple(candidates),
        )

    @staticmethod
    def _validate_authority_columns(row: dict[str, str], context: DocumentContext, row_number: int) -> None:
        expected = {
            "quote_id": context.quote_id,
            "quote_version": str(context.quote_version),
            "document_id": context.document_id,
        }
        if context.scenario_id is not None:
            expected["scenario_id"] = context.scenario_id
        if context.supplier_id is not None:
            expected["supplier_id"] = context.supplier_id
        mismatches = {
            key: {"expected": value, "actual": (row.get(key) or "").strip()}
            for key, value in expected.items()
            if (row.get(key) or "").strip() != value
        }
        if mismatches:
            raise ContractError(
                "csv_authority_mismatch",
                "CSV identity/version does not match authority-owned context",
                row_number=row_number,
                mismatches=mismatches,
            )
