"""Pydantic contracts shared at the B-to-C/D integration boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


class ValidationStatus(StrEnum):
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


class Origin(StrEnum):
    DOCUMENT = "DOCUMENT"
    USER_INPUT = "USER_INPUT"
    USER_CORRECTION = "USER_CORRECTION"
    DERIVED = "DERIVED"


class SourceKind(StrEnum):
    PDF_TEXT_BLOCK = "PDF_TEXT_BLOCK"
    PDF_TABLE_CELL = "PDF_TABLE_CELL"
    PDF_OCR_BLOCK = "PDF_OCR_BLOCK"
    CSV_CELL = "CSV_CELL"


class PageRoute(StrEnum):
    NATIVE_TEXT = "NATIVE_TEXT"
    OCR = "OCR"
    HYBRID = "HYBRID"
    MANUAL_REQUIRED = "MANUAL_REQUIRED"


class CoordinateSpace(StrEnum):
    PDF_POINTS = "PDF_POINTS"
    IMAGE_PIXELS = "IMAGE_PIXELS"


class EvidenceContextPurpose(StrEnum):
    FIELD_AND_VALUE = "FIELD_AND_VALUE"
    TABLE_ROW = "TABLE_ROW"
    ADJACENT_EXPLANATION = "ADJACENT_EXPLANATION"


class CandidateProducer(StrEnum):
    DETERMINISTIC_PARSER = "DETERMINISTIC_PARSER"
    MODEL_ADAPTER = "MODEL_ADAPTER"


class AdapterOutputMode(StrEnum):
    FIXED = "FIXED"
    REAL = "REAL"


class AdapterEnvironment(StrEnum):
    FIXED_TEST = "FIXED_TEST"
    LOCAL = "LOCAL"
    ORGANIZER = "ORGANIZER"


class ExtractionRunStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class ExtractionFailureCategory(StrEnum):
    PARSING = "PARSING"
    OCR = "OCR"
    TRANSPORT = "TRANSPORT"
    MODEL_STRUCTURE = "MODEL_STRUCTURE"
    EVIDENCE = "EVIDENCE"


class ModelAttemptOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DocumentContext(FrozenModel):
    """Authority-owned identity supplied to B; never inferred from document text."""

    task_id: str = Field(min_length=1)
    task_revision: int = Field(ge=1)
    scenario_id: str | None = None
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    supplier_id: str | None = None


class BoundingBox(FrozenModel):
    x0: float
    top: float
    x1: float
    bottom: float

    @model_validator(mode="after")
    def coordinates_are_ordered(self) -> "BoundingBox":
        if self.x1 < self.x0 or self.bottom < self.top:
            raise ValueError("bounding-box coordinates must be ordered")
        return self


RatioString = Annotated[
    StrictStr,
    Field(pattern=r"^(?:0(?:\.\d+)?|1(?:\.0+)?)$"),
]


class PageAnalysis(FrozenModel):
    """Deterministic per-page routing evidence produced before extraction."""

    page_number: int = Field(ge=1)
    route: PageRoute
    native_char_count: int = Field(ge=0)
    printable_ratio: RatioString
    alnum_ratio: RatioString
    image_area_ratio: RatioString
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    coordinate_space: CoordinateSpace
    quality_reasons: tuple[str, ...]
    rendered_page_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class OcrMetadata(FrozenModel):
    """OCR provenance for an atomic image-pixel source in schema 1.1."""

    engine: str = Field(min_length=1)
    engine_version: str = Field(min_length=1)
    confidence: RatioString | None = None
    rendered_page_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_dpi: int = Field(gt=0)
    preprocessing_steps: tuple[str, ...] = ()


class EvidenceContextGroup(FrozenModel):
    """Non-citable grouping that gives the model layout context around atomic sources."""

    context_group_id: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    source_ids: tuple[str, ...] = Field(min_length=1)
    purpose: EvidenceContextPurpose

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> "EvidenceContextGroup":
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("context-group source IDs must be unique")
        return self


class EvidenceSource(FrozenModel):
    source_id: str = Field(min_length=1)
    kind: SourceKind
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    block_id: str | None = None
    bbox: BoundingBox | None = None
    row_number: int | None = Field(default=None, ge=2)
    column_name: str | None = None
    coordinate_space: CoordinateSpace | None = None
    ocr_metadata: OcrMetadata | None = None
    table_id: str | None = None
    row_index: int | None = Field(default=None, ge=0)
    column_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def location_matches_kind(self) -> "EvidenceSource":
        pdf_kinds = {
            SourceKind.PDF_TEXT_BLOCK,
            SourceKind.PDF_TABLE_CELL,
            SourceKind.PDF_OCR_BLOCK,
        }
        if self.kind in pdf_kinds:
            if self.page_number is None or self.block_id is None:
                raise ValueError("PDF source requires page_number and block_id")
            if self.row_number is not None or self.column_name is not None:
                raise ValueError("PDF source cannot contain CSV location")
            # schema 1.0 PDF_TEXT_BLOCK records predate this explicit field and
            # must remain readable during the 1.0 -> 1.1 migration. New parser
            # output always writes PDF_POINTS; new source kinds require it.
            if self.kind != SourceKind.PDF_TEXT_BLOCK and self.coordinate_space is None:
                raise ValueError("PDF source requires an explicit coordinate_space")
            if (
                self.kind in {SourceKind.PDF_TEXT_BLOCK, SourceKind.PDF_TABLE_CELL}
                and self.coordinate_space not in {None, CoordinateSpace.PDF_POINTS}
            ):
                raise ValueError("native PDF source coordinates must use PDF_POINTS")
            if self.kind == SourceKind.PDF_OCR_BLOCK:
                if self.ocr_metadata is None:
                    raise ValueError("OCR source requires ocr_metadata")
                if self.coordinate_space != CoordinateSpace.IMAGE_PIXELS:
                    raise ValueError("OCR source coordinates must use IMAGE_PIXELS")
            elif self.ocr_metadata is not None:
                raise ValueError("non-OCR source cannot contain ocr_metadata")
            if self.kind == SourceKind.PDF_TABLE_CELL:
                if self.table_id is None or self.row_index is None or self.column_index is None:
                    raise ValueError("PDF table source requires table, row, and column location")
            elif self.table_id is not None or self.row_index is not None or self.column_index is not None:
                raise ValueError("non-table source cannot contain table-cell location")
        elif self.kind == SourceKind.CSV_CELL:
            if self.row_number is None or self.column_name is None:
                raise ValueError("CSV source requires row_number and column_name")
            if self.page_number is not None or self.block_id is not None or self.bbox is not None:
                raise ValueError("CSV source cannot contain PDF location")
            if (
                self.coordinate_space is not None
                or self.ocr_metadata is not None
                or self.table_id is not None
                or self.row_index is not None
                or self.column_index is not None
            ):
                raise ValueError("CSV source cannot contain PDF/OCR layout metadata")
        return self


class ParsedInput(FrozenModel):
    context: DocumentContext
    original_filename: str = Field(min_length=1)
    media_type: Literal["application/pdf", "text/csv"]
    file_size_bytes: int = Field(gt=0)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    sources: tuple[EvidenceSource, ...]
    parser_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    page_analyses: tuple[PageAnalysis, ...] = ()
    context_groups: tuple[EvidenceContextGroup, ...] = ()

    @model_validator(mode="after")
    def sources_belong_to_document(self) -> "ParsedInput":
        seen: set[str] = set()
        for source in self.sources:
            if source.source_id in seen:
                raise ValueError(f"duplicate source_id: {source.source_id}")
            seen.add(source.source_id)
            if (
                source.document_id != self.context.document_id
                or source.document_version != self.context.document_version
                or source.document_sha256 != self.document_sha256
                or source.parser_version != self.parser_version
            ):
                raise ValueError(f"source {source.source_id} belongs to another document/version")
        if self.media_type == "text/csv":
            if self.page_analyses or self.context_groups:
                raise ValueError("CSV input cannot contain PDF page analysis or context groups")
            return self

        if self.page_analyses:
            page_numbers = [analysis.page_number for analysis in self.page_analyses]
            expected_pages = list(range(1, len(self.page_analyses) + 1))
            if page_numbers != expected_pages:
                raise ValueError("PDF page analyses must be unique, ordered, and contiguous from page 1")
            allowed_pages = set(page_numbers)
            if any(source.page_number not in allowed_pages for source in self.sources):
                raise ValueError("PDF source references a page without page analysis")

        group_ids: set[str] = set()
        for group in self.context_groups:
            if group.context_group_id in group_ids:
                raise ValueError(f"duplicate context_group_id: {group.context_group_id}")
            group_ids.add(group.context_group_id)
            if self.page_analyses and group.page_number not in {
                analysis.page_number for analysis in self.page_analyses
            }:
                raise ValueError("context group references a page without page analysis")
            if any(source_id not in seen for source_id in group.source_ids):
                raise ValueError("context group references an unknown atomic source")
        return self


class SourceCitation(FrozenModel):
    source_id: str = Field(min_length=1)
    quoted_text: str = Field(min_length=1)


NormalizedScalar = Annotated[StrictStr | StrictInt | StrictBool, Field(union_mode="left_to_right")]


class QuoteFieldCandidate(FrozenModel):
    field_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    field_version: int = Field(default=1, ge=1)
    field_name: str = Field(min_length=1)
    raw_value: str | None = None
    normalized_value: NormalizedScalar | None = None
    unit: str | None = None
    validation_status: ValidationStatus
    origin: Origin | None = None
    source_refs: tuple[SourceCitation, ...] = ()
    producer: CandidateProducer
    adapter_version: str | None = None
    prompt_version: str | None = None

    @model_validator(mode="after")
    def provenance_shape_is_consistent(self) -> "QuoteFieldCandidate":
        if self.validation_status == ValidationStatus.MISSING:
            if (
                self.raw_value is not None
                or self.normalized_value is not None
                or self.origin is not None
                or self.source_refs
            ):
                raise ValueError("MISSING candidate must have null values, null origin, and no source refs")
        elif self.origin is None:
            raise ValueError("non-missing candidate requires origin")
        if self.origin == Origin.DOCUMENT:
            if not self.source_refs:
                raise ValueError("non-missing DOCUMENT candidate requires source refs")
        if self.producer == CandidateProducer.MODEL_ADAPTER:
            if not self.adapter_version or not self.prompt_version:
                raise ValueError("model candidate requires adapter_version and prompt_version")
        elif self.adapter_version is not None or self.prompt_version is not None:
            raise ValueError("parser candidate cannot carry model adapter metadata")
        return self


class NormalizationEvent(FrozenModel):
    """Auditable deterministic cleanup applied after the model response."""

    field_name: str = Field(min_length=1)
    input_value: NormalizedScalar
    output_value: NormalizedScalar | None
    rule_id: str = Field(min_length=1)


class ModelAttemptRecord(FrozenModel):
    """Safe per-attempt telemetry; it never contains prompts or response text."""

    attempt: int = Field(ge=1)
    call_number: int = Field(ge=1)
    outcome: ModelAttemptOutcome
    wait_response_ms: float = Field(ge=0)
    decode_ms: float = Field(default=0, ge=0)
    structure_validation_ms: float = Field(default=0, ge=0)
    evidence_validation_ms: float = Field(default=0, ge=0)
    retry_delay_ms: float = Field(default=0, ge=0)
    error_code: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    provider_request_id: str | None = None
    provider_trace_id: str | None = None
    response_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    response_length_bytes: int | None = Field(default=None, ge=0)


class ExtractionRun(FrozenModel):
    extraction_run_id: str = Field(min_length=1)
    graph_run_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    protocol: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    environment: AdapterEnvironment
    output_mode: AdapterOutputMode
    adapter_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    calls_before: int = Field(ge=0)
    calls_after: int = Field(ge=0)
    attempts: int = Field(ge=0)
    enable_thinking: bool | None = None
    provider_request_id: str | None = None
    provider_trace_id: str | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    status: ExtractionRunStatus = ExtractionRunStatus.SUCCEEDED
    failure_category: ExtractionFailureCategory | None = None
    failure_code: str | None = None
    request_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_construction_ms: float = Field(default=0, ge=0)
    wait_response_ms: float = Field(default=0, ge=0)
    decode_ms: float = Field(default=0, ge=0)
    structure_validation_ms: float = Field(default=0, ge=0)
    evidence_validation_ms: float = Field(default=0, ge=0)
    total_duration_ms: float = Field(default=0, ge=0)
    attempt_records: tuple[ModelAttemptRecord, ...] = ()
    diagnostic_artifact_id: str | None = None
    started_at: datetime
    finished_at: datetime
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counters_are_monotonic(self) -> "ExtractionRun":
        if self.calls_after < self.calls_before:
            raise ValueError("calls_after cannot be below calls_before")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.status == ExtractionRunStatus.SUCCEEDED:
            if self.failure_category is not None or self.failure_code is not None:
                raise ValueError("successful run cannot contain failure metadata")
        elif self.failure_category is None or self.failure_code is None:
            raise ValueError("failed run requires failure_category and failure_code")
        if self.attempt_records and self.attempts != len(self.attempt_records):
            raise ValueError("attempts must equal the number of attempt records")
        return self


class ExtractionBatch(FrozenModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    dictionary_version: str = Field(min_length=1)
    parsed_input: ParsedInput
    candidates: tuple[QuoteFieldCandidate, ...]
    normalization_events: tuple[NormalizationEvent, ...] = ()
    run: ExtractionRun | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def schema_matches_parsed_input(self) -> "ExtractionBatch":
        has_v11_content = bool(
            self.parsed_input.parser_fingerprint
            or self.parsed_input.page_analyses
            or self.parsed_input.context_groups
            or any(
                source.kind in {SourceKind.PDF_TABLE_CELL, SourceKind.PDF_OCR_BLOCK}
                for source in self.parsed_input.sources
            )
        )
        if self.schema_version == "1.0" and has_v11_content:
            raise ValueError("schema 1.0 cannot contain page, layout, or OCR metadata")
        if (
            self.schema_version == "1.1"
            and self.parsed_input.media_type == "application/pdf"
            and (not self.parsed_input.parser_fingerprint or not self.parsed_input.page_analyses)
        ):
            raise ValueError("schema 1.1 PDF requires parser_fingerprint and page_analyses")
        return self
