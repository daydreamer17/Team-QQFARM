from __future__ import annotations

import json
from pathlib import Path

from supplier_comparison.rag.evaluation import EvaluationCase, evaluate_retrievals


class FixedEvalRetriever:
    def __init__(self, result):
        self.result = result

    def retrieve(self, request):
        del request
        return self.result


def test_development_dataset_has_eight_cases_covering_required_scenarios() -> None:
    path = Path(__file__).resolve().parents[2] / "evaluation" / "reference" / "policy_rag" / "development_questions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [EvaluationCase.model_validate(item) for item in payload]
    assert len(cases) == 8
    assert {case.scenario for case in cases} >= {"ANSWER", "NO_EVIDENCE", "OLD_VERSION", "CONFLICT"}


def test_evaluator_reports_stage_recall_and_citation_support() -> None:
    from datetime import datetime, timezone

    from supplier_comparison.rag.contracts import (
        PolicyCitation,
        RetrievalCandidate,
        RetrievalResult,
        RetrievalStatus,
    )

    citation = PolicyCitation(
        citation_id="CIT-1",
        retrieval_id="RET-1",
        policy_set_version="2026.09.1",
        policy_id="POL-1",
        document_id="DOC-1",
        document_version="1.0.0",
        clause_id="QTE-001",
        section="Required fields",
        text="Quotes must state currency.",
        content_sha256="a" * 64,
        control_code="QUOTE_COMPLETENESS",
        bm25_rank=1,
        bm25_score=1.0,
        vector_rank=1,
        vector_score=0.9,
        fusion_rank=1,
        fusion_score=0.03,
        rerank_rank=1,
        rerank_score=0.99,
    )
    result = RetrievalResult(
        retrieval_id="RET-1",
        status=RetrievalStatus.OK,
        policy_set_version="2026.09.1",
        policy_index_version="pidx-1",
        embedding_model="fixed",
        rerank_model="fixed",
        filters={},
        covered_control_codes=["QUOTE_COMPLETENESS"],
        missing_control_codes=[],
        citations=[citation],
        candidates=[
            RetrievalCandidate(
                clause_id="QTE-001",
                bm25_rank=1,
                bm25_score=1.0,
                vector_rank=1,
                vector_score=0.9,
                fusion_rank=1,
                fusion_score=0.03,
                rerank_rank=1,
                rerank_score=0.99,
            )
        ],
        latency_ms={"total": 10},
        attempts={"embedding": 1, "rerank": 1},
    )
    case = EvaluationCase(
        case_id="DEV-1",
        scenario="ANSWER",
        query="Which fields are required?",
        required_control_codes=["QUOTE_COMPLETENESS"],
        category="Electronics",
        region="SG",
        evaluated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
        expected_status=RetrievalStatus.OK,
        relevant_clause_ids=["QTE-001"],
    )
    report = evaluate_retrievals(
        [case],
        retriever=FixedEvalRetriever(result),
        policy_set_version="2026.09.1",
        policy_index_version="pidx-1",
    )
    assert report.bm25_recall_at_10 == 1.0
    assert report.vector_recall_at_10 == 1.0
    assert report.fusion_recall_at_10 == 1.0
    assert report.rerank_recall_at_3 == 1.0
    assert report.citation_support_rate == 1.0
    assert report.status_accuracy == 1.0
