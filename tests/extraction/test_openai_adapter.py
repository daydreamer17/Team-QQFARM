from __future__ import annotations

import json
import urllib.error

import pytest

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import AdapterEnvironment, AdapterOutputMode
from supplier_comparison.extraction.errors import AdapterError, ModelCallBudgetExceeded
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser

from .conftest import context_for, quote_path


class FakeResponse:
    def __init__(self, body: bytes, headers=None) -> None:
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


def _valid_response(quote_dictionary) -> bytes:
    payload = {
        "candidates": [
            {
                "field_name": field.field_name,
                "raw_value": None,
                "normalized_value": None,
                "unit": None,
                "validation_status": "MISSING",
                "source_refs": [],
            }
            for field in quote_dictionary.extractable_fields
        ]
    }
    envelope = {
        "id": "request-test-1",
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    return json.dumps(envelope).encode()


def _config(max_attempts: int = 3) -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        provider="local-test",
        model_id="test-model",
        base_url="http://127.0.0.1:9999/v1",
        environment=AdapterEnvironment.LOCAL,
        max_attempts=max_attempts,
        retry_backoff_seconds=0,
    )


def test_transient_transport_failure_retries_with_a_bound(quote_dictionary) -> None:
    responses = iter(
        (
            urllib.error.URLError("temporary-1"),
            urllib.error.URLError("temporary-2"),
            FakeResponse(_valid_response(quote_dictionary), {"x-siliconcloud-trace-id": "trace-test-1"}),
        )
    )

    def opener(request, timeout):
        del request, timeout
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    parsed = PdfQuoteParser().parse(
        quote_path("c"),
        context_for("c"),
    )
    budget = ModelCallBudget(graph_run_id="GRAPH-RETRY")
    result = OpenAICompatibleAdapter(_config(), opener=opener, sleeper=lambda _: None).extract(
        parsed, quote_dictionary, budget, "EXTRACT-RETRY"
    )
    assert result.run.output_mode == AdapterOutputMode.REAL
    assert result.run.attempts == 3
    assert result.run.calls_after == 3
    assert len(result.run.errors) == 2
    assert budget.calls_used == 3
    assert result.run.enable_thinking is False
    assert result.run.provider_request_id == "request-test-1"
    assert result.run.provider_trace_id == "trace-test-1"
    assert result.run.finish_reason == "stop"
    assert result.run.prompt_tokens == 100
    assert result.run.completion_tokens == 40
    assert result.run.reasoning_tokens == 0
    assert result.run.total_tokens == 140


def test_request_disables_thinking_and_sets_output_limit(quote_dictionary) -> None:
    captured = {}

    def opener(request, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(_valid_response(quote_dictionary))

    parsed = PdfQuoteParser().parse(
        quote_path("b"),
        context_for("b"),
    )
    config = _config(max_attempts=1).model_copy(update={"max_tokens": 4096})
    OpenAICompatibleAdapter(config, opener=opener).extract(
        parsed,
        quote_dictionary,
        ModelCallBudget(graph_run_id="GRAPH-REQUEST"),
        "EXTRACT-REQUEST",
    )
    assert captured["enable_thinking"] is False
    assert captured["max_tokens"] == 4096
    extracted_schema = captured["response_format"]["json_schema"]["schema"]["$defs"][
        "ExtractedModelFieldCandidate"
    ]
    assert "null" not in {
        choice["type"] for choice in extracted_schema["properties"]["normalized_value"]["anyOf"]
    }
    prompt = json.loads(captured["messages"][1]["content"])
    assert {example["field_name"] for example in prompt["normalization_examples"]} == {
        "currency",
        "condition",
        "moq_unit",
        "unit_price",
        "shipping_fee_status",
    }
    shipping_contract = next(
        field for field in prompt["field_contract"] if field["field_name"] == "shipping_fee_status"
    )
    assert shipping_contract["allowed_normalized_values"] == [
        "KNOWN_AMOUNT",
        "FREE",
        "INCLUDED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ]


def test_restored_call_count_cannot_be_reset_by_retry(quote_dictionary) -> None:
    def opener(request, timeout):
        del request, timeout
        raise urllib.error.URLError("temporary")

    parsed = PdfQuoteParser().parse(
        quote_path("c"),
        context_for("c"),
    )
    budget = ModelCallBudget(graph_run_id="GRAPH-RESTORED", calls_used=7, max_calls=8)
    adapter = OpenAICompatibleAdapter(_config(), opener=opener, sleeper=lambda _: None)
    with pytest.raises(ModelCallBudgetExceeded):
        adapter.extract(parsed, quote_dictionary, budget, "EXTRACT-BUDGET")
    assert budget.calls_used == 8


def test_invalid_provider_envelope_is_not_retried(quote_dictionary) -> None:
    def opener(request, timeout):
        del request, timeout
        return FakeResponse(b"not-json")

    parsed = PdfQuoteParser().parse(
        quote_path("a"),
        context_for("a"),
    )
    budget = ModelCallBudget(graph_run_id="GRAPH-FAIL")
    adapter = OpenAICompatibleAdapter(_config(max_attempts=3), opener=opener, sleeper=lambda _: None)
    with pytest.raises(AdapterError) as raised:
        adapter.extract(parsed, quote_dictionary, budget, "EXTRACT-FAIL")
    assert raised.value.code == "model_response_invalid"
    assert raised.value.details["attempts"] == 1
    assert raised.value.details["calls_after"] == 1
    assert raised.value.details["provider_body_preview"] == "not-json"
    assert len(raised.value.details["provider_body_sha256"]) == 64
    assert budget.calls_used == 1


def test_schema_failure_keeps_provider_metadata_and_raw_content_without_retry(quote_dictionary) -> None:
    invalid_payload = {
        "candidates": [
            {
                "field_name": "currency",
                "raw_value": "S$",
                "normalized_value": None,
                "unit": None,
                "validation_status": "EXTRACTED",
                "source_refs": [{"source_id": "SRC-1", "quoted_text": "S$"}],
            }
        ]
    }
    raw_content = json.dumps(invalid_payload)
    envelope = {
        "id": "request-invalid-1",
        "choices": [{"message": {"content": raw_content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 111,
            "completion_tokens": 22,
            "total_tokens": 133,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }

    def opener(request, timeout):
        del request, timeout
        return FakeResponse(
            json.dumps(envelope).encode(),
            {"x-siliconcloud-trace-id": "trace-invalid-1"},
        )

    parsed = PdfQuoteParser().parse(
        quote_path("b"),
        context_for("b"),
    )
    budget = ModelCallBudget(graph_run_id="GRAPH-SCHEMA-FAIL")
    adapter = OpenAICompatibleAdapter(_config(max_attempts=3), opener=opener, sleeper=lambda _: None)
    with pytest.raises(AdapterError) as raised:
        adapter.extract(parsed, quote_dictionary, budget, "EXTRACT-SCHEMA-FAIL")

    assert raised.value.code == "model_output_schema_invalid"
    assert raised.value.details["attempts"] == 1
    assert raised.value.details["calls_after"] == 1
    assert raised.value.details["provider_request_id"] == "request-invalid-1"
    assert raised.value.details["provider_trace_id"] == "trace-invalid-1"
    assert raised.value.details["finish_reason"] == "stop"
    assert raised.value.details["prompt_tokens"] == 111
    assert raised.value.details["completion_tokens"] == 22
    assert raised.value.details["reasoning_tokens"] == 0
    assert raised.value.details["total_tokens"] == 133
    assert raised.value.details["raw_model_content"] == raw_content
    assert budget.calls_used == 1
