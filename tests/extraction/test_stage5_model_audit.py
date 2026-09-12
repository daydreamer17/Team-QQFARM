from __future__ import annotations

import base64
import io
import json
import os
import urllib.error
from pathlib import Path

import pytest

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import AdapterEnvironment, ExtractionRun
from supplier_comparison.extraction.errors import (
    AdapterError,
    ExtractionFailureCategory,
    classify_failure_code,
)
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser

from .conftest import context_for, quote_path
from .test_openai_adapter import FakeResponse, _valid_response


def _config(**updates: object) -> OpenAICompatibleConfig:
    base = OpenAICompatibleConfig(
        provider="local-test",
        model_id="test-model",
        base_url="http://127.0.0.1:9999/v1",
        environment=AdapterEnvironment.LOCAL,
        retry_backoff_seconds=0.1,
        retry_jitter_seconds=0.5,
        max_retry_delay_seconds=5,
    )
    return base.model_copy(update=updates)


def _http_error(status: int, body: bytes, headers: dict[str, str] | None = None):
    return urllib.error.HTTPError(
        "https://provider.invalid/v1/chat/completions",
        status,
        "provider detail must be redacted",
        headers or {},
        io.BytesIO(body),
    )


def _parsed():
    return PdfQuoteParser().parse(quote_path("a"), context_for("a"))


def test_retry_after_and_jitter_are_bounded_and_audited(quote_dictionary) -> None:
    provider_text = b'{"error":"private quote detail"}'
    responses = iter(
        (
            _http_error(429, provider_text, {"Retry-After": "2"}),
            _http_error(503, provider_text, {"Retry-After": "999"}),
            FakeResponse(_valid_response(quote_dictionary)),
        )
    )
    sleeps: list[float] = []

    def opener(request, timeout):
        del request, timeout
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    result = OpenAICompatibleAdapter(
        _config(),
        opener=opener,
        sleeper=sleeps.append,
        randomizer=lambda: 0.5,
    ).extract(_parsed(), quote_dictionary, ModelCallBudget("GRAPH-STAGE5-RETRY"), "RUN-RETRY")

    assert sleeps == [2.25, 5]
    assert [record.outcome for record in result.run.attempt_records] == [
        "RETRYABLE_FAILURE",
        "RETRYABLE_FAILURE",
        "SUCCEEDED",
    ]
    assert result.run.calls_after == 3
    assert all("private quote detail" not in error for error in result.run.errors)


def test_timeout_then_success_records_safe_timings(quote_dictionary) -> None:
    responses = iter((TimeoutError("contains sensitive host"), FakeResponse(_valid_response(quote_dictionary))))

    def opener(request, timeout):
        del request, timeout
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    result = OpenAICompatibleAdapter(
        _config(max_attempts=2),
        opener=opener,
        sleeper=lambda _: None,
        randomizer=lambda: 0,
    ).extract(_parsed(), quote_dictionary, ModelCallBudget("GRAPH-TIMEOUT"), "RUN-TIMEOUT")

    assert result.run.status == "SUCCEEDED"
    assert result.run.prompt_construction_ms >= 0
    assert result.run.wait_response_ms >= 0
    assert result.run.decode_ms >= 0
    assert result.run.structure_validation_ms >= 0
    assert result.run.evidence_validation_ms >= 0
    assert result.run.total_duration_ms >= 0
    assert len(result.run.request_fingerprint or "") == 64
    assert "sensitive host" not in result.run.model_dump_json()


@pytest.mark.parametrize("status", [400, 401, 403])
def test_auth_and_bad_request_are_terminal_and_body_is_not_in_error_details(
    quote_dictionary,
    status: int,
) -> None:
    body = b'{"error":"secret provider body"}'
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        del request, timeout
        calls += 1
        raise _http_error(status, body)

    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(_config(), opener=opener).extract(
            _parsed(), quote_dictionary, ModelCallBudget(f"GRAPH-{status}"), f"RUN-{status}"
        )

    assert calls == 1
    assert raised.value.details["http_status"] == status
    assert raised.value.details["provider_body_length_bytes"] == len(body)
    assert "secret provider body" not in json.dumps(raised.value.details)
    assert raised.value.details["run_record"]["status"] == "FAILED"
    assert raised.value.details["run_record"]["failure_category"] == "TRANSPORT"


def test_restored_budget_exhaustion_has_terminal_run_record(quote_dictionary) -> None:
    def opener(request, timeout):
        del request, timeout
        raise urllib.error.URLError("private network detail")

    budget = ModelCallBudget("GRAPH-RESTORED-AUDIT", calls_used=7, max_calls=8)
    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(
            _config(), opener=opener, sleeper=lambda _: None, randomizer=lambda: 0
        ).extract(_parsed(), quote_dictionary, budget, "RUN-RESTORED-AUDIT")

    assert raised.value.code == "model_call_budget_exceeded"
    run = raised.value.details["run_record"]
    assert run["status"] == "FAILED"
    assert run["calls_before"] == 7
    assert run["calls_after"] == 8
    assert run["attempts"] == 1
    assert run["failure_category"] == "TRANSPORT"


