from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FusedRank:
    clause_id: str
    fusion_score: float
    bm25_rank: int | None
    vector_rank: int | None


def reciprocal_rank_fusion(
    *, bm25_clause_ids: list[str], vector_clause_ids: list[str], k: int = 60
) -> list[FusedRank]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    bm25 = {clause_id: index for index, clause_id in enumerate(bm25_clause_ids, start=1)}
    vector = {clause_id: index for index, clause_id in enumerate(vector_clause_ids, start=1)}
    result = []
    for clause_id in set(bm25) | set(vector):
        score = 0.0
        if clause_id in bm25:
            score += 1 / (k + bm25[clause_id])
        if clause_id in vector:
            score += 1 / (k + vector[clause_id])
        result.append(FusedRank(clause_id, score, bm25.get(clause_id), vector.get(clause_id)))
    return sorted(result, key=lambda item: (-item.fusion_score, item.clause_id))


def validate_rerank_indexes(indexes: list[int], *, candidate_count: int) -> list[int]:
    if len(indexes) != len(set(indexes)):
        raise ValueError("rerank indexes must be unique")
    if any(index < 0 or index >= candidate_count for index in indexes):
        raise ValueError("rerank index is outside candidate range")
    return indexes
