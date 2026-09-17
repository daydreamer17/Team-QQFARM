from __future__ import annotations

import json
import time
from collections import defaultdict
from uuid import uuid4

from rank_bm25 import BM25Okapi

from .clients import EmbeddingClient, RerankClient, is_transient_model_error
from .contracts import (
    PolicyCitation,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
)
from .preprocessing import tokenize_policy_text
from .ranking import reciprocal_rank_fusion, validate_rerank_indexes
from .repository import PolicyRepository, StoredPolicyClause


class FixedPolicyRetriever:
    """Offline adapter for compliance and graph integration tests."""

    def __init__(self, result: RetrievalResult) -> None:
        self._result = result
        self.calls: list[RetrievalRequest] = []

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self.calls.append(request)
        if (
            request.policy_set_version != self._result.policy_set_version
            or request.policy_index_version != self._result.policy_index_version
        ):
            raise ValueError("fixed retrieval result cannot be reused across a policy version")
        return self._result


class HybridPolicyRetriever:
    def __init__(
        self,
        repository: PolicyRepository,
        embedding_client: EmbeddingClient,
        rerank_client: RerankClient,
        *,
        bm25_limit: int = 10,
        vector_limit: int = 10,
        final_limit: int = 3,
        rrf_k: int = 60,
    ) -> None:
        self._repository = repository
        self._embedding = embedding_client
        self._rerank = rerank_client
        self._bm25_limit = bm25_limit
        self._vector_limit = vector_limit
        self._final_limit = final_limit
        self._rrf_k = rrf_k

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        retrieval_id = f"RET-{uuid4().hex}"
        total_started = time.perf_counter()
        timings: dict[str, float] = {}
        attempts = {"embedding": 0, "rerank": 0}
        filters = {
            "control_codes": request.required_control_codes,
            "category": request.category,
            "region": request.region,
            "effective_from_inclusive": request.evaluated_at.isoformat(),
            "effective_to_exclusive": request.evaluated_at.isoformat(),
        }
        candidates: list[RetrievalCandidate] = []
        try:
            context = self._repository.load_published_clauses(request)
        except Exception:
            return self._save_error(
                request,
                retrieval_id,
                filters,
                candidates,
                timings,
                attempts,
                total_started,
                "policy_index_unavailable",
            )
        if (
            context.embedding_model != self._embedding.model_id
            or context.embedding_dimension != self._embedding.dimension
        ):
            return self._save_error(
                request,
                retrieval_id,
                filters,
                candidates,
                timings,
                attempts,
                total_started,
                "embedding_index_mismatch",
            )
        clauses = context.clauses
        if not clauses:
            result = self._result(
                request,
                retrieval_id,
                RetrievalStatus.NO_EVIDENCE,
                filters,
                [],
                [],
                sorted(request.required_control_codes),
                candidates,
                timings,
                attempts,
                total_started,
            )
            self._repository.save_trace(request, result)
            return result

        by_id = {clause.clause_id: clause for clause in clauses}
        bm25_started = time.perf_counter()
        bm25_ids, bm25_scores = _bm25_rank(request.query, clauses, limit=self._bm25_limit)
        timings["bm25"] = _elapsed_ms(bm25_started)
        try:
            embedding = self._embedding.embed([request.query])
            attempts["embedding"] = embedding.attempts
            timings["embedding"] = embedding.latency_ms
            vector_started = time.perf_counter()
            vector_rows = self._repository.vector_search(
                index_version=request.policy_index_version,
                allowed_clause_ids=list(by_id),
                query_vector=embedding.vectors[0],
                limit=self._vector_limit,
            )
            timings["vector"] = _elapsed_ms(vector_started)
        except Exception as exc:
            attempts["embedding"] = int(getattr(exc, "attempts", attempts["embedding"]))
            fused = reciprocal_rank_fusion(
                bm25_clause_ids=bm25_ids, vector_clause_ids=[], k=self._rrf_k
            )
            candidates = _candidate_contracts(fused, bm25_scores, {}, {})
            return self._save_error(
                request,
                retrieval_id,
                filters,
                candidates,
                timings,
                attempts,
                total_started,
                "embedding_transient_error" if is_transient_model_error(exc) else "embedding_or_vector_failed",
            )
        vector_ids = [row.clause_id for row in vector_rows]
        vector_scores = {row.clause_id: row.score for row in vector_rows}
        fused = reciprocal_rank_fusion(
            bm25_clause_ids=bm25_ids,
            vector_clause_ids=vector_ids,
            k=self._rrf_k,
        )
        fused_ids = [item.clause_id for item in fused]
        try:
            rerank = self._rerank.rerank(
                request.query,
                [by_id[clause_id].text for clause_id in fused_ids],
                top_n=min(self._final_limit, len(fused_ids)),
            )
            attempts["rerank"] = rerank.attempts
            timings["rerank"] = rerank.latency_ms
            indexes = validate_rerank_indexes(
                [item.index for item in rerank.items], candidate_count=len(fused_ids)
            )
            if len(indexes) != min(self._final_limit, len(fused_ids)):
                raise ValueError("rerank response does not contain requested top_n")
        except Exception as exc:
            attempts["rerank"] = int(getattr(exc, "attempts", attempts["rerank"]))
            candidates = _candidate_contracts(fused, bm25_scores, vector_scores, {})
            return self._save_error(
                request,
                retrieval_id,
                filters,
                candidates,
                timings,
                attempts,
                total_started,
                "rerank_transient_error" if is_transient_model_error(exc) else "rerank_response_invalid",
            )

        rerank_by_clause = {
            fused_ids[item.index]: (rank, item.score)
            for rank, item in enumerate(rerank.items, start=1)
        }
        candidates = _candidate_contracts(fused, bm25_scores, vector_scores, rerank_by_clause)
        candidate_by_id = {item.clause_id: item for item in candidates}
        citations = []
        for rank, item in enumerate(rerank.items, start=1):
            clause = by_id[fused_ids[item.index]]
            stage = candidate_by_id[clause.clause_id]
            citations.append(
                PolicyCitation(
                    citation_id=f"CIT-{retrieval_id.removeprefix('RET-')}-{rank}",
                    retrieval_id=retrieval_id,
                    policy_set_version=clause.policy_set_version,
                    policy_id=clause.policy_id,
                    document_id=clause.document_id,
                    document_version=clause.document_version,
                    clause_id=clause.clause_id,
                    section=clause.section,
                    text=clause.text,
                    content_sha256=clause.content_sha256,
                    control_code=clause.control_code,
                    bm25_rank=stage.bm25_rank,
                    bm25_score=stage.bm25_score,
                    vector_rank=stage.vector_rank,
                    vector_score=stage.vector_score,
                    fusion_rank=stage.fusion_rank,
                    fusion_score=stage.fusion_score,
                    rerank_rank=rank,
                    rerank_score=item.score,
                )
            )
        try:
            self._repository.validate_citations(
                index_version=request.policy_index_version, citations=citations
            )
        except Exception:
            return self._save_error(
                request,
                retrieval_id,
                filters,
                candidates,
                timings,
                attempts,
                total_started,
                "citation_validation_failed",
            )

        covered = sorted({citation.control_code for citation in citations})
        missing = sorted(set(request.required_control_codes) - set(covered))
        if _has_conflicting_rules(clauses):
            status = RetrievalStatus.CONFLICT
        elif missing:
            status = RetrievalStatus.NO_EVIDENCE
        else:
            status = RetrievalStatus.OK
        result = self._result(
            request,
            retrieval_id,
            status,
            filters,
            citations,
            covered,
            missing,
            candidates,
            timings,
            attempts,
            total_started,
        )
        self._repository.save_trace(request, result)
        return result

    def _save_error(
        self,
        request: RetrievalRequest,
        retrieval_id: str,
        filters: dict,
        candidates: list[RetrievalCandidate],
        timings: dict[str, float],
        attempts: dict[str, int],
        total_started: float,
        error_code: str,
    ) -> RetrievalResult:
        result = self._result(
            request,
            retrieval_id,
            RetrievalStatus.ERROR,
            filters,
            [],
            [],
            sorted(request.required_control_codes),
            candidates,
            timings,
            attempts,
            total_started,
            error_code=error_code,
        )
        self._repository.save_trace(request, result)
        return result

    def _result(
        self,
        request: RetrievalRequest,
        retrieval_id: str,
        status: RetrievalStatus,
        filters: dict,
        citations: list[PolicyCitation],
        covered: list[str],
        missing: list[str],
        candidates: list[RetrievalCandidate],
        timings: dict[str, float],
        attempts: dict[str, int],
        total_started: float,
        *,
        error_code: str | None = None,
    ) -> RetrievalResult:
        all_timings = dict(timings)
        all_timings["total"] = _elapsed_ms(total_started)
        return RetrievalResult(
            retrieval_id=retrieval_id,
            status=status,
            policy_set_version=request.policy_set_version,
            policy_index_version=request.policy_index_version,
            embedding_model=self._embedding.model_id,
            rerank_model=self._rerank.model_id,
            filters=filters,
            covered_control_codes=covered,
            missing_control_codes=missing,
            citations=citations,
            candidates=candidates,
            latency_ms=all_timings,
            attempts=attempts,
            error_code=error_code,
        )


