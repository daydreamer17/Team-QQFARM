"""Deterministic five-layer review gate for extraction batches."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from .contracts import (
    AdapterEnvironment,
    CandidateProducer,
    EvidenceContextPurpose,
    ExtractionBatch,
    EvidenceSource,
    Origin,
    QuoteFieldCandidate,
    SourceKind,
    ValidationStatus,
)
from .criticality import (
    ALWAYS_CRITICAL_FIELDS,
    CONDITIONAL_CRITICAL_FIELDS,
    NON_CRITICAL_FIELDS,
    CriticalityContext,
    resolve_criticalities,
    validate_policy_fields,
)
from .dictionary import QuoteDictionary
from .evidence import (
    ORDER_CONSTRAINT_PATTERN,
    SHIPPING_FIELDS,
    SHIPPING_SOURCE_PATTERN,
    source_semantic_contexts,
)
from .files import stable_id
from .review_contracts import (
    CandidateValueSnapshot,
    CorrectionEvent,
    CorrectionState,
    CriticalityAssessment,
    EffectiveCriticality,
    FieldReviewDecision,
    FieldReviewFinding,
    HumanReviewAction,
    ReviewChecks,
    ReviewEnvelope,
    ReviewEvent,
    ReviewReason,
    ReviewSeverity,
    ReviewStatus,
    ReviewSummary,
)


CROSS_FIELD_CODES = frozenset(
    {
        "PRICE_GROUP_INCOMPLETE",
        "MONEY_UNIT_MISMATCH",
        "FEE_STATUS_AMOUNT_CONFLICT",
        "PACKAGING_CONVERSION_INCOMPLETE",
        "MOQ_PACKAGING_UNIT_MISMATCH",
        "LEAD_TIME_GROUP_INCOMPLETE",
        "QUOTE_DATE_AFTER_VALID_UNTIL",
        "CRITICAL_FIELD_CONFLICT",
    }
)
IDENTITY_CODES = frozenset(
    {
        "CANDIDATE_FIELD_SET_INVALID",
        "CANDIDATE_AUTHORITY_MISMATCH",
        "DICTIONARY_VERSION_MISMATCH",
        "SOURCE_IDENTITY_MISMATCH",
        "SOURCE_REF_UNKNOWN",
        "SOURCE_QUOTE_MISMATCH",
        "HUMAN_REVIEW_EVENT_INVALID",
        "CORRECTION_EVENT_INVALID",
        "MODEL_SELF_VERIFIED",
    }
)
TYPE_CODES = frozenset(
    {
        "NORMALIZED_VALUE_REQUIRED",
        "NORMALIZED_TYPE_INVALID",
        "NORMALIZED_ENUM_INVALID",
        "MONEY_VALUE_INVALID",
        "ISO_DATE_REQUIRED",
        "HUMAN_ORIGIN_REQUIRES_VERIFIED",
    }
)
FEE_STATUS_FIELDS = {
    "shipping_fee_status": "shipping_fee_amount",
    "other_fees_status": "other_fees_amount",
}
FEE_STATUSES = frozenset(
    {"KNOWN_AMOUNT", "FREE", "INCLUDED", "NOT_APPLICABLE", "UNKNOWN"}
)
ABSENCE_STATEMENT_PATTERN = re.compile(
    r"\bno\b.{0,120}\b(?:statement|term)\b.{0,40}\b(?:appears?|provided|stated)\b",
    re.IGNORECASE,
)
ORDER_DATE_PATTERN = re.compile(r"\border\s+date\b", re.IGNORECASE)
CLEARED_PAYMENT_PATTERN = re.compile(
    r"\b(?:cleared\s+(?:payment|funds)|payment\s+(?:receipt|received))\b",
    re.IGNORECASE,
)
OCR_CRITICAL_CONFIDENCE_THRESHOLD = Decimal("0.90")
OCR_CONFLICT_REASON_PREFIX = "NATIVE_IMAGE_CRITICAL_TOKEN_CONFLICT:"
UNIT_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:quoted\s+price|unit\s+(?:price|rate)|price\s+for\s+stated\s+basis|rate)\b",
    re.IGNORECASE,
)
NON_PRODUCT_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:freight|shipping|delivery|handling|tax|gst|vat)\b",
    re.IGNORECASE,
)
CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?:S\$|SGD|USD|EUR|GBP|MYR)\s*([0-9]+(?:\.[0-9]{1,4})?)",
    re.IGNORECASE,
)
OTHER_FEE_SOURCE_PATTERN = re.compile(
    r"\b(?:other|additional|handling|service|surcharge)\s+"
    r"(?:fee|fees|charge|charges)\b|\bfee\s+total\b",
    re.IGNORECASE,
)


def review_extraction_batch(
    batch: ExtractionBatch,
    dictionary: QuoteDictionary,
    criticality_context: CriticalityContext,
    *,
    input_is_synthetic: bool,
    reviewed_at: datetime | None = None,
    environment: AdapterEnvironment | None = None,
    review_events: tuple[ReviewEvent, ...] = (),
    corrections: tuple[CorrectionEvent, ...] = (),
    source_result: dict[str, object] | None = None,
) -> ReviewEnvelope:
    """Review one immutable extraction batch and return a C-compatible envelope."""

    now = reviewed_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("reviewed_at must be timezone-aware")

    expected_fields = tuple(definition.field_name for definition in dictionary.extractable_fields)
    validate_policy_fields(expected_fields)
    actual_fields = [candidate.field_name for candidate in batch.candidates]
    if not _candidate_set_is_valid(actual_fields, expected_fields):
        return _candidate_set_rejected_envelope(
            batch,
            input_is_synthetic=input_is_synthetic,
            reviewed_at=now,
            environment=environment,
            review_events=review_events,
            corrections=corrections,
            source_result=source_result,
        )
    assessments = resolve_criticalities(batch, criticality_context)
    assessment_by_name = {item.field_name: item for item in assessments}
    by_name = {candidate.field_name: candidate for candidate in batch.candidates}
    source_by_id = {source.source_id: source for source in batch.parsed_input.sources}
    semantic_context_by_id = source_semantic_contexts(batch.parsed_input)
    findings: list[FieldReviewFinding] = []

    findings.extend(_review_batch_identity(batch, dictionary, expected_fields, assessment_by_name))
    findings.extend(_review_pdf_page_findings(batch))
    valid_review_events, event_findings = _review_human_events(
        batch, review_events, by_name, source_by_id, assessment_by_name
    )
    findings.extend(event_findings)
    findings.extend(
        _review_correction_events(
            batch, corrections, by_name, assessment_by_name
        )
    )

    if not any(finding.decision == FieldReviewDecision.REJECTED for finding in findings):
        for candidate in batch.candidates:
            assessment = assessment_by_name[candidate.field_name]
            findings.extend(
                _review_candidate(
                    candidate,
                    assessment,
                    dictionary,
                    source_by_id,
                    semantic_context_by_id,
                    valid_review_events.get(candidate.field_name),
                )
            )
        findings.extend(
            _review_cross_field(batch, assessment_by_name, valid_review_events)
        )

    unresolved_rejected = [
        finding
        for finding in findings
        if finding.decision == FieldReviewDecision.REJECTED and not finding.resolved
    ]
    unresolved_blocking = [
        finding
        for finding in findings
        if finding.severity == ReviewSeverity.BLOCKING and not finding.resolved
    ]
    if unresolved_rejected:
        review_status = ReviewStatus.REJECTED
    elif unresolved_blocking:
        review_status = ReviewStatus.REVIEW_REQUIRED
    else:
        review_status = ReviewStatus.READY_FOR_DOWNSTREAM

    downstream_ready = review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    critical_values_complete = downstream_ready and _critical_values_complete(
        by_name, assessments, findings
    )
    warning_fields = tuple(
        sorted(
            {
                finding.field_name
                for finding in findings
                if finding.severity == ReviewSeverity.WARNING
            }
        )
    )
    blocking_fields = tuple(
        sorted({finding.field_name for finding in unresolved_blocking})
    )
    review_run_id = stable_id(
        "review",
        {
            "document_sha256": batch.parsed_input.document_sha256,
            "quote_id": batch.parsed_input.context.quote_id,
            "quote_version": batch.parsed_input.context.quote_version,
            "reviewed_at": now.isoformat(),
            "review_events": [event.review_event_id for event in review_events],
            "corrections": [event.correction_id for event in corrections],
        },
    )
    summary = ReviewSummary(
        review_run_id=review_run_id,
        field_count=len(batch.candidates),
        always_critical_fields=tuple(sorted(ALWAYS_CRITICAL_FIELDS)),
        applicable_conditional_fields=tuple(
            sorted(
                assessment.field_name
                for assessment in assessments
                if assessment.criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
            )
        ),
        not_applicable_conditional_fields=tuple(
            sorted(
                assessment.field_name
                for assessment in assessments
                if assessment.criticality == EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE
            )
        ),
        noncritical_fields=tuple(sorted(NON_CRITICAL_FIELDS)),
        warning_fields=warning_fields,
        blocking_fields=blocking_fields,
        findings=tuple(findings),
        reviewed_at=now,
    )
    checks = _build_checks(
        findings,
        assessments,
        critical_values_complete=critical_values_complete,
    )
    selected_environment = environment or (
        batch.run.environment if batch.run is not None else AdapterEnvironment.LOCAL
    )
    return ReviewEnvelope(
        environment=selected_environment,
        input_is_synthetic=input_is_synthetic,
        correction_state=(
            CorrectionState.POST_CORRECTION
            if corrections
            else CorrectionState.PRE_CORRECTION
        ),
        review_status=review_status,
        downstream_ready=downstream_ready,
        calculation_inputs_complete=critical_values_complete,
        checks=checks,
        review=summary,
        batch=batch,
        source_result=source_result,
        review_events=review_events,
        corrections=corrections,
    )


def model_failed_envelope(
    *,
    environment: AdapterEnvironment,
    input_is_synthetic: bool,
    errors: tuple[str, ...],
) -> ReviewEnvelope:
    """Represent a terminal model failure without fabricating a missing batch."""

    return ReviewEnvelope(
        environment=environment,
        input_is_synthetic=input_is_synthetic,
        correction_state=CorrectionState.PRE_CORRECTION,
        review_status=ReviewStatus.MODEL_FAILED,
        downstream_ready=False,
        calculation_inputs_complete=False,
        errors=errors,
    )


def _candidate_set_is_valid(
    actual_fields: list[str], expected_fields: tuple[str, ...]
) -> bool:
    counts = Counter(actual_fields)
    return (
        len(actual_fields) == len(expected_fields)
        and set(actual_fields) == set(expected_fields)
        and all(count == 1 for count in counts.values())
    )


def _candidate_set_rejected_envelope(
    batch: ExtractionBatch,
    *,
    input_is_synthetic: bool,
    reviewed_at: datetime,
    environment: AdapterEnvironment | None,
    review_events: tuple[ReviewEvent, ...],
    corrections: tuple[CorrectionEvent, ...],
    source_result: dict[str, object] | None,
) -> ReviewEnvelope:
    assessment = _system_assessment("candidate set must match the 30-field dictionary")
    finding = _finding(
        batch,
        field_name="__batch__",
        assessment=assessment,
        decision=FieldReviewDecision.REJECTED,
        severity=ReviewSeverity.BLOCKING,
        reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
        code="CANDIDATE_FIELD_SET_INVALID",
        message="Candidate fields must match the extractable dictionary exactly once.",
    )
    summary = ReviewSummary(
        review_run_id=stable_id(
            "review",
            {
                "document_sha256": batch.parsed_input.document_sha256,
                "reviewed_at": reviewed_at.isoformat(),
                "reason": "CANDIDATE_FIELD_SET_INVALID",
            },
        ),
        field_count=len(batch.candidates),
        always_critical_fields=tuple(sorted(ALWAYS_CRITICAL_FIELDS)),
        applicable_conditional_fields=(),
        not_applicable_conditional_fields=tuple(sorted(CONDITIONAL_CRITICAL_FIELDS)),
        noncritical_fields=tuple(sorted(NON_CRITICAL_FIELDS)),
        warning_fields=(),
        blocking_fields=("__batch__",),
        findings=(finding,),
        reviewed_at=reviewed_at,
    )
    checks = ReviewChecks(
        candidate_set_complete=False,
        contract_valid=False,
        system_audit_valid=False,
        source_identity_valid=False,
        conditional_applicability_resolved=False,
        always_critical_reviewed=False,
        applicable_conditional_reviewed=False,
        accepted_values_semantically_valid=False,
        cross_field_valid=False,
        noncritical_fields_isolated=False,
        critical_values_complete=False,
    )
    selected_environment = environment or (
        batch.run.environment if batch.run is not None else AdapterEnvironment.LOCAL
    )
    return ReviewEnvelope(
        environment=selected_environment,
        input_is_synthetic=input_is_synthetic,
        correction_state=(
            CorrectionState.POST_CORRECTION
            if corrections
            else CorrectionState.PRE_CORRECTION
        ),
        review_status=ReviewStatus.REJECTED,
        downstream_ready=False,
        calculation_inputs_complete=False,
        checks=checks,
        review=summary,
        batch=batch,
        source_result=source_result,
        review_events=review_events,
        corrections=corrections,
    )


def _review_batch_identity(
    batch: ExtractionBatch,
    dictionary: QuoteDictionary,
    expected_fields: tuple[str, ...],
    assessments: dict[str, CriticalityAssessment],
) -> list[FieldReviewFinding]:
    findings: list[FieldReviewFinding] = []
    actual_fields = [candidate.field_name for candidate in batch.candidates]
    counts = Counter(actual_fields)
    if (
        set(actual_fields) != set(expected_fields)
        or any(count != 1 for count in counts.values())
        or len(actual_fields) != len(expected_fields)
    ):
        findings.append(
            _finding(
                batch,
                field_name="__batch__",
                assessment=_system_assessment("candidate set must match the 30-field dictionary"),
                decision=FieldReviewDecision.REJECTED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                code="CANDIDATE_FIELD_SET_INVALID",
                message="Candidate fields must match the extractable dictionary exactly once.",
            )
        )
        return findings
    if batch.dictionary_version != dictionary.version:
        findings.append(
            _finding(
                batch,
                field_name="__batch__",
                assessment=_system_assessment("batch must use the active dictionary version"),
                decision=FieldReviewDecision.REJECTED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                code="DICTIONARY_VERSION_MISMATCH",
                message="Extraction batch dictionary version does not match the active dictionary.",
            )
        )
    context = batch.parsed_input.context
    for source in batch.parsed_input.sources:
        if (
            source.document_id != context.document_id
            or source.document_version != context.document_version
            or source.document_sha256 != batch.parsed_input.document_sha256
            or source.parser_version != batch.parsed_input.parser_version
        ):
            findings.append(
                _finding(
                    batch,
                    field_name="__batch__",
                    assessment=_system_assessment("all sources must belong to this document version"),
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="SOURCE_IDENTITY_MISMATCH",
                    message="A parsed source belongs to another document, version, hash, or parser.",
                    source_ids=(source.source_id,),
                )
            )
    for candidate in batch.candidates:
        if candidate.quote_id != context.quote_id or candidate.quote_version != context.quote_version:
            findings.append(
                _finding(
                    batch,
                    field_name=candidate.field_name,
                    assessment=assessments[candidate.field_name],
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="CANDIDATE_AUTHORITY_MISMATCH",
                    message="Candidate belongs to another quote or quote version.",
                    candidate=candidate,
                )
            )
        if (
            candidate.producer == CandidateProducer.MODEL_ADAPTER
            and candidate.validation_status == ValidationStatus.VERIFIED
            and candidate.origin == Origin.DOCUMENT
        ):
            findings.append(
                _finding(
                    batch,
                    field_name=candidate.field_name,
                    assessment=assessments[candidate.field_name],
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="MODEL_SELF_VERIFIED",
                    message="A model-produced candidate cannot self-assign VERIFIED.",
                    candidate=candidate,
                )
            )
    return findings


def _review_pdf_page_findings(batch: ExtractionBatch) -> list[FieldReviewFinding]:
    findings: list[FieldReviewFinding] = []
    for analysis in batch.parsed_input.page_analyses:
        conflict_reasons = tuple(
            reason
            for reason in analysis.quality_reasons
            if reason.startswith(OCR_CONFLICT_REASON_PREFIX)
        )
        if not conflict_reasons:
            continue
        source_ids = tuple(
            source.source_id
            for source in batch.parsed_input.sources
            if source.page_number == analysis.page_number
        )
        findings.append(
            _finding(
                batch,
                field_name="__batch__",
                assessment=_system_assessment(
                    "native PDF text and the visible page image must agree on critical tokens"
                ),
                decision=FieldReviewDecision.REVIEW_REQUIRED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.DOCUMENT_CONFLICT,
                code="pdf_native_image_conflict",
                message=(
                    "Native text and visible-image OCR disagree on critical values on "
                    f"page {analysis.page_number}: {', '.join(conflict_reasons)}."
                ),
                source_ids=source_ids,
            )
        )
    return findings


def _review_human_events(
    batch: ExtractionBatch,
    events: tuple[ReviewEvent, ...],
    by_name: dict[str, QuoteFieldCandidate],
    source_by_id: dict[str, object],
    assessments: dict[str, CriticalityAssessment],
) -> tuple[dict[str, ReviewEvent], list[FieldReviewFinding]]:
    valid: dict[str, ReviewEvent] = {}
    findings: list[FieldReviewFinding] = []
    context = batch.parsed_input.context
    for event in events:
        candidate = by_name.get(event.field_name)
        identity_valid = (
            candidate is not None
            and event.candidate_field_id == candidate.field_id
            and event.candidate_status == candidate.validation_status
            and event.task_revision == context.task_revision
            and event.quote_id == context.quote_id
            and event.quote_version == context.quote_version
            and event.document_id == context.document_id
            and event.document_version == context.document_version
            and event.document_sha256 == batch.parsed_input.document_sha256
            and all(source_id in source_by_id for source_id in event.basis_source_ids)
            and event.field_name not in valid
        )
        action_valid = candidate is not None and (
            (
                event.action == HumanReviewAction.CONFIRM_MISSING
                and candidate.validation_status == ValidationStatus.MISSING
            )
            or (
                event.action == HumanReviewAction.CONFIRM_CONFLICT
                and candidate.validation_status == ValidationStatus.CONFLICT
            )
        )
        if identity_valid and action_valid:
            valid[event.field_name] = event
            continue
        assessment = assessments.get(
            event.field_name,
            _system_assessment("human review event field is outside the batch"),
        )
        findings.append(
            _finding(
                batch,
                field_name=event.field_name,
                assessment=assessment,
                decision=FieldReviewDecision.REJECTED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                code="HUMAN_REVIEW_EVENT_INVALID",
                message="Human review event does not match the current candidate/document version.",
                candidate=candidate,
            )
        )
    return valid, findings


def _review_correction_events(
    batch: ExtractionBatch,
    events: tuple[CorrectionEvent, ...],
    by_name: dict[str, QuoteFieldCandidate],
    assessments: dict[str, CriticalityAssessment],
) -> list[FieldReviewFinding]:
    findings: list[FieldReviewFinding] = []
    context = batch.parsed_input.context
    source_ids = {source.source_id for source in batch.parsed_input.sources}
    latest_by_field: dict[str, CorrectionEvent] = {}
    for event in events:
        latest_by_field[event.field_name] = event
    for field_name, event in latest_by_field.items():
        candidate = by_name.get(field_name)
        valid = (
            candidate is not None
            and event.after == CandidateValueSnapshot.from_candidate(candidate)
            and event.task_revision == context.task_revision
            and event.quote_id == context.quote_id
            and event.quote_version == context.quote_version
            and event.document_id == context.document_id
            and event.document_version == context.document_version
            and event.document_sha256 == batch.parsed_input.document_sha256
            and all(source_id in source_ids for source_id in event.basis_source_ids)
        )
        if valid:
            continue
        findings.append(
            _finding(
                batch,
                field_name=field_name,
                assessment=assessments.get(
                    field_name,
                    _system_assessment("correction field is outside the batch"),
                ),
                decision=FieldReviewDecision.REJECTED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                code="CORRECTION_EVENT_INVALID",
                message="Correction event does not match the current candidate/document version.",
                candidate=candidate,
            )
        )
    for candidate in batch.candidates:
        if (
            candidate.origin in {Origin.USER_INPUT, Origin.USER_CORRECTION}
            and candidate.field_name not in latest_by_field
        ):
            findings.append(
                _finding(
                    batch,
                    field_name=candidate.field_name,
                    assessment=assessments[candidate.field_name],
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="CORRECTION_AUDIT_MISSING",
                    message="Human input or correction requires a matching CorrectionEvent.",
                    candidate=candidate,
                )
            )
    return findings


def _review_candidate(
    candidate: QuoteFieldCandidate,
    assessment: CriticalityAssessment,
    dictionary: QuoteDictionary,
    source_by_id: dict[str, object],
    semantic_context_by_id: dict[str, str],
    review_event: ReviewEvent | None,
) -> list[FieldReviewFinding]:
    findings: list[FieldReviewFinding] = []
    is_critical = assessment.is_critical
    if candidate.validation_status == ValidationStatus.MISSING:
        if not is_critical:
            return [
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.PASS,
                    severity=ReviewSeverity.INFO,
                    reason=None,
                    code="NON_BLOCKING_MISSING",
                    message="Field is legitimately missing and is not currently critical.",
                    accepted=False,
                )
            ]
        if review_event is not None and review_event.action == HumanReviewAction.CONFIRM_MISSING:
            return [
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.WARNING,
                    severity=ReviewSeverity.WARNING,
                    reason=ReviewReason.MISSING_REQUIRED_INFO,
                    code="HUMAN_CONFIRMED_MISSING",
                    message="Human review confirmed that the critical value is absent from the document.",
                    accepted=False,
                    resolved=True,
                    resolution_event_id=review_event.review_event_id,
                )
            ]
        return [
            _candidate_finding(
                candidate,
                assessment,
                decision=FieldReviewDecision.REVIEW_REQUIRED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.MISSING_REQUIRED_INFO,
                code="CRITICAL_FIELD_MISSING",
                message="A currently critical field is missing and requires human review.",
                accepted=False,
            )
        ]

    if candidate.validation_status == ValidationStatus.CONFLICT:
        if not is_critical:
            return [
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.WARNING,
                    severity=ReviewSeverity.WARNING,
                    reason=ReviewReason.NON_CRITICAL_ISSUE,
                    code="NON_CRITICAL_FIELD_CONFLICT",
                    message="A non-critical display field contains unresolved document conflict.",
                    accepted=False,
                )
            ]
        if review_event is not None and review_event.action == HumanReviewAction.CONFIRM_CONFLICT:
            return [
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.WARNING,
                    severity=ReviewSeverity.WARNING,
                    reason=ReviewReason.DOCUMENT_CONFLICT,
                    code="HUMAN_CONFIRMED_CONFLICT",
                    message="Human review confirmed that the document conflict cannot be resolved.",
                    accepted=False,
                    resolved=True,
                    resolution_event_id=review_event.review_event_id,
                )
            ]
        return [
            _candidate_finding(
                candidate,
                assessment,
                decision=FieldReviewDecision.REVIEW_REQUIRED,
                severity=ReviewSeverity.BLOCKING,
                reason=ReviewReason.DOCUMENT_CONFLICT,
                code="CRITICAL_FIELD_CONFLICT",
                message="A currently critical field contains unresolved document conflict.",
                accepted=False,
            )
        ]

    definition = dictionary.fields[candidate.field_name]
    findings.extend(_review_candidate_shape(candidate, assessment, definition.value_type))
    allowed_values = definition.allowed_normalized_values
    if allowed_values is not None and candidate.normalized_value not in allowed_values:
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="NORMALIZED_ENUM_INVALID",
                message="Candidate value is outside the dictionary enum.",
            )
        )
    if candidate.field_name in FEE_STATUS_FIELDS and candidate.normalized_value not in FEE_STATUSES:
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="NORMALIZED_ENUM_INVALID",
                message="Fee status is outside the supported enum.",
            )
        )
    if (
        candidate.field_name in FEE_STATUS_FIELDS
        and candidate.normalized_value == "UNKNOWN"
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="FEE_STATUS_UNKNOWN",
                message="Unknown fee status requires human confirmation before calculation.",
                reason=ReviewReason.MISSING_REQUIRED_INFO,
            )
        )
    findings.extend(
        _review_sources(
            candidate,
            assessment,
            source_by_id,
            semantic_context_by_id,
        )
    )

    if candidate.origin in {Origin.USER_INPUT, Origin.USER_CORRECTION} and (
        candidate.validation_status != ValidationStatus.VERIFIED
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="HUMAN_ORIGIN_REQUIRES_VERIFIED",
                message="Human input or correction must be marked VERIFIED.",
            )
        )
    if not findings:
        findings.append(
            _candidate_finding(
                candidate,
                assessment,
                decision=FieldReviewDecision.PASS,
                severity=ReviewSeverity.INFO,
                reason=None,
                code="FIELD_ACCEPTED",
                message="Candidate passed deterministic field review.",
                accepted=is_critical,
            )
        )
    return findings


def _review_candidate_shape(
    candidate: QuoteFieldCandidate,
    assessment: CriticalityAssessment,
    value_type: str,
) -> list[FieldReviewFinding]:
    value = candidate.normalized_value
    if value is None:
        return [
            _candidate_problem(
                candidate,
                assessment,
                code="NORMALIZED_VALUE_REQUIRED",
                message="EXTRACTED/VERIFIED candidate requires a normalized value.",
            )
        ]
    if value_type.startswith("integer"):
        minimum = 0 if candidate.field_name == "lead_time_days" else 1
        normalized_integer = (
            int(value)
            if isinstance(value, str) and value.isascii() and value.isdigit()
            else value
        )
        if (
            isinstance(normalized_integer, bool)
            or not isinstance(normalized_integer, int)
            or normalized_integer < minimum
        ):
            return [
                _candidate_problem(
                    candidate,
                    assessment,
                    code="NORMALIZED_TYPE_INVALID",
                    message=f"Candidate must be an integer greater than or equal to {minimum}.",
                )
            ]
    elif value_type.startswith("decimal"):
        if not isinstance(value, str) or not _valid_decimal(value):
            return [
                _candidate_problem(
                    candidate,
                    assessment,
                    code="MONEY_VALUE_INVALID",
                    message="Money candidate must be a finite non-negative decimal string.",
                )
            ]
    elif value_type.startswith("date"):
        if not isinstance(value, str) or not _valid_date(value):
            return [
                _candidate_problem(
                    candidate,
                    assessment,
                    code="ISO_DATE_REQUIRED",
                    message="Date candidate must use YYYY-MM-DD.",
                )
            ]
    elif not isinstance(value, str) or not value:
        return [
            _candidate_problem(
                candidate,
                assessment,
                code="NORMALIZED_TYPE_INVALID",
                message="Candidate must be a non-empty normalized string.",
            )
        ]
    return []


def _review_sources(
    candidate: QuoteFieldCandidate,
    assessment: CriticalityAssessment,
    source_by_id: dict[str, EvidenceSource],
    semantic_context_by_id: dict[str, str],
) -> list[FieldReviewFinding]:
    findings: list[FieldReviewFinding] = []
    cited_sources = []
    for citation in candidate.source_refs:
        source = source_by_id.get(citation.source_id)
        if source is None:
            findings.append(
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="SOURCE_REF_UNKNOWN",
                    message="Candidate cites a source outside the current parsed input.",
                    accepted=False,
                )
            )
            continue
        quoted = _normalize_whitespace(citation.quoted_text)
        original = _normalize_whitespace(source.raw_text)
        if quoted not in original:
            findings.append(
                _candidate_finding(
                    candidate,
                    assessment,
                    decision=FieldReviewDecision.REJECTED,
                    severity=ReviewSeverity.BLOCKING,
                    reason=ReviewReason.SYSTEM_IDENTITY_ERROR,
                    code="SOURCE_QUOTE_MISMATCH",
                    message="Quoted evidence is not present in the bound source text.",
                    accepted=False,
                )
            )
            continue
        cited_sources.append(source)
    source_contexts = [
        semantic_context_by_id[source.source_id].replace("_", " ")
        for source in cited_sources
    ]
    if candidate.field_name in SHIPPING_FIELDS and cited_sources and not any(
        SHIPPING_SOURCE_PATTERN.search(context) for context in source_contexts
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="SOURCE_SEMANTIC_MISMATCH",
                message="Evidence does not contain shipping-specific semantics.",
                reason=ReviewReason.EVIDENCE_ERROR,
            )
        )
    if (
        candidate.field_name in {"other_fees_status", "other_fees_amount"}
        and cited_sources
        and not any(OTHER_FEE_SOURCE_PATTERN.search(context) for context in source_contexts)
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="SOURCE_SEMANTIC_MISMATCH",
                message="Evidence does not contain other-fee-specific semantics.",
                reason=ReviewReason.EVIDENCE_ERROR,
            )
        )
    if (
        candidate.field_name in FEE_STATUS_FIELDS
        and cited_sources
        and any(ABSENCE_STATEMENT_PATTERN.search(context) for context in source_contexts)
        and candidate.normalized_value != "UNKNOWN"
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="DOCUMENT_ABSENCE_MISREAD_AS_FEE_VALUE",
                message="A statement that the document omits fee terms cannot prove a fee value.",
            )
        )
    if (
        candidate.field_name == "unit_price"
        and isinstance(candidate.normalized_value, str)
        and _valid_decimal(candidate.normalized_value)
        and cited_sources
        and not any(
            candidate.normalized_value in source.raw_text for source in cited_sources
        )
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="NORMALIZED_PRICE_NOT_IN_EVIDENCE",
                message="Unit price must preserve the quoted price-basis amount rather than derive an each-price.",
            )
        )
    if candidate.field_name in {"price_basis_quantity", "price_basis_unit"} and any(
        ORDER_CONSTRAINT_PATTERN.search(context) for context in source_contexts
    ):
        findings.append(
            _candidate_problem(
                candidate,
                assessment,
                code="SOURCE_SEMANTIC_MISMATCH",
                message="Price-basis field cites an order-constraint source.",
                reason=ReviewReason.EVIDENCE_ERROR,
            )
        )
    if assessment.is_critical:
        unavailable_confidence = [
            source
            for source in cited_sources
            if source.kind == SourceKind.PDF_OCR_BLOCK
            and (
                source.ocr_metadata is None
                or source.ocr_metadata.confidence is None
            )
        ]
        low_confidence = [
            source
            for source in cited_sources
            if source.kind == SourceKind.PDF_OCR_BLOCK
            and source.ocr_metadata is not None
            and source.ocr_metadata.confidence is not None
            and Decimal(source.ocr_metadata.confidence)
            < OCR_CRITICAL_CONFIDENCE_THRESHOLD
        ]
        if unavailable_confidence:
            findings.append(
                _candidate_problem(
                    candidate,
                    assessment,
                    code="OCR_CRITICAL_CONFIDENCE_UNAVAILABLE",
                    message=(
                        "Critical OCR evidence has no confidence value and requires "
                        "human verification."
                    ),
                )
            )
        if low_confidence:
            minimum = min(
                Decimal(source.ocr_metadata.confidence)
                for source in low_confidence
                if source.ocr_metadata is not None
                and source.ocr_metadata.confidence is not None
            )
            findings.append(
                _candidate_problem(
                    candidate,
                    assessment,
                    code="OCR_CRITICAL_CONFIDENCE_LOW",
                    message=(
                        "Critical OCR evidence confidence "
                        f"{minimum} is below the 0.90 human-review threshold."
                    ),
                )
            )
        if (
            candidate.field_name == "manufacturer_part_number"
            and not unavailable_confidence
            and not low_confidence
            and any(source.kind == SourceKind.PDF_OCR_BLOCK for source in cited_sources)
            and _has_confusable_identifier_token(candidate.raw_value or "")
        ):
            findings.append(
                _candidate_problem(
                    candidate,
                    assessment,
                    code="OCR_CRITICAL_CONFUSABLE_TOKEN",
                    message=(
                        "OCR manufacturer part number contains a confusable 0/O or "
                        "1/I/l token and requires human verification."
                    ),
                )
            )
    return findings


def _review_cross_field(
    batch: ExtractionBatch,
    assessments: dict[str, CriticalityAssessment],
    review_events: dict[str, ReviewEvent],
) -> list[FieldReviewFinding]:
    by_name = {candidate.field_name: candidate for candidate in batch.candidates}
    findings: list[FieldReviewFinding] = []
    currency = _usable_value(by_name.get("currency"))
    document_text = _document_text_for_review(batch)

    unit_price = by_name.get("unit_price")
    document_unit_prices = _document_unit_price_values(batch)
    if (
        unit_price is not None
        and _usable_value(unit_price) is not None
        and len(document_unit_prices) > 1
    ):
        findings.append(
            _candidate_problem(
                unit_price,
                assessments["unit_price"],
                code="CRITICAL_FIELD_CONFLICT",
                message=(
                    "Document contains multiple distinct product unit-price amounts; "
                    "a single extracted value cannot be selected automatically."
                ),
                reason=ReviewReason.DOCUMENT_CONFLICT,
            )
        )

    if (
        ORDER_DATE_PATTERN.search(document_text)
        and CLEARED_PAYMENT_PATTERN.search(document_text)
        and "start_event" not in review_events
        and _usable_value(by_name.get("start_event")) is not None
    ):
        findings.append(
            _candidate_problem(
                by_name["start_event"],
                assessments["start_event"],
                code="START_EVENT_DOCUMENT_CONFLICT",
                message="Document contains both order-date and cleared-payment start events.",
                reason=ReviewReason.DOCUMENT_CONFLICT,
            )
        )

    for field_name in ("unit_price", "shipping_fee_amount", "other_fees_amount"):
        candidate = by_name.get(field_name)
        if (
            candidate is not None
            and _usable_value(candidate) is not None
            and isinstance(currency, str)
            and candidate.unit is not None
            and candidate.unit != currency
        ):
            findings.append(
                _candidate_problem(
                    candidate,
                    assessments[field_name],
                    code="MONEY_UNIT_MISMATCH",
                    message="Money candidate unit must match the quote currency.",
                )
            )

    price_fields = ("unit_price", "price_basis_quantity", "price_basis_unit")
    present_price_fields = [
        field_name for field_name in price_fields if _usable_value(by_name.get(field_name)) is not None
    ]
    if present_price_fields and len(present_price_fields) != len(price_fields):
        for field_name in price_fields:
            candidate = by_name[field_name]
            if _usable_value(candidate) is None and field_name not in review_events:
                findings.append(_candidate_problem(
                    candidate,
                    assessments[field_name],
                    code="PRICE_GROUP_INCOMPLETE",
                    message="Unit price, basis quantity, and basis unit must be usable together.",
                ))

    for status_field, amount_field in FEE_STATUS_FIELDS.items():
        status_candidate = by_name[status_field]
        amount_candidate = by_name[amount_field]
        status = _usable_value(status_candidate)
        amount = _usable_value(amount_candidate)
        conflict = False
        if status in {"UNKNOWN", "INCLUDED"} and amount is not None:
            conflict = True
        elif status in {"FREE", "NOT_APPLICABLE"} and amount is not None:
            conflict = not (isinstance(amount, str) and _decimal_is_zero(amount))
        elif status == "KNOWN_AMOUNT" and amount is None:
            conflict = True
        if conflict and amount_field not in review_events:
            findings.append(
                _candidate_problem(
                    amount_candidate,
                    assessments[amount_field],
                    code="FEE_STATUS_AMOUNT_CONFLICT",
                    message=f"{status_field} and {amount_field} are inconsistent.",
                )
            )

    moq_unit = _usable_value(by_name.get("moq_unit"))
    if isinstance(moq_unit, str) and moq_unit != "piece":
        packaging = _usable_value(by_name.get("packaging_type"))
        units = _usable_value(by_name.get("units_per_pack"))
        if packaging is None or units is None:
            for field_name in ("packaging_type", "units_per_pack"):
                if _usable_value(by_name[field_name]) is None and field_name not in review_events:
                    findings.append(_candidate_problem(
                        by_name[field_name],
                        assessments[field_name],
                        code="PACKAGING_CONVERSION_INCOMPLETE",
                        message="Non-piece MOQ requires packaging type and units per pack.",
                    ))
        elif packaging != moq_unit:
            findings.append(
                _candidate_problem(
                    by_name["packaging_type"],
                    assessments["packaging_type"],
                    code="MOQ_PACKAGING_UNIT_MISMATCH",
                    message="Packaging type must match the non-piece MOQ unit.",
                )
            )

    lead_fields = ("lead_time_days", "day_basis", "delivery_semantics", "start_event")
    lead_presence = [_usable_value(by_name.get(field_name)) is not None for field_name in lead_fields]
    if any(lead_presence) and not all(lead_presence):
        for field_name in lead_fields:
            if _usable_value(by_name[field_name]) is None and field_name not in review_events:
                findings.append(_candidate_problem(
                    by_name[field_name],
                    assessments[field_name],
                    code="LEAD_TIME_GROUP_INCOMPLETE",
                    message="Relative delivery fields must be usable as a complete group.",
                ))

    quote_date = _usable_value(by_name.get("quote_date"))
    valid_until = _usable_value(by_name.get("valid_until"))
    if (
        isinstance(quote_date, str)
        and isinstance(valid_until, str)
        and _valid_date(quote_date)
        and _valid_date(valid_until)
        and date.fromisoformat(quote_date) > date.fromisoformat(valid_until)
    ):
        findings.append(
            _candidate_problem(
                by_name["quote_date"],
                assessments["quote_date"],
                code="QUOTE_DATE_AFTER_VALID_UNTIL",
                message="Quote date cannot be later than valid_until.",
            )
        )
    return findings


def _document_text_for_review(batch: ExtractionBatch) -> str:
    return "\n".join(
        " ".join(
            value
            for value in (source.column_name, source.raw_text)
            if isinstance(value, str) and value
        ).replace("_", " ")
        for source in batch.parsed_input.sources
    )


def _document_unit_price_values(batch: ExtractionBatch) -> frozenset[Decimal]:
    source_by_id = {source.source_id: source for source in batch.parsed_input.sources}
    values: set[Decimal] = set()
    for group in batch.parsed_input.context_groups:
        if group.purpose != EvidenceContextPurpose.FIELD_AND_VALUE:
            continue
        members = [source_by_id[source_id] for source_id in group.source_ids]
        labels = [
            source.raw_text
            for source in members
            if UNIT_PRICE_LABEL_PATTERN.search(source.raw_text)
            and not NON_PRODUCT_PRICE_LABEL_PATTERN.search(source.raw_text)
        ]
        if not labels:
            continue
        for source in members:
            for match in CURRENCY_AMOUNT_PATTERN.finditer(source.raw_text):
                try:
                    values.add(Decimal(match.group(1)))
                except InvalidOperation:
                    continue
    return frozenset(values)


def _has_confusable_identifier_token(value: str) -> bool:
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value):
        if "0" in token and "O" in token:
            return True
        if "1" in token and ("I" in token or "l" in token):
            return True
    return False


def _critical_values_complete(
    by_name: dict[str, QuoteFieldCandidate],
    assessments: tuple[CriticalityAssessment, ...],
    findings: list[FieldReviewFinding],
) -> bool:
    failing_fields = {
        finding.field_name
        for finding in findings
        if not finding.resolved
        and finding.severity == ReviewSeverity.BLOCKING
    }
    for assessment in assessments:
        if not assessment.is_critical:
            continue
        candidate = by_name[assessment.field_name]
        if (
            assessment.field_name in failing_fields
            or candidate.validation_status not in {ValidationStatus.EXTRACTED, ValidationStatus.VERIFIED}
            or candidate.normalized_value is None
        ):
            return False
    return True


def _build_checks(
    findings: list[FieldReviewFinding],
    assessments: tuple[CriticalityAssessment, ...],
    *,
    critical_values_complete: bool,
) -> ReviewChecks:
    unresolved = [finding for finding in findings if not finding.resolved]
    codes = {code for finding in unresolved for code in finding.codes}
    blocking_by_field = {
        finding.field_name
        for finding in unresolved
        if finding.severity == ReviewSeverity.BLOCKING
    }
    always_fields = {
        assessment.field_name
        for assessment in assessments
        if assessment.criticality == EffectiveCriticality.ALWAYS
    }
    conditional_fields = {
        assessment.field_name
        for assessment in assessments
        if assessment.criticality == EffectiveCriticality.CONDITIONAL_APPLICABLE
    }
    return ReviewChecks(
        candidate_set_complete="CANDIDATE_FIELD_SET_INVALID" not in codes,
        contract_valid=not bool(codes & TYPE_CODES),
        system_audit_valid=not bool(codes & IDENTITY_CODES),
        source_identity_valid=not bool(
            codes & {"SOURCE_IDENTITY_MISMATCH", "SOURCE_REF_UNKNOWN", "SOURCE_QUOTE_MISMATCH"}
        ),
        conditional_applicability_resolved=True,
        always_critical_reviewed=not bool(always_fields & blocking_by_field),
        applicable_conditional_reviewed=not bool(conditional_fields & blocking_by_field),
        accepted_values_semantically_valid=not any(
            finding.review_reason
            in {ReviewReason.SUSPECTED_EXTRACTION_ERROR, ReviewReason.EVIDENCE_ERROR}
            and finding.severity == ReviewSeverity.BLOCKING
            for finding in unresolved
        ),
        cross_field_valid=not bool(codes & CROSS_FIELD_CODES),
        noncritical_fields_isolated=not any(
            finding.field_name in NON_CRITICAL_FIELDS and finding.accepted_for_calculation
            for finding in findings
        ),
        critical_values_complete=critical_values_complete,
    )


def _candidate_problem(
    candidate: QuoteFieldCandidate,
    assessment: CriticalityAssessment,
    *,
    code: str,
    message: str,
    reason: ReviewReason = ReviewReason.SUSPECTED_EXTRACTION_ERROR,
) -> FieldReviewFinding:
    if assessment.is_critical:
        decision = FieldReviewDecision.REVIEW_REQUIRED
        severity = ReviewSeverity.BLOCKING
        review_reason = reason
    else:
        decision = FieldReviewDecision.WARNING
        severity = ReviewSeverity.WARNING
        review_reason = ReviewReason.NON_CRITICAL_ISSUE
    return _candidate_finding(
        candidate,
        assessment,
        decision=decision,
        severity=severity,
        reason=review_reason,
        code=code,
        message=message,
        accepted=False,
    )


def _candidate_finding(
    candidate: QuoteFieldCandidate,
    assessment: CriticalityAssessment,
    *,
    decision: FieldReviewDecision,
    severity: ReviewSeverity,
    reason: ReviewReason | None,
    code: str,
    message: str,
    accepted: bool,
    resolved: bool = False,
    resolution_event_id: str | None = None,
) -> FieldReviewFinding:
    payload = {
        "quote_id": candidate.quote_id,
        "quote_version": candidate.quote_version,
        "field_id": candidate.field_id,
        "code": code,
        "resolution_event_id": resolution_event_id,
    }
    return FieldReviewFinding(
        finding_id=stable_id("finding", payload),
        field_name=candidate.field_name,
        criticality=assessment.criticality,
        applicable=assessment.applicable,
        applicability_basis=assessment.applicability_basis,
        decision=decision,
        severity=severity,
        review_reason=reason,
        codes=(code,),
        message=message,
        candidate_field_id=candidate.field_id,
        source_ids=tuple(ref.source_id for ref in candidate.source_refs),
        accepted_for_calculation=accepted,
        resolved=resolved,
        resolution_event_id=resolution_event_id,
    )


def _finding(
    batch: ExtractionBatch,
    *,
    field_name: str,
    assessment: CriticalityAssessment,
    decision: FieldReviewDecision,
    severity: ReviewSeverity,
    reason: ReviewReason,
    code: str,
    message: str,
    candidate: QuoteFieldCandidate | None = None,
    source_ids: tuple[str, ...] = (),
) -> FieldReviewFinding:
    context = batch.parsed_input.context
    return FieldReviewFinding(
        finding_id=stable_id(
            "finding",
            {
                "quote_id": context.quote_id,
                "quote_version": context.quote_version,
                "field_name": field_name,
                "code": code,
            },
        ),
        field_name=field_name,
        criticality=assessment.criticality,
        applicable=assessment.applicable,
        applicability_basis=assessment.applicability_basis,
        decision=decision,
        severity=severity,
        review_reason=reason,
        codes=(code,),
        message=message,
        candidate_field_id=candidate.field_id if candidate is not None else None,
        source_ids=source_ids or (
            tuple(ref.source_id for ref in candidate.source_refs)
            if candidate is not None
            else ()
        ),
        accepted_for_calculation=False,
    )


def _system_assessment(basis: str) -> CriticalityAssessment:
    return CriticalityAssessment(
        field_name="__batch__",
        criticality=EffectiveCriticality.SYSTEM_AUDIT,
        applicable=True,
        applicability_basis=basis,
    )


def _usable_value(candidate: QuoteFieldCandidate | None) -> object | None:
    if candidate is None or candidate.validation_status not in {
        ValidationStatus.EXTRACTED,
        ValidationStatus.VERIFIED,
    }:
        return None
    return candidate.normalized_value


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _valid_decimal(value: str) -> bool:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return False
    return amount.is_finite() and amount >= 0


def _decimal_is_zero(value: str) -> bool:
    try:
        return Decimal(value) == 0
    except InvalidOperation:
        return False


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
