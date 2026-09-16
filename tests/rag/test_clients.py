from __future__ import annotations

import json
import urllib.error

import pytest

from supplier_comparison.rag.clients import (
    EmbeddingConfig,
    FixedEmbeddingClient,
    FixedRerankClient,
    ModelClientError,
    RerankConfig,
    SiliconFlowEmbeddingClient,
    SiliconFlowRerankClient,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


def test_fixed_clients_are_deterministic_and_offline() -> None:
    embeddings = FixedEmbeddingClient(
        {"hello": [0.5, 0.5], "world": [0.25, 0.75]}, model_id="fixed", dimension=2
    )
    assert embeddings.embed(["hello", "world"]).vectors == [[0.5, 0.5], [0.25, 0.75]]
    reranker = FixedRerankClient([1, 0], scores=[0.9, 0.8], model_id="fixed-rerank")
    result = reranker.rerank("query", ["first", "second"], top_n=2)
    assert [item.index for item in result.items] == [1, 0]


def test_embedding_adapter_validates_dimension_and_request(monkeypatch) -> None:
    monkeypatch.setenv("TEST_EMBED_KEY", "secret")
    captured = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data)
        return FakeResponse(
            {
                "data": [
                    {"index": 0, "embedding": [0.1, 0.2]},
                    {"index": 1, "embedding": [0.3, 0.4]},
                ],
                "model": "test-embedding",
            }
        )

    client = SiliconFlowEmbeddingClient(
        EmbeddingConfig(
            model_id="test-embedding",
            base_url="https://api.example/v1",
            api_key_env="TEST_EMBED_KEY",
            dimension=2,
            timeout_seconds=30,
            max_attempts=2,
            batch_size=16,
        ),
        opener=opener,
        sleeper=lambda _: None,
    )
    result = client.embed(["a", "b"])
    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "https://api.example/v1/embeddings"
    assert captured["payload"] == {"model": "test-embedding", "input": ["a", "b"]}
    assert captured["authorization"] == "Bearer secret"
    assert captured["timeout"] == 30

    bad = SiliconFlowEmbeddingClient(
        client.config.model_copy(update={"dimension": 3}),
        opener=opener,
        sleeper=lambda _: None,
    )
    with pytest.raises(ValueError, match="dimension"):
        bad.embed(["a", "b"])


def test_rerank_adapter_preserves_provider_indexes(monkeypatch) -> None:
    monkeypatch.setenv("TEST_RERANK_KEY", "secret")

    def opener(request, timeout):
        del timeout
        assert request.full_url == "https://api.example/v1/rerank"
        assert json.loads(request.data) == {
            "model": "test-rerank",
            "query": "shipping",
            "documents": ["a", "b"],
            "top_n": 2,
            "return_documents": False,
        }
        return FakeResponse(
            {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8}]}
        )

    client = SiliconFlowRerankClient(
        RerankConfig(
            model_id="test-rerank",
            base_url="https://api.example/v1",
            api_key_env="TEST_RERANK_KEY",
        ),
        opener=opener,
        sleeper=lambda _: None,
    )
    assert [item.index for item in client.rerank("shipping", ["a", "b"], top_n=2).items] == [1, 0]


def test_transport_failure_reports_bounded_attempt_count(monkeypatch) -> None:
    monkeypatch.setenv("TEST_EMBED_KEY", "secret")
    calls = []

    def opener(request, timeout):
        del request, timeout
        calls.append(1)
        raise urllib.error.URLError("temporary")

    client = SiliconFlowEmbeddingClient(
        EmbeddingConfig(
            model_id="test-embedding",
            base_url="https://api.example/v1",
            api_key_env="TEST_EMBED_KEY",
            dimension=2,
            max_attempts=2,
        ),
        opener=opener,
        sleeper=lambda _: None,
    )
    with pytest.raises(ModelClientError) as captured:
        client.embed(["a"])
    assert captured.value.attempts == 2
    assert len(calls) == 2
