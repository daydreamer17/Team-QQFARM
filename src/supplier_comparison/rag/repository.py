from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from .contracts import PolicyCitation, RetrievalRequest, RetrievalResult
from .models import (
    PolicyClause,
    PolicyClauseEmbedding,
    PolicyDocument,
    PolicyIndex,
    PolicySet,
    RetrievalTrace,
)


@dataclass(frozen=True, slots=True)
class StoredPolicyClause:
    policy_set_version: str
    policy_id: str
    document_id: str
    document_version: str
    clause_id: str
    section: str
    text: str
    content_sha256: str
    control_code: str
    rule_parameters: dict


@dataclass(frozen=True, slots=True)
class IndexContext:
    embedding_model: str
    embedding_dimension: int
    clauses: list[StoredPolicyClause]


@dataclass(frozen=True, slots=True)
class ScoredClause:
    clause_id: str
    score: float


class PolicyRepository(Protocol):
    def load_published_clauses(self, request: RetrievalRequest) -> IndexContext: ...

    def vector_search(
        self,
        *,
        index_version: str,
        allowed_clause_ids: list[str],
        query_vector: list[float],
        limit: int,
    ) -> list[ScoredClause]: ...

    def validate_citations(
        self, *, index_version: str, citations: list[PolicyCitation]
    ) -> None: ...

    def save_trace(self, request: RetrievalRequest, result: RetrievalResult) -> None: ...


class SQLPolicyRepository:
    """PostgreSQL repository; vector ordering is exact and has no ANN index."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def load_published_clauses(self, request: RetrievalRequest) -> IndexContext:
        with self._sessions() as session:
            index_rows = list(
                session.execute(
                    select(PolicyIndex, PolicySet)
                    .join(PolicySet, PolicyIndex.policy_set_record_id == PolicySet.policy_set_record_id)
                    .where(
                        PolicyIndex.policy_index_version == request.policy_index_version,
                        PolicyIndex.status == "PUBLISHED",
                        PolicySet.policy_set_version == request.policy_set_version,
                        PolicySet.status == "PUBLISHED",
                    )
                )
            )
            if len(index_rows) != 1:
                raise ValueError("requested policy set/index is not uniquely published")
            index, _policy_set = index_rows[0]
            filters = [
                PolicyClause.policy_set_record_id == index.policy_set_record_id,
                PolicyDocument.effective_from <= request.evaluated_at,
                or_(
                    PolicyDocument.effective_to.is_(None),
                    PolicyDocument.effective_to > request.evaluated_at,
                ),
                cast(PolicyDocument.categories, JSONB).contains([request.category]),
                cast(PolicyDocument.regions, JSONB).contains([request.region]),
            ]
            if request.required_control_codes:
                filters.append(PolicyClause.control_code.in_(request.required_control_codes))
            rows = list(
                session.execute(
                    select(PolicyClause, PolicyDocument)
                    .join(
                        PolicyDocument,
                        PolicyClause.policy_document_record_id
                        == PolicyDocument.policy_document_record_id,
                    )
                    .where(*filters)
                    .order_by(PolicyClause.clause_id)
                )
            )
            clauses = [
                StoredPolicyClause(
                    policy_set_version=request.policy_set_version,
                    policy_id=document.policy_id,
                    document_id=document.document_id,
                    document_version=document.document_version,
                    clause_id=clause.clause_id,
                    section=clause.section,
                    text=clause.text,
                    content_sha256=clause.content_sha256,
                    control_code=clause.control_code,
                    rule_parameters=clause.rule_parameters,
                )
                for clause, document in rows
            ]
            return IndexContext(
                embedding_model=index.embedding_model,
                embedding_dimension=index.embedding_dimension,
                clauses=clauses,
            )

    def vector_search(
        self,
        *,
        index_version: str,
        allowed_clause_ids: list[str],
        query_vector: list[float],
        limit: int,
    ) -> list[ScoredClause]:
        if not allowed_clause_ids:
            return []
        distance = PolicyClauseEmbedding.embedding.cosine_distance(query_vector)
        with self._sessions() as session:
            rows = list(
                session.execute(
                    select(PolicyClause.clause_id, distance.label("distance"))
                    .join(
                        PolicyClauseEmbedding,
                        PolicyClauseEmbedding.policy_clause_record_id
                        == PolicyClause.policy_clause_record_id,
                    )
                    .where(
                        PolicyClauseEmbedding.policy_index_version == index_version,
                        PolicyClause.clause_id.in_(allowed_clause_ids),
                    )
                    .order_by(distance, PolicyClause.clause_id)
                    .limit(limit)
                )
            )
        return [ScoredClause(clause_id, 1.0 - float(distance_value)) for clause_id, distance_value in rows]

    def validate_citations(
        self, *, index_version: str, citations: list[PolicyCitation]
    ) -> None:
        if not citations:
            return
        clause_ids = [citation.clause_id for citation in citations]
        with self._sessions() as session:
            rows = list(
                session.execute(
                    select(PolicyClause, PolicyDocument, PolicySet)
                    .join(
                        PolicyDocument,
                        PolicyClause.policy_document_record_id
                        == PolicyDocument.policy_document_record_id,
                    )
                    .join(PolicySet, PolicyClause.policy_set_record_id == PolicySet.policy_set_record_id)
                    .join(
                        PolicyClauseEmbedding,
                        PolicyClauseEmbedding.policy_clause_record_id
                        == PolicyClause.policy_clause_record_id,
                    )
                    .where(
                        PolicyClauseEmbedding.policy_index_version == index_version,
                        PolicyClause.clause_id.in_(clause_ids),
                    )
                )
            )
        persisted = {clause.clause_id: (clause, document, policy_set) for clause, document, policy_set in rows}
        if len(persisted) != len(citations):
            raise ValueError("citation does not map uniquely to the published index")
        for citation in citations:
            clause, document, policy_set = persisted[citation.clause_id]
            expected = (
                policy_set.policy_set_version,
                document.policy_id,
                document.document_id,
                document.document_version,
                clause.section,
                clause.text,
                clause.content_sha256,
                clause.control_code,
            )
            actual = (
                citation.policy_set_version,
                citation.policy_id,
                citation.document_id,
                citation.document_version,
                citation.section,
                citation.text,
                citation.content_sha256,
                citation.control_code,
            )
            if actual != expected:
                raise ValueError(f"citation content/hash mismatch: {citation.clause_id}")

    def save_trace(self, request: RetrievalRequest, result: RetrievalResult) -> None:
        with self._sessions.begin() as session:
            session.add(
                RetrievalTrace(
                    retrieval_id=result.retrieval_id,
                    task_id=request.task_id,
                    task_revision=request.task_revision,
                    snapshot_id=request.snapshot_id,
                    policy_set_version=result.policy_set_version,
                    policy_index_version=result.policy_index_version,
                    embedding_model=result.embedding_model,
                    rerank_model=result.rerank_model,
                    status=result.status.value,
                    query=request.query,
                    filters=result.filters,
                    required_control_codes=request.required_control_codes,
                    covered_control_codes=result.covered_control_codes,
                    missing_control_codes=result.missing_control_codes,
                    candidates=[item.model_dump(mode="json") for item in result.candidates],
                    citations=[item.model_dump(mode="json") for item in result.citations],
                    latency_ms=result.latency_ms,
                    attempts=result.attempts,
                    error_code=result.error_code,
                )
            )
