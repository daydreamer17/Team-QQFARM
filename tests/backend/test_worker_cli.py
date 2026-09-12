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
