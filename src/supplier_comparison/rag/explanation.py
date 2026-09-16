from __future__ import annotations

from typing import Any, Protocol

from .contracts import PolicyCitation, PolicyExplanation, RetrievalResult


class ExplanationClient(Protocol):
    def explain(
        self,
        *,
        retrieval_id: str,
        confirmed_facts: dict[str, Any],
        citations: list[PolicyCitation],
    ) -> PolicyExplanation: ...


class FixedExplanationClient:
    def __init__(self, explanation: PolicyExplanation) -> None:
        self._explanation = explanation
        self.calls: list[dict[str, Any]] = []

    def explain(
        self,
        *,
        retrieval_id: str,
        confirmed_facts: dict[str, Any],
        citations: list[PolicyCitation],
    ) -> PolicyExplanation:
        self.calls.append(
            {
                "retrieval_id": retrieval_id,
                "confirmed_facts": confirmed_facts,
                "citation_ids": [citation.citation_id for citation in citations],
            }
        )
        return self._explanation


class PolicyExplanationService:
    def __init__(self, client: ExplanationClient) -> None:
        self._client = client

    def explain(
        self, result: RetrievalResult, *, confirmed_facts: dict[str, Any]
    ) -> PolicyExplanation:
        explanation = self._client.explain(
            retrieval_id=result.retrieval_id,
            confirmed_facts=confirmed_facts,
            citations=result.citations,
        )
        if explanation.confirmed_facts != confirmed_facts:
            raise ValueError("explanation must contain exactly the caller-confirmed facts")
        explanation.validate_against(result)
        return explanation
