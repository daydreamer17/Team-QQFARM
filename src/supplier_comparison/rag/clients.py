from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..extraction.adapters import trusted_urlopen


class ClientModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelClientError(ValueError):
    def __init__(self, message: str, *, attempts: int, error_code: str = "model_client_error"):
        super().__init__(message)
        self.attempts = attempts
        self.error_code = error_code


class EmbeddingConfig(ClientModel):
    provider: str = "siliconflow"
    model_id: str = "BAAI/bge-m3"
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key_env: str = "QQFARM_SILICONFLOW_API_KEY"
    dimension: int = Field(default=1024, ge=1)
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_attempts: int = Field(default=2, ge=1, le=2)
    batch_size: int = Field(default=16, ge=1, le=128)

    @classmethod
    def from_env(cls, prefix: str = "SUPPLIER_EMBEDDING_") -> "EmbeddingConfig":
        return cls(
            provider=os.getenv(f"{prefix}PROVIDER", "siliconflow"),
            model_id=os.getenv(f"{prefix}MODEL_ID", "BAAI/bge-m3"),
            base_url=os.getenv(f"{prefix}BASE_URL", "https://api.siliconflow.cn/v1"),
            api_key_env=os.getenv(f"{prefix}API_KEY_ENV", "QQFARM_SILICONFLOW_API_KEY"),
            dimension=int(os.getenv(f"{prefix}DIMENSION", "1024")),
            timeout_seconds=float(os.getenv(f"{prefix}TIMEOUT_SECONDS", "30")),
            max_attempts=int(os.getenv(f"{prefix}MAX_ATTEMPTS", "2")),
            batch_size=int(os.getenv(f"{prefix}BATCH_SIZE", "16")),
        )


class RerankConfig(ClientModel):
    provider: str = "siliconflow"
    model_id: str = "BAAI/bge-reranker-v2-m3"
    base_url: str = "https://api.siliconflow.cn/v1"
    api_key_env: str = "QQFARM_SILICONFLOW_API_KEY"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_attempts: int = Field(default=2, ge=1, le=2)

    @classmethod
    def from_env(cls, prefix: str = "SUPPLIER_RERANK_") -> "RerankConfig":
        return cls(
            provider=os.getenv(f"{prefix}PROVIDER", "siliconflow"),
            model_id=os.getenv(f"{prefix}MODEL_ID", "BAAI/bge-reranker-v2-m3"),
            base_url=os.getenv(f"{prefix}BASE_URL", "https://api.siliconflow.cn/v1"),
            api_key_env=os.getenv(f"{prefix}API_KEY_ENV", "QQFARM_SILICONFLOW_API_KEY"),
            timeout_seconds=float(os.getenv(f"{prefix}TIMEOUT_SECONDS", "30")),
            max_attempts=int(os.getenv(f"{prefix}MAX_ATTEMPTS", "2")),
        )


class EmbeddingBatch(ClientModel):
    vectors: list[list[float]]
    model_id: str
    attempts: int = Field(ge=1)
    latency_ms: float = Field(ge=0)


class RerankItem(ClientModel):
    index: int = Field(ge=0)
    score: float


class RerankBatch(ClientModel):
    items: list[RerankItem]
    model_id: str
    attempts: int = Field(ge=1)
    latency_ms: float = Field(ge=0)


@runtime_checkable
class EmbeddingClient(Protocol):
    model_id: str
    dimension: int

    def embed(self, texts: list[str]) -> EmbeddingBatch: ...


@runtime_checkable
class RerankClient(Protocol):
    model_id: str

    def rerank(self, query: str, documents: list[str], *, top_n: int) -> RerankBatch: ...


class FixedEmbeddingClient:
    def __init__(self, vectors_by_text: dict[str, list[float]], *, model_id: str, dimension: int):
        self._vectors = vectors_by_text
        self.model_id = model_id
        self.dimension = dimension
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        self.calls.append(list(texts))
        vectors = [self._vectors[text] for text in texts]
        _validate_vectors(vectors, expected_count=len(texts), dimension=self.dimension)
        return EmbeddingBatch(vectors=vectors, model_id=self.model_id, attempts=1, latency_ms=0)


class FixedRerankClient:
    def __init__(self, indexes: list[int], *, scores: list[float], model_id: str):
        if len(indexes) != len(scores):
            raise ValueError("fixed rerank indexes and scores must have equal length")
        self._indexes = indexes
        self._scores = scores
        self.model_id = model_id
        self.calls: list[tuple[str, list[str], int]] = []

    def rerank(self, query: str, documents: list[str], *, top_n: int) -> RerankBatch:
        self.calls.append((query, list(documents), top_n))
        items = [RerankItem(index=i, score=s) for i, s in zip(self._indexes, self._scores)][:top_n]
        return RerankBatch(items=items, model_id=self.model_id, attempts=1, latency_ms=0)


