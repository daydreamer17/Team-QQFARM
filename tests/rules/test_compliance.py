from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from supplier_comparison.rules.compliance import (
    ComplianceEvidence, ExecutableRuleParameters, evaluate_compliance_rule,
)


NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def parameters(**changes):
    return dict(version="compliance-rule/1.0", control_code="ROHS_COMPLIANCE",
                reviewed_by="reviewer", reviewed_at=NOW.isoformat(),
                matching_fields=["supplier_id", "manufacturer", "manufacturer_part_number"],
                date_basis="EVALUATED_AT", missing_outcome="REVIEW_REQUIRED",
                expired_outcome="FAIL", mismatch_outcome="FAIL",
                execution_stage="BEFORE_RECOMMENDATION", **changes)


def evidence(**changes):
    return ComplianceEvidence(**(dict(evidence_id="ev-1", control_code="ROHS_COMPLIANCE",
        supplier_id="supplier-1", manufacturer="Maker", manufacturer_part_number="Part",
        coverage_confirmed=True, outcome="PASS", effective_from=date(2026, 1, 1),
        expires_on=date(2026, 12, 31), source_refs=("file-version:1",)) | changes))


def evaluate(params=None, records=(), **changes):
    return evaluate_compliance_rule(**(dict(clause_id="clause-1", parameters=params,
        supplier_id="supplier-1", manufacturer="Maker", manufacturer_part_number="Part",
        evaluated_at=NOW, evidence=records) | changes))


def test_missing_and_unreviewed_parameters_are_not_invented():
    assert evaluate().status == "NOT_EVALUATED"
    assert evaluate({"threshold": "100"}).status == "NOT_EVALUATED"
    assert evaluate(parameters() | {"reviewed_by": ""}).status == "NOT_EVALUATED"


@pytest.mark.parametrize(("changes", "status", "reason"), [
    ({}, "PASS", "EVIDENCE_CONFIRMED"),
    ({"coverage_confirmed": False}, "REVIEW_REQUIRED", "COVERAGE_UNCONFIRMED"),
    ({"expires_on": None}, "REVIEW_REQUIRED", "VALIDITY_UNSPECIFIED"),
    ({"effective_from": None}, "REVIEW_REQUIRED", "VALIDITY_UNSPECIFIED"),
    ({"expires_on": date(2026, 9, 23)}, "FAIL", "EVIDENCE_EXPIRED"),
    ({"manufacturer_part_number": "Other"}, "FAIL", "EVIDENCE_SCOPE_MISMATCH"),
    ({"supplier_id": "other"}, "FAIL", "EVIDENCE_SCOPE_MISMATCH"),
])
def test_evidence_identity_validity_and_coverage(changes, status, reason):
    result = evaluate(parameters(), (evidence(**changes),))
    assert result.status == status
    assert reason in result.reason_codes


def test_conflicting_evidence_requires_review_and_preserves_references():
    result = evaluate(parameters(), (evidence(), evidence(evidence_id="ev-2", outcome="FAIL")))
    assert result.status == "REVIEW_REQUIRED"
    assert result.evidence_ids == ("ev-1", "ev-2")
    assert result.source_refs == ("file-version:1",)


def test_amount_threshold_is_an_action_not_purchase_approval():
    params = parameters() | dict(control_code="AMOUNT_APPROVAL", matching_fields=[],
        currency="SGD", monetary_basis="TOTAL_COST", threshold="100", operator="GTE",
        action="MANAGER_REVIEW", execution_stage="AFTER_SELECTION")
    assert evaluate(params, amount=Decimal("100"), currency="SGD").status == "NOT_APPLICABLE"
    result = evaluate(params, amount=Decimal("100"), currency="SGD", execution_stage="AFTER_SELECTION")
    assert result.status == "PASS"
    assert result.triggered is True
    assert result.action == "MANAGER_REVIEW"
    assert evaluate(params, amount=None, currency="SGD", execution_stage="AFTER_SELECTION").status == "REVIEW_REQUIRED"
    assert evaluate(params, amount=Decimal("100"), currency="USD", execution_stage="AFTER_SELECTION").status == "REVIEW_REQUIRED"


def test_binary_float_threshold_is_not_executable():
    params = parameters() | dict(control_code="AMOUNT_APPROVAL", matching_fields=[],
        currency="SGD", monetary_basis="TOTAL_COST", threshold=0.1, operator="GT", action="REVIEW")
    assert evaluate(params).status == "NOT_EVALUATED"


def test_declared_executable_import_parameters_must_be_valid_and_match_control():
    from supplier_comparison.rag.uploads import PolicyDraftClauseInput
    from pydantic import ValidationError
    base = dict(clause_id="clause", title="RoHS", text="Must match RoHS evidence.",
                control_code="ROHS_COMPLIANCE")
    assert PolicyDraftClauseInput(**base, rule_parameters={}).rule_parameters == {}
    assert PolicyDraftClauseInput(**base, rule_parameters=parameters())
    with pytest.raises(ValidationError):
        PolicyDraftClauseInput(**base, rule_parameters={"version": "compliance-rule/1.0"})
    with pytest.raises(ValidationError):
        PolicyDraftClauseInput(**base, rule_parameters=parameters() | {"control_code": "APPROVED_SUPPLIER"})


def test_validity_override_is_explicit_and_permanent_does_not_invent_start_date():
    records = (evidence(expires_on=None),)
    assert evaluate(parameters(), records).status == "REVIEW_REQUIRED"
    assert evaluate(parameters(allow_unspecified_validity=True), records).status == "PASS"
    assert evaluate(parameters(), (evidence(expires_on=None, permanent=True),)).status == "PASS"
    assert evaluate(parameters(), (evidence(expires_on=None, permanent=True, effective_from=None),)).status == "REVIEW_REQUIRED"


def test_missing_task_supplier_identity_cannot_be_a_hard_failure():
    assert evaluate(parameters() | {"missing_outcome": "FAIL"}, supplier_id=None).status == "REVIEW_REQUIRED"
