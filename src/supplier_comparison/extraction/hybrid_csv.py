"""Registered CSV mapping with a narrow model-review escape hatch."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
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
from .csv_parser import FROZEN_CSV_COLUMNS
from .dictionary import QuoteDictionary
from .errors import ContractError, UnreadableInputError
from .files import FileLimits, require_csv_shape, stable_id, validate_regular_file


PARSER_VERSION = "registered-hybrid-csv/1.0.0"
CANONICAL_START_EVENTS = frozenset({"ORDER_DATE"})
SEMANTIC_PAYMENT_PATTERN = re.compile(
    r"\b(?:after|begins?|starts?|receipt|cleared|dispatch|arrival|clock)\b",
    re.IGNORECASE,
)
RANGE_PATTERN = re.compile(r"\b\d+\s*(?:-|to)\s*\d+\b", re.IGNORECASE)
MANDATORY_FEE_PATTERN = re.compile(
    r"\b(?:mandatory|required)\b.{0,30}\b(?:fee|charge)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class HybridCsvParseResult:
    batch: ExtractionBatch
    semantic_review_fields: tuple[str, ...]


def detect_semantic_review_fields(row: dict[str, str]) -> tuple[str, ...]:
    """Select only registered fields whose text needs semantic interpretation."""

    selected: set[str] = set()
    payment_terms = row.get("payment_terms", "").strip()
    lead_time = row.get("lead_time_days", "").strip()
    start_event = row.get("start_event", "").strip()

    if SEMANTIC_PAYMENT_PATTERN.search(payment_terms):
        selected.add("payment_terms")
        selected.add("start_event")
    if RANGE_PATTERN.search(lead_time) or RANGE_PATTERN.search(payment_terms):
        selected.add("lead_time_days")
    if start_event and start_event not in CANONICAL_START_EVENTS:
        selected.add("start_event")
    if MANDATORY_FEE_PATTERN.search(payment_terms):
        selected.add("other_fees_status")
    return tuple(sorted(selected))


class RegisteredHybridCsvParser:
    """Map scalar cells deterministically and defer flagged fields to a model."""

    def __init__(
        self,
        dictionary: QuoteDictionary,
        limits: FileLimits | None = None,
    ) -> None:
        self.dictionary = dictionary
        self.limits = limits or FileLimits()

    def parse_row(
        self,
        path: str | Path,
        context: DocumentContext,
        row_number: int = 2,
    ) -> HybridCsvParseResult:
        if row_number < 2:
            raise ContractError(
                "csv_row_invalid",
                "CSV data row numbers start at 2",
                row_number=row_number,
            )
        csv_path, size, file_hash = validate_regular_file(path, self.limits)
        require_csv_shape(csv_path)
        selected = self._read_row(csv_path, row_number)
        self._validate_authority(selected, context, row_number)
        semantic_fields = detect_semantic_review_fields(selected)

        sources: list[EvidenceSource] = []
        source_by_column: dict[str, EvidenceSource] = {}
        for column_name in FROZEN_CSV_COLUMNS:
            raw_value = selected[column_name].strip()
            if not raw_value:
                continue
            source_id = stable_id(
                "src",
                {
                    "kind": SourceKind.CSV_CELL,
                    "document_id": context.document_id,
                    "document_version": context.document_version,
                    "sha256": file_hash,
                    "parser_version": PARSER_VERSION,
                    "row": row_number,
                    "column": column_name,
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
                column_name=column_name,
            )
            sources.append(source)
            source_by_column[column_name] = source

        candidates = tuple(
            self._candidate(
                definition.field_name,
                definition.value_type,
                selected,
                source_by_column,
                context,
                field_name in semantic_fields,
            )
            for definition in self.dictionary.extractable_fields
            for field_name in (definition.field_name,)
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
        return HybridCsvParseResult(
            batch=ExtractionBatch(
                dictionary_version=self.dictionary.version,
                parsed_input=parsed,
                candidates=candidates,
            ),
            semantic_review_fields=semantic_fields,
        )

    @staticmethod
    def _read_row(path: Path, row_number: int) -> dict[str, str]:
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_columns = tuple(reader.fieldnames or ())
                if actual_columns != FROZEN_CSV_COLUMNS:
                    raise ContractError(
                        "csv_header_mismatch",
                        "CSV header does not match FIXED-QUOTE-CSV-V1",
                        expected=list(FROZEN_CSV_COLUMNS),
                        actual=list(actual_columns),
                    )
                for current_row, row in enumerate(reader, start=2):
                    if current_row == row_number:
                        return {
                            column: row.get(column) or ""
                            for column in FROZEN_CSV_COLUMNS
                        }
        except UnicodeDecodeError as exc:
            raise UnreadableInputError(
                "csv_not_utf8",
                "CSV input must be UTF-8",
                path=str(path),
            ) from exc
        except csv.Error as exc:
            raise UnreadableInputError(
                "csv_parse_failed",
                "CSV input is malformed",
                path=str(path),
            ) from exc
        raise ContractError(
            "csv_row_missing",
            "requested CSV row does not exist",
            row_number=row_number,
        )

    @staticmethod
    def _validate_authority(
        row: dict[str, str],
        context: DocumentContext,
        row_number: int,
    ) -> None:
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
            key: {"expected": value, "actual": row.get(key, "").strip()}
            for key, value in expected.items()
            if row.get(key, "").strip() != value
        }
        if mismatches:
            raise ContractError(
                "csv_authority_mismatch",
                "CSV identity/version does not match authority-owned context",
                row_number=row_number,
                mismatches=mismatches,
            )

    @staticmethod
    def _candidate(
        field_name: str,
        value_type: str,
        row: dict[str, str],
        source_by_column: dict[str, EvidenceSource],
        context: DocumentContext,
        deferred: bool,
    ) -> QuoteFieldCandidate:
        raw_value = row.get(field_name, "").strip()
        field_id = stable_id(
            "fld",
            {
                "quote_id": context.quote_id,
                "quote_version": context.quote_version,
                "field_name": field_name,
                "field_version": 1,
            },
        )
        if deferred or not raw_value:
            return QuoteFieldCandidate(
                field_id=field_id,
                quote_id=context.quote_id,
                quote_version=context.quote_version,
                field_name=field_name,
                validation_status=ValidationStatus.MISSING,
                origin=None,
                producer=CandidateProducer.DETERMINISTIC_PARSER,
            )

        normalized_value: str | int = raw_value
        if value_type.startswith("integer"):
            try:
                normalized_value = int(raw_value)
            except ValueError as exc:
                raise ContractError(
                    "csv_integer_invalid",
                    f"{field_name} must be an integer unless selected for semantic review",
                    field_name=field_name,
                    raw_value=raw_value,
                ) from exc
        source = source_by_column[field_name]
        return QuoteFieldCandidate(
            field_id=field_id,
            quote_id=context.quote_id,
            quote_version=context.quote_version,
            field_name=field_name,
            raw_value=raw_value,
            normalized_value=normalized_value,
            unit=_unit(field_name, row),
            validation_status=ValidationStatus.EXTRACTED,
            origin=Origin.DOCUMENT,
            source_refs=(
                SourceCitation(source_id=source.source_id, quoted_text=raw_value),
            ),
            producer=CandidateProducer.DETERMINISTIC_PARSER,
        )


def selected_dictionary(
    dictionary: QuoteDictionary,
    field_names: tuple[str, ...],
) -> QuoteDictionary:
    """Return a dictionary view limited to explicitly deferred fields."""

    selected = set(field_names)
    unknown = selected - set(dictionary.fields)
    if unknown:
        raise ContractError(
            "semantic_review_field_unknown",
            "semantic review selected fields outside the active dictionary",
            field_names=sorted(unknown),
        )
    return QuoteDictionary(
        version=dictionary.version,
        fields={
            name: definition
            for name, definition in dictionary.fields.items()
            if name in selected
        },
    )


def merge_semantic_review(
    deterministic_batch: ExtractionBatch,
    semantic_batch: ExtractionBatch,
    semantic_fields: tuple[str, ...],
) -> ExtractionBatch:
    """Replace only deferred candidates while preserving deterministic fields."""

    if (
        deterministic_batch.parsed_input.document_sha256
        != semantic_batch.parsed_input.document_sha256
        or deterministic_batch.parsed_input.context
        != semantic_batch.parsed_input.context
    ):
        raise ContractError(
            "hybrid_batch_identity_mismatch",
            "semantic result belongs to another input or authority context",
        )
    replacements = {
        candidate.field_name: candidate for candidate in semantic_batch.candidates
    }
    if set(replacements) != set(semantic_fields):
        raise ContractError(
            "hybrid_semantic_field_mismatch",
            "semantic result must contain exactly the deferred fields",
            expected=sorted(semantic_fields),
            actual=sorted(replacements),
        )
    return ExtractionBatch(
        dictionary_version=deterministic_batch.dictionary_version,
        parsed_input=deterministic_batch.parsed_input,
        candidates=tuple(
            replacements.get(candidate.field_name, candidate)
            for candidate in deterministic_batch.candidates
        ),
        normalization_events=semantic_batch.normalization_events,
        run=semantic_batch.run,
    )


def _unit(field_name: str, row: dict[str, str]) -> str | None:
    if field_name in {"unit_price", "shipping_fee_amount", "other_fees_amount"}:
        return row.get("currency", "").strip() or None
    if field_name in {"price_basis_quantity", "order_multiple_units", "units_per_pack"}:
        return "piece"
    if field_name == "moq_quantity":
        return row.get("moq_unit", "").strip() or None
    if field_name == "lead_time_days":
        return row.get("day_basis", "").strip() or None
    return None
