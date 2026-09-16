from __future__ import annotations

from datetime import datetime, timezone

from supplier_comparison.rag.clients import (
    FixedEmbeddingClient,
    FixedRerankClient,
    ModelClientError,
)
from supplier_comparison.rag.contracts import RetrievalRequest, RetrievalStatus
from supplier_comparison.rag.repository import IndexContext, ScoredClause, StoredPolicyClause
from supplier_comparison.rag.retriever import FixedPolicyRetriever, HybridPolicyRetriever


def _request(*codes: str) -> RetrievalRequest:
    return RetrievalRequest(
        task_id="TASK-1",
        task_revision=3,
        snapshot_id="SNAP-1",
        policy_set_version="2026.09.1",
        policy_index_version="pidx-test",
        query="Which quote fields and shipping costs are required?",
        required_control_codes=list(codes),
        category="Electronics",
        region="SG",
        evaluated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )


def _clause(clause_id: str, text: str, control: str, *, params=None) -> StoredPolicyClause:
    return StoredPolicyClause(
        policy_set_version="2026.09.1",
        policy_id="POL-1",
        document_id="DOC-1",
        document_version="1.0.0",
        clause_id=clause_id,
        section=f"Section {clause_id}",
        text=text,
        content_sha256=(clause_id[-1].lower() if clause_id[-1].isalnum() else "a") * 64,
        control_code=control,
        rule_parameters=params or {},
    )


class FakeRepository:
    def __init__(self, clauses: list[StoredPolicyClause], vector_order: list[str]) -> None:
        self.clauses = clauses
        self.vector_order = vector_order
        self.saved = []
        self.validation_error: str | None = None

    def load_published_clauses(self, request):
        self.request = request
        return IndexContext(
            embedding_model="BAAI/bge-m3",
            embedding_dimension=2,
            clauses=self.clauses,
        )

    def vector_search(self, *, index_version, allowed_clause_ids, query_vector, limit):
        del index_version, query_vector
        allowed = set(allowed_clause_ids)
        return [
            ScoredClause(clause_id=clause_id, score=1 - index / 10)
            for index, clause_id in enumerate(self.vector_order[:limit], start=1)
            if clause_id in allowed
        ]

    def validate_citations(self, *, index_version, citations):
        del index_version, citations
        if self.validation_error:
            raise ValueError(self.validation_error)

    def save_trace(self, request, result):
        self.saved.append((request, result))


def _retriever(repository: FakeRepository, *, rerank_indexes=None) -> HybridPolicyRetriever:
    embedding = FixedEmbeddingClient(
        {_request("TOTAL_COST").query: [0.1, 0.2]}, model_id="BAAI/bge-m3", dimension=2
    )
    indexes = [1, 0, 2] if rerank_indexes is None else rerank_indexes
    rerank = FixedRerankClient(
        indexes,
        scores=[0.95, 0.9, 0.8][: len(indexes)],
        model_id="BAAI/bge-reranker-v2-m3",
    )
    return HybridPolicyRetriever(repository, embedding, rerank)


def test_hybrid_retrieval_records_all_ranks_and_top_three() -> None:
    clauses = [
        _clause("CLAUSE-1", "Every quote must state shipping costs.", "TOTAL_COST"),
        _clause("CLAUSE-2", "Currency and unit price are mandatory quote fields.", "TOTAL_COST"),
        _clause("CLAUSE-3", "Approval is required above SGD 10000.", "TOTAL_COST"),
        _clause("CLAUSE-4", "RoHS declarations apply to electronics.", "TOTAL_COST"),
    ]
    repository = FakeRepository(clauses, ["CLAUSE-2", "CLAUSE-1", "CLAUSE-4"])
    result = _retriever(repository).retrieve(_request("TOTAL_COST"))

    assert result.status == RetrievalStatus.OK
    assert len(result.citations) == 3
    assert result.citations[0].rerank_rank == 1
    assert result.citations[0].clause_id in {"CLAUSE-1", "CLAUSE-2", "CLAUSE-3", "CLAUSE-4"}
    assert result.citations[0].fusion_rank >= 1
    assert result.candidates[0].bm25_rank is not None
    assert result.covered_control_codes == ["TOTAL_COST"]
    assert repository.saved[0][1] == result