def _bm25_rank(
    query: str, clauses: list[StoredPolicyClause], *, limit: int
) -> tuple[list[str], dict[str, float]]:
    corpus = [tokenize_policy_text(clause.text) for clause in clauses]
    model = BM25Okapi(corpus)
    raw_scores = model.get_scores(tokenize_policy_text(query))
    scores = {clause.clause_id: float(score) for clause, score in zip(clauses, raw_scores)}
    ranked = sorted(clauses, key=lambda clause: (-scores[clause.clause_id], clause.clause_id))
    return [clause.clause_id for clause in ranked[:limit]], scores


def _candidate_contracts(fused, bm25_scores, vector_scores, rerank_by_clause):
    result = []
    for fusion_rank, item in enumerate(fused, start=1):
        rerank = rerank_by_clause.get(item.clause_id)
        result.append(
            RetrievalCandidate(
                clause_id=item.clause_id,
                bm25_rank=item.bm25_rank,
                bm25_score=bm25_scores.get(item.clause_id),
                vector_rank=item.vector_rank,
                vector_score=vector_scores.get(item.clause_id),
                fusion_rank=fusion_rank,
                fusion_score=item.fusion_score,
                rerank_rank=rerank[0] if rerank else None,
                rerank_score=rerank[1] if rerank else None,
            )
        )
    return result


def _has_conflicting_rules(clauses: list[StoredPolicyClause]) -> bool:
    by_parameter: dict[tuple[str, str], set[str]] = defaultdict(set)
    for clause in clauses:
        for parameter_name, value in clause.rule_parameters.items():
            by_parameter[(clause.control_code, parameter_name)].add(
                json.dumps(value, sort_keys=True, separators=(",", ":"))
            )
    return any(len(values) > 1 for values in by_parameter.values())


def _elapsed_ms(started: float) -> float:
    return max(0.0, (time.perf_counter() - started) * 1000)
