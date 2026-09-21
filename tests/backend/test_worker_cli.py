from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from supplier_comparison import worker
from supplier_comparison.rag.clients import ModelClientError


def test_worker_cli_sanitizes_unexpected_exception(monkeypatch, capsys) -> None:
    def fail(_job_id: str):
        raise RuntimeError("Authorization: secret-provider-token")

    monkeypatch.setattr(worker, "run_job", fail)

    exit_code = worker.main(["run-job", "--job-id", "job_test"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "secret-provider-token" not in captured.out
    assert "secret-provider-token" not in captured.err
    assert json.loads(captured.err) == {
        "error": {
            "code": "worker_failed",
            "message": "Workflow execution failed.",
        },
        "job_id": "job_test",
    }


def test_worker_cli_reports_sanitized_model_error_code() -> None:
    payload = worker._error_payload(
        "job_model",
        ModelClientError(
            "conversation validation failed: missing reference",
            attempts=2,
            error_code="conversation_model_output_invalid",
        ),
    )

    assert payload == {
        "error": {
            "code": "conversation_model_output_invalid",
            "message": "conversation validation failed: missing reference",
        },
        "job_id": "job_model",
    }


def test_worker_loop_processes_pending_jobs_until_idle() -> None:
    class FakeService:
        def __init__(self) -> None:
            self.jobs = iter(("job_one", "job_two", None))

        def next_pending_job_id(self):
            return next(self.jobs)

    executed: list[str] = []

    result = worker.run_loop(
        stop_when_idle=True,
        service=FakeService(),  # type: ignore[arg-type]
        execute_job=lambda job_id: executed.append(job_id) or {"job_id": job_id},
        sleeper=lambda _: None,
    )

    assert executed == ["job_one", "job_two"]
    assert result == {"status": "IDLE", "processed_jobs": 2}


def test_conversation_worker_completes_validated_turn(monkeypatch) -> None:
    class FakeService:
        def __init__(self):
            self.completed = None

        def conversation_job_context(self, job_id):
            return {"job_id": job_id, "allowed_reference_ids": ["RESULT:1"]}

        def complete_conversation_job(self, job_id, **kwargs):
            self.completed = (job_id, kwargs)
            return {"job_id": job_id, "job_status": "SUCCEEDED"}

        def fail_conversation_job(self, *_args, **_kwargs):
            raise AssertionError("success path must not fail the job")

    service = FakeService()
    config = SimpleNamespace(provider="fixed-test", model_id="fixed-conversation")
    monkeypatch.setattr(worker.ConversationModelConfig, "from_env", lambda: config)
    monkeypatch.setattr(
        worker,
        "generate_conversation_turn",
        lambda context, actual: (
            {
                "assistant_text": "当前结果来自冻结事实。",
                "reference_ids": ["RESULT:1"],
                "changes": None,
            },
            1,
        ),
    )

    result = worker._run_decision_conversation_job(service, "job-conversation")

    assert result["job_status"] == "SUCCEEDED"
    assert service.completed[1]["attempts"] == 1
    assert service.completed[1]["provider"] == "fixed-test"


def test_conversation_worker_persists_sanitized_model_failure(monkeypatch) -> None:
    class FakeService:
        def __init__(self):
            self.failed = None

        def conversation_job_context(self, job_id):
            return {"job_id": job_id}

        def complete_conversation_job(self, *_args, **_kwargs):
            raise AssertionError("failure path must not complete the job")

        def fail_conversation_job(self, job_id, **kwargs):
            self.failed = (job_id, kwargs)

    service = FakeService()
    config = SimpleNamespace(provider="fixed-test", model_id="fixed-conversation")
    monkeypatch.setattr(worker.ConversationModelConfig, "from_env", lambda: config)

    def fail(_context, _config):
        raise ModelClientError(
            "sanitized transport failure", attempts=2, error_code="model_transport_error"
        )

    monkeypatch.setattr(worker, "generate_conversation_turn", fail)
    with pytest.raises(ModelClientError):
        worker._run_decision_conversation_job(service, "job-conversation-failed")
    assert service.failed == (
        "job-conversation-failed",
        {
            "code": "model_transport_error",
            "message": "sanitized transport failure",
            "attempts": 2,
        },
    )

    def fail_validation(_context, _config):
        raise ModelClientError(
            "conversation model response failed validation",
            attempts=1,
            error_code="conversation_model_output_invalid",
        )

    monkeypatch.setattr(worker, "generate_conversation_turn", fail_validation)
    with pytest.raises(ModelClientError):
        worker._run_decision_conversation_job(service, "job-conversation-invalid")
    assert service.failed == (
        "job-conversation-invalid",
        {
            "code": "conversation_model_output_invalid",
            "message": "回答未通过事实与引用校验，请重试或缩小问题范围。",
            "attempts": 1,
            "diagnostic": "conversation model response failed validation",
        },
    )
