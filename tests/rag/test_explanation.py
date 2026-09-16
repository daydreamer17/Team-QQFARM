from __future__ import annotations

import pytest

from supplier_comparison.rag.contracts import PolicyExplanation
from supplier_comparison.rag.explanation import FixedExplanationClient, PolicyExplanationService

from .test_contracts import _citation


def test_explanation_is_limited_to_confirmed_facts_and_current_citations() -> None:
    from supplier_comparison.rag.contracts import RetrievalResult, RetrievalStatus

    result = RetrievalResult(
        retrieval_id="RET-1",
        status=RetrievalStatus.OK,
        policy_set_version="electronics-v1",
        policy_index_version="idx-1",
        embedding_model="BAAI/bge-m3",
        rerank_model="BAAI/bge-reranker-v2-m3",
        filters={},
        covered_control_codes=["QUOTE_COMPLETENESS"],
        missing_control_codes=[],
        citations=[_citation()],
        candidates=[],
        latency_ms={},
        attempts={},
    )
    expected = PolicyExplanation(
        explanation_id="EXP-1",
        retrieval_id="RET-1",
        confirmed_facts={"currency": "SGD"},
        claims=[{"text": "Shipping is required.", "citation_ids": ["CIT-1"]}],
    )
    service = PolicyExplanationService(FixedExplanationClient(expected))
    assert service.explain(result, confirmed_facts={"currency": "SGD"}) == expected

    with pytest.raises(ValueError, match="confirmed facts"):
        service.explain(result, confirmed_facts={"currency": "USD"})
