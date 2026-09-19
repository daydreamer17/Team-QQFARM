from __future__ import annotations

import json

from supplier_comparison import worker


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
