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
from supplier_comparison.extraction.csv_parser import ProfiledCsvQuoteParser
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


def test_profiled_csv_prompt_includes_cell_location_metadata(quote_dictionary) -> None:
    captured = {}

    def opener(request, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(_valid_response(quote_dictionary))

    parsed = ProfiledCsvQuoteParser().parse_row(
        quote_path("b", version=2, extension="csv"),
        context_for("b", version=2),
        2,
        profile_id="v2_supplier_b",
    )
    result = OpenAICompatibleAdapter(_config(max_attempts=1), opener=opener).extract(
        parsed,
        quote_dictionary,
        ModelCallBudget(graph_run_id="GRAPH-V2-CSV-PROMPT"),
        "EXTRACT-V2-CSV-PROMPT",
    )

    prompt = json.loads(captured["messages"][1]["content"])
    sources_by_column = {source["column_name"]: source for source in prompt["sources"]}
    price_source = sources_by_column["Each Price"]

    assert price_source["kind"] == "CSV_CELL"
    assert price_source["row_number"] == 2
    assert price_source["text"] == "6.80"
    assert "page_number" not in price_source
    assert "block_id" not in price_source
    assert result.run.prompt_version == "quote-extraction/2.1.0"
    boundaries = prompt["field_specific_boundaries"]
    assert "other fees" in boundaries["shipping_vs_other_fees"]
    assert "INCLUDED" in boundaries["fee_status_vs_separate_amount"]
    assert "not zero" in boundaries["fee_status_vs_separate_amount"]
    assert "Never cite either column" in boundaries["price_basis_vs_order_increment"]
    assert "return CONFLICT" in boundaries["start_event"]
    assert {example["field_name"] for example in prompt["unit_examples"]} == {
        "unit_price",
        "price_basis_quantity",
        "units_per_pack",
        "moq_quantity",
        "lead_time_days",
    }
    fee_example, price_example, tray_price_example = prompt["cross_field_examples"]
    assert fee_example["outputs"]["shipping_fee_status"]["validation_status"] == "MISSING"
    assert fee_example["outputs"]["shipping_fee_amount"]["validation_status"] == "MISSING"
    assert fee_example["outputs"]["other_fees_amount"]["unit"] == "SGD"
    assert price_example["outputs"]["price_basis_quantity"]["cite_columns"] == [
        "Each Price",
        "Supply Form",
    ]
    assert price_example["outputs"]["order_multiple_units"]["cite_columns"] == [
        "Order Increment"
    ]
    assert tray_price_example["outputs"]["price_basis_quantity"] == {
        "normalized_value": "100",
        "unit": "piece",
        "cite_columns": ["Price", "Packaging"],
    }
    assert tray_price_example["outputs"]["price_basis_unit"]["normalized_value"] == "piece"


def test_pdf_prompt_includes_page_and_block_location_metadata(quote_dictionary) -> None:
    captured = {}

    def opener(request, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(_valid_response(quote_dictionary))

    parsed = PdfQuoteParser().parse(quote_path("a", version=2), context_for("a", version=2))
    OpenAICompatibleAdapter(_config(max_attempts=1), opener=opener).extract(
        parsed,
        quote_dictionary,
        ModelCallBudget(graph_run_id="GRAPH-V2-PDF-PROMPT"),
        "EXTRACT-V2-PDF-PROMPT",
    )

    prompt = json.loads(captured["messages"][1]["content"])
    first_source = prompt["sources"][0]

    assert first_source["source_id"] == "S001"
    assert first_source["kind"] == "PDF_TEXT_BLOCK"
    assert first_source["page_number"] >= 1
    assert first_source["block_id"]
    assert "row_number" not in first_source
    assert "column_name" not in first_source


def test_real_adapter_grounds_allowed_source_handle_to_authoritative_text(
    quote_dictionary,
) -> None:
    captured = {}
    parsed = PdfQuoteParser().parse(quote_path("a"), context_for("a"))
    source = parsed.sources[0]
    payload = json.loads(
        _valid_response(quote_dictionary).decode("utf-8")
    )["choices"][0]["message"]["content"]
    payload = json.loads(payload)
    payload["candidates"][0] = {
        "field_name": quote_dictionary.extractable_fields[0].field_name,
        "raw_value": source.raw_text,
        "normalized_value": source.raw_text,
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": "S001", "quoted_text": "model paraphrase"}],
    }
    envelope = {
        "id": "request-grounding-1",
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
        "usage": {},
    }

    def opener(request, timeout):
        del timeout
        captured.update(json.loads(request.data.decode("utf-8")))
        return FakeResponse(json.dumps(envelope).encode("utf-8"))

    result = OpenAICompatibleAdapter(_config(max_attempts=1), opener=opener).extract(
        parsed,
        quote_dictionary,
        ModelCallBudget(graph_run_id="GRAPH-SOURCE-HANDLE"),
        "EXTRACT-SOURCE-HANDLE",
    )

    source_ref = result.payload.candidates[0].source_refs[0]
    assert source_ref.source_id == source.source_id
    assert source_ref.quoted_text == source.raw_text
    assert result.model_payload_before_grounding is not None
    assert result.model_payload_before_grounding.candidates[0].source_refs[0].source_id == "S001"
    allowed = captured["response_format"]["json_schema"]["schema"]["$defs"][
        "SourceCitation"
    ]["properties"]["source_id"]["enum"]
    assert allowed == [f"S{index:03d}" for index in range(1, len(parsed.sources) + 1)]


def test_real_adapter_rejects_unknown_source_handle_without_retry(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(quote_path("a"), context_for("a"))
    payload = json.loads(
        _valid_response(quote_dictionary).decode("utf-8")
    )["choices"][0]["message"]["content"]
    payload = json.loads(payload)
    payload["candidates"][0] = {
        "field_name": quote_dictionary.extractable_fields[0].field_name,
        "raw_value": "QUOTATION",
        "normalized_value": "QUOTATION",
        "unit": None,
        "validation_status": "EXTRACTED",
        "source_refs": [{"source_id": "S999", "quoted_text": "QUOTATION"}],
    }
    raw_content = json.dumps(payload)
    envelope = {
        "id": "request-unknown-handle-1",
        "choices": [{"message": {"content": raw_content}, "finish_reason": "stop"}],
        "usage": {},
    }

    def opener(request, timeout):
        del request, timeout
        return FakeResponse(json.dumps(envelope).encode("utf-8"))

    budget = ModelCallBudget(graph_run_id="GRAPH-UNKNOWN-HANDLE")
    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(_config(max_attempts=3), opener=opener).extract(
            parsed,
            quote_dictionary,
            budget,
            "EXTRACT-UNKNOWN-HANDLE",
        )

    assert raised.value.code == "model_source_handle_unknown"
    assert "raw_model_content" not in raised.value.details
    assert raised.value.details["model_content_length_bytes"] == len(raw_content.encode())
    assert len(raised.value.details["model_content_sha256"]) == 64
    assert raised.value.details["failure_category"] == "EVIDENCE"
    assert raised.value.details["run_record"]["status"] == "FAILED"
    assert budget.calls_used == 1


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
    assert "provider_body_preview" not in raised.value.details
    assert raised.value.details["provider_body_length_bytes"] == len(b"not-json")
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
    run = raised.value.details["run_record"]
    assert run["finish_reason"] == "stop"
    assert run["prompt_tokens"] == 111
    assert run["completion_tokens"] == 22
    assert run["reasoning_tokens"] == 0
    assert run["total_tokens"] == 133
    assert "raw_model_content" not in raised.value.details
    assert raised.value.details["model_content_length_bytes"] == len(raw_content.encode())
    assert budget.calls_used == 1
