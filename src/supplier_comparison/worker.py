from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from langgraph.checkpoint.postgres import PostgresSaver

from .backend.checkpoints import checkpoint_connection_string
from .backend.conversations import CONVERSATION_PROMPT_VERSION, ConversationModelConfig, process_conversation_turn
from .backend.decision_intents import CONVERSATION_INTENT_VERSION
from .backend.database import create_session_factory
from .backend.service import BackendError, BackendService
from .backend.settings import settings
from .backend.workflow import DefaultQuoteProcessor, DraftReviewRunner, WorkflowRunner
from .backend.worker_health import write_worker_heartbeat
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
        conversation_job_stale_seconds=settings.supplier_conversation_job_stale_seconds,
        supplier_history_root=settings.supplier_history_root,
        supplier_history_dataset_version=settings.supplier_history_dataset_version,
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
    started_at = time.monotonic()
    stage = "intent"
    try:
        config = ConversationModelConfig.from_env()
        if config is None:
            raise ModelClientError(
                "conversation model is not configured",
                attempts=0,
                error_code="conversation_model_unconfigured",
            )
        def record_stage(next_stage: str, calls: int) -> None:
            nonlocal stage
            stage = next_stage
            service.record_conversation_stage(
                job_id, stage=stage, calls_used=calls, elapsed_seconds=time.monotonic() - started_at,
                provider=config.provider, model_id=config.model_id,
                prompt_version=f"{CONVERSATION_INTENT_VERSION}+{CONVERSATION_PROMPT_VERSION}",
            )

        turn, attempts = process_conversation_turn(context, config, on_stage=record_stage)
        return service.complete_conversation_job(
            job_id,
            turn=turn,
            attempts=attempts,
            provider=config.provider,
            model_id=config.model_id,
            prompt_version=f"{CONVERSATION_INTENT_VERSION}+{CONVERSATION_PROMPT_VERSION}",
        )
    except ModelClientError as exc:
        attempts += exc.attempts
        message = {
            "conversation_model_output_invalid": "本次说明未通过事实与引用核验，未更改正式结果。可以重试生成说明。",
            "conversation_intent_invalid": "未能可靠识别本次请求，尚未生成模拟。请明确主次排序指标、容差或供应商名称后重试。",
            "conversation_intent_mismatch": "回答与已识别的请求不一致，本次未保存，也未更改正式结果。请重试。",
        }.get(exc.error_code, str(exc))
        service.fail_conversation_job(
            job_id,
            code=exc.error_code,
            message=message,
            attempts=attempts,
            diagnostic=f"stage={stage}; elapsed={time.monotonic()-started_at:.3f}s; {str(exc)}"[:1000],
        )
        raise
    except BackendError as exc:
        messages = {
            "selection_review_required": "参与本次模拟的报价仍有待审核字段。请到“待处理事项／集中审核”确认这些字段，重新分析后再生成模拟；已排除报价重新纳入时也需要通过审核。",
            "conversation_stale": "当前对话依据的结果已过期。请打开最新决策结果后重新提出模拟请求。",
            "selection_input_stale": "计算期间任务版本发生变化，本次未保存。请刷新最新结果后重试。",
            "conversation_model_output_invalid": "本次回复在保存前校验失败，未更改正式结果。请重试；若持续失败，请提供任务编号。",
        }
        service.fail_conversation_job(
            job_id, code=exc.code,
            message=messages.get(exc.code, "本次模拟或保存未完成。请核对当前版本与待审核字段，再重试。"),
            attempts=attempts,
            diagnostic=f"stage={stage}; elapsed={time.monotonic()-started_at:.3f}s; code={exc.code}; {str(exc)}"[:1000],
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
            conversation_job_stale_seconds=settings.supplier_conversation_job_stale_seconds,
            supplier_history_root=settings.supplier_history_root,
            supplier_history_dataset_version=settings.supplier_history_dataset_version,
        )
    execute = execute_job or run_job
    processed = 0
    heartbeat_stop = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    if owned_engine is not None:
        def heartbeat_loop() -> None:
            while not heartbeat_stop.is_set():
                write_worker_heartbeat(settings.quote_storage_path)
                heartbeat_stop.wait(settings.supplier_worker_heartbeat_interval_seconds)

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name="supplier-worker-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
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
        heartbeat_stop.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=2)
        if owned_engine is not None:
            owned_engine.dispose()


def _error_payload(job_id: str, exc: Exception) -> dict:
    known_error = isinstance(exc, (BackendError, ExtractionError, ModelClientError))
    code = (
        exc.error_code
        if isinstance(exc, ModelClientError)
        else exc.code
        if isinstance(exc, (BackendError, ExtractionError))
        else "worker_failed"
    )
    return {
        "error": {
            "code": code,
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
