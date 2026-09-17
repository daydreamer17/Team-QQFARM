from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable

from langgraph.checkpoint.postgres import PostgresSaver

from .backend.checkpoints import checkpoint_connection_string
from .backend.database import create_session_factory
from .backend.service import BackendError, BackendService
from .backend.settings import settings
from .backend.workflow import DefaultQuoteProcessor, DraftReviewRunner, WorkflowRunner
from .extraction.dictionary import QuoteDictionary
from .extraction.errors import ExtractionError
from .rag.clients import (
    EmbeddingConfig,
    RerankConfig,
    SiliconFlowEmbeddingClient,
    SiliconFlowRerankClient,
)
from .rag.repository import SQLPolicyRepository
from .rag.retriever import HybridPolicyRetriever


def run_job(job_id: str) -> dict:
    _engine, sessions = create_session_factory(settings.database_url)
    service = BackendService(
        sessions,
        settings.quote_storage_path,
        actor_id=settings.test_user_id,
        quote_dictionary_path=settings.quote_dictionary_path,
    )
    dictionary = QuoteDictionary.load(settings.quote_dictionary_path)
    processor = DefaultQuoteProcessor(dictionary)
    if service.job_type(job_id) == "DRAFT_REVIEW":
        return DraftReviewRunner(
            service,
            processor=processor,
            dictionary_path=settings.quote_dictionary_path,
        ).run_job(job_id)
    policy_retriever = HybridPolicyRetriever(
        SQLPolicyRepository(sessions),
        SiliconFlowEmbeddingClient(EmbeddingConfig.from_env()),
        SiliconFlowRerankClient(RerankConfig.from_env()),
    )
    connection_string = checkpoint_connection_string(settings.database_url)
    with PostgresSaver.from_conn_string(connection_string) as saver:
        runner = WorkflowRunner(
            service,
            processor=processor,
            policy_retriever=policy_retriever,
            checkpointer=saver,
            dictionary_path=settings.quote_dictionary_path,
        )
        return runner.run_job(job_id)


def run_loop(
    poll_interval_seconds: float = 1.0,
    *,
    stop_when_idle: bool = False,
    service: BackendService | None = None,
    execute_job: Callable[[str], dict] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Continuously execute persisted jobs for the single-worker deployment."""

    owned_engine = None
    if service is None:
        owned_engine, sessions = create_session_factory(settings.database_url)
        service = BackendService(
            sessions,
            settings.quote_storage_path,
            actor_id=settings.test_user_id,
            quote_dictionary_path=settings.quote_dictionary_path,
        )
    execute = execute_job or run_job
    processed = 0
    try:
        while True:
            job_id = service.next_pending_job_id()
            if job_id is None:
                if stop_when_idle:
                    return {"status": "IDLE", "processed_jobs": processed}
                sleeper(poll_interval_seconds)
                continue
            try:
                result = execute(job_id)
            except Exception as exc:
                print(json.dumps(_error_payload(job_id, exc), ensure_ascii=False, sort_keys=True), file=sys.stderr)
            else:
                processed += 1
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    except KeyboardInterrupt:
        return {"status": "STOPPED", "processed_jobs": processed}
    finally:
        if owned_engine is not None:
            owned_engine.dispose()


def _error_payload(job_id: str, exc: Exception) -> dict:
    known_error = isinstance(exc, (BackendError, ExtractionError))
    return {
        "error": {
            "code": exc.code if known_error else "worker_failed",
            "message": str(exc) if known_error else "Workflow execution failed.",
        },
        "job_id": job_id,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Supplier Comparison job")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-job")
    run_parser.add_argument("--job-id", required=True)
    loop_parser = subparsers.add_parser("run-loop")
    loop_parser.add_argument("--poll-interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.command == "run-loop":
        if args.poll_interval <= 0:
            parser.error("--poll-interval must be greater than zero")
        result = run_loop(args.poll_interval)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    try:
        result = run_job(args.job_id)
    except Exception as exc:
        print(json.dumps(_error_payload(args.job_id, exc), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
