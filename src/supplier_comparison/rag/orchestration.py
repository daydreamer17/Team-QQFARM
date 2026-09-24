"""Version-bound policy planning and fail-closed, requirement-level retrieval.

The catalog is built from approved policy clauses, never evaluation labels.
This service retrieves policy evidence only; READY does not mean compliant.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

from .contracts import FrozenModel, PolicyRetriever, RetrievalRequest, RetrievalResult, RetrievalStatus
from .manifest import LoadedPolicyManifest, load_policy_manifest

REVIEWED_POLICY_VERSION = "2026.07.1"
REVIEWED_MANIFEST_SHA256 = "c509e72538c92e02ba21a4807730fa29ade156d185ebef4b9a8aebd9afbed342"


class PlanningContext(FrozenModel):
    task_id: str = Field(min_length=1, max_length=64)
    task_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1, max_length=64)
    policy_set_version: str
    policy_index_version: str = Field(min_length=1)
    category: str
    region: str
    evaluated_at: datetime
    currency: str = "SGD"
    total_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    tax_mode: str = "NOT_APPLICABLE"

    @model_validator(mode="after")
    def timezone_required(self):
        if self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        return self


class ControlRequirement(FrozenModel):
    requirement_id: str
    control_code: str
    query: str
    clause_id: str
    document_id: str
    document_version: str
    policy_id: str
    section: str
    text: str
    content_sha256: str
    rule_parameters: dict[str, Any]
    evaluation_phase: str


class ControlPlan(FrozenModel):
    plan_id: str
    context: PlanningContext
    catalog_version: str
    manifest_sha256: str
    requirements: list[ControlRequirement]
    status: str
    reasons: list[str]
    manager_approval_required: bool | None


class RequirementCoverage(FrozenModel):
    requirement_id: str
    control_code: str
    status: str
    citation_ids: list[str]


class PolicyEvidenceBundle(FrozenModel):
    bundle_id: str
    plan: ControlPlan
    status: str
    reasons: list[str]
    retrieval_results: list[RetrievalResult]
    requirement_coverage: list[RequirementCoverage]
    requests_used: int


class PolicyOrchestrator:
    def __init__(self, manifest: LoadedPolicyManifest, retriever: PolicyRetriever):
        if (
            manifest.policy_set_version != REVIEWED_POLICY_VERSION
            or manifest.content_sha256 != REVIEWED_MANIFEST_SHA256
        ):
            raise ValueError("only the reviewed electronics policy catalog is supported")
        self.manifest = manifest
        self.retriever = retriever

    def plan(self, context: PlanningContext) -> ControlPlan:
        reasons = []
        if context.policy_set_version != self.manifest.policy_set_version:
            reasons.append("POLICY_VERSION_MISMATCH")
        requirements = []
        for doc in self.manifest.documents:
            if (context.category not in doc.categories or context.region not in doc.regions
                or context.evaluated_at < doc.effective_from
                or (doc.effective_to is not None and context.evaluated_at >= doc.effective_to)):
                continue
            for clause in doc.clauses:
                requirements.append(ControlRequirement(
                    requirement_id=clause.clause_id, control_code=clause.control_code,
                    query=f"What policy requirement applies: {clause.title}? {clause.text}",
                    clause_id=clause.clause_id, document_id=doc.document_id,
                    document_version=doc.document_version, policy_id=doc.policy_id,
                    section=clause.title, text=clause.text, content_sha256=clause.content_sha256,
                    rule_parameters=clause.rule_parameters,
                    evaluation_phase="APPROVAL" if clause.control_code == "AMOUNT_APPROVAL" else "PRE_RECOMMENDATION",
                ))
        if not requirements:
            reasons.append("POLICY_SCOPE_UNSUPPORTED")
        if context.tax_mode != "NOT_APPLICABLE":
            reasons.append("COST_MODE_UNSUPPORTED")
        if context.total_cost is None:
            reasons.append("TOTAL_COST_UNKNOWN")
        thresholds = [
            clause.rule_parameters
            for document in self.manifest.documents
            for clause in document.clauses
            if clause.control_code == "AMOUNT_APPROVAL"
            and {"currency", "threshold", "operator"} <= clause.rule_parameters.keys()
        ]
        threshold = thresholds[0] if len(thresholds) == 1 else {}
        if (
            threshold.get("currency") != context.currency
            or threshold.get("operator") != ">="
        ):
            reasons.append("APPROVAL_PARAMETER_INVALID")
        try:
            threshold_amount = Decimal(str(threshold["threshold"]))
        except Exception:
            threshold_amount = None
            if "APPROVAL_PARAMETER_INVALID" not in reasons:
                reasons.append("APPROVAL_PARAMETER_INVALID")
        manager = (
            None
            if reasons or threshold_amount is None
            else context.total_cost >= threshold_amount
        )
        return ControlPlan(plan_id=f"PLAN-{uuid4().hex}", context=context,
            catalog_version=f"{self.manifest.policy_set_id}/{self.manifest.policy_set_version}",
            manifest_sha256=self.manifest.content_sha256,
            requirements=requirements, status="REVIEW_REQUIRED" if reasons else "READY",
            reasons=reasons, manager_approval_required=manager)

    def retrieve(self, context: PlanningContext) -> PolicyEvidenceBundle:
        plan = self.plan(context)
        results = []
        requests_used = 0
        reasons = list(plan.reasons)
        supported: dict[str, list[str]] = {r.requirement_id: [] for r in plan.requirements}
        states = {r.requirement_id: "MISSING" for r in plan.requirements}

        def call(control: str, query: str) -> bool:
            nonlocal requests_used
            requests_used += 1
            request = RetrievalRequest(**context.model_dump(exclude={"currency", "total_cost", "tax_mode"}),
                                       query=query[:4000], required_control_codes=[control])
            try:
                result = self.retriever.retrieve(request)
                results.append(result)
                if (result.policy_set_version != context.policy_set_version
                    or result.policy_index_version != context.policy_index_version):
                    raise ValueError("result version mismatch")
                if result.status != RetrievalStatus.OK:
                    reasons.append(f"{control}:{result.status.value}")
                    for req in plan.requirements:
                        if req.control_code == control:
                            states[req.requirement_id] = result.status.value
                    return False
                for citation in result.citations:
                    req = next((r for r in plan.requirements if r.clause_id == citation.clause_id), None)
                    if req is None or req.control_code != control or any(
                        getattr(citation, name) != getattr(req, name) for name in
                        ("policy_id", "document_id", "document_version", "text", "content_sha256")):
                        raise ValueError("citation does not match catalog")
                    if citation.policy_set_version != context.policy_set_version:
                        raise ValueError("citation version mismatch")
                    supported[req.requirement_id].append(citation.citation_id)
                    states[req.requirement_id] = "SUPPORTED"
                return True
            except Exception:
                reasons.append(f"{control}:ERROR")
                for req in plan.requirements:
                    if req.control_code == control:
                        states[req.requirement_id] = "ERROR"
                return False

        if plan.status == "READY":
            for control in sorted({r.control_code for r in plan.requirements}):
                group = [r for r in plan.requirements if r.control_code == control]
                if not call(control, f"What are the {control.replace('_', ' ').lower()} requirements for electronics procurement?"):
                    continue
                for req in group:
                    if not supported[req.requirement_id] and not call(control, req.query):
                        break
        coverage = [RequirementCoverage(requirement_id=r.requirement_id, control_code=r.control_code,
                     status=states[r.requirement_id], citation_ids=supported[r.requirement_id])
                    for r in plan.requirements]
        if any(r.status != "SUPPORTED" for r in coverage):
            reasons.append("REQUIREMENT_COVERAGE_INCOMPLETE")
        return PolicyEvidenceBundle(bundle_id=f"BUNDLE-{uuid4().hex}", plan=plan,
            status="REVIEW_REQUIRED" if reasons else "READY", reasons=list(dict.fromkeys(reasons)),
            retrieval_results=results, requirement_coverage=coverage, requests_used=requests_used)


def load_reviewed_catalog(path: str | Path, *, allowed_root: str | Path) -> LoadedPolicyManifest:
    """Load a repository policy catalog that already passed manifest validation."""
    manifest = load_policy_manifest(path, allowed_root=allowed_root)
    if (
        manifest.policy_set_version != REVIEWED_POLICY_VERSION
        or manifest.content_sha256 != REVIEWED_MANIFEST_SHA256
    ):
        raise ValueError("unreviewed policy version")
    return manifest