def test_missing_required_control_returns_no_evidence_with_partial_trace() -> None:
    clauses = [_clause("CLAUSE-1", "Shipping costs are required.", "TOTAL_COST")]
    repository = FakeRepository(clauses, ["CLAUSE-1"])
    result = _retriever(repository, rerank_indexes=[0]).retrieve(
        _request("TOTAL_COST", "ROHS_COMPLIANCE")
    )
    assert result.status == RetrievalStatus.NO_EVIDENCE
    assert result.covered_control_codes == ["TOTAL_COST"]
    assert result.missing_control_codes == ["ROHS_COMPLIANCE"]
    assert len(result.citations) == 1


def test_conflicting_active_rule_parameters_return_conflict() -> None:
    clauses = [
        _clause("CLAUSE-1", "Approval is required above SGD 10000.", "AMOUNT_APPROVAL", params={"threshold": 10000}),
        _clause("CLAUSE-2", "Approval is required above SGD 12000.", "AMOUNT_APPROVAL", params={"threshold": 12000}),
    ]
    result = _retriever(FakeRepository(clauses, ["CLAUSE-1", "CLAUSE-2"]), rerank_indexes=[0, 1]).retrieve(
        _request("AMOUNT_APPROVAL")
    )
    assert result.status == RetrievalStatus.CONFLICT


def test_distinct_rule_parameter_names_do_not_create_false_conflict() -> None:
    clauses = [
        _clause(
            "CLAUSE-1",
            "Approval starts at SGD 10000.",
            "AMOUNT_APPROVAL",
            params={"threshold": 10000},
        ),
        _clause(
            "CLAUSE-2",
            "Approval evidence records the manager.",
            "AMOUNT_APPROVAL",
            params={"required_evidence": "manager"},
        ),
    ]
    result = _retriever(
        FakeRepository(clauses, ["CLAUSE-1", "CLAUSE-2"]), rerank_indexes=[0, 1]
    ).retrieve(_request("AMOUNT_APPROVAL"))
    assert result.status == RetrievalStatus.OK


def test_invalid_rerank_shape_or_tampered_citation_returns_error_and_trace() -> None:
    clauses = [
        _clause("CLAUSE-1", "Shipping costs are required.", "TOTAL_COST"),
        _clause("CLAUSE-2", "Currency is required.", "TOTAL_COST"),
    ]
    duplicate_repository = FakeRepository(clauses, ["CLAUSE-1", "CLAUSE-2"])
    duplicate = _retriever(duplicate_repository, rerank_indexes=[0, 0]).retrieve(
        _request("TOTAL_COST")
    )
    assert duplicate.status == RetrievalStatus.ERROR
    assert duplicate.error_code == "rerank_response_invalid"
    assert duplicate.citations == []
    assert duplicate.candidates

    tampered_repository = FakeRepository(clauses, ["CLAUSE-1", "CLAUSE-2"])
    tampered_repository.validation_error = "hash mismatch"
    tampered = _retriever(tampered_repository, rerank_indexes=[0, 1]).retrieve(
        _request("TOTAL_COST")
    )
    assert tampered.status == RetrievalStatus.ERROR
    assert tampered.error_code == "citation_validation_failed"


def test_fixed_retriever_rejects_cross_version_reuse() -> None:
    repository = FakeRepository(
        [_clause("CLAUSE-1", "Shipping costs are required.", "TOTAL_COST")],
        ["CLAUSE-1"],
    )
    result = _retriever(repository, rerank_indexes=[0]).retrieve(_request("TOTAL_COST"))
    fixed = FixedPolicyRetriever(result)
    assert fixed.retrieve(_request("TOTAL_COST")) == result
    mismatch = _request("TOTAL_COST").model_copy(update={"policy_index_version": "other"})
    try:
        fixed.retrieve(mismatch)
    except ValueError as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("cross-version fixed result must be rejected")


def test_embedding_failure_persists_actual_attempt_count_and_bm25_diagnostics() -> None:
    clauses = [_clause("CLAUSE-1", "Shipping costs are required.", "TOTAL_COST")]
    repository = FakeRepository(clauses, ["CLAUSE-1"])

    class FailingEmbedding:
        model_id = "BAAI/bge-m3"
        dimension = 2

        def embed(self, texts):
            del texts
            raise ModelClientError("embedding transport failed", attempts=2)

    result = HybridPolicyRetriever(
        repository,
        FailingEmbedding(),
        FixedRerankClient([0], scores=[1.0], model_id="fixed-rerank"),
    ).retrieve(_request("TOTAL_COST"))
    assert result.status == RetrievalStatus.ERROR
    assert result.attempts["embedding"] == 2
    assert result.candidates[0].bm25_rank == 1
