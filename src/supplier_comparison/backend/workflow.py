from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from supplier_comparison.extraction import (
    CorrectionAction,
    CriticalityContext,
    DocumentContext,
    ExtractionBatch,
    HumanReviewAction,
    ReviewEnvelope,
    CorrectionEvent,
    apply_candidate_correction,
    create_review_event,
    merge_semantic_review,
    review_extraction_batch,
    selected_dictionary,
)
from supplier_comparison.extraction.adapters import (
    ModelAdapter,
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.hybrid_csv import RegisteredHybridCsvParser
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates
from supplier_comparison.rules import ProcurementRequirement, compare_reviewed_extractions
from supplier_comparison.rules.contracts import RULE_VERSION

from .service import BackendError, BackendService, ConflictError, new_id


class QuoteProcessor(Protocol):
    def process(
        self,
        *,
        path: Path,
        media_type: str,
        context: DocumentContext,
        budget: ModelCallBudget,
    ) -> ExtractionBatch: ...


class DefaultQuoteProcessor:
    """Production B boundary for native PDF and registered CSV inputs."""

    def __init__(
        self,
        dictionary: QuoteDictionary,
        *,
        adapter: ModelAdapter | None = None,
    ) -> None:
        self.dictionary = dictionary
        self.adapter = adapter

    def _adapter(self) -> ModelAdapter:
        if self.adapter is None:
            self.adapter = OpenAICompatibleAdapter(OpenAICompatibleConfig.from_env())
        return self.adapter

    def process(
        self,
        *,
        path: Path,
        media_type: str,
        context: DocumentContext,
        budget: ModelCallBudget,
    ) -> ExtractionBatch:
        extraction_run_id = new_id("extract")
        if media_type == "application/pdf":
            parsed = PdfQuoteParser().parse(path, context)
            return extract_quote_candidates(
                parsed,
                self.dictionary,
                self._adapter(),
                budget,
                extraction_run_id,
            )
        if media_type == "text/csv":
            hybrid = RegisteredHybridCsvParser(self.dictionary).parse_row(path, context)
            if not hybrid.semantic_review_fields:
                return hybrid.batch
            semantic_dictionary = selected_dictionary(
                self.dictionary, hybrid.semantic_review_fields
            )
            semantic_batch = extract_quote_candidates(
                hybrid.batch.parsed_input,
                semantic_dictionary,
                self._adapter(),
                budget,
                extraction_run_id,
            )
            return merge_semantic_review(
                hybrid.batch, semantic_batch, hybrid.semantic_review_fields
            )
        raise BackendError(
            "unsupported_media_type",
            "Only application/pdf and text/csv quote files are supported.",
        )


class WorkflowState(TypedDict, total=False):
    task_id: str
    graph_run_id: str
    task_revision: int
    batch_artifact_ids: dict[str, str]
    review_artifact_ids: dict[str, str]
    target_quote_id: str
    missing_issue_id: str
    shipping_issue_id: str
    draft_result_id: str
    final_result_id: str
    evaluated_at: str


class WorkflowRunner:
    def __init__(
        self,
        service: BackendService,
        *,
        processor: QuoteProcessor,
        checkpointer,
        dictionary_path: str | Path,
        evaluated_at: datetime | None = None,
    ) -> None:
        self.service = service
        self.processor = processor
        self.dictionary_path = Path(dictionary_path)
        self.dictionary = QuoteDictionary.load(self.dictionary_path)
        self.dictionary_sha256 = hashlib.sha256(
            self.dictionary_path.read_bytes()
        ).hexdigest()
        self.evaluated_at = evaluated_at or datetime.now(timezone.utc)
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        builder = StateGraph(WorkflowState)
        builder.add_node("load_context", self._load_context)
        builder.add_node("extract_documents", self._extract_documents)
        builder.add_node("review_quotes", self._review_quotes)
        builder.add_node("await_missing_confirmation", self._await_missing_confirmation)
        builder.add_node("apply_missing_confirmation", self._apply_missing_confirmation)
        builder.add_node("freeze_draft_and_compare", self._freeze_draft_and_compare)
        builder.add_node("await_shipping_amount", self._await_shipping_amount)
        builder.add_node("apply_shipping_amount", self._apply_shipping_amount)
        builder.add_node("freeze_final_and_compare", self._freeze_final_and_compare)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "extract_documents")
        builder.add_edge("extract_documents", "review_quotes")
        builder.add_conditional_edges(
            "review_quotes",
            self._route_after_review,
            {
                "needs_shipping_confirmation": "await_missing_confirmation",
                "ready": "freeze_final_and_compare",
            },
        )
        builder.add_edge("await_missing_confirmation", "apply_missing_confirmation")
        builder.add_edge("apply_missing_confirmation", "freeze_draft_and_compare")
        builder.add_edge("freeze_draft_and_compare", "await_shipping_amount")
        builder.add_edge("await_shipping_amount", "apply_shipping_amount")
        builder.add_edge("apply_shipping_amount", "freeze_final_and_compare")
        builder.add_edge("freeze_final_and_compare", END)
        self.graph = builder.compile(checkpointer=checkpointer)

    def run_job(self, job_id: str) -> dict[str, Any]:
        job = self.service.claim_job(job_id)
        config = {"configurable": {"thread_id": job["graph_run_id"]}}
        try:
            if job["job_type"] == "START":
                result = self.graph.invoke(
                    {
                        "task_id": job["task_id"],
                        "graph_run_id": job["graph_run_id"],
                        "task_revision": job["task_revision"],
                    },
                    config=config,
                )
            else:
                result = self.graph.invoke(
                    Command(resume={"issue_id": job["issue_id"]}), config=config
                )
            waiting = bool(result.get("__interrupt__"))
            finished = self.service.finish_job(job_id, waiting_input=waiting)
            response: dict[str, Any] = {
                "job_id": job_id,
                "graph_run_id": job["graph_run_id"],
                "status": finished["status"],
            }
            if waiting:
                response["issue"] = self.service.current_issue(job["graph_run_id"])
            elif result.get("final_result_id"):
                response["result_id"] = result["final_result_id"]
            return response
        except Exception as exc:
            code = exc.code if isinstance(exc, BackendError) else "workflow_failed"
            message = (
                exc.message
                if isinstance(exc, BackendError)
                else "Workflow execution failed."
            )
            self.service.fail_job(job_id, code=code, message=message)
            raise

    def _load_context(self, state: WorkflowState) -> WorkflowState:
        context = self.service.workflow_context(state["graph_run_id"])
        if not context["requirement"]:
            raise BackendError("requirement_missing", "Task requirement is missing.")
        if not context["documents"]:
            raise BackendError("quotes_missing", "Task has no active quote documents.")
        return {
            "task_revision": context["effective_revision"],
            "evaluated_at": state.get("evaluated_at") or self.evaluated_at.isoformat(),
        }

    def _extract_documents(self, state: WorkflowState) -> WorkflowState:
        workflow_context = self.service.workflow_context(state["graph_run_id"])
        batch_ids: dict[str, str] = {}
        for document in workflow_context["documents"]:
            execution = self.service.ensure_document_execution(
                graph_run_id=state["graph_run_id"],
                document_id=document["document_id"],
                max_calls=8,
            )
            if execution["batch_artifact_id"]:
                batch_ids[document["quote_id"]] = execution["batch_artifact_id"]
                continue
            context = DocumentContext(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                scenario_id=workflow_context["scenario_id"],
                quote_id=document["quote_id"],
                quote_version=document["quote_version"],
                document_id=document["document_id"],
                document_version=document["document_version"],
                supplier_id=document["supplier_id"],
            )
            budget = ModelCallBudget(
                graph_run_id=state["graph_run_id"],
                calls_used=execution["calls_used"],
                max_calls=execution["max_calls"],
            )
            try:
                batch = self.processor.process(
                    path=Path(document["storage_path"]),
                    media_type=document["media_type"],
                    context=context,
                    budget=budget,
                )
            finally:
                self.service.record_document_calls(
                    execution["document_execution_id"],
                    expected_calls_used=execution["calls_used"],
                    calls_after=budget.calls_used,
                )
            parsed_artifact = self.service.append_artifact(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                artifact_type="PARSED_INPUT",
                schema_version=batch.schema_version,
                payload=batch.parsed_input.model_dump(mode="json"),
                quote_id=document["quote_id"],
                document_id=document["document_id"],
                graph_run_id=state["graph_run_id"],
            )
            batch_artifact = self.service.append_artifact(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                artifact_type="EXTRACTION_BATCH",
                schema_version=batch.schema_version,
                payload=batch.model_dump(mode="json"),
                parent_artifact_id=parsed_artifact["artifact_id"],
                quote_id=document["quote_id"],
                document_id=document["document_id"],
                graph_run_id=state["graph_run_id"],
            )
            self.service.link_document_artifacts(
                execution["document_execution_id"],
                parsed_artifact_id=parsed_artifact["artifact_id"],
                batch_artifact_id=batch_artifact["artifact_id"],
                status="EXTRACTED",
            )
            batch_ids[document["quote_id"]] = batch_artifact["artifact_id"]
        return {"batch_artifact_ids": batch_ids}

    def _review_quotes(self, state: WorkflowState) -> WorkflowState:
        context = self.service.workflow_context(state["graph_run_id"])
        review_ids: dict[str, str] = {}
        requirement = ProcurementRequirement.model_validate(context["requirement"])
        documents = {item["quote_id"]: item for item in context["documents"]}
        for quote_id, batch_id in state["batch_artifact_ids"].items():
            batch = ExtractionBatch.model_validate(self.service.artifact_payload(batch_id))
            corrections = tuple(
                CorrectionEvent.model_validate(payload)
                for payload in self.service.correction_event_payloads_for_batch(batch_id)
            )
            envelope = self._review(
                batch,
                requirement,
                documents[quote_id]["is_synthetic"],
                reviewed_at=self._state_evaluated_at(state),
                corrections=corrections,
            )
            artifact = self.service.append_artifact(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                artifact_type="REVIEW_ENVELOPE",
                schema_version=envelope.schema_version,
                payload=envelope.model_dump(mode="json"),
                parent_artifact_id=batch_id,
                quote_id=quote_id,
                document_id=documents[quote_id]["document_id"],
                graph_run_id=state["graph_run_id"],
            )
            execution = self.service.ensure_document_execution(
                graph_run_id=state["graph_run_id"],
                document_id=documents[quote_id]["document_id"],
                max_calls=8,
            )
            self.service.link_document_artifacts(
                execution["document_execution_id"],
                review_artifact_id=artifact["artifact_id"],
                status="REVIEWED",
            )
            review_ids[quote_id] = artifact["artifact_id"]
        return {"review_artifact_ids": review_ids}

    def _route_after_review(self, state: WorkflowState) -> str:
        missing = []
        not_ready = []
        for quote_id, artifact_id in state["review_artifact_ids"].items():
            envelope = ReviewEnvelope.model_validate(
                self.service.artifact_payload(artifact_id)
            )
            if envelope.downstream_ready:
                continue
            not_ready.append(quote_id)
            if (
                envelope.review is not None
                and "shipping_fee_status" in envelope.review.blocking_fields
            ):
                missing.append(quote_id)
        if not not_ready:
            return "ready"
        if len(not_ready) == 1 and missing == not_ready:
            return "needs_shipping_confirmation"
        raise BackendError(
            "review_required",
            "One or more quotes require a correction before comparison.",
            quote_ids=sorted(not_ready),
        )

    def _await_missing_confirmation(self, state: WorkflowState) -> WorkflowState:
        target_quote_id = self._missing_shipping_quote(state)
        issue = self.service.open_issue(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            issue_type="CONFIRM_MISSING",
            quote_id=target_quote_id,
            field_name="shipping_fee_status",
            question="请确认该报价 PDF/CSV 确实没有提供运费信息。",
            answer_schema={"answer_type": "CONFIRM_MISSING"},
        )
        resumed = interrupt(self._safe_interrupt(issue))
        if not isinstance(resumed, dict) or resumed.get("issue_id") != issue["issue_id"]:
            raise ConflictError("resume_issue_mismatch", "Resume token does not match the issue.")
        resolved = self.service.get_issue(issue["issue_id"])
        if resolved["status"] != "RESOLVED":
            raise ConflictError("issue_not_resolved", "Issue must be resolved before resume.")
        return {
            "target_quote_id": target_quote_id,
            "missing_issue_id": issue["issue_id"],
            "task_revision": resolved["resolved_revision"],
        }

    def _apply_missing_confirmation(self, state: WorkflowState) -> WorkflowState:
        context = self.service.workflow_context(state["graph_run_id"])
        quote_id = state["target_quote_id"]
        batch_id = state["batch_artifact_ids"][quote_id]
        batch = ExtractionBatch.model_validate(self.service.artifact_payload(batch_id))
        issue = self.service.get_issue(state["missing_issue_id"])
        reviewed_at = datetime.fromisoformat(issue["answered_at"])
        event = create_review_event(
            batch,
            field_name="shipping_fee_status",
            action=HumanReviewAction.CONFIRM_MISSING,
            reviewer_id=issue["answered_by"],
            reviewed_at=reviewed_at,
        )
        event_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="REVIEW_EVENT",
            payload=event.model_dump(mode="json"),
            parent_artifact_id=batch_id,
            quote_id=quote_id,
            graph_run_id=state["graph_run_id"],
        )
        requirement = ProcurementRequirement.model_validate(context["requirement"])
        document = next(
            item for item in context["documents"] if item["quote_id"] == quote_id
        )
        envelope = self._review(
            batch,
            requirement,
            document["is_synthetic"],
            reviewed_at=self._state_evaluated_at(state),
            review_events=(event,),
        )
        review_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="REVIEW_ENVELOPE",
            schema_version=envelope.schema_version,
            payload=envelope.model_dump(mode="json"),
            parent_artifact_id=event_artifact["artifact_id"],
            quote_id=quote_id,
            document_id=document["document_id"],
            graph_run_id=state["graph_run_id"],
        )
        execution = self.service.ensure_document_execution(
            graph_run_id=state["graph_run_id"],
            document_id=document["document_id"],
            max_calls=8,
        )
        self.service.link_document_artifacts(
            execution["document_execution_id"],
            review_artifact_id=review_artifact["artifact_id"],
            status="REVIEWED",
        )
        review_ids = dict(state["review_artifact_ids"])
        review_ids[quote_id] = review_artifact["artifact_id"]
        return {"review_artifact_ids": review_ids}

    def _freeze_draft_and_compare(self, state: WorkflowState) -> WorkflowState:
        snapshot_id, result_id = self._freeze_compare_publish(state)
        return {"draft_result_id": result_id}

    def _await_shipping_amount(self, state: WorkflowState) -> WorkflowState:
        issue = self.service.open_issue(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            issue_type="SHIPPING_AMOUNT",
            quote_id=state["target_quote_id"],
            field_name="shipping_fee_amount",
            question="请提供该供应商的 SGD 运费金额。",
            answer_schema={
                "answer_type": "SHIPPING_AMOUNT",
                "amount": "decimal-string",
                "currency": "SGD",
            },
        )
        resumed = interrupt(self._safe_interrupt(issue))
        if not isinstance(resumed, dict) or resumed.get("issue_id") != issue["issue_id"]:
            raise ConflictError("resume_issue_mismatch", "Resume token does not match the issue.")
        resolved = self.service.get_issue(issue["issue_id"])
        if resolved["status"] != "RESOLVED":
            raise ConflictError("issue_not_resolved", "Issue must be resolved before resume.")
        return {
            "shipping_issue_id": issue["issue_id"],
            "task_revision": resolved["resolved_revision"],
        }

    def _apply_shipping_amount(self, state: WorkflowState) -> WorkflowState:
        context = self.service.workflow_context(state["graph_run_id"])
        quote_id = state["target_quote_id"]
        prior_envelope = ReviewEnvelope.model_validate(
            self.service.artifact_payload(state["review_artifact_ids"][quote_id])
        )
        if prior_envelope.batch is None:
            raise BackendError("review_batch_missing", "Review envelope has no extraction batch.")
        issue = self.service.get_issue(state["shipping_issue_id"])
        answer = issue["answer"]
        reviewed_at = datetime.fromisoformat(issue["answered_at"])
        with_status, status_event = apply_candidate_correction(
            prior_envelope.batch,
            field_name="shipping_fee_status",
            action=CorrectionAction.USER_INPUT,
            raw_value="KNOWN_AMOUNT",
            normalized_value="KNOWN_AMOUNT",
            unit=None,
            reason_code="AUTHORIZED_SHIPPING_ANSWER",
            reason="Buyer supplied the missing shipping status.",
            reviewer_id=issue["answered_by"],
            reviewed_at=reviewed_at,
        )
        corrected, amount_event = apply_candidate_correction(
            with_status,
            field_name="shipping_fee_amount",
            action=CorrectionAction.USER_INPUT,
            raw_value=f"{answer['currency']} {answer['amount']}",
            normalized_value=answer["amount"],
            unit=answer["currency"],
            reason_code="AUTHORIZED_SHIPPING_ANSWER",
            reason="Buyer supplied the missing shipping amount.",
            reviewer_id=issue["answered_by"],
            reviewed_at=reviewed_at,
        )
        batch_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="EXTRACTION_BATCH",
            schema_version=corrected.schema_version,
            payload=corrected.model_dump(mode="json"),
            parent_artifact_id=state["batch_artifact_ids"][quote_id],
            quote_id=quote_id,
            graph_run_id=state["graph_run_id"],
        )
        last_parent = batch_artifact["artifact_id"]
        for event in (status_event, amount_event):
            saved = self.service.append_artifact(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                artifact_type="CORRECTION_EVENT",
                payload=event.model_dump(mode="json"),
                parent_artifact_id=last_parent,
                quote_id=quote_id,
                graph_run_id=state["graph_run_id"],
            )
            last_parent = saved["artifact_id"]
        requirement = ProcurementRequirement.model_validate(context["requirement"])
        document = next(
            item for item in context["documents"] if item["quote_id"] == quote_id
        )
        envelope = self._review(
            corrected,
            requirement,
            document["is_synthetic"],
            reviewed_at=self._state_evaluated_at(state),
            corrections=(status_event, amount_event),
        )
        review_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="REVIEW_ENVELOPE",
            schema_version=envelope.schema_version,
            payload=envelope.model_dump(mode="json"),
            parent_artifact_id=last_parent,
            quote_id=quote_id,
            document_id=document["document_id"],
            graph_run_id=state["graph_run_id"],
        )
        execution = self.service.ensure_document_execution(
            graph_run_id=state["graph_run_id"],
            document_id=document["document_id"],
            max_calls=8,
        )
        self.service.link_document_artifacts(
            execution["document_execution_id"],
            review_artifact_id=review_artifact["artifact_id"],
            status="REVIEWED",
        )
        batch_ids = dict(state["batch_artifact_ids"])
        review_ids = dict(state["review_artifact_ids"])
        batch_ids[quote_id] = batch_artifact["artifact_id"]
        review_ids[quote_id] = review_artifact["artifact_id"]
        return {
            "batch_artifact_ids": batch_ids,
            "review_artifact_ids": review_ids,
        }

    def _freeze_final_and_compare(self, state: WorkflowState) -> WorkflowState:
        _snapshot_id, result_id = self._freeze_compare_publish(state)
        return {"final_result_id": result_id}

    def _freeze_compare_publish(self, state: WorkflowState) -> tuple[str, str]:
        context = self.service.workflow_context(state["graph_run_id"])
        envelopes = tuple(
            ReviewEnvelope.model_validate(self.service.artifact_payload(artifact_id))
            for _quote_id, artifact_id in sorted(state["review_artifact_ids"].items())
        )
        requirement = ProcurementRequirement.model_validate(context["requirement"])
        evaluated_at = self._state_evaluated_at(state)
        result = compare_reviewed_extractions(
            requirement, envelopes, evaluated_at=evaluated_at
        )
        snapshot = {
            "task_id": state["task_id"],
            "task_revision": state["task_revision"],
            "graph_run_id": state["graph_run_id"],
            "requirement": context["requirement"],
            "batch_artifact_ids": state["batch_artifact_ids"],
            "review_artifact_ids": state["review_artifact_ids"],
            "dictionary_version": self.dictionary.version,
            "dictionary_sha256": self.dictionary_sha256,
            "rule_version": RULE_VERSION,
            "policy_set_version": None,
            "evaluated_at": evaluated_at.isoformat(),
        }
        snapshot_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="INPUT_SNAPSHOT",
            schema_version="1.0",
            payload=snapshot,
            graph_run_id=state["graph_run_id"],
        )
        result_artifact = self.service.append_artifact(
            task_id=state["task_id"],
            task_revision=state["task_revision"],
            artifact_type="COMPARISON_RESULT",
            schema_version="1.0",
            payload=result.model_dump(mode="json"),
            parent_artifact_id=snapshot_artifact["artifact_id"],
            graph_run_id=state["graph_run_id"],
        )
        self.service.publish_result(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            snapshot_id=snapshot_artifact["artifact_id"],
            result_id=result_artifact["artifact_id"],
        )
        return snapshot_artifact["artifact_id"], result_artifact["artifact_id"]

    def _missing_shipping_quote(self, state: WorkflowState) -> str:
        matches: list[str] = []
        for quote_id, artifact_id in state["review_artifact_ids"].items():
            envelope = ReviewEnvelope.model_validate(self.service.artifact_payload(artifact_id))
            if (
                envelope.review is not None
                and "shipping_fee_status" in envelope.review.blocking_fields
            ):
                matches.append(quote_id)
        if len(matches) != 1:
            raise BackendError(
                "shipping_confirmation_scope_invalid",
                "Week1 workflow requires exactly one quote with missing shipping status.",
                matching_quote_count=len(matches),
            )
        return matches[0]

    def _review(
        self,
        batch: ExtractionBatch,
        requirement: ProcurementRequirement,
        input_is_synthetic: bool,
        *,
        reviewed_at: datetime,
        review_events=(),
        corrections=(),
    ) -> ReviewEnvelope:
        return review_extraction_batch(
            batch,
            self.dictionary,
            CriticalityContext(
                required_revision=requirement.revision,
                base_unit=requirement.base_unit,
            ),
            input_is_synthetic=input_is_synthetic,
            reviewed_at=reviewed_at,
            review_events=tuple(review_events),
            corrections=tuple(corrections),
        )

    @staticmethod
    def _state_evaluated_at(state: WorkflowState) -> datetime:
        value = state.get("evaluated_at")
        if not value:
            raise BackendError(
                "evaluation_time_missing",
                "Workflow state does not contain its frozen evaluation time.",
            )
        evaluated_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise BackendError(
                "evaluation_time_invalid",
                "Workflow evaluation time must include a timezone.",
            )
        return evaluated_at

    @staticmethod
    def _safe_interrupt(issue: dict[str, Any]) -> dict[str, Any]:
        return {
            "issue_id": issue["issue_id"],
            "type": issue["issue_type"],
            "field_name": issue["field_name"],
            "question": issue["question"],
            "answer_schema": issue["answer_schema"],
            "expected_task_revision": issue["created_revision"],
        }
