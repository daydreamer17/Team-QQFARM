"""Typed errors exposed by the extraction boundary."""

from __future__ import annotations

from typing import Any

from .contracts import ExtractionFailureCategory


class ExtractionError(Exception):
    """Base error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class UnsupportedInputError(ExtractionError):
    pass


class InputLimitError(ExtractionError):
    pass


class UnreadableInputError(ExtractionError):
    pass


class ContractError(ExtractionError):
    pass


class EvidenceValidationError(ExtractionError):
    pass


class AdapterError(ExtractionError):
    pass


class ModelCallBudgetExceeded(AdapterError):
    pass


class DownstreamNotReadyError(ExtractionError):
    """Raised when an unreviewed or rejected extraction is sent to C."""

    pass


def classify_failure_code(code: str) -> ExtractionFailureCategory:
    """Map stable extraction errors to the five V7 audit categories."""

    if code.startswith("pdf_ocr_") or code.startswith("ocr_"):
        return ExtractionFailureCategory.OCR
    if code in {
        "model_http_error",
        "model_transport_failed",
        "model_call_budget_exceeded",
        "model_credential_missing",
    }:
        return ExtractionFailureCategory.TRANSPORT
    if code in {"model_response_invalid", "model_output_schema_invalid"}:
        return ExtractionFailureCategory.MODEL_STRUCTURE
    if code.startswith("source_") or code.startswith("candidate_") or code in {
        "model_source_handle_unknown",
        "evidence_validation_failed",
    }:
        return ExtractionFailureCategory.EVIDENCE
    return ExtractionFailureCategory.PARSING
