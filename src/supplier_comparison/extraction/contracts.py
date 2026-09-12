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
    CSV_CELL = "CSV_CELL"


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

    @model_validator(mode="after")
    def location_matches_kind(self) -> "EvidenceSource":
        if self.kind == SourceKind.PDF_TEXT_BLOCK:
            if self.page_number is None or self.block_id is None:
                raise ValueError("PDF source requires page_number and block_id")
            if self.row_number is not None or self.column_name is not None:
                raise ValueError("PDF source cannot contain CSV location")
        elif self.kind == SourceKind.CSV_CELL:
            if self.row_number is None or self.column_name is None:
                raise ValueError("CSV source requires row_number and column_name")
            if self.page_number is not None or self.block_id is not None or self.bbox is not None:
                raise ValueError("CSV source cannot contain PDF location")
        return self


class ParsedInput(FrozenModel):
    context: DocumentContext
    original_filename: str = Field(min_length=1)
    media_type: Literal["application/pdf", "text/csv"]
    file_size_bytes: int = Field(gt=0)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    sources: tuple[EvidenceSource, ...]

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
    started_at: datetime
    finished_at: datetime
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def counters_are_monotonic(self) -> "ExtractionRun":
        if self.calls_after < self.calls_before:
            raise ValueError("calls_after cannot be below calls_before")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        return self


class ExtractionBatch(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    dictionary_version: str = Field(min_length=1)
    parsed_input: ParsedInput
    candidates: tuple[QuoteFieldCandidate, ...]
    normalization_events: tuple[NormalizationEvent, ...] = ()
    run: ExtractionRun | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
