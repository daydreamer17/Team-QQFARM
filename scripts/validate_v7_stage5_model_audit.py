#!/usr/bin/env python3
"""Exercise V7 stage 5 retry and redaction paths without external network calls."""

from __future__ import annotations

import io
import json
import os
import tempfile
import urllib.error
from pathlib import Path

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import AdapterEnvironment, DocumentContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import AdapterError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "evaluation/results/local/2026-09-11/v7_stage5_model_audit/stage5_validation.json"
)
INPUT_PATH = REPO_ROOT / "data/generated/inputs/development/quote_V1/supplier_a_quote_v1.pdf"


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


def _http_error(status: int, body: bytes, headers: dict[str, str] | None = None):
    return urllib.error.HTTPError(
        "https://provider.invalid/v1/chat/completions",
        status,
        "redacted-provider-reason",
        headers or {},
        io.BytesIO(body),
    )


def _config(**updates: object) -> OpenAICompatibleConfig:
    config = OpenAICompatibleConfig(
        provider="injected-offline-provider",
        model_id="injected-offline-model",
        base_url="https://provider.invalid/v1",
        environment=AdapterEnvironment.LOCAL,
        retry_backoff_seconds=0.1,
        retry_jitter_seconds=0.5,
        max_retry_delay_seconds=5,
    )
    return config.model_copy(update=updates)