def test_transient_failures_exhaust_three_attempts_with_one_terminal_record(
    quote_dictionary,
) -> None:
    def opener(request, timeout):
        del request, timeout
        raise urllib.error.URLError("private network detail")

    budget = ModelCallBudget("GRAPH-EXHAUST")
    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(
            _config(), opener=opener, sleeper=lambda _: None, randomizer=lambda: 0
        ).extract(_parsed(), quote_dictionary, budget, "RUN-EXHAUST")

    run = raised.value.details["run_record"]
    assert run["status"] == "FAILED"
    assert run["attempts"] == 3
    assert run["calls_after"] == 3
    assert [record["outcome"] for record in run["attempt_records"]] == [
        "RETRYABLE_FAILURE",
        "RETRYABLE_FAILURE",
        "TERMINAL_FAILURE",
    ]
    assert "private network detail" not in json.dumps(raised.value.details)


def test_api_key_and_provider_reason_are_suppressed_from_failure_record(
    quote_dictionary,
    monkeypatch,
) -> None:
    secret = "stage5-super-secret-token"
    monkeypatch.setenv("STAGE5_TEST_API_KEY", secret)

    def opener(request, timeout):
        del timeout
        assert request.headers["Authorization"] == f"Bearer {secret}"
        raise _http_error(403, b'{"error":"full quotation text"}')

    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(
            _config(max_attempts=1, api_key_env="STAGE5_TEST_API_KEY"),
            opener=opener,
        ).extract(_parsed(), quote_dictionary, ModelCallBudget("GRAPH-SECRET"), "RUN-SECRET")

    public_error = json.dumps(raised.value.details)
    assert secret not in public_error
    assert "full quotation text" not in public_error
    assert "provider detail must be redacted" not in public_error
    assert raised.value.__suppress_context__ is True


def test_restricted_diagnostic_artifact_contains_full_body_but_api_error_does_not(
    quote_dictionary,
    tmp_path: Path,
) -> None:
    invalid_content = '{"quote_full_text":"CONFIDENTIAL SGD 123.45"}'
    envelope = {
        "id": "request-diagnostic",
        "choices": [{"message": {"content": invalid_content}, "finish_reason": "stop"}],
    }
    provider_body = json.dumps(envelope).encode()

    def opener(request, timeout):
        del request, timeout
        return FakeResponse(provider_body)

    with pytest.raises(AdapterError) as raised:
        OpenAICompatibleAdapter(
            _config(max_attempts=1, diagnostic_artifact_dir=str(tmp_path)),
            opener=opener,
        ).extract(_parsed(), quote_dictionary, ModelCallBudget("GRAPH-DIAG"), "RUN-DIAG")

    public_details = json.dumps(raised.value.details)
    assert "CONFIDENTIAL" not in public_details
    artifact_id = raised.value.details["run_record"]["diagnostic_artifact_id"]
    artifact_path = tmp_path / "model_failures" / artifact_id
    artifact = json.loads(artifact_path.read_text())
    assert artifact["raw_model_content"] == invalid_content
    assert base64.b64decode(artifact["provider_body_base64"]) == provider_body
    assert os.stat(tmp_path / "model_failures").st_mode & 0o777 == 0o700
    assert os.stat(artifact_path).st_mode & 0o777 == 0o600


def test_request_fingerprint_is_stable_and_contains_no_plaintext(quote_dictionary) -> None:
    parsed = _parsed()

    def extract(model_id: str):
        adapter = OpenAICompatibleAdapter(
            _config(max_attempts=1, model_id=model_id),
            opener=lambda request, timeout: FakeResponse(_valid_response(quote_dictionary)),
        )
        return adapter.extract(
            parsed,
            quote_dictionary,
            ModelCallBudget(f"GRAPH-{model_id}"),
            f"RUN-{model_id}",
        ).run.request_fingerprint

    first = extract("test-model")
    second = extract("test-model")
    changed = extract("different-model")
    assert first == second
    assert first != changed
    assert first and len(first) == 64
    assert parsed.sources[0].raw_text not in first


def test_pre_stage5_extraction_run_json_remains_readable(quote_dictionary) -> None:
    run = OpenAICompatibleAdapter(
        _config(max_attempts=1),
        opener=lambda request, timeout: FakeResponse(_valid_response(quote_dictionary)),
    ).extract(
        _parsed(),
        quote_dictionary,
        ModelCallBudget("GRAPH-LEGACY"),
        "RUN-LEGACY",
    ).run
    legacy = run.model_dump(mode="json")
    for field in (
        "status",
        "failure_category",
        "failure_code",
        "request_fingerprint",
        "prompt_construction_ms",
        "wait_response_ms",
        "decode_ms",
        "structure_validation_ms",
        "evidence_validation_ms",
        "total_duration_ms",
        "attempt_records",
        "diagnostic_artifact_id",
    ):
        legacy.pop(field)

    restored = ExtractionRun.model_validate(legacy)
    assert restored.status == "SUCCEEDED"
    assert restored.attempt_records == ()


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("pdf_invalid", ExtractionFailureCategory.PARSING),
        ("pdf_ocr_page_timeout", ExtractionFailureCategory.OCR),
        ("model_transport_failed", ExtractionFailureCategory.TRANSPORT),
        ("model_output_schema_invalid", ExtractionFailureCategory.MODEL_STRUCTURE),
        ("source_ref_unknown", ExtractionFailureCategory.EVIDENCE),
    ],
)
def test_failure_codes_have_complete_stage5_categories(code, category) -> None:
    assert classify_failure_code(code) == category
