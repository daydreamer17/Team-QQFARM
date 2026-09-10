"""Member B's quote parsing, model adapter, and evidence boundary."""

from .contracts import (
    AdapterEnvironment,
    AdapterOutputMode,
    BoundingBox,
    CandidateProducer,
    DocumentContext,
    EvidenceSource,
    ExtractionBatch,
    ExtractionRun,
    NormalizationEvent,
    Origin,
    ParsedInput,
    QuoteFieldCandidate,
    SourceCitation,
    SourceKind,
    ValidationStatus,
)

__all__ = [
    "AdapterEnvironment",
    "AdapterOutputMode",
    "BoundingBox",
    "CandidateProducer",
    "DocumentContext",
    "EvidenceSource",
    "ExtractionBatch",
    "ExtractionRun",
    "NormalizationEvent",
    "Origin",
    "ParsedInput",
    "QuoteFieldCandidate",
    "SourceCitation",
    "SourceKind",
    "ValidationStatus",
]
