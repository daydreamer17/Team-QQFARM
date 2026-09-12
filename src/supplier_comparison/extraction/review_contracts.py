"""Contracts for deterministic extraction review and human resolution.

The envelope deliberately wraps, rather than replaces, ``ExtractionBatch`` so
the existing C boundary can continue to validate ``payload["batch"]``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import (
    AdapterEnvironment,
    ExtractionBatch,
    NormalizedScalar,
    QuoteFieldCandidate,
    ValidationStatus,
)


REVIEW_SCHEMA_VERSION = "review-envelope/1.0.0"
REVIEW_POLICY_VERSION = "extraction-review/1.1.0"
CRITICALITY_POLICY_VERSION = "c-field-criticality/1.0.0"


class FrozenReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReviewStatus(StrEnum):
    READY_FOR_DOWNSTREAM = "READY_FOR_DOWNSTREAM"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"
    MODEL_FAILED = "MODEL_FAILED"


class FieldReviewDecision(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class ReviewSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class ReviewReason(StrEnum):
    MISSING_REQUIRED_INFO = "MISSING_REQUIRED_INFO"
    SUSPECTED_EXTRACTION_ERROR = "SUSPECTED_EXTRACTION_ERROR"
    EVIDENCE_ERROR = "EVIDENCE_ERROR"
    DOCUMENT_CONFLICT = "DOCUMENT_CONFLICT"
    NON_CRITICAL_ISSUE = "NON_CRITICAL_ISSUE"
    SYSTEM_IDENTITY_ERROR = "SYSTEM_IDENTITY_ERROR"


class EffectiveCriticality(StrEnum):
    ALWAYS = "ALWAYS"
    CONDITIONAL_APPLICABLE = "CONDITIONAL_APPLICABLE"
    CONDITIONAL_NOT_APPLICABLE = "CONDITIONAL_NOT_APPLICABLE"
    NON_CRITICAL = "NON_CRITICAL"
    SYSTEM_AUDIT = "SYSTEM_AUDIT"


class ReviewerType(StrEnum):
    DETERMINISTIC_RULE = "DETERMINISTIC_RULE"
    HUMAN = "HUMAN"


class HumanReviewAction(StrEnum):
    CONFIRM_MISSING = "CONFIRM_MISSING"
    CONFIRM_CONFLICT = "CONFIRM_CONFLICT"


class CorrectionAction(StrEnum):
    USER_INPUT = "USER_INPUT"
    USER_CORRECTION = "USER_CORRECTION"


class CorrectionState(StrEnum):
    PRE_CORRECTION = "PRE_CORRECTION"
    POST_CORRECTION = "POST_CORRECTION"


class CriticalityAssessment(FrozenReviewModel):
    field_name: str = Field(min_length=1)
    criticality: EffectiveCriticality
    applicable: bool
    applicability_basis: str = Field(min_length=1)

    @property
    def is_critical(self) -> bool:
        return self.criticality in {
            EffectiveCriticality.ALWAYS,
            EffectiveCriticality.CONDITIONAL_APPLICABLE,
            EffectiveCriticality.SYSTEM_AUDIT,
        }


class FieldReviewFinding(FrozenReviewModel):
    finding_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    criticality: EffectiveCriticality
    applicable: bool
    applicability_basis: str = Field(min_length=1)
    decision: FieldReviewDecision
    severity: ReviewSeverity
    review_reason: ReviewReason | None = None
    codes: tuple[str, ...] = Field(min_length=1)
    message: str = Field(min_length=1)
    candidate_field_id: str | None = None
    source_ids: tuple[str, ...] = ()
    accepted_for_calculation: bool
    resolved: bool = False
    resolution_event_id: str | None = None
    reviewer_type: ReviewerType = ReviewerType.DETERMINISTIC_RULE
    rule_version: str = REVIEW_POLICY_VERSION
    criticality_policy_version: str = CRITICALITY_POLICY_VERSION

    @model_validator(mode="after")
    def resolution_shape_is_consistent(self) -> "FieldReviewFinding":
        if self.resolved != (self.resolution_event_id is not None):
            raise ValueError("resolved finding must reference exactly one resolution event")
        if self.decision in {
            FieldReviewDecision.REVIEW_REQUIRED,
            FieldReviewDecision.REJECTED,
        } and self.review_reason is None:
            raise ValueError("blocking or rejected finding requires a review_reason")
        return self


class ReviewEvent(FrozenReviewModel):
    review_event_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    candidate_field_id: str = Field(min_length=1)
    action: HumanReviewAction
    candidate_status: ValidationStatus
    reason_code: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    reviewed_at: datetime
    task_revision: int = Field(ge=1)
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_source_scope: Literal["ENTIRE_DOCUMENT"] = "ENTIRE_DOCUMENT"
    basis_source_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def action_matches_candidate_status(self) -> "ReviewEvent":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        expected = {
            HumanReviewAction.CONFIRM_MISSING: ValidationStatus.MISSING,
            HumanReviewAction.CONFIRM_CONFLICT: ValidationStatus.CONFLICT,
        }[self.action]
        if self.candidate_status != expected:
            raise ValueError("human review action does not match candidate status")
        if self.action == HumanReviewAction.CONFIRM_CONFLICT and not self.basis_source_ids:
            raise ValueError("CONFIRM_CONFLICT must retain the conflicting source IDs")
        return self


class CandidateValueSnapshot(FrozenReviewModel):
    field_id: str = Field(min_length=1)
    field_version: int = Field(ge=1)
    raw_value: str | None = None
    normalized_value: NormalizedScalar | None = None
    unit: str | None = None
    validation_status: ValidationStatus
    origin: str | None = None
    source_ids: tuple[str, ...] = ()

    @classmethod
    def from_candidate(cls, candidate: QuoteFieldCandidate) -> "CandidateValueSnapshot":
        return cls(
            field_id=candidate.field_id,
            field_version=candidate.field_version,
            raw_value=candidate.raw_value,
            normalized_value=candidate.normalized_value,
            unit=candidate.unit,
            validation_status=candidate.validation_status,
            origin=candidate.origin.value if candidate.origin is not None else None,
            source_ids=tuple(ref.source_id for ref in candidate.source_refs),
        )


class CorrectionEvent(FrozenReviewModel):
    correction_id: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    action: CorrectionAction
    before: CandidateValueSnapshot
    after: CandidateValueSnapshot
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    basis_source_ids: tuple[str, ...] = ()
    reviewer_id: str = Field(min_length=1)
    reviewed_at: datetime
    task_revision: int = Field(ge=1)
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def correction_is_versioned_and_auditable(self) -> "CorrectionEvent":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if self.after.field_version != self.before.field_version + 1:
            raise ValueError("correction must increment field_version exactly once")
        expected_origin = self.action.value
        if self.after.origin != expected_origin:
            raise ValueError("corrected candidate origin must match correction action")
        if self.after.validation_status != ValidationStatus.VERIFIED:
            raise ValueError("corrected candidate must be VERIFIED")
        return self


class ReviewChecks(FrozenReviewModel):
    candidate_set_complete: bool
    contract_valid: bool
    system_audit_valid: bool
    source_identity_valid: bool
    conditional_applicability_resolved: bool
    always_critical_reviewed: bool
    applicable_conditional_reviewed: bool
    accepted_values_semantically_valid: bool
    cross_field_valid: bool
    noncritical_fields_isolated: bool
    critical_values_complete: bool


class ReviewSummary(FrozenReviewModel):
    review_run_id: str = Field(min_length=1)
    policy_version: str = REVIEW_POLICY_VERSION
    criticality_policy_version: str = CRITICALITY_POLICY_VERSION
    field_count: int = Field(ge=0)
    always_critical_fields: tuple[str, ...]
    applicable_conditional_fields: tuple[str, ...]
    not_applicable_conditional_fields: tuple[str, ...]
    noncritical_fields: tuple[str, ...]
    warning_fields: tuple[str, ...]
    blocking_fields: tuple[str, ...]
    findings: tuple[FieldReviewFinding, ...]
    reviewed_at: datetime

    @model_validator(mode="after")
    def reviewed_at_is_timezone_aware(self) -> "ReviewSummary":
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("reviewed_at must be timezone-aware")
        return self


class ReviewEnvelope(FrozenReviewModel):
    schema_version: Literal["review-envelope/1.0.0"] = REVIEW_SCHEMA_VERSION
    result_kind: Literal["REVIEWED_EXTRACTION"] = "REVIEWED_EXTRACTION"
    environment: AdapterEnvironment
    input_is_synthetic: bool
    correction_state: CorrectionState
    review_status: ReviewStatus
    downstream_ready: bool
    calculation_inputs_complete: bool
    checks: ReviewChecks | None = None
    review: ReviewSummary | None = None
    batch: ExtractionBatch | None = None
    source_result: dict[str, Any] | None = None
    review_events: tuple[ReviewEvent, ...] = ()
    corrections: tuple[CorrectionEvent, ...] = ()
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def status_and_flags_are_consistent(self) -> "ReviewEnvelope":
        ready = self.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
        if self.downstream_ready != ready:
            raise ValueError("downstream_ready must match READY_FOR_DOWNSTREAM")
        if self.calculation_inputs_complete and not self.downstream_ready:
            raise ValueError("complete calculation inputs cannot bypass review")
        if self.review_status == ReviewStatus.MODEL_FAILED:
            if not self.errors:
                raise ValueError("MODEL_FAILED envelope requires errors")
            if self.batch is not None or self.checks is not None or self.review is not None:
                raise ValueError("MODEL_FAILED must not fabricate a reviewed extraction batch")
        elif self.batch is None or self.checks is None or self.review is None:
            raise ValueError("reviewed extraction result requires batch, checks, and review")
        if self.correction_state == CorrectionState.POST_CORRECTION and not self.corrections:
            raise ValueError("POST_CORRECTION envelope requires correction events")
        return self
