from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalStatus(StrEnum):
    OK = "OK"
    NO_EVIDENCE = "NO_EVIDENCE"
    CONFLICT = "CONFLICT"
    ERROR = "ERROR"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievalRequest(FrozenModel):
    task_id: str = Field(min_length=1, max_length=64)
    task_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1, max_length=64)
    policy_set_version: str = Field(min_length=1, max_length=128)
    policy_index_version: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=3, max_length=4000)
    required_control_codes: list[str] = Field(default_factory=list)
    category: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=64)
    evaluated_at: datetime

    @model_validator(mode="after")
    def validate_request(self) -> "RetrievalRequest":
        if not re.search(r"[A-Za-z]", self.query):
            raise ValueError("query must be English")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include a timezone")
        normalized = [code.strip().upper() for code in self.required_control_codes]
        if any(not code for code in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("required_control_codes must be unique and non-empty")
        object.__setattr__(self, "required_control_codes", normalized)
        return self


class RetrievalCandidate(FrozenModel):
    clause_id: str = Field(min_length=1)
    bm25_rank: int | None = Field(default=None, ge=1)
    bm25_score: float | None = None
    vector_rank: int | None = Field(default=None, ge=1)
    vector_score: float | None = None
    fusion_rank: int = Field(ge=1)
    fusion_score: float = Field(ge=0)
    rerank_rank: int | None = Field(default=None, ge=1)
    rerank_score: float | None = None


class PolicyCitation(FrozenModel):
    citation_id: str = Field(min_length=1, max_length=64)
    retrieval_id: str = Field(min_length=1, max_length=64)
    policy_set_version: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    document_version: str = Field(min_length=1, max_length=64)
    clause_id: str = Field(min_length=1, max_length=128)
    section: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    control_code: str = Field(min_length=1, max_length=128)
    bm25_rank: int | None = Field(default=None, ge=1)
    bm25_score: float | None = None
    vector_rank: int | None = Field(default=None, ge=1)
    vector_score: float | None = None
    fusion_rank: int = Field(ge=1)
    fusion_score: float = Field(ge=0)
    rerank_rank: int = Field(ge=1)
    rerank_score: float


class RetrievalResult(FrozenModel):
    retrieval_id: str = Field(min_length=1, max_length=64)
    status: RetrievalStatus
    policy_set_version: str = Field(min_length=1)
    policy_index_version: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    rerank_model: str = Field(min_length=1)
    filters: dict[str, Any]
    covered_control_codes: list[str]
    missing_control_codes: list[str]
    citations: list[PolicyCitation] = Field(max_length=3)
    candidates: list[RetrievalCandidate]
    latency_ms: dict[str, float]
    attempts: dict[str, int]
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_status_shape(self) -> "RetrievalResult":
        if self.status == RetrievalStatus.OK and self.missing_control_codes:
            raise ValueError("OK cannot have missing control codes")
        if any(citation.retrieval_id != self.retrieval_id for citation in self.citations):
            raise ValueError("citation retrieval_id must match the result")
        return self


class ExplanationClaim(FrozenModel):
    text: str = Field(min_length=1)
    citation_ids: list[str] = Field(min_length=1)
    evidence_quotes: dict[str, str] = Field(default_factory=dict)


class PolicyExplanation(FrozenModel):
    explanation_id: str = Field(min_length=1, max_length=64)
    retrieval_id: str = Field(min_length=1, max_length=64)
    confirmed_facts: dict[str, Any]
    claims: list[ExplanationClaim]

    def validate_against(self, result: RetrievalResult) -> None:
        if self.retrieval_id != result.retrieval_id:
            raise ValueError("explanation retrieval does not match retrieval result")
        allowed = {citation.citation_id for citation in result.citations}
        for claim in self.claims:
            unknown = set(claim.citation_ids) - allowed
            if unknown:
                raise ValueError(f"claim contains citation IDs outside this retrieval: {sorted(unknown)}")


@runtime_checkable
class PolicyRetriever(Protocol):
    def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...
