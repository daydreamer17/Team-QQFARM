"""Build immutable rule-engine inputs from a task-bound history release."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from supplier_comparison.extraction import ReviewEnvelope, ValidationStatus
from supplier_comparison.rules import (
    HistoryAvailabilityStatus,
    IdentityMatchStatus,
    RateMetric,
    SupplierHistoryDatasetContext,
    SupplierHistorySnapshot,
)
from supplier_comparison.supplier_history import SupplierHistoryDataset


def normalized_supplier_name(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def normalized_supplier_id(value: str) -> str:
    """Normalize an external directory key without changing its stored spelling."""
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _accepted_supplier_name(envelope: ReviewEnvelope) -> str | None:
    """Return a name only after deterministic review accepted its candidate.

    A document-origin value normally remains EXTRACTED even when the review
    engine has proved it is safe for calculation. Requiring VERIFIED here made
    every untouched supplier name impossible to match to the history directory.
    """
    if envelope.batch is None:
        return None
    candidate = next(
        (item for item in envelope.batch.candidates if item.field_name == "supplier_name"),
        None,
    )
    if candidate is None or candidate.normalized_value is None:
        return None
    accepted = candidate.validation_status == ValidationStatus.VERIFIED
    if envelope.review is not None:
        accepted = accepted or any(
            finding.field_name == "supplier_name"
            and finding.candidate_field_id == candidate.field_id
            and finding.accepted_for_calculation
            for finding in envelope.review.findings
        )
    return str(candidate.normalized_value) if accepted else None


def history_inputs(
    context: dict[str, Any],
    envelopes: tuple[ReviewEnvelope, ...],
) -> tuple[SupplierHistoryDatasetContext | None, tuple[SupplierHistorySnapshot, ...]]:
    """Resolve a frozen binding without consulting a newer published release."""
    binding = context.get("supplier_history_binding")
    dataset_payload = context.get("supplier_history_dataset")
    if not binding or not dataset_payload:
        return None, ()

    dataset = SupplierHistoryDataset.model_validate(dataset_payload)
    history_context = SupplierHistoryDatasetContext(
        dataset_id=binding["dataset_id"],
        dataset_version=binding["dataset_version"],
        content_sha256=binding["content_sha256"],
        manifest_sha256=binding["manifest_sha256"],
        rating_method_version=binding["rating_method_version"],
        scope=binding["scope"],
        as_of_date=dataset.context.as_of_date,
        period_start=dataset.context.period_start,
        period_end=dataset.context.period_end,
        is_synthetic=dataset.context.is_synthetic,
    )
    performance_by_id = {
        normalized_supplier_id(item.supplier_id): item for item in dataset.suppliers
    }
    known_names = {
        normalized_supplier_name(name): supplier.supplier_id
        for supplier in dataset.suppliers
        for name in (supplier.supplier_name, *supplier.aliases)
    }
    snapshots: list[SupplierHistorySnapshot] = []
    for envelope in envelopes:
        if envelope.batch is None:
            continue
        quote_id = envelope.batch.parsed_input.context.quote_id
        supplier_id = envelope.batch.parsed_input.context.supplier_id
        supplier_name = _accepted_supplier_name(envelope)
        performance = performance_by_id.get(normalized_supplier_id(supplier_id or ""))
        if performance is None or supplier_name is None:
            identity = IdentityMatchStatus.REVIEW_REQUIRED
        else:
            normalized = normalized_supplier_name(supplier_name)
            if normalized in {
                normalized_supplier_name(performance.supplier_name),
                *(normalized_supplier_name(alias) for alias in performance.aliases),
            }:
                identity = IdentityMatchStatus.MATCHED
            elif normalized in known_names:
                identity = IdentityMatchStatus.CONFLICT
            else:
                identity = IdentityMatchStatus.REVIEW_REQUIRED

        canonical_supplier_id = performance.supplier_id if performance is not None else supplier_id
        evidence = (
            f"HISTORY:{binding['dataset_id']}:{binding['dataset_version']}:{canonical_supplier_id}"
            if canonical_supplier_id
            else f"HISTORY:{binding['dataset_id']}:{binding['dataset_version']}:UNRESOLVED"
        )
        if binding["binding_status"] == "OUT_OF_SCOPE":
            availability = HistoryAvailabilityStatus.OUT_OF_SCOPE
            performance = None
        elif performance is None:
            availability = HistoryAvailabilityStatus.NO_DATA
        else:
            availability = HistoryAvailabilityStatus(
                performance.history_availability_status.value
            )
        snapshots.append(
            SupplierHistorySnapshot(
                quote_id=quote_id,
                supplier_id=canonical_supplier_id,
                supplier_name=supplier_name,
                identity_match_status=identity,
                history_availability_status=availability,
                overall_grade=performance.overall_grade if performance else None,
                on_time=(RateMetric.model_validate(performance.on_time.model_dump()) if performance else None),
                rejected_lines=(
                    RateMetric.model_validate(performance.rejected_lines.model_dump())
                    if performance
                    else None
                ),
                evidence_refs=(evidence,),
            )
        )
    return history_context, tuple(snapshots)
