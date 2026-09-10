"""Orchestration boundary that wraps model payloads in authority-owned metadata."""

from __future__ import annotations

from .adapters import ModelAdapter, ModelCallBudget
from .contracts import (
    CandidateProducer,
    ExtractionBatch,
    Origin,
    ParsedInput,
    QuoteFieldCandidate,
)
from .dictionary import QuoteDictionary
from .evidence import validate_candidates
from .errors import EvidenceValidationError
from .files import stable_id
from .normalization import normalize_model_payload


def extract_quote_candidates(
    parsed_input: ParsedInput,
    dictionary: QuoteDictionary,
    adapter: ModelAdapter,
    budget: ModelCallBudget,
    extraction_run_id: str,
) -> ExtractionBatch:
    result = adapter.extract(parsed_input, dictionary, budget, extraction_run_id)
    normalized_payload, normalization_events = normalize_model_payload(result.payload)
    context = parsed_input.context
    candidates = tuple(
        QuoteFieldCandidate(
            field_id=stable_id(
                "fld",
                {
                    "quote_id": context.quote_id,
                    "quote_version": context.quote_version,
                    "field_name": model_candidate.field_name,
                    "field_version": 1,
                },
            ),
            quote_id=context.quote_id,
            quote_version=context.quote_version,
            field_name=model_candidate.field_name,
            raw_value=model_candidate.raw_value,
            normalized_value=model_candidate.normalized_value,
            unit=model_candidate.unit,
            validation_status=model_candidate.validation_status,
            origin=Origin.DOCUMENT,
            source_refs=model_candidate.source_refs,
            producer=CandidateProducer.MODEL_ADAPTER,
            adapter_version=result.run.adapter_version,
            prompt_version=result.run.prompt_version,
        )
        for model_candidate in normalized_payload.candidates
    )
    try:
        validate_candidates(parsed_input, candidates, dictionary)
    except EvidenceValidationError as exc:
        raise EvidenceValidationError(
            exc.code,
            str(exc),
            **exc.details,
            adapter_run=result.run.model_dump(mode="json"),
        ) from exc
    return ExtractionBatch(
        dictionary_version=dictionary.version,
        parsed_input=parsed_input,
        candidates=candidates,
        normalization_events=normalization_events,
        run=result.run,
    )
