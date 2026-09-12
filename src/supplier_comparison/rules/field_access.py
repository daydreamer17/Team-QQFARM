"""Read normalized quote candidates without re-parsing document text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from supplier_comparison.extraction.contracts import (
    NormalizedScalar,
    QuoteFieldCandidate,
    ValidationStatus,
)

from .contracts import QuoteInput, RuleIssue


@dataclass(frozen=True, slots=True)
class FieldAccess:
    """Index one quote and turn unusable fields into stable pending issues."""

    quote: QuoteInput
    _by_name: Mapping[str, QuoteFieldCandidate]

    @classmethod
    def from_quote(cls, quote: QuoteInput) -> "FieldAccess":
        return cls(
            quote=quote,
            _by_name={candidate.field_name: candidate for candidate in quote.candidates},
        )

    def candidate(self, field_name: str) -> QuoteFieldCandidate | None:
        return self._by_name.get(field_name)

    def require(
        self, field_name: str
    ) -> tuple[NormalizedScalar | None, RuleIssue | None]:
        candidate = self.candidate(field_name)
        if candidate is None:
            return None, RuleIssue(
                code="FIELD_ABSENT_FROM_CONTRACT",
                fields=(field_name,),
                message=f"Required field {field_name} is absent from the quote contract.",
            )
        if candidate.validation_status == ValidationStatus.MISSING:
            return None, RuleIssue(
                code="FIELD_MISSING",
                fields=(field_name,),
                message=f"Required field {field_name} is missing from the quote.",
            )
        if candidate.validation_status == ValidationStatus.CONFLICT:
            return None, RuleIssue(
                code="FIELD_CONFLICT",
                fields=(field_name,),
                message=f"Required field {field_name} is ambiguous or contradictory.",
            )
        if candidate.normalized_value is None:
            return None, RuleIssue(
                code="FIELD_VALUE_INVALID",
                fields=(field_name,),
                message=f"Required field {field_name} has no normalized value.",
            )
        return candidate.normalized_value, None
