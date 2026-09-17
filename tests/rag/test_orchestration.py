from datetime import datetime, timezone
from pathlib import Path

import pytest

from supplier_comparison.rag.contracts import PolicyCitation, RetrievalResult
from supplier_comparison.rag.orchestration import PlanningContext, PolicyOrchestrator, load_reviewed_catalog


ROOT = Path(__file__).resolve().parents[2] / "data/policies"


def context(**changes):
    data = dict(task_id="task-demo", task_revision=1, snapshot_id="snap-demo",
                policy_set_version="2026.09.2", policy_index_version="pidx-test",
                category="Electronics", region="SG", evaluated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
                total_cost="7000.00")
    return PlanningContext(**(data | changes))


class Retriever:
    def __init__(self, manifest, mode="OK"):
        self.manifest, self.mode, self.calls = manifest, mode, []

    def retrieve(self, request):
        self.calls.append(request)
        if self.mode == "RAISE":
            raise RuntimeError("private provider error")
        code = request.required_control_codes[0]
        rows = [(d, c) for d in self.manifest.documents for c in d.clauses if c.control_code == code]
        # Requirement query must promote the matching authoritative clause.
        rows.sort(key=lambda row: (self.mode == "STUCK" or row[1].text not in request.query, row[1].clause_id))
        citations = [PolicyCitation(citation_id=f"cit-{len(self.calls)}-{i}", retrieval_id=f"ret-{len(self.calls)}",
            policy_set_version=request.policy_set_version, policy_id=d.policy_id, document_id=d.document_id,
            document_version=d.document_version, clause_id=c.clause_id, section=c.title,
            text=c.text if self.mode != "TAMPER" else "tampered", content_sha256=c.content_sha256,
            control_code=code, fusion_rank=i+1, fusion_score=1, rerank_rank=i+1, rerank_score=1)
            for i, (d, c) in enumerate(rows[:3])] if self.mode in {"OK", "TAMPER", "STUCK"} else []
        return RetrievalResult(retrieval_id=f"ret-{len(self.calls)}", status="OK" if citations else self.mode,
            policy_set_version=request.policy_set_version, policy_index_version=request.policy_index_version,
            embedding_model="test", rerank_model="test", filters={}, covered_control_codes=[code] if citations else [],
            missing_control_codes=[] if citations else [code], citations=citations, candidates=[], latency_ms={}, attempts={})


def service(mode="OK"):
    manifest = load_reviewed_catalog(ROOT / "electronics-v2/manifest.json", allowed_root=ROOT)
    retriever = Retriever(manifest, mode)
    return PolicyOrchestrator(manifest, retriever), retriever


def test_all_six_controls_and_24_requirements_are_retrieved_with_bounded_supplement():
    orchestrator, retriever = service()
    result = orchestrator.retrieve(context())
    assert result.status == "READY"
    assert len(result.requirement_coverage) == 24
    assert all(c.status == "SUPPORTED" for c in result.requirement_coverage)
    assert 6 < result.requests_used <= 30
    assert all(len(r.required_control_codes) == 1 for r in retriever.calls)
    assert result.plan.manager_approval_required is False


@pytest.mark.parametrize("amount,expected", [("9999.99", False), ("10000.00", True), ("10000.01", True)])
def test_manager_threshold_is_inclusive(amount, expected):
    orchestrator, _ = service()
    assert orchestrator.plan(context(total_cost=amount)).manager_approval_required is expected


@pytest.mark.parametrize("changes", [{"total_cost": None}, {"currency": "USD"}, {"tax_mode": "UNKNOWN"},
    {"region": "US"}, {"category": "Packaging"}, {"policy_set_version": "2026.09.1"},
    {"evaluated_at": datetime(2025, 1, 1, tzinfo=timezone.utc)}])
def test_invalid_preconditions_do_not_call_models(changes):
    orchestrator, retriever = service()
    result = orchestrator.retrieve(context(**changes))
    assert result.status == "REVIEW_REQUIRED"
    assert not retriever.calls
    assert result.plan.manager_approval_required is None


@pytest.mark.parametrize("mode", ["NO_EVIDENCE", "CONFLICT", "ERROR", "TAMPER", "RAISE"])
def test_errors_and_untrusted_citations_fail_closed(mode):
    orchestrator, _ = service(mode)
    result = orchestrator.retrieve(context())
    assert result.status == "REVIEW_REQUIRED"
    assert result.requests_used == 6
    assert result.reasons


def test_old_and_changed_catalogs_are_rejected():
    with pytest.raises(ValueError):
        load_reviewed_catalog(ROOT / "electronics-v1/manifest.json", allowed_root=ROOT)
    orchestrator, retriever = service()
    modified = orchestrator.manifest.model_copy(update={"content_sha256": "0" * 64})
    with pytest.raises(ValueError):
        PolicyOrchestrator(modified, retriever)


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        context(evaluated_at=datetime(2026, 9, 16))


def test_persistent_missing_requirements_are_not_hidden_by_control_coverage():
    orchestrator, _ = service("STUCK")
    result = orchestrator.retrieve(context())
    assert result.status == "REVIEW_REQUIRED"
    assert any(c.status == "MISSING" for c in result.requirement_coverage)
    assert result.requests_used <= 30


def test_index_mismatch_is_not_accepted():
    orchestrator, retriever = service()
    original = retriever.retrieve
    retriever.retrieve = lambda request: original(request).model_copy(update={"policy_index_version": "wrong"})
    assert orchestrator.retrieve(context()).status == "REVIEW_REQUIRED"
