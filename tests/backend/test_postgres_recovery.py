from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.checkpoints import checkpoint_connection_string
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.settings import settings
from supplier_comparison.backend.workflow import WorkflowRunner
from supplier_comparison.backend.investigation import AgentConfig, InvestigationRunner, LiveInvestigationPlanner
from supplier_comparison.backend.models import WorkflowArtifact

from .test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH, _requirement
from .test_workflow_policy_rag import (
    POLICY_INDEX_VERSION,
    POLICY_SET_VERSION,
    RecordingPolicyRetriever,
)
from .test_investigation import ScriptedPlanner, call
from .test_policy_investigation import FlakyRetriever


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 to run the PostgreSQL recovery test",
)


def test_pgvector_extension_is_enabled() -> None:
    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            extension_version = connection.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        assert extension_version is not None
    finally:
        engine.dispose()


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


def test_postgres_workflow_persists_policy_gate_and_citations(tmp_path: Path) -> None:
    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    actor_id = f"postgres-policy-{uuid4().hex}"
    service = BackendService(sessions, tmp_path / "quotes", actor_id=actor_id)
    task = service.create_task(
        _requirement(),
        idempotency_key=f"create-{actor_id}",
        scenario_id="POSTGRES-POLICY-GATE",
        policy_set_version=POLICY_SET_VERSION,
        policy_index_version=POLICY_INDEX_VERSION,
        policy_category="Electronics",
        policy_region="SG",
    )
    uploaded = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-024",
        original_filename="supplier-c.csv",
        media_type="text/csv",
        content=b"postgres-policy-placeholder",
        idempotency_key=f"upload-{actor_id}",
        is_synthetic=True,
    )
    started = service.start_run(
        task["task_id"],
        expected_task_revision=uploaded["task_revision"],
        idempotency_key=f"run-{actor_id}",
    )
    connection_string = checkpoint_connection_string(database_url)
    try:
        with PostgresSaver.from_conn_string(connection_string) as saver:
            outcome = WorkflowRunner(
                service,
                processor=CanonicalCsvProcessor(tmp_path),
                policy_retriever=RecordingPolicyRetriever(),
                checkpointer=saver,
                dictionary_path=DICTIONARY_PATH,
                evaluated_at=datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc),
            ).run_job(started["job_id"])
        assert outcome["status"] == "SUCCEEDED"

        engine.dispose()
        reopened_engine = create_engine(database_url)
        reopened_sessions = sessionmaker(reopened_engine, expire_on_commit=False)
        reopened_service = BackendService(
            reopened_sessions, tmp_path / "quotes", actor_id=actor_id
        )
        current = reopened_service.list_results(task["task_id"])[0]
        assert current["is_current"] is True
        assert len(current["policy_retrievals"]) == 3
        assert {item["status"] for item in current["policy_retrievals"]} == {"OK"}
        reopened_engine.dispose()
    finally:
        cleanup_engine = create_engine(database_url)
        with cleanup_engine.begin() as connection:
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
                {"actor_id": actor_id},
            )
        cleanup_engine.dispose()


@pytest.fixture
def postgres_agent_task(tmp_path):
    """Unique owner, task and checkpoint; cleanup never touches another run."""
    url = os.getenv('TEST_DATABASE_URL', settings.database_url)
    engine = create_engine(url, connect_args={'connect_timeout': 5})
    service = BackendService(sessionmaker(engine, expire_on_commit=False), tmp_path / 'quotes',
                             actor_id='pg-agent-' + uuid4().hex)
    task = service.create_task(_requirement(), idempotency_key='create',
        policy_set_version=POLICY_SET_VERSION, policy_index_version=POLICY_INDEX_VERSION,
        policy_category='Electronics', policy_region='SG')
    upload = service.upload_quote(task['task_id'], expected_task_revision=1, supplier_id='SUP-024',
        original_filename='synthetic-c.csv', media_type='text/csv', content=b'pg-agent-synthetic',
        idempotency_key='upload', is_synthetic=True)
    started = service.start_run(task['task_id'], expected_task_revision=upload['task_revision'], idempotency_key='start')
    try:
        yield url, engine, service, task, started
    finally:
        engine.dispose()
        cleanup = create_engine(url, connect_args={'connect_timeout': 5})
        try:
            with cleanup.begin() as connection:
                for table in ('checkpoint_writes', 'checkpoint_blobs', 'checkpoints'):
                    connection.execute(text(f'DELETE FROM {table} WHERE thread_id = :thread_id'),
                                       {'thread_id': started['graph_run_id']})
                connection.execute(text('DELETE FROM tasks WHERE task_id = :task_id'), {'task_id': task['task_id']})
                connection.execute(text('DELETE FROM idempotency_records WHERE actor_id = :actor'), {'actor': service.actor_id})
        finally:
            cleanup.dispose()


