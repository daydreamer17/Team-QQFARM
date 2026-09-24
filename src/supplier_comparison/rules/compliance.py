"""Finite, reviewed compliance controls. No retrieval, database, or model calls."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError, field_validator, model_validator

from .contracts import FrozenModel

ControlCode = Literal["APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL"]
ExecutionStage = Literal["BEFORE_RECOMMENDATION", "BEFORE_PUBLICATION", "AFTER_SELECTION"]
RuleStatus = Literal["PASS", "FAIL", "REVIEW_REQUIRED", "NOT_APPLICABLE", "NOT_EVALUATED"]
FailureOutcome = Literal["FAIL", "REVIEW_REQUIRED"]


class ExecutableRuleParameters(FrozenModel):
    # All behavioral fields are explicitly reviewed. Legacy dicts are not rules.
    version: Literal["compliance-rule/1.0"]
    control_code: ControlCode
    reviewed_by: str = Field(min_length=1)
    reviewed_at: datetime
    matching_fields: tuple[Literal["supplier_id", "manufacturer", "manufacturer_part_number"], ...]
    date_basis: Literal["EVALUATED_AT"]
    missing_outcome: FailureOutcome
    expired_outcome: FailureOutcome
    mismatch_outcome: FailureOutcome
    execution_stage: ExecutionStage
    allow_unspecified_validity: bool = False
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    monetary_basis: Literal["TOTAL_COST"] | None = None
    threshold: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    operator: Literal["GT", "GTE", "LT", "LTE"] | None = None
    action: str | None = Field(default=None, min_length=1)

    @field_validator("threshold", mode="before")
    @classmethod
    def exact_money(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("threshold requires exact decimal money")
        return value

    @model_validator(mode="after")
    def reviewed_rule_is_complete(self):
        if not self.reviewed_by.strip() or self.reviewed_at.utcoffset() is None:
            raise ValueError("review must name a reviewer and timezone-aware time")
        fields = set(self.matching_fields)
        if len(fields) != len(self.matching_fields):
            raise ValueError("matching fields must be unique")
        if self.control_code == "AMOUNT_APPROVAL":
            if fields or any(value is None for value in (
                self.currency, self.monetary_basis, self.threshold, self.operator, self.action,
            )):
                raise ValueError("amount rule needs exact basis, threshold, operator, and action")
        else:
            required = {"supplier_id"}
            if self.control_code == "ROHS_COMPLIANCE":
                required |= {"manufacturer", "manufacturer_part_number"}
            if not required.issubset(fields):
                raise ValueError("control is missing required matching fields")
            if any(value is not None for value in (
                self.currency, self.monetary_basis, self.threshold, self.operator, self.action,
            )):
                raise ValueError("evidence controls cannot carry amount rules")
        return self


class ComplianceEvidence(FrozenModel):
    evidence_id: str = Field(min_length=1)
    control_code: Literal["APPROVED_SUPPLIER", "ROHS_COMPLIANCE"]
    supplier_id: str = Field(min_length=1)
    manufacturer: str | None = None
    manufacturer_part_number: str | None = None
    coverage_confirmed: bool
    outcome: Literal["PASS", "FAIL"]
    effective_from: date | None = None
    expires_on: date | None = None
    permanent: bool = False
    source_refs: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_dates_and_sources(self):
        if any(not value.strip() for value in self.source_refs):
            raise ValueError("evidence source references cannot be blank")
        if self.permanent and self.expires_on is not None:
            raise ValueError("permanent evidence cannot also expire")
        if self.effective_from and self.expires_on and self.expires_on < self.effective_from:
            raise ValueError("evidence expiration precedes start")
        return self


class ClauseEvaluation(FrozenModel):
    clause_id: str
    control_code: str | None = None
    status: RuleStatus
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    execution_stage: ExecutionStage | None = None
    triggered: bool | None = None
    action: str | None = None
    rule_version: str | None = None


def evaluate_compliance_rule(
    *, clause_id: str, parameters: dict[str, Any] | ExecutableRuleParameters | None,
    supplier_id: str | None, manufacturer: str, manufacturer_part_number: str,
    evaluated_at: datetime, evidence: tuple[ComplianceEvidence, ...] = (),
    amount: Decimal | None = None, currency: str | None = None,
    execution_stage: ExecutionStage = "BEFORE_RECOMMENDATION",
) -> ClauseEvaluation:
    """Evaluate one clause; PASS for amount means evaluated, never procurement approval."""
    try:
        rule = ExecutableRuleParameters.model_validate(parameters)
    except (ValidationError, TypeError):
        return ClauseEvaluation(clause_id=clause_id, status="NOT_EVALUATED",
                                reason_codes=("EXECUTABLE_PARAMETERS_REQUIRED",))
    common = dict(clause_id=clause_id, control_code=rule.control_code,
                  execution_stage=rule.execution_stage, rule_version=rule.version)

    def result(status, reason, records=(), **extra):
        return ClauseEvaluation(**common, status=status, reason_codes=(reason,),
            evidence_ids=tuple(item.evidence_id for item in records),
            source_refs=tuple(dict.fromkeys(ref for item in records for ref in item.source_refs)),
            **extra)

    if evaluated_at.utcoffset() is None:
        return result("NOT_EVALUATED", "EVALUATION_TIME_INVALID")
    if rule.reviewed_at > evaluated_at:
        return result("NOT_EVALUATED", "RULE_NOT_YET_REVIEWED")
    if rule.execution_stage != execution_stage:
        return result("NOT_APPLICABLE", "EXECUTION_STAGE_DEFERRED")
    if rule.control_code == "AMOUNT_APPROVAL":
        if amount is None or not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
            return result("REVIEW_REQUIRED", "AMOUNT_UNKNOWN")
        if currency != rule.currency:
            return result("REVIEW_REQUIRED", "CURRENCY_MISMATCH")
        threshold = rule.threshold
        assert threshold is not None
        triggered = {"GT": amount > threshold, "GTE": amount >= threshold,
                     "LT": amount < threshold, "LTE": amount <= threshold}[rule.operator]
        return result("PASS", "AMOUNT_RULE_EVALUATED", triggered=triggered,
                      action=rule.action if triggered else None)

    if not supplier_id:
        return result("REVIEW_REQUIRED", "SUPPLIER_IDENTITY_UNCONFIRMED")
    control_records = tuple(item for item in evidence if item.control_code == rule.control_code)
    records = tuple(item for item in control_records if item.supplier_id == supplier_id)
    if not records:
        if control_records:
            return result(rule.mismatch_outcome, "EVIDENCE_SCOPE_MISMATCH", control_records)
        return result(rule.missing_outcome, "EVIDENCE_MISSING")
    # Conflicts must be resolved through an audited replacement, never newest-wins.
    signatures = {(item.outcome, item.manufacturer, item.manufacturer_part_number,
                   item.coverage_confirmed, item.effective_from, item.expires_on, item.permanent)
                  for item in records}
    if len(signatures) > 1:
        return result("REVIEW_REQUIRED", "EVIDENCE_CONFLICT", records)
    item = records[0]
    if not item.coverage_confirmed:
        return result("REVIEW_REQUIRED", "COVERAGE_UNCONFIRMED", records)
    expected = dict(supplier_id=supplier_id, manufacturer=manufacturer,
                    manufacturer_part_number=manufacturer_part_number)
    if any(getattr(item, name) != expected[name] for name in rule.matching_fields):
        return result(rule.mismatch_outcome, "EVIDENCE_SCOPE_MISMATCH", records)
    today = evaluated_at.astimezone(ZoneInfo("Asia/Singapore")).date()
    if item.effective_from and item.effective_from > today:
        return result(rule.missing_outcome, "EVIDENCE_NOT_YET_EFFECTIVE", records)
    if item.expires_on and item.expires_on < today:
        return result(rule.expired_outcome, "EVIDENCE_EXPIRED", records)
    if not rule.allow_unspecified_validity and (
        item.effective_from is None or (item.expires_on is None and not item.permanent)
    ):
        return result("REVIEW_REQUIRED", "VALIDITY_UNSPECIFIED", records)
    return result(item.outcome, "EVIDENCE_CONFIRMED", records)
