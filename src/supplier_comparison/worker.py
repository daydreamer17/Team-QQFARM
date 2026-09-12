from __future__ import annotations

import argparse
import json
import sys

from langgraph.checkpoint.postgres import PostgresSaver

from .backend.checkpoints import checkpoint_connection_string
from .backend.database import create_session_factory
from .backend.service import BackendService
from .backend.settings import settings
from .backend.workflow import DefaultQuoteProcessor, WorkflowRunner
from .extraction.dictionary import QuoteDictionary


def run_job(job_id: str) -> dict:
    _engine, sessions = create_session_factory(settings.database_url)
    service = BackendService(
        sessions, settings.quote_storage_path, actor_id=settings.test_user_id
    )
    dictionary = QuoteDictionary.load(settings.quote_dictionary_path)
    processor = DefaultQuoteProcessor(dictionary)
    connection_string = checkpoint_connection_string(settings.database_url)
    with PostgresSaver.from_conn_string(connection_string) as saver:
        runner = WorkflowRunner(
            service,
            processor=processor,
            checkpointer=saver,
            dictionary_path=settings.quote_dictionary_path,
        )
        return runner.run_job(job_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Supplier Comparison job")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run-job")
    run_parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_job(args.job_id)
    except Exception:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "worker_failed",
                        "message": "Workflow execution failed.",
                    },
                    "job_id": args.job_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
