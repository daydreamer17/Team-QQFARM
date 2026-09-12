"""Orchestration boundary that wraps model payloads in authority-owned metadata."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from .adapters import ModelAdapter, ModelCallBudget
from .contracts import (
    CandidateProducer,
    ExtractionBatch,
    ExtractionFailureCategory,
    ExtractionRunStatus,
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
            origin=(
                None
                if model_candidate.validation_status == "MISSING"
                else Origin.DOCUMENT
            ),
            source_refs=model_candidate.source_refs,
            producer=CandidateProducer.MODEL_ADAPTER,
            adapter_version=result.run.adapter_version,
            prompt_version=result.run.prompt_version,
        )
        for model_candidate in normalized_payload.candidates
    )
    evidence_started = time.perf_counter()
    try:
        validate_candidates(parsed_input, candidates, dictionary)
    except EvidenceValidationError as exc:
        rejected_payload = result.model_payload_before_grounding or result.payload
        raw_payload = rejected_payload.model_dump_json()
        payload_bytes = raw_payload.encode("utf-8")
        evidence_validation_ms = max(0.0, (time.perf_counter() - evidence_started) * 1000)
        artifact_id = adapter.store_diagnostic_artifact(
            extraction_run_id=extraction_run_id,
            request_fingerprint=result.run.request_fingerprint or ("0" * 64),
            failure_code=exc.code,
            provider_body=None,
            raw_model_content=raw_payload,
        )
        failed_run = result.run.model_copy(
            update={
                "status": ExtractionRunStatus.FAILED,
                "failure_category": ExtractionFailureCategory.EVIDENCE,
                "failure_code": exc.code,
                "evidence_validation_ms": (
                    result.run.evidence_validation_ms + evidence_validation_ms
                ),
                "total_duration_ms": result.run.total_duration_ms + evidence_validation_ms,
                "diagnostic_artifact_id": artifact_id,
                "finished_at": datetime.now(timezone.utc),
                "errors": (*result.run.errors, f"{exc.code}:redacted"),
            }
        )
        detail_keys = sorted(str(key) for key in exc.details)
        detail_digest = hashlib.sha256(
            json.dumps(exc.details, default=str, sort_keys=True).encode("utf-8")
        ).hexdigest()
        raise EvidenceValidationError(
            exc.code,
            "candidate evidence validation failed",
            failure_category=ExtractionFailureCategory.EVIDENCE.value,
            error_summary=f"{exc.code}:redacted",
            evidence_detail_keys=detail_keys,
            evidence_details_sha256=detail_digest,
            rejected_model_payload_sha256=hashlib.sha256(payload_bytes).hexdigest(),
            rejected_model_payload_length_bytes=len(payload_bytes),
            adapter_run=failed_run.model_dump(mode="json"),
        ) from None
    evidence_validation_ms = max(0.0, (time.perf_counter() - evidence_started) * 1000)
    completed_run = result.run.model_copy(
        update={
            "evidence_validation_ms": (
                result.run.evidence_validation_ms + evidence_validation_ms
            ),
            "total_duration_ms": result.run.total_duration_ms + evidence_validation_ms,
            "finished_at": datetime.now(timezone.utc),
        }
    )
    return ExtractionBatch(
        schema_version=("1.1" if parsed_input.page_analyses else "1.0"),
        dictionary_version=dictionary.version,
        parsed_input=parsed_input,
        candidates=candidates,
        normalization_events=normalization_events,
        run=completed_run,
    )
