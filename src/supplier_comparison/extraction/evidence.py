"""Deterministic source-reference and candidate-boundary validation."""

from __future__ import annotations

import re
from collections import Counter

from .contracts import (
    EvidenceContextPurpose,
    EvidenceSource,
    ParsedInput,
    QuoteFieldCandidate,
    ValidationStatus,
)
from .dictionary import QuoteDictionary
from .errors import EvidenceValidationError


SHIPPING_FIELDS = frozenset({"shipping_fee_status", "shipping_fee_amount"})
PRICE_BASIS_FIELDS = frozenset({"price_basis_quantity", "price_basis_unit"})
SHIPPING_SOURCE_PATTERN = re.compile(
    r"\b(?:shipping|freight|logistics)\b|\bdelivery\s+(?:charge|fee)\b",
    re.IGNORECASE,
)
ORDER_CONSTRAINT_PATTERN = re.compile(
    r"\b(?:order\s+increment|minimum\s+(?:qty|quantity|order)|moq)\b",
    re.IGNORECASE,
)


def _normalized_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def source_semantic_contexts(parsed_input: ParsedInput) -> dict[str, str]:
    """Return source text plus only its deterministic field/value context.

    TABLE_ROW groups are intentionally excluded: allowing every header in a row
    to support every cell would weaken field-specific evidence checks.
    """

    source_map = {source.source_id: source for source in parsed_input.sources}
    pieces: dict[str, list[str]] = {
        source.source_id: [
            value
            for value in (source.column_name, source.raw_text)
            if value is not None
        ]
        for source in parsed_input.sources
    }
    for group in parsed_input.context_groups:
        if group.purpose != EvidenceContextPurpose.FIELD_AND_VALUE:
            continue
        group_text = [source_map[source_id].raw_text for source_id in group.source_ids]
        for source_id in group.source_ids:
            pieces[source_id].extend(group_text)
    return {
        source_id: _normalized_whitespace(" ".join(dict.fromkeys(values)))
        for source_id, values in pieces.items()
    }


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
    semantic_context_by_id = source_semantic_contexts(parsed_input)
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
        if (
            candidate.validation_status == ValidationStatus.CONFLICT
            and isinstance(candidate.normalized_value, str)
            and candidate.normalized_value.upper() in {status.value for status in ValidationStatus}
        ):
            raise EvidenceValidationError(
                "candidate_status_placeholder_invalid",
                f"candidate {candidate.field_name} uses a status label as its normalized value",
                field_name=candidate.field_name,
                normalized_value=candidate.normalized_value,
            )
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
        cited_sources = []
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
            cited_sources.append(source)
        if candidate.field_name in SHIPPING_FIELDS and not any(
            SHIPPING_SOURCE_PATTERN.search(semantic_context_by_id[source.source_id])
            for source in cited_sources
        ):
            raise EvidenceValidationError(
                "source_semantic_mismatch",
                f"candidate {candidate.field_name} lacks shipping-specific evidence",
                field_name=candidate.field_name,
                required_source_semantics="shipping, freight, logistics, or delivery charge",
            )
        if candidate.field_name in PRICE_BASIS_FIELDS and any(
            ORDER_CONSTRAINT_PATTERN.search(semantic_context_by_id[source.source_id])
            for source in cited_sources
        ):
            raise EvidenceValidationError(
                "source_semantic_mismatch",
                f"candidate {candidate.field_name} cites an order constraint as price-basis evidence",
                field_name=candidate.field_name,
                forbidden_source_semantics="order increment, minimum quantity/order, or MOQ",
            )
