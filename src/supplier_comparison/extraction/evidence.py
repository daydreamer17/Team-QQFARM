"""Deterministic source-reference and candidate-boundary validation."""

from __future__ import annotations

import re
from collections import Counter

from .contracts import ParsedInput, QuoteFieldCandidate, ValidationStatus
from .dictionary import QuoteDictionary
from .errors import EvidenceValidationError


def _normalized_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def validate_candidates(
    parsed_input: ParsedInput,
    candidates: tuple[QuoteFieldCandidate, ...],
    dictionary: QuoteDictionary,
) -> None:
    expected_fields = {definition.field_name for definition in dictionary.extractable_fields}
    actual_fields = [candidate.field_name for candidate in candidates]
    counts = Counter(actual_fields)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    unknown = sorted(set(actual_fields) - expected_fields)
    missing = sorted(expected_fields - set(actual_fields))
    if duplicates or unknown or missing:
        raise EvidenceValidationError(
            "candidate_field_set_invalid",
            "candidate fields must match A's extractable quote dictionary exactly once",
            duplicates=duplicates,
            unknown=unknown,
            missing=missing,
        )

    source_map = {source.source_id: source for source in parsed_input.sources}
    context = parsed_input.context
    for candidate in candidates:
        if candidate.quote_id != context.quote_id or candidate.quote_version != context.quote_version:
            raise EvidenceValidationError(
                "candidate_authority_mismatch",
                f"candidate {candidate.field_name} belongs to another quote/version",
                field_name=candidate.field_name,
            )
        if candidate.validation_status == ValidationStatus.MISSING:
            continue
        definition = dictionary.fields[candidate.field_name]
        allowed_values = definition.allowed_normalized_values
        if allowed_values is not None and candidate.normalized_value not in allowed_values:
            raise EvidenceValidationError(
                "candidate_enum_invalid",
                f"candidate {candidate.field_name} is outside the allowed normalized values",
                field_name=candidate.field_name,
                normalized_value=candidate.normalized_value,
                allowed_values=list(allowed_values),
            )
        for citation in candidate.source_refs:
            source = source_map.get(citation.source_id)
            if source is None:
                raise EvidenceValidationError(
                    "source_ref_unknown",
                    f"candidate {candidate.field_name} cites an unknown source",
                    field_name=candidate.field_name,
                    source_id=citation.source_id,
                )
            quoted = _normalized_whitespace(citation.quoted_text)
            original = _normalized_whitespace(source.raw_text)
            if quoted not in original:
                raise EvidenceValidationError(
                    "source_quote_mismatch",
                    f"candidate {candidate.field_name} quote is not present in parsed source text",
                    field_name=candidate.field_name,
                    source_id=citation.source_id,
                )
