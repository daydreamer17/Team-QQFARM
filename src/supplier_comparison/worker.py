from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from langgraph.checkpoint.postgres import PostgresSaver

from .backend.checkpoints import checkpoint_connection_string
from .backend.conversations import CONVERSATION_PROMPT_VERSION, ConversationModelConfig, process_conversation_turn
from .backend.decision_intents import CONVERSATION_INTENT_VERSION
from .backend.response_language import conversation_response_language
from .backend.database import create_session_factory
from .backend.service import BackendError, BackendService
from .backend.settings import settings
from .backend.workflow import DefaultQuoteProcessor, DraftReviewRunner, WorkflowRunner
from .backend.worker_health import write_worker_heartbeat
from .backend.investigation import AgentConfig, AgentLimits, InvestigationRunner, LiveInvestigationPlanner
from .backend.decision_investigation import DecisionInvestigationTools
from .backend.intake import RequirementModelConfig, extract_requirement_candidates, parse_requirement_document
from .backend.summaries import (
    SummaryModelConfig,
    generate_summary_narrative,
    summary_config_for_remaining_calls,
)
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
            config = summary_config_for_remaining_calls(
                config, context["max_calls"] - calls_used
            )
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
    response_language = conversation_response_language(context)
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

        def investigate(turn_context: dict) -> tuple[dict, int]:
            frozen = service.requested_investigation_context(
                turn_context["task_id"], expected_task_revision=turn_context["task_revision"], quote_id=None,
            )
            tools = DecisionInvestigationTools(service, task_id=turn_context["task_id"], context=frozen)
            latest_question = next((row["content"] for row in reversed(turn_context["recent_messages"])
                                    if row["role"] == "USER"), "")
            case = tools.prepare_case(tools.requested_case(request_id=str(uuid4()), question=latest_question))
            emitted = 0

            def save_and_report(updated):
                nonlocal emitted
                tools.save(updated)
                for observation in updated.observations[emitted:]:
                    service.record_conversation_tool(
                        job_id, tool_name=observation.result.tool_name,
                        status=observation.result.status, sequence=observation.sequence,
                        reason=observation.reason, plan=observation.plan,
                    )
                emitted = len(updated.observations)

            save_and_report(case)
            runner = InvestigationRunner(
                LiveInvestigationPlanner(AgentConfig.from_env()), limits=AgentLimits.from_env(),
            )
            completed = runner.run((case,), tools, save_and_report)[0]
            if not tools.current():
                raise BackendError("conversation_stale", "Investigation inputs changed during the turn.")
            record = next((item for item in service.list_investigations(turn_context["task_id"])
                           if item["case_id"] == completed.case_id), None)
            if record is None:
                raise BackendError("investigation_record_missing", "Investigation record was not saved.")
            reference = "INVESTIGATION:" + record["artifact_id"]
            evidence = {key: record.get(key) for key in (
                "case_id", "kind", "status", "stop_reason", "goal", "unknown_fields", "clarification", "observations",
            )}
            enriched = dict(turn_context)
            enriched["frozen_references"] = dict(turn_context["frozen_references"]) | {reference: evidence}
            enriched["allowed_reference_ids"] = sorted(enriched["frozen_references"])
            enriched["investigation_reference_id"] = reference
            return enriched, completed.model_calls

        turn, attempts = process_conversation_turn(
            context, config, on_stage=record_stage,
            investigate=investigate if settings.supplier_agent_enabled else None,
        )
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
        messages = {
            "conversation_model_output_invalid": "This explanation did not pass factual and citation validation. The official result was not changed. You may regenerate the explanation.",
            "conversation_intent_invalid": "The request could not be interpreted reliably and no simulation was generated. Specify primary and secondary criteria, tolerance, or supplier names and try again.",
            "conversation_intent_mismatch": "The answer did not match the interpreted request. It was not saved and the official result was not changed. Try again.",
        }
        if response_language == "zh":
            messages = {
                "conversation_model_output_invalid": "回答未通过事实与引用校验，正式结果未发生变化。请重新生成。",
                "conversation_intent_invalid": "系统无法可靠识别这次请求，因此没有生成模拟结果。请明确主要、次要指标，成本容差或供应商名称后重试。",
                "conversation_intent_mismatch": "生成的回答与识别出的请求不一致，因此未保存，也未修改正式结果。请重试。",
            }
        message = messages.get(exc.error_code, str(exc))
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
            "selection_review_required": "Quotations included in this simulation still contain fields requiring review. Confirm them under Action Items / Consolidated Review, rerun the analysis, and then generate the simulation. A previously excluded quotation must also pass review before being restored.",
            "conversation_stale": "The result underlying this conversation is stale. Open the latest decision result and submit the simulation request again.",
            "selection_input_stale": "The task revision changed during calculation, so this attempt was not saved. Refresh the latest result and try again.",
            "conversation_model_output_invalid": "This response failed validation before saving and did not change the official result. Try again; if the issue persists, provide the task ID.",
        }
        if response_language == "zh":
            messages = {
                "selection_review_required": "本次模拟包含仍需审核的报价字段。请先在“待处理事项 / 集中审核”中确认字段并重新分析；此前被排除的报价也必须通过审核后才能恢复。",
                "conversation_stale": "该对话基于的决策结果已经过期。请打开最新决策结果后重新提交请求。",
                "selection_input_stale": "计算过程中任务版本发生变化，本次结果未保存。请刷新最新结果后重试。",
                "conversation_model_output_invalid": "回答在保存前未通过校验，正式结果未发生变化。请重新生成；如果仍然失败，请提供任务 ID。",
            }
        service.fail_conversation_job(
            job_id, code=exc.code,
            message=messages.get(exc.code, "This simulation or save operation did not complete. Check the current revision and fields requiring review, then try again."),
            attempts=attempts,
            diagnostic=f"stage={stage}; elapsed={time.monotonic()-started_at:.3f}s; code={exc.code}; {str(exc)}"[:1000],
        )
        raise
    except Exception as exc:
        message = (
            "对话处理时出现意外错误，正式结果未发生变化。请重新生成；如果仍然失败，请提供任务 ID。"
            if response_language == "zh"
            else
            "Conversation processing failed unexpectedly. The official result was not changed. "
            "Regenerate the answer; if it still fails, provide the task ID."
        )
        service.fail_conversation_job(
            job_id,
            code="conversation_processing_failed",
            message=message,
            attempts=attempts,
            diagnostic=(
                f"stage={stage}; elapsed={time.monotonic()-started_at:.3f}s; "
                f"{exc.__class__.__name__}: {str(exc)}"
            )[:1000],
        )
        raise BackendError(
            "conversation_processing_failed",
            message,
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
