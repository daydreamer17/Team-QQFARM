from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from .backend.checkpoints import checkpoint_connection_string
from .backend.conversations import ConversationModelConfig, generate_conversation_turn
from .backend.database import create_session_factory
from .backend.service import BackendError, BackendService
from .backend.settings import settings
from .backend.workflow import DefaultQuoteProcessor, DraftReviewRunner, WorkflowRunner
from .backend.investigation import AgentConfig, AgentLimits, InvestigationRunner, LiveInvestigationPlanner
from .backend.intake import RequirementModelConfig, extract_requirement_candidates, parse_requirement_document
from .backend.summaries import SummaryModelConfig, generate_summary_narrative
from .extraction.dictionary import QuoteDictionary
from .extraction.errors import ExtractionError
from .rag.clients import (
    EmbeddingConfig,
    ModelClientError,
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
    job_type = service.job_type(job_id)
    if job_type == "REQUIREMENT_DRAFT_PARSE":
        context = service.requirement_draft_job_context(job_id)
        calls_used = 0
        try:
            parsed = parse_requirement_document(Path(context["storage_path"]), context["media_type"])
            config = RequirementModelConfig.from_env()
            if config is None:
                raise ModelClientError("requirement model is not configured", attempts=0, error_code="requirement_model_unconfigured")
            candidates, calls_used = extract_requirement_candidates(parsed, config)
            return service.complete_requirement_draft_job(
                job_id, parsed=parsed, candidates=candidates, calls_used=calls_used
            )
        except ModelClientError as exc:
            calls_used += exc.attempts
            service.fail_requirement_draft_job(job_id, code=exc.error_code, message=str(exc), calls_used=calls_used)
            raise
        except ValueError as exc:
            code = str(exc) if str(exc).startswith("requirement_") else "requirement_parse_failed"
            service.fail_requirement_draft_job(job_id, code=code, message="Requirement document could not be parsed.")
            raise BackendError(code, "Requirement document could not be parsed.") from exc
        except Exception as exc:
            service.fail_requirement_draft_job(
                job_id,
                code="requirement_processing_failed",
                message="Requirement processing failed unexpectedly.",
                calls_used=calls_used,
            )
            raise BackendError(
                "requirement_processing_failed",
                "Requirement processing failed unexpectedly.",
            ) from exc
    if job_type == "SUMMARY_GENERATION":
        context = service.summary_job_context(job_id)
        calls_used = context["calls_used"]
        try:
            config = SummaryModelConfig.from_env()
            if config is None:
                raise ModelClientError("summary model is not configured", attempts=0, error_code="summary_model_unconfigured")
            narrative, attempts = generate_summary_narrative(context["facts"], config)
            calls_used += attempts
            return service.complete_summary_job(job_id, narrative=narrative, calls_used=calls_used)
        except ModelClientError as exc:
            calls_used += exc.attempts
            service.fail_summary_job(job_id, code=exc.error_code, message=str(exc), calls_used=calls_used)
            raise
        except Exception as exc:
            service.fail_summary_job(
                job_id,
                code="summary_processing_failed",
                message="Summary processing failed unexpectedly.",
                calls_used=calls_used,
            )
            raise BackendError(
                "summary_processing_failed",
                "Summary processing failed unexpectedly.",
            ) from exc
    if job_type == "DECISION_CONVERSATION":
        return _run_decision_conversation_job(service, job_id)
    if job_type == "DRAFT_REVIEW":
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
    investigator = (
        InvestigationRunner(LiveInvestigationPlanner(AgentConfig.from_env()), limits=AgentLimits.from_env())
        if settings.supplier_agent_enabled else None
    )
    with PostgresSaver.from_conn_string(connection_string) as saver:
        runner = WorkflowRunner(
            service,
            processor=processor,
            policy_retriever=policy_retriever,
            checkpointer=saver,
            dictionary_path=settings.quote_dictionary_path,
            investigator=investigator,
            policy_max_retries=settings.supplier_agent_policy_max_retries,
        )
        return runner.run_job(job_id)


def _run_decision_conversation_job(
    service: BackendService, job_id: str
) -> dict:
    context = service.conversation_job_context(job_id)
    attempts = 0
    try:
        config = ConversationModelConfig.from_env()
        if config is None:
            raise ModelClientError(
                "conversation model is not configured",
                attempts=0,
                error_code="conversation_model_unconfigured",
            )
        turn, attempts = generate_conversation_turn(context, config)
        return service.complete_conversation_job(
            job_id,
            turn=turn,
            attempts=attempts,
            provider=config.provider,
            model_id=config.model_id,
        )
    except ModelClientError as exc:
        attempts += exc.attempts
        service.fail_conversation_job(
            job_id,
            code=exc.error_code,
            message=str(exc),
            attempts=attempts,
        )
        raise
    except Exception as exc:
        service.fail_conversation_job(
            job_id,
            code="conversation_processing_failed",
            message="Conversation processing failed unexpectedly.",
            attempts=attempts,
        )
        raise BackendError(
            "conversation_processing_failed",
            "Conversation processing failed unexpectedly.",
        ) from exc


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
