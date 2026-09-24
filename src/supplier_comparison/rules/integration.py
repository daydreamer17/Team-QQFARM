"""Small B-to-C integration adapters without database or workflow concerns."""

from __future__ import annotations

from datetime import datetime

from supplier_comparison.extraction.contracts import ExtractionBatch, QuoteFieldCandidate
from supplier_comparison.extraction.contracts import ValidationStatus
from supplier_comparison.extraction.criticality import NON_CRITICAL_FIELDS
from supplier_comparison.extraction.errors import DownstreamNotReadyError
from supplier_comparison.extraction.readiness import require_downstream_ready
from supplier_comparison.extraction.submission import _review_event_matches_current
from supplier_comparison.extraction.review_contracts import (
    FieldReviewDecision, HumanReviewAction, ReviewEnvelope, ReviewReason, ReviewSeverity, ReviewStatus,
)

from .contracts import (
    ComparisonRequest,
    ComparisonResult,
    DecisionPreferences,
    PolicyEligibility,
    ProcurementRequirement,
    QuoteInput,
    SupplierHistoryDatasetContext,
    SupplierHistorySnapshot,
)
from .engine import compare_suppliers
from .decision_impact import (
    DecisionImpactRequest, DecisionImpactResult, FEE_FIELDS, analyze_decision_impact,
)


def quote_input_from_extraction(batch: ExtractionBatch) -> QuoteInput:
    """Reuse B's normalized candidates as C's quote input."""

    context = batch.parsed_input.context
    return QuoteInput(
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        supplier_id=context.supplier_id,
        candidates=batch.candidates,
    )


def compare_extraction_batches(
    requirement: ProcurementRequirement,
    batches: tuple[ExtractionBatch, ...],
    *,
    evaluated_at: datetime,
) -> ComparisonResult:
    """Run C's pure comparison directly over completed B extraction batches."""

    request = ComparisonRequest(
        requirement=requirement,
        quotes=tuple(quote_input_from_extraction(batch) for batch in batches),
        evaluated_at=evaluated_at,
    )
    return compare_suppliers(request)


def _payment_calculation_candidate(candidate: QuoteFieldCandidate, envelope: ReviewEnvelope) -> QuoteFieldCandidate:
    # Reuse submission's version/hash checks; never mutate the original extraction
    # or let a model claim verification. Only an audited same-value confirmation
    # supplies the missing verification at the calculation boundary.
    if (
        candidate.field_name == "payment_terms"
        and candidate.validation_status == ValidationStatus.EXTRACTED
        and envelope.batch is not None
        and any(
            event.field_name == candidate.field_name
            and event.action == HumanReviewAction.CONFIRM_VALUE
            and _review_event_matches_current(envelope.batch, candidate, event)
            for event in envelope.review_events
        )
    ):
        return candidate.model_copy(update={"validation_status": ValidationStatus.VERIFIED})
    return candidate


def quote_input_from_reviewed_extraction(envelope: ReviewEnvelope) -> QuoteInput:
    """Construct C input only after B's review envelope permits handoff.

    Display-only fields are deliberately omitted at this stricter boundary so
    they cannot silently become comparison inputs if C evolves later.
    """

    batch = require_downstream_ready(envelope)
    context = batch.parsed_input.context
    decision_conditional_fields = {"payment_terms", "payment_start_event"}
    calculation_candidates = tuple(
        _payment_calculation_candidate(candidate, envelope)
        for candidate in batch.candidates
        if candidate.field_name not in NON_CRITICAL_FIELDS
        or candidate.field_name in decision_conditional_fields
    )
    return QuoteInput(
        quote_id=context.quote_id,
        quote_version=context.quote_version,
        supplier_id=context.supplier_id,
        candidates=calculation_candidates,
    )


def compare_reviewed_extractions(
    requirement: ProcurementRequirement,
    envelopes: tuple[ReviewEnvelope, ...],
    *,
    evaluated_at: datetime,
) -> ComparisonResult:
    """Run C over reviewed batches, including confirmed MISSING/PENDING cases."""

    request = ComparisonRequest(
        requirement=requirement,
        quotes=tuple(
            quote_input_from_reviewed_extraction(envelope)
            for envelope in envelopes
        ),
        evaluated_at=evaluated_at,
    )
    return compare_suppliers(request)


def quote_input_for_decision_impact(envelope: ReviewEnvelope) -> QuoteInput:
    """Allow a partial comparison ONLY for audited, legal missing fee facts.

    This does not mark the review ready, confirm document absence, or invent a
    human event. The original strict handoff function remains unchanged.
    """
    if envelope.downstream_ready:
        return quote_input_from_reviewed_extraction(envelope)
    checks, summary, batch = envelope.checks, envelope.review, envelope.batch
    if envelope.review_status != ReviewStatus.REVIEW_REQUIRED or not all((checks, summary, batch)):
        require_downstream_ready(envelope)
    assert checks is not None and summary is not None and batch is not None
    blocking = tuple(
        finding for finding in summary.findings
        if not finding.resolved and (
            finding.severity == ReviewSeverity.BLOCKING
            or finding.decision == FieldReviewDecision.REJECTED
        )
    )
    audit_flags = (
        checks.candidate_set_complete, checks.contract_valid, checks.system_audit_valid,
        checks.source_identity_valid, checks.conditional_applicability_resolved,
        checks.accepted_values_semantically_valid, checks.cross_field_valid,
        checks.noncritical_fields_isolated,
    )
    by_name = {candidate.field_name: candidate for candidate in batch.candidates}
    eligible = all(audit_flags) and bool(blocking) and all(
        finding.field_name in FEE_FIELDS
        and finding.decision == FieldReviewDecision.REVIEW_REQUIRED
        and finding.review_reason == ReviewReason.MISSING_REQUIRED_INFO
        and set(finding.codes).issubset({"CRITICAL_FIELD_MISSING", "FEE_STATUS_UNKNOWN"})
        and finding.candidate_field_id == by_name[finding.field_name].field_id
        and (
            by_name[finding.field_name].validation_status == ValidationStatus.MISSING
            or (
                finding.field_name.endswith("_status")
                and by_name[finding.field_name].normalized_value == "UNKNOWN"
                and by_name[finding.field_name].validation_status
                in {ValidationStatus.EXTRACTED, ValidationStatus.VERIFIED}
            )
        )
        for finding in blocking
    )
    if not eligible:
        raise DownstreamNotReadyError(
            "decision_impact_review_unsafe",
            "Only audited missing fees may enter the preliminary decision scope.",
            blocking_fields=list(summary.blocking_fields),
        )
    return QuoteInput(
        quote_id=batch.parsed_input.context.quote_id,
        quote_version=batch.parsed_input.context.quote_version,
        supplier_id=batch.parsed_input.context.supplier_id,
        candidates=tuple(
            _payment_calculation_candidate(c, envelope) for c in batch.candidates
            if c.field_name not in NON_CRITICAL_FIELDS
            or c.field_name in {"payment_terms", "payment_start_event"}
        ),
    )