def test_postgres_agent_investigation_retry_cap_survives_new_connection_and_resume(postgres_agent_task, tmp_path):
    url, engine, service, task, started = postgres_agent_task
    retriever = FlakyRetriever(failures=99)
    planner = ScriptedPlanner([call('get_policy_retrieval_status'), call('retry_policy_retrieval'),
                               call('retry_policy_retrieval'), call('request_clarification')])
    with PostgresSaver.from_conn_string(checkpoint_connection_string(url)) as saver:
        first = WorkflowRunner(service, processor=CanonicalCsvProcessor(tmp_path), checkpointer=saver,
            dictionary_path=DICTIONARY_PATH, policy_retriever=retriever,
            investigator=InvestigationRunner(planner),
            evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)).run_job(started['job_id'])
    assert first['status'] == 'WAITING_INPUT'
    assert retriever.rohs_calls == 3  # original plus two reserved automatic retries
    engine.dispose()
    reopened = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        recovered = BackendService(sessionmaker(reopened, expire_on_commit=False), tmp_path / 'quotes', actor_id=service.actor_id)
        record = recovered.list_investigations(task['task_id'])[0]
        assert record['kind'] == 'POLICY' and record['status'] == 'WAITING_INPUT'
        assert record['model_calls'] == 4 and len(record['observations']) == 4
        assert record['observations'][1]['result']['data']['retrieval_artifact_id']
        assert recovered.reserve_policy_retry(task['task_id'], graph_run_id=started['graph_run_id'],
                                              task_revision=2, max_attempts=2, payload={}) is False
        with recovered.session_factory() as session:
            attempts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task['task_id'], WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT')).all()
            assert len(attempts) == 2
            assert attempts[0].payload['request'] == attempts[1].payload['request']
        resumed = recovered.answer_issue(task['task_id'], first['issue']['issue_id'], expected_task_revision=2,
            answer={'answer_type': 'RETRY_POLICY_RETRIEVAL'}, idempotency_key='operator-repaired-retry')
        processor = CanonicalCsvProcessor(tmp_path)
        with PostgresSaver.from_conn_string(checkpoint_connection_string(url)) as saver:
            final = WorkflowRunner(recovered, processor=processor, checkpointer=saver,
                dictionary_path=DICTIONARY_PATH, policy_retriever=RecordingPolicyRetriever(),
                investigator=InvestigationRunner(ScriptedPlanner([])),
                evaluated_at=datetime(2026, 9, 30, tzinfo=timezone.utc)).run_job(resumed['job_id'])
        assert final['status'] == 'SUCCEEDED' and processor.calls == []
        assert recovered.list_results(task['task_id'])[0]['result']['evaluated_at'] == '2026-09-14T01:00:00Z'
        assert recovered.list_investigations(task['task_id'])[0]['status'] == 'STALE'
        assert recovered.reserve_policy_retry(task['task_id'], graph_run_id=started['graph_run_id'],
                                              task_revision=3, max_attempts=2, payload={}) is False
    finally:
        reopened.dispose()


def test_postgres_agent_concurrent_retry_reservations_cannot_exceed_cap(postgres_agent_task):
    url, engine, service, task, started = postgres_agent_task

    def reserve(index):
        return service.reserve_policy_retry(task['task_id'], graph_run_id=started['graph_run_id'],
            task_revision=2, max_attempts=2, payload={'control_code': 'ROHS_COMPLIANCE', 'test_request': index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))
    assert results.count(True) == 2 and results.count(False) == 6
    engine.dispose()
    reopened = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        recovered = BackendService(sessionmaker(reopened, expire_on_commit=False), service.storage_root, actor_id=service.actor_id)
        assert recovered.reserve_policy_retry(task['task_id'], graph_run_id=started['graph_run_id'],
                                              task_revision=2, max_attempts=2, payload={}) is False
        with recovered.session_factory() as session:
            assert len(session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task['task_id'], WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT')).all()) == 2
    finally:
        reopened.dispose()


@pytest.mark.skipif(os.getenv('RUN_AGENT_LIVE_TESTS') != '1', reason='set RUN_AGENT_LIVE_TESTS=1 for paid live Agent acceptance')
def test_postgres_live_agent_policy_recovery_persists_real_tool_observation(postgres_agent_task, tmp_path):
    url, engine, service, task, started = postgres_agent_task
    config = AgentConfig.from_env()
    assert config.model_id and os.getenv(config.api_key_env), 'Configure the existing model and API key.'
    planner = LiveInvestigationPlanner(config)
    retriever = FlakyRetriever(failures=1)
    with PostgresSaver.from_conn_string(checkpoint_connection_string(url)) as saver:
        outcome = WorkflowRunner(service, processor=CanonicalCsvProcessor(tmp_path), checkpointer=saver,
            dictionary_path=DICTIONARY_PATH, policy_retriever=retriever,
            investigator=InvestigationRunner(planner),
            evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)).run_job(started['job_id'])
    assert outcome['status'] == 'SUCCEEDED' and retriever.rohs_calls == 2
    engine.dispose()
    reopened = create_engine(url, connect_args={'connect_timeout': 5})
    try:
        recovered = BackendService(sessionmaker(reopened, expire_on_commit=False), service.storage_root, actor_id=service.actor_id)
        case = recovered.list_investigations(task['task_id'])[0]
        assert case['status'] == 'RESOLVED' and case['stop_reason'] == 'EVIDENCE_CONFIRMED'
        observation = next(o for o in case['observations'] if o['result']['tool_name'] == 'retry_policy_retrieval')
        assert observation['result']['status'] == 'OK' and observation['result']['sources']
        assert recovered.get_task(task['task_id'])['current_result_id'] == outcome['result_id']
        with recovered.session_factory() as session:
            attempts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task['task_id'], WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT')).all()
            assert len(attempts) == 1
        print({'scenario': 'postgres_live_policy_recovery', 'model_id': config.model_id,
               'status': case['status'], 'stop_reason': case['stop_reason'], 'reopened_tool_status': observation['result']['status'],
               'model_calls': case['model_calls']})
    finally:
        reopened.dispose()
