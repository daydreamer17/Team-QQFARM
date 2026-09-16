from __future__ import annotations

from datetime import datetime
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PolicyRetriever, RetrievalRequest, RetrievalStatus


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvaluationCase(EvalModel):
    case_id: str = Field(min_length=1)
    scenario: Literal["ANSWER", "NO_EVIDENCE", "OLD_VERSION", "CONFLICT"]
    query: str = Field(min_length=3)
    required_control_codes: list[str]
    category: str
    region: str
    evaluated_at: datetime
    expected_status: RetrievalStatus
    relevant_clause_ids: list[str]
    supported_clause_ids: list[str] = Field(default_factory=list)
    policy_set_version: str | None = None
    policy_index_version: str | None = None


class EvaluationCaseResult(EvalModel):
    case_id: str
    expected_status: RetrievalStatus
    actual_status: RetrievalStatus
    relevant_clause_ids: list[str]
    bm25_clause_ids: list[str]
    vector_clause_ids: list[str]
    fusion_clause_ids: list[str]
    rerank_clause_ids: list[str]
    citation_clause_ids: list[str]
    latency_ms: float
    error_code: str | None = None


class RetrievalEvaluationReport(EvalModel):
    case_count: int
    answer_case_count: int
    bm25_recall_at_10: float
    vector_recall_at_10: float
    fusion_recall_at_10: float
    rerank_recall_at_3: float
    citation_support_rate: float
    status_accuracy: float
    mean_latency_ms: float
    error_count: int
    cases: list[EvaluationCaseResult]


def evaluate_retrievals(
    cases: list[EvaluationCase],
    *,
    retriever: PolicyRetriever,
    policy_set_version: str,
    policy_index_version: str,
) -> RetrievalEvaluationReport:
    results: list[EvaluationCaseResult] = []
    recalls = {"bm25": [], "vector": [], "fusion": [], "rerank": []}
    supported_citations = 0
    returned_citations = 0
    for case in cases:
        request = RetrievalRequest(
            task_id=f"EVAL-{case.case_id}",
            task_revision=1,
            snapshot_id=f"EVAL-SNAPSHOT-{case.case_id}",
            policy_set_version=case.policy_set_version or policy_set_version,
            policy_index_version=case.policy_index_version or policy_index_version,
            query=case.query,
            required_control_codes=case.required_control_codes,
            category=case.category,
            region=case.region,
            evaluated_at=case.evaluated_at,
        )
        result = retriever.retrieve(request)
        bm25 = [
            candidate.clause_id
            for candidate in sorted(
                (item for item in result.candidates if item.bm25_rank is not None),
                key=lambda item: item.bm25_rank or 999,
            )[:10]
        ]
        vector = [
            candidate.clause_id
            for candidate in sorted(
                (item for item in result.candidates if item.vector_rank is not None),
                key=lambda item: item.vector_rank or 999,
            )[:10]
        ]
        fusion = [item.clause_id for item in sorted(result.candidates, key=lambda item: item.fusion_rank)[:10]]
        rerank = [citation.clause_id for citation in result.citations[:3]]
        relevant = set(case.relevant_clause_ids)
        if relevant:
            recalls["bm25"].append(_recall(bm25, relevant))
            recalls["vector"].append(_recall(vector, relevant))
            recalls["fusion"].append(_recall(fusion, relevant))
            recalls["rerank"].append(_recall(rerank, relevant))
            returned_citations += len(result.citations)
            supported = set(case.supported_clause_ids) or relevant
            supported_citations += sum(citation.clause_id in supported for citation in result.citations)
        results.append(
            EvaluationCaseResult(
                case_id=case.case_id,
                expected_status=case.expected_status,
                actual_status=result.status,
                relevant_clause_ids=case.relevant_clause_ids,
                bm25_clause_ids=bm25,
                vector_clause_ids=vector,
                fusion_clause_ids=fusion,
                rerank_clause_ids=rerank,
                citation_clause_ids=[citation.clause_id for citation in result.citations],
                latency_ms=result.latency_ms.get("total", 0.0),
                error_code=result.error_code,
            )
        )
    return RetrievalEvaluationReport(
        case_count=len(cases),
        answer_case_count=sum(bool(case.relevant_clause_ids) for case in cases),
        bm25_recall_at_10=_mean(recalls["bm25"]),
        vector_recall_at_10=_mean(recalls["vector"]),
        fusion_recall_at_10=_mean(recalls["fusion"]),
        rerank_recall_at_3=_mean(recalls["rerank"]),
        citation_support_rate=(supported_citations / returned_citations if returned_citations else 1.0),
        status_accuracy=(
            sum(item.actual_status == item.expected_status for item in results) / len(results)
            if results
            else 1.0
        ),
        mean_latency_ms=_mean([item.latency_ms for item in results]),
        error_count=sum(item.actual_status == RetrievalStatus.ERROR for item in results),
        cases=results,
    )


def _recall(retrieved: list[str], relevant: set[str]) -> float:
    return len(set(retrieved) & relevant) / len(relevant)


def _mean(values: list[float]) -> float:
    return fmean(values) if values else 1.0