class SiliconFlowEmbeddingClient:
    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        opener: Callable[..., object] = trusted_urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.model_id = config.model_id
        self.dimension = config.dimension
        self._opener = opener
        self._sleeper = sleeper

    def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts:
            raise ValueError("embedding input cannot be empty")
        all_vectors: list[list[float]] = []
        total_attempts = 0
        started = time.perf_counter()
        for offset in range(0, len(texts), self.config.batch_size):
            batch = texts[offset : offset + self.config.batch_size]
            try:
                payload, attempts = self._post({"model": self.model_id, "input": batch})
            except ModelClientError as exc:
                raise ModelClientError(
                    str(exc), attempts=total_attempts + exc.attempts, error_code=exc.error_code
                ) from exc
            total_attempts += attempts
            try:
                data = payload.get("data")
                if not isinstance(data, list):
                    raise ValueError("embedding response data must be a list")
                indexes = [item.get("index") for item in data if isinstance(item, dict)]
                if sorted(indexes) != list(range(len(batch))):
                    raise ValueError("embedding response indexes are invalid")
                ordered = sorted(data, key=lambda item: item["index"])
                vectors = [item.get("embedding") for item in ordered]
                _validate_vectors(vectors, expected_count=len(batch), dimension=self.dimension)
            except (TypeError, ValueError) as exc:
                raise ModelClientError(
                    f"embedding response invalid: {exc}",
                    attempts=total_attempts,
                    error_code="embedding_response_invalid",
                ) from exc
            all_vectors.extend(vectors)
        return EmbeddingBatch(
            vectors=all_vectors,
            model_id=self.model_id,
            attempts=total_attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _post(self, body: dict) -> tuple[dict, int]:
        return _post_json(
            f"{self.config.base_url.rstrip('/')}/embeddings",
            body,
            api_key_env=self.config.api_key_env,
            timeout_seconds=self.config.timeout_seconds,
            max_attempts=self.config.max_attempts,
            opener=self._opener,
            sleeper=self._sleeper,
        )


class SiliconFlowRerankClient:
    def __init__(
        self,
        config: RerankConfig,
        *,
        opener: Callable[..., object] = trusted_urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.model_id = config.model_id
        self._opener = opener
        self._sleeper = sleeper

    def rerank(self, query: str, documents: list[str], *, top_n: int) -> RerankBatch:
        if not documents:
            raise ValueError("rerank documents cannot be empty")
        started = time.perf_counter()
        payload, attempts = _post_json(
            f"{self.config.base_url.rstrip('/')}/rerank",
            {
                "model": self.model_id,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            },
            api_key_env=self.config.api_key_env,
            timeout_seconds=self.config.timeout_seconds,
            max_attempts=self.config.max_attempts,
            opener=self._opener,
            sleeper=self._sleeper,
        )
        try:
            raw_results = payload.get("results")
            if not isinstance(raw_results, list):
                raise ValueError("rerank response results must be a list")
            items = []
            for raw in raw_results:
                if not isinstance(raw, dict) or not isinstance(raw.get("index"), int):
                    raise ValueError("rerank result index is invalid")
                score = raw.get("relevance_score", raw.get("score"))
                if not isinstance(score, (int, float)):
                    raise ValueError("rerank result score is invalid")
                items.append(RerankItem(index=raw["index"], score=float(score)))
        except (TypeError, ValueError) as exc:
            raise ModelClientError(
                f"rerank response invalid: {exc}",
                attempts=attempts,
                error_code="rerank_response_invalid",
            ) from exc
        return RerankBatch(
            items=items,
            model_id=self.model_id,
            attempts=attempts,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def _post_json(
    url: str,
    body: dict,
    *,
    api_key_env: str,
    timeout_seconds: float,
    max_attempts: int,
    opener: Callable[..., object],
    sleeper: Callable[[float], None],
) -> tuple[dict, int]:
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise ModelClientError(
            f"API key environment variable is not set: {api_key_env}",
            attempts=0,
            error_code="api_key_missing",
        )
    encoded = json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(1, max_attempts + 1):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                decoded = json.loads(response.read())
            if not isinstance(decoded, dict):
                raise ValueError("provider response must be a JSON object")
            return decoded, attempt
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt >= max_attempts:
                raise ModelClientError(
                    f"model HTTP request failed with status {exc.code}",
                    attempts=attempt,
                    error_code="model_http_error",
                ) from exc
            sleeper(0.25 * attempt)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt >= max_attempts:
                raise ModelClientError(
                    "model transport request failed",
                    attempts=attempt,
                    error_code="model_transport_error",
                ) from exc
            sleeper(0.25 * attempt)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ModelClientError(
                "provider response is not valid JSON",
                attempts=attempt,
                error_code="model_response_invalid",
            ) from exc
    raise AssertionError("unreachable")


def _validate_vectors(vectors: object, *, expected_count: int, dimension: int) -> None:
    if not isinstance(vectors, list) or len(vectors) != expected_count:
        raise ValueError("embedding response count does not match input")
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimension:
            raise ValueError(f"embedding dimension must be {dimension}")
        if any(not isinstance(value, (int, float)) for value in vector):
            raise ValueError("embedding values must be numeric")
