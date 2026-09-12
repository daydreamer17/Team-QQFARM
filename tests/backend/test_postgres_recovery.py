from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.checkpoints import checkpoint_connection_string
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.settings import settings
from supplier_comparison.backend.workflow import WorkflowRunner

from .test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH, _requirement


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 to run the PostgreSQL recovery test",
)


def test_postgres_checkpoint_resumes_with_a_new_connection(tmp_path: Path) -> None:
    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = BackendService(sessions, tmp_path / "quotes", actor_id="postgres-test-user")
    test_run_id = uuid4().hex
    task = service.create_task(
        _requirement(), idempotency_key=f"create-{test_run_id}", scenario_id="MCU-DEMO-001"
    )
    revision = 1
    for alias, supplier in (("a", "SUP-022"), ("b", "SUP-023"), ("c", "SUP-024")):
        uploaded = service.upload_quote(
            task["task_id"],
            expected_task_revision=revision,
            supplier_id=supplier,
            original_filename=f"supplier-{alias}.csv",
            media_type="text/csv",
            content=f"postgres-placeholder-{alias}".encode(),
            idempotency_key=f"upload-{alias}-{test_run_id}",
            is_synthetic=True,
        )
        revision = uploaded["task_revision"]
    started = service.start_run(
        task["task_id"],
        expected_task_revision=revision,
        idempotency_key=f"run-{test_run_id}",
    )
    first_processor = CanonicalCsvProcessor(tmp_path)
    connection_string = checkpoint_connection_string(database_url)
    try:
        with PostgresSaver.from_conn_string(connection_string) as first_saver:
            first = WorkflowRunner(
                service,
                processor=first_processor,
                checkpointer=first_saver,
                dictionary_path=DICTIONARY_PATH,
                evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
            ).run_job(started["job_id"])
        assert first["issue"]["issue_type"] == "CONFIRM_MISSING"
        confirmation = service.answer_issue(
            task["task_id"],
            first["issue"]["issue_id"],
            expected_task_revision=revision,
            answer={"answer_type": "CONFIRM_MISSING"},
            idempotency_key=f"confirm-{test_run_id}",
        )

        second_processor = CanonicalCsvProcessor(tmp_path)
        with PostgresSaver.from_conn_string(connection_string) as second_saver:
            second = WorkflowRunner(
                service,
                processor=second_processor,
                checkpointer=second_saver,
                dictionary_path=DICTIONARY_PATH,
                evaluated_at=datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc),
            ).run_job(confirmation["job_id"])
        assert second["issue"]["issue_type"] == "SHIPPING_AMOUNT"
        assert service.list_results(task["task_id"])[0]["result"]["evaluated_at"] == (
            "2026-09-14T01:00:00Z"
        )
        shipping = service.answer_issue(
            task["task_id"],
            second["issue"]["issue_id"],
            expected_task_revision=revision + 1,
            answer={
                "answer_type": "SHIPPING_AMOUNT",
                "amount": "200.00",
                "currency": "SGD",
            },
            idempotency_key=f"shipping-{test_run_id}",
        )
        third_processor = CanonicalCsvProcessor(tmp_path)
        with PostgresSaver.from_conn_string(connection_string) as third_saver:
            final = WorkflowRunner(
                service,
                processor=third_processor,
                checkpointer=third_saver,
                dictionary_path=DICTIONARY_PATH,
                evaluated_at=datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc),
            ).run_job(shipping["job_id"])
        assert final["status"] == "SUCCEEDED"
        result = service.list_results(task["task_id"])[0]["result"]
        supplier_c = next(
            item
            for item in result["supplier_results"]
            if item["supplier_name"] == "Sterling Components"
        )
        correction = service.correct_field(
            task_id=task["task_id"],
            quote_id=supplier_c["quote_id"],
            field_name="shipping_fee_amount",
            expected_task_revision=revision + 2,
            raw_value="S$0.00",
            normalized_value="0.00",
            unit="SGD",
            reason="PostgreSQL correction recovery check.",
            idempotency_key=f"correction-{test_run_id}",
        )
        correction_processor = CanonicalCsvProcessor(tmp_path)
        with PostgresSaver.from_conn_string(connection_string) as fourth_saver:
            corrected = WorkflowRunner(
                service,
                processor=correction_processor,
                checkpointer=fourth_saver,
                dictionary_path=DICTIONARY_PATH,
                evaluated_at=datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc),
            ).run_job(correction["job_id"])
        assert corrected["status"] == "SUCCEEDED"
        assert len(first_processor.calls) == 3
        assert second_processor.calls == []
        assert third_processor.calls == []
        assert correction_processor.calls == []
    finally:
        with engine.begin() as connection:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE thread_id = :thread_id"),
                    {"thread_id": started["graph_run_id"]},
                )
            connection.execute(
                text("DELETE FROM tasks WHERE task_id = :task_id"),
                {"task_id": task["task_id"]},
            )
            connection.execute(
                text("DELETE FROM idempotency_records WHERE actor_id = :actor_id"),
                {"actor_id": "postgres-test-user"},
            )


def test_postgres_serializes_same_revision_quote_uploads(tmp_path: Path) -> None:
    from supplier_comparison.backend.service import ConflictError

    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    actor_id = f"postgres-concurrency-{uuid4().hex}"
    service = BackendService(sessions, tmp_path / "quotes", actor_id=actor_id)
    task = service.create_task(
        _requirement(),
        idempotency_key=f"create-{actor_id}",
        scenario_id="POSTGRES-CONCURRENCY",
    )

    def upload(suffix: str):
        try:
            return (
                "ok",
                service.upload_quote(
                    task["task_id"],
                    expected_task_revision=1,
                    supplier_id=f"SUP-{suffix}",
                    original_filename=f"supplier-{suffix}.pdf",
                    media_type="application/pdf",
                    content=f"quote-{suffix}".encode(),
                    idempotency_key=f"upload-{suffix}-{actor_id}",
                ),
            )
        except ConflictError as exc:
            return "conflict", exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(upload, ("A", "B")))
        assert sorted(kind for kind, _value in outcomes) == ["conflict", "ok"]
        assert next(value for kind, value in outcomes if kind == "conflict") == (
            "task_revision_conflict"
        )
        assert service.get_task(task["task_id"])["task_revision"] == 2
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM tasks WHERE task_id = :task_id"),
                {"task_id": task["task_id"]},
            )
            connection.execute(
                text("DELETE FROM idempotency_records WHERE actor_id = :actor_id"),
                {"actor_id": actor_id},
            )
