"""Typed errors exposed by the extraction boundary."""

from __future__ import annotations

from typing import Any


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
