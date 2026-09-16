from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from supplier_comparison.rag.contracts import (
    PolicyCitation,
    PolicyExplanation,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
)


def _citation() -> PolicyCitation:
    return PolicyCitation(
        citation_id="CIT-1",
        retrieval_id="RET-1",
        policy_set_version="electronics-v1",
        policy_id="POL-QUOTE",
        document_id="DOC-QUOTE",
        document_version="1.0.0",
        clause_id="QUOTE-001",
        section="Required quote fields",
        text="Every quote must state currency, unit price, quantity, and shipping.",
        content_sha256="a" * 64,
        control_code="QUOTE_COMPLETENESS",
        bm25_rank=1,
        vector_rank=2,
        fusion_rank=1,
        fusion_score=0.0325,
        rerank_rank=1,
        rerank_score=0.98,
    )


def test_retrieval_request_requires_english_and_timezone() -> None:
    request = RetrievalRequest(
        task_id="TASK-1",
        task_revision=2,
        snapshot_id="SNAP-1",
        policy_set_version="electronics-v1",
        policy_index_version="idx-1",
        query="Which quote fields are mandatory?",
        required_control_codes=["QUOTE_COMPLETENESS"],
        category="Electronics",
        region="SG",
        evaluated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    assert request.required_control_codes == ["QUOTE_COMPLETENESS"]

    with pytest.raises(ValidationError):
        request.model_copy(update={"query": "报价需要哪些字段？"}, deep=True).__class__.model_validate(
            {**request.model_dump(), "query": "报价需要哪些字段？"}
        )
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate(
            {**request.model_dump(), "evaluated_at": "2026-09-16T00:00:00"}
        )


def test_result_and_explanation_are_bound_to_same_retrieval() -> None:
    citation = _citation()
    result = RetrievalResult(
        retrieval_id="RET-1",
        status=RetrievalStatus.OK,
        policy_set_version="electronics-v1",
        policy_index_version="idx-1",
        embedding_model="BAAI/bge-m3",
        rerank_model="BAAI/bge-reranker-v2-m3",
        filters={"category": "Electronics", "region": "SG"},
        covered_control_codes=["QUOTE_COMPLETENESS"],
        missing_control_codes=[],
        citations=[citation],
        candidates=[],
        latency_ms={"total": 12.0},
        attempts={"embedding": 1, "rerank": 1},
    )
    explanation = PolicyExplanation(
        explanation_id="EXP-1",
        retrieval_id="RET-1",
        confirmed_facts={"currency": "SGD"},
        claims=[
            {
                "text": "The quote must include shipping.",
                "citation_ids": ["CIT-1"],
            }
        ],
    )
    explanation.validate_against(result)

    invalid = explanation.model_copy(update={"retrieval_id": "RET-OTHER"})
    with pytest.raises(ValueError, match="retrieval"):
        invalid.validate_against(result)


def test_no_evidence_result_may_keep_partial_citations_for_diagnosis() -> None:
    result = RetrievalResult(
        retrieval_id="RET-2",
        status=RetrievalStatus.NO_EVIDENCE,
        policy_set_version="electronics-v1",
        policy_index_version="idx-1",
        embedding_model="BAAI/bge-m3",
        rerank_model="BAAI/bge-reranker-v2-m3",
        filters={},
        covered_control_codes=["QUOTE_COMPLETENESS"],
        missing_control_codes=["ROHS"],
        citations=[_citation().model_copy(update={"retrieval_id": "RET-2"})],
        candidates=[],
        latency_ms={},
        attempts={},
    )
    assert result.status == RetrievalStatus.NO_EVIDENCE


def test_handoff_examples_validate_and_share_retrieval_id() -> None:
    root = Path(__file__).resolve().parents[2] / "data" / "examples" / "policy_rag"
    result = RetrievalResult.model_validate(
        json.loads((root / "retrieval_result.json").read_text(encoding="utf-8"))
    )
    explanation = PolicyExplanation.model_validate(
        json.loads((root / "policy_explanation.json").read_text(encoding="utf-8"))
    )
    explanation.validate_against(result)