def reviewed_decision_impact_request(
    requirement: ProcurementRequirement,
    envelopes: tuple[ReviewEnvelope, ...],
    *,
    task_id: str,
    task_revision: int,
    evaluated_at: datetime,
    policy_binding: dict[str, str | None] | None = None,
    decision_preferences: DecisionPreferences | None = None,
    supplier_history_snapshots: tuple[SupplierHistorySnapshot, ...] = (),
    history_dataset_context: SupplierHistoryDatasetContext | None = None,
    payment_supplements: dict[str, dict[str, str | None]] | None = None,
    policy_eligibility: dict[str, PolicyEligibility] | None = None,
) -> DecisionImpactRequest:
    """Version-bound preliminary scope; unsafe review findings still reject."""
    for envelope in envelopes:
        if envelope.batch is not None and envelope.batch.parsed_input.context.task_id != task_id:
            raise DownstreamNotReadyError(
                "decision_impact_task_mismatch", "Review belongs to another task."
            )
    preferences = decision_preferences or DecisionPreferences()
    excluded_suppliers = set(preferences.excluded_supplier_ids)
    selected_envelopes = tuple(
        envelope for envelope in envelopes
        if envelope.batch is None
        or envelope.batch.parsed_input.context.supplier_id not in excluded_suppliers
    )
    return DecisionImpactRequest(
        task_id=task_id, task_revision=task_revision,
        comparison=ComparisonRequest(
            requirement=requirement,
            quotes=tuple(
                quote_input_for_decision_impact(envelope).model_copy(update={
                    "payment_start_event_override": (payment_supplements or {}).get(
                        envelope.batch.parsed_input.context.quote_id, {}
                    ).get("payment_start_event"),
                    "payment_start_event_evidence_ref": (payment_supplements or {}).get(
                        envelope.batch.parsed_input.context.quote_id, {}
                    ).get("evidence_ref"),
                })
                for envelope in selected_envelopes
            ),
            evaluated_at=evaluated_at,
            supplier_history_snapshots=tuple(
                snapshot
                for snapshot in supplier_history_snapshots
                if snapshot.quote_id
                in {
                    envelope.batch.parsed_input.context.quote_id
                    for envelope in selected_envelopes
                    if envelope.batch is not None
                }
            ),
            history_dataset_context=history_dataset_context,
            policy_eligibility=(None if policy_eligibility is None else {
                key: value for key, value in policy_eligibility.items()
                if key in {envelope.batch.parsed_input.context.quote_id
                           for envelope in selected_envelopes if envelope.batch is not None}
            }),
        ),
        policy_binding=policy_binding or {},
        review_bindings={
            envelope.batch.parsed_input.context.quote_id:
                envelope.batch.parsed_input.document_sha256 + ":" + envelope.review.review_run_id
            for envelope in selected_envelopes if envelope.batch is not None and envelope.review is not None
        },
        supplier_bindings={
            envelope.batch.parsed_input.context.quote_id:
                envelope.batch.parsed_input.context.supplier_id
            for envelope in selected_envelopes
            if envelope.batch is not None
            and envelope.batch.parsed_input.context.supplier_id is not None
        },
        decision_preferences=preferences,
    )


def analyze_reviewed_decision_impact(
    requirement: ProcurementRequirement,
    envelopes: tuple[ReviewEnvelope, ...],
    *,
    task_id: str,
    task_revision: int,
    evaluated_at: datetime,
    policy_binding: dict[str, str | None] | None = None,
    decision_preferences: DecisionPreferences | None = None,
    supplier_history_snapshots: tuple[SupplierHistorySnapshot, ...] = (),
    history_dataset_context: SupplierHistoryDatasetContext | None = None,
    payment_supplements: dict[str, dict[str, str | None]] | None = None,
    policy_eligibility: dict[str, PolicyEligibility] | None = None,
) -> DecisionImpactResult:
    return analyze_decision_impact(reviewed_decision_impact_request(
        requirement, envelopes, task_id=task_id, task_revision=task_revision,
        evaluated_at=evaluated_at, policy_binding=policy_binding,
        decision_preferences=decision_preferences,
        supplier_history_snapshots=supplier_history_snapshots,
        history_dataset_context=history_dataset_context, payment_supplements=payment_supplements,
        policy_eligibility=policy_eligibility,
    ))