def _valid_response(dictionary: QuoteDictionary) -> bytes:
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
            for field in dictionary.extractable_fields
        ]
    }
    envelope = {
        "id": "injected-request-success",
        "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    return json.dumps(envelope).encode("utf-8")


def _context() -> DocumentContext:
    return DocumentContext(
        task_id="TASK-V7-STAGE5-OFFLINE",
        task_revision=1,
        scenario_id="V7-STAGE5-SYNTHETIC",
        quote_id="QUOTE-V7-STAGE5-A",
        quote_version=1,
        document_id="DOC-V7-STAGE5-A",
        document_version=1,
        supplier_id="V5-SUP-A",
    )


def _raise_record(adapter, parsed, dictionary, budget, run_id) -> tuple[str, dict]:
    try:
        adapter.extract(parsed, dictionary, budget, run_id)
    except AdapterError as exc:
        return exc.code, exc.details
    raise AssertionError(f"{run_id} unexpectedly succeeded")


def main() -> int:
    dictionary = QuoteDictionary.load(REPO_ROOT / "data/contracts/quote_data_field.csv")
    parsed = PdfQuoteParser().parse(INPUT_PATH, _context())
    valid = _valid_response(dictionary)
    sensitive_body = b'{"error":"CONFIDENTIAL QUOTE SGD 999.99"}'

    retry_responses = iter(
        (
            TimeoutError("sensitive transport detail"),
            _http_error(429, sensitive_body, {"Retry-After": "2"}),
            FakeResponse(valid),
        )
    )
    retry_sleeps: list[float] = []

    def retry_opener(request, timeout):
        del request, timeout
        response = next(retry_responses)
        if isinstance(response, Exception):
            raise response
        return response

    retry_result = OpenAICompatibleAdapter(
        _config(),
        opener=retry_opener,
        sleeper=retry_sleeps.append,
        randomizer=lambda: 0.5,
    ).extract(parsed, dictionary, ModelCallBudget("GRAPH-STAGE5-RETRY"), "RUN-STAGE5-RETRY")

    def exhausted_opener(request, timeout):
        del request, timeout
        raise _http_error(503, sensitive_body, {"Retry-After": "999"})

    exhausted_code, exhausted_details = _raise_record(
        OpenAICompatibleAdapter(
            _config(),
            opener=exhausted_opener,
            sleeper=lambda _: None,
            randomizer=lambda: 0,
        ),
        parsed,
        dictionary,
        ModelCallBudget("GRAPH-STAGE5-EXHAUST"),
        "RUN-STAGE5-EXHAUST",
    )

    def auth_opener(request, timeout):
        del request, timeout
        raise _http_error(401, sensitive_body)

    auth_code, auth_details = _raise_record(
        OpenAICompatibleAdapter(_config(), opener=auth_opener),
        parsed,
        dictionary,
        ModelCallBudget("GRAPH-STAGE5-AUTH"),
        "RUN-STAGE5-AUTH",
    )

    invalid_content = '{"quote":"CONFIDENTIAL INVALID MODEL OUTPUT"}'
    invalid_body = json.dumps(
        {
            "id": "injected-request-invalid",
            "choices": [{"message": {"content": invalid_content}, "finish_reason": "stop"}],
        }
    ).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="supplier-stage5-diagnostics-") as directory:
        schema_code, schema_details = _raise_record(
            OpenAICompatibleAdapter(
                _config(max_attempts=1, diagnostic_artifact_dir=directory),
                opener=lambda request, timeout: FakeResponse(invalid_body),
            ),
            parsed,
            dictionary,
            ModelCallBudget("GRAPH-STAGE5-SCHEMA"),
            "RUN-STAGE5-SCHEMA",
        )
        artifact_id = schema_details["run_record"]["diagnostic_artifact_id"]
        artifact_path = Path(directory) / "model_failures" / artifact_id
        diagnostic_checks = {
            "artifact_written": artifact_path.is_file(),
            "directory_mode_0700": os.stat(artifact_path.parent).st_mode & 0o777 == 0o700,
            "file_mode_0600": os.stat(artifact_path).st_mode & 0o777 == 0o600,
            "raw_content_only_in_artifact": (
                "CONFIDENTIAL INVALID MODEL OUTPUT" in artifact_path.read_text()
                and "CONFIDENTIAL INVALID MODEL OUTPUT" not in json.dumps(schema_details)
            ),
        }

    public_records = json.dumps(
        [retry_result.run.model_dump(mode="json"), exhausted_details, auth_details, schema_details]
    )
    checks = {
        "timeout_and_429_then_success": retry_result.run.status == "SUCCEEDED",
        "retry_calls_counted": retry_result.run.calls_after == 3,
        "retry_after_and_jitter_applied": retry_sleeps == [0.35, 2.25],
        "request_fingerprint_present": len(retry_result.run.request_fingerprint or "") == 64,
        "phase_timings_present": all(
            value >= 0
            for value in (
                retry_result.run.prompt_construction_ms,
                retry_result.run.wait_response_ms,
                retry_result.run.decode_ms,
                retry_result.run.structure_validation_ms,
                retry_result.run.evidence_validation_ms,
                retry_result.run.total_duration_ms,
            )
        ),
        "503_exhaustion_failed_after_three_calls": (
            exhausted_code == "model_http_error"
            and exhausted_details["run_record"]["attempts"] == 3
            and exhausted_details["run_record"]["calls_after"] == 3
        ),
        "401_not_retried": (
            auth_code == "model_http_error"
            and auth_details["run_record"]["attempts"] == 1
        ),
        "schema_failure_not_retried": (
            schema_code == "model_output_schema_invalid"
            and schema_details["run_record"]["attempts"] == 1
        ),
        "ordinary_records_redacted": (
            "CONFIDENTIAL" not in public_records
            and "sensitive transport detail" not in public_records
            and "redacted-provider-reason" not in public_records
        ),
        **diagnostic_checks,
    }
    status = "PASSED" if all(checks.values()) else "FAILED"
    output = {
        "result_kind": "V7_STAGE5_OFFLINE_INJECTED_TRANSPORT_VALIDATION",
        "status": status,
        "input_is_synthetic": True,
        "external_network_used": False,
        "real_model_used": False,
        "fixed_or_injected_output": True,
        "provider": "injected-offline-provider",
        "checks": checks,
        "successful_run": retry_result.run.model_dump(mode="json"),
        "terminal_failure_summaries": {
            "exhausted": exhausted_details["run_record"],
            "auth": auth_details["run_record"],
            "schema": schema_details["run_record"],
        },
    }
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": status, "output": str(DEFAULT_OUTPUT), "checks": checks}))
    return 0 if status == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
