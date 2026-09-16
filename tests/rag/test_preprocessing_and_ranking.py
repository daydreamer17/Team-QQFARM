from __future__ import annotations

import pytest

from supplier_comparison.rag.preprocessing import normalize_policy_text, tokenize_policy_text
from supplier_comparison.rag.ranking import reciprocal_rank_fusion, validate_rerank_indexes


def test_preprocessing_is_nfkc_lowercase_and_preserves_hyphens() -> None:
    text = "ＲｏＨＳ-3 COMPLIANT; Total-Cost (SGD)."
    assert normalize_policy_text(text) == "rohs-3 compliant; total-cost (sgd)."
    assert tokenize_policy_text(text) == ["rohs-3", "compliant", "total-cost", "sgd"]


def test_rrf_uses_k_60_and_clause_id_for_stable_ties() -> None:
    fused = reciprocal_rank_fusion(
        bm25_clause_ids=["C-2", "C-1", "C-3"],
        vector_clause_ids=["C-1", "C-2", "C-4"],
        k=60,
    )
    assert [item.clause_id for item in fused[:2]] == ["C-1", "C-2"]
    assert fused[0].bm25_rank == 2
    assert fused[0].vector_rank == 1
    assert fused[0].fusion_score == pytest.approx(1 / 62 + 1 / 61)


def test_rerank_indexes_must_be_unique_and_in_range() -> None:
    assert validate_rerank_indexes([2, 0], candidate_count=3) == [2, 0]
    with pytest.raises(ValueError, match="unique"):
        validate_rerank_indexes([1, 1], candidate_count=3)
    with pytest.raises(ValueError, match="range"):
        validate_rerank_indexes([3], candidate_count=3)
