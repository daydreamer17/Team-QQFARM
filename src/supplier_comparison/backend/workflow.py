from __future__ import annotations

import hashlib
import logging
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
    ValidationStatus,
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
from supplier_comparison.extraction.errors import ExtractionError
from supplier_comparison.extraction.hybrid_csv import RegisteredHybridCsvParser
from supplier_comparison.extraction.csv_parser import ProfiledCsvQuoteParser, identify_csv_contract
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates
from supplier_comparison.rag.contracts import (
    PolicyRetriever,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
)
from supplier_comparison.rules import (
    ComparisonResult,
    DecisionImpactResult,
    DecisionPreferences,
    ImpactStatus,
    ProcurementRequirement,
    analyze_reviewed_decision_impact,
)
from supplier_comparison.rag.clients import is_transient_model_error
from supplier_comparison.extraction.errors import DownstreamNotReadyError
from supplier_comparison.extraction.contracts import ValidationStatus
from supplier_comparison.rules.contracts import RULE_VERSION

from .service import BackendError, BackendService, ConflictError, new_id
from .investigation import CaseStatus, InvestigationRunner
from .investigation_tools import ScopedInvestigationTools
from .policy_investigation import ScopedPolicyInvestigationTools, diagnose_policy


logger = logging.getLogger(__name__)


POLICY_RETRIEVAL_QUERIES = {
    "APPROVED_SUPPLIER": (
        "What procurement policy evidence is required to use an approved supplier "
        "for an electronics purchase in Singapore?"
    ),
    "ROHS_COMPLIANCE": (
        "What procurement policy evidence is required to verify current RoHS "
        "compliance for an electronics supplier?"
    ),
    "AMOUNT_APPROVAL": (
        "What procurement approval policy applies to the confirmed total amount "
        "of an electronics purchase in Singapore?"
    ),
}


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
            profile_id = identify_csv_contract(path)
            if profile_id is not None:
                parsed = ProfiledCsvQuoteParser().parse_row(
                    path, context, 2, profile_id=profile_id
                )
                return extract_quote_candidates(
                    parsed, self.dictionary, self._adapter(), budget, extraction_run_id
                )
            hybrid = RegisteredHybridCsvParser(self.dictionary).parse_row(
                path, context, validate_authority=False
            )
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


class DraftReviewRunner:
    """Parse and deterministically review a quote before it becomes task input."""

    def __init__(
        self,
        service: BackendService,
        *,
        processor: QuoteProcessor,
        dictionary_path: str | Path,
    ) -> None:
        self.service = service
        self.processor = processor
        self.dictionary = QuoteDictionary.load(dictionary_path)

    def run_job(self, job_id: str) -> dict[str, Any]:
        job = self.service.claim_quote_draft_job(job_id)
        context = self.service.quote_draft_job_context(job_id)
        budget = ModelCallBudget(
            graph_run_id=context["quote_draft_id"],
            calls_used=context["calls_used"],
            max_calls=context["max_calls"],
        )
        try:
            document_context = DocumentContext(
                task_id=context["task_id"],
                task_revision=context["task_revision"],
                scenario_id=context["scenario_id"],
                quote_id=context["quote_id"],
                quote_version=1,
                document_id=context["document_id"],
                document_version=1,
                supplier_id=context["supplier_id"],
            )
            try:
                batch = self.processor.process(
                    path=Path(context["storage_path"]),
                    media_type=context["media_type"],
                    context=document_context,
                    budget=budget,
                )
            finally:
                self.service.record_quote_draft_calls(
                    context["quote_draft_id"], budget.calls_used
                )
            parsed_artifact = self.service.append_artifact(
                task_id=context["task_id"],
                task_revision=context["task_revision"],
                artifact_type="PARSED_INPUT",
                schema_version=batch.schema_version,
                payload=batch.parsed_input.model_dump(mode="json"),
                quote_id=context["quote_id"],
                document_id=context["document_id"],
            )
            batch_artifact = self.service.append_artifact(
                task_id=context["task_id"],
                task_revision=context["task_revision"],
                artifact_type="EXTRACTION_BATCH",
                schema_version=batch.schema_version,
                payload=batch.model_dump(mode="json"),
                parent_artifact_id=parsed_artifact["artifact_id"],
                quote_id=context["quote_id"],
                document_id=context["document_id"],
            )
            requirement = ProcurementRequirement.model_validate(context["requirement"])
            envelope = review_extraction_batch(
                batch,
                self.dictionary,
                CriticalityContext(
                    required_revision=requirement.revision,
                    base_unit=requirement.base_unit,
                ),
                input_is_synthetic=context["is_synthetic"],
                reviewed_at=datetime.now(timezone.utc),
            )
            review_artifact = self.service.append_artifact(
                task_id=context["task_id"],
                task_revision=context["task_revision"],
                artifact_type="REVIEW_ENVELOPE",
                schema_version=envelope.schema_version,
                payload=envelope.model_dump(mode="json"),
                parent_artifact_id=batch_artifact["artifact_id"],
                quote_id=context["quote_id"],
                document_id=context["document_id"],
            )
            completed = self.service.complete_quote_draft_job(
                job_id,
                parsed_artifact_id=parsed_artifact["artifact_id"],
                batch_artifact_id=batch_artifact["artifact_id"],
                review_artifact_id=review_artifact["artifact_id"],
                downstream_ready=envelope.downstream_ready,
            )
            return {
                "job_id": job_id,
                "quote_draft_id": context["quote_draft_id"],
                "status": completed["status"],
            }
        except Exception as exc:
            logger.exception("Quote draft job %s failed", job_id)
            if isinstance(exc, BackendError):
                code, message = exc.code, exc.message
            elif isinstance(exc, ExtractionError):
                code, message = exc.code, str(exc)
            else:
                code, message = "draft_review_failed", "Quote draft review failed."
            self.service.fail_quote_draft_job(job_id, code=code, message=message)
            raise


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
    snapshot_id: str
    comparison_result_id: str
    policy_retrieval_artifact_ids: dict[str, str]
    decision_impact_artifact_id: str
    nonblocking_unknown_quote_ids: list[str]
    blocking_unknown_quote_ids: list[str]
    undetermined_quote_ids: list[str]
    final_result_id: str
    evaluated_at: str
    investigation_waiting: bool
    investigation_case_ids: list[str]


class WorkflowRunner:
    def __init__(
        self,
        service: BackendService,
        *,
        processor: QuoteProcessor,
        policy_retriever: PolicyRetriever | None = None,
        checkpointer,
        dictionary_path: str | Path,
        evaluated_at: datetime | None = None,
        investigator: InvestigationRunner | None = None,
        policy_max_retries: int = 2,
    ) -> None:
        self.service = service
        self.processor = processor
        self.policy_retriever = policy_retriever
        self.investigator = investigator
        if not 0 <= policy_max_retries <= 3:
            raise ValueError('policy retry cap must be between 0 and 3')
        self.policy_max_retries = policy_max_retries
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
        builder.add_node("analyze_decision_impact", self._analyze_decision_impact)
        builder.add_node("investigate_quotes", self._investigate_quotes)
        builder.add_node("await_batch_review", self._await_batch_review)
        builder.add_node("await_missing_confirmation", self._await_missing_confirmation)
        builder.add_node("apply_missing_confirmation", self._apply_missing_confirmation)
        builder.add_node("freeze_draft_and_compare", self._freeze_draft_and_compare)
        builder.add_node("await_shipping_amount", self._await_shipping_amount)
        builder.add_node("apply_shipping_amount", self._apply_shipping_amount)
        builder.add_node("freeze_final_and_compare", self._freeze_final_and_compare)
        builder.add_node("retrieve_policies", self._retrieve_policies)
        builder.add_node('investigate_policies', self._investigate_policies)
        builder.add_node("await_policy_evidence_review", self._await_policy_evidence_review)
        builder.add_node("publish_final_result", self._publish_final_result)
        builder.add_edge(START, "load_context")
        builder.add_edge("load_context", "extract_documents")
        builder.add_edge("extract_documents", "review_quotes")
        builder.add_edge("review_quotes", "analyze_decision_impact")
        builder.add_edge("analyze_decision_impact", "investigate_quotes")
        builder.add_conditional_edges(
            "investigate_quotes",
            self._route_after_review,
            {
                "needs_shipping_confirmation": "await_missing_confirmation",
                "ready": "freeze_final_and_compare",
                "needs_batch_review": "await_batch_review",
            },
        )
        builder.add_edge("await_missing_confirmation", "apply_missing_confirmation")
        builder.add_edge("apply_missing_confirmation", "freeze_draft_and_compare")
        builder.add_edge("freeze_draft_and_compare", "await_shipping_amount")
        builder.add_edge("await_shipping_amount", "apply_shipping_amount")
        builder.add_edge("apply_shipping_amount", "freeze_final_and_compare")
        builder.add_edge("freeze_final_and_compare", "retrieve_policies")
        builder.add_edge('retrieve_policies', 'investigate_policies')
        builder.add_conditional_edges(
            "investigate_policies",
            self._route_after_policy_retrieval,
            {
                "ready": "publish_final_result",
                "review_required": "await_policy_evidence_review",
            },
        )
        builder.add_edge("publish_final_result", END)
        builder.add_edge("await_batch_review", END)
        builder.add_edge("await_policy_evidence_review", "freeze_final_and_compare")
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
            logger.exception(
                "Workflow job %s failed in graph %s",
                job_id,
                job["graph_run_id"],
            )
            if isinstance(exc, BackendError):
                code = exc.code
                message = exc.message
            elif isinstance(exc, ExtractionError):
                code = exc.code
                message = str(exc)
            else:
                code = "workflow_failed"
                message = "Workflow execution failed."
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

    def _impact_report(self, state: WorkflowState) -> DecisionImpactResult:
        context = self.service.workflow_context(state["graph_run_id"])
        envelopes = tuple(
            ReviewEnvelope.model_validate(self.service.artifact_payload(artifact_id))
            for _, artifact_id in sorted(state["review_artifact_ids"].items())
        )
        documents = {item["quote_id"]: item for item in context["documents"]}
        if context["effective_revision"] != state["task_revision"]:
            raise BackendError("decision_impact_revision_mismatch", "Impact revision is stale.")
        if set(state["review_artifact_ids"]) != set(documents):
            raise BackendError("decision_impact_scope_mismatch", "Review scope must include all active quotes.")
        for quote_id, envelope in zip(sorted(state["review_artifact_ids"]), envelopes):
            if envelope.batch is None:
                continue  # The strict adapter below rejects model-failed envelopes.
            parsed = envelope.batch.parsed_input
            document = documents.get(parsed.context.quote_id)
            if document is None or (
                parsed.context.quote_id != quote_id
                or parsed.context.quote_version != document["quote_version"]
                or parsed.context.document_id != document["document_id"]
                or parsed.context.document_version != document["document_version"]
                or parsed.document_sha256 != document["document_sha256"]
            ):
                raise BackendError("decision_impact_identity_mismatch", "Impact input is stale.")
        try:
            return analyze_reviewed_decision_impact(
                ProcurementRequirement.model_validate(context["requirement"]), envelopes,
                task_id=state["task_id"], task_revision=state["task_revision"],
                evaluated_at=self._state_evaluated_at(state),
                policy_binding={key: context[key] for key in (
                    "policy_set_version", "policy_index_version", "policy_category", "policy_region"
                )},
                decision_preferences=DecisionPreferences.model_validate(
                    context["decision_profile"]["preferences"]
                ),
            )
        except DownstreamNotReadyError as exc:
            raise BackendError(
                "review_required", "Unsafe review findings must be resolved before impact analysis."
            ) from exc

    def _analyze_decision_impact(self, state: WorkflowState) -> WorkflowState:
        try:
            report = self._impact_report(state)
        except BackendError as exc:
            if self.investigator is None or exc.code != "review_required":
                raise
            # No impact proof for unsafe fields. The agent may only investigate
            # and ask for correction; it cannot release this review gate.
            return {"decision_impact_artifact_id": "", "undetermined_quote_ids": sorted(state["review_artifact_ids"]),
                    "nonblocking_unknown_quote_ids": [], "blocking_unknown_quote_ids": sorted(state["review_artifact_ids"])}
        artifact = self.service.append_artifact(
            task_id=state["task_id"], task_revision=state["task_revision"],
            artifact_type="DECISION_IMPACT_RESULT", schema_version=report.schema_version,
            payload=report.model_dump(mode="json"), graph_run_id=state["graph_run_id"],
        )
        compared_quote_ids = {
            row.quote_id for row in report.comparison.supplier_results
        }
        excluded_quote_ids = set(state["review_artifact_ids"]) - compared_quote_ids
        return {
            "decision_impact_artifact_id": artifact["artifact_id"],
            "nonblocking_unknown_quote_ids": sorted(
                set(report.nonblocking_unknown_quote_ids) | excluded_quote_ids
            ),
            "blocking_unknown_quote_ids": list(report.blocking_quote_ids),
            "undetermined_quote_ids": [
                impact.quote_id for impact in report.quote_impacts
                if impact.status == ImpactStatus.UNDETERMINED
            ],
        }

    def _investigate_quotes(self, state: WorkflowState) -> WorkflowState:
        if self.investigator is None:
            return {"investigation_waiting": False, "investigation_case_ids": []}
        tools = ScopedInvestigationTools(
            self.service, task_id=state["task_id"], graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"], impact_artifact_id=state.get("decision_impact_artifact_id") or None,
            evaluated_at=self._state_evaluated_at(state), policy_retriever=self.policy_retriever,
        )
        cases = self.investigator.run(tools.cases(), tools, tools.save)
        if any(case.status == CaseStatus.STALE for case in cases) or not tools.current():
            raise ConflictError("investigation_input_changed", "Investigation inputs changed; restart on current inputs.")
        unresolved = any(case.status != CaseStatus.RESOLVED for case in cases)
        if state.get("undetermined_quote_ids") and not cases:
            raise BackendError("review_required", "No safe field investigation is available; operator repair required.")
        return {"investigation_waiting": unresolved, "investigation_case_ids": [case.case_id for case in cases]}

    def _await_batch_review(self, state: WorkflowState) -> WorkflowState:
        records = self.service.list_investigations(state["task_id"])
        cards = [card for record in records if record["is_current"]
                 and record["case_id"] in state["investigation_case_ids"] for card in record["clarification"]]
        issue = self.service.open_issue(
            task_id=state["task_id"], graph_run_id=state["graph_run_id"], task_revision=state["task_revision"],
            issue_type="BATCH_FIELD_REVIEW", quote_id=None, field_name=f"batch_review:{state['task_revision']}",
            question="请一次核对本轮各报价的待确认字段，统一提交纠正；系统会重新审核与计算。",
            answer_schema={"answer_type": "BATCH_FIELD_CORRECTIONS",
                           "submit_to": f"/api/v1/tasks/{state['task_id']}/fields/corrections",
                           "expected_task_revision": state["task_revision"], "cards": cards},
        )
        interrupt(self._safe_interrupt(issue))
        # Batch correction starts a new versioned graph, never resumes this one.
        raise ConflictError("batch_review_requires_correction", "Submit corrections to start a new reviewed input version.")

    def _route_after_review(self, state: WorkflowState) -> str:
        if state.get("investigation_waiting"):
            return "needs_batch_review"
        if state.get("undetermined_quote_ids"):
            raise BackendError(
                "review_required", "Resolve unsupported or non-fee facts before requesting fees.",
                quote_ids=state["undetermined_quote_ids"],
            )
        missing = []
        not_ready = []
        blocking_by_quote: dict[str, list[str]] = {}
        for quote_id, artifact_id in state["review_artifact_ids"].items():
            envelope = ReviewEnvelope.model_validate(
                self.service.artifact_payload(artifact_id)
            )
            if quote_id in state.get("nonblocking_unknown_quote_ids", []):
                continue
            if envelope.downstream_ready:
                continue
            not_ready.append(quote_id)
            blocking_by_quote[quote_id] = sorted(
                set(envelope.review.blocking_fields if envelope.review else ())
            )
            if (
                envelope.review is not None
                and "shipping_fee_status" in envelope.review.blocking_fields
            ):
                missing.append(quote_id)
        if not not_ready and not state.get("blocking_unknown_quote_ids"):
            return "ready"
        if len(not_ready) == 1 and missing == not_ready:
            envelope = ReviewEnvelope.model_validate(
                self.service.artifact_payload(state["review_artifact_ids"][missing[0]])
            )
            assert envelope.batch is not None
            status = next(c for c in envelope.batch.candidates if c.field_name == "shipping_fee_status")
            confirmed_unknown = (
                status.validation_status == ValidationStatus.VERIFIED
                and status.normalized_value == "UNKNOWN"
                and status.origin is not None
                and status.origin.value in {"USER_INPUT", "USER_CORRECTION"}
            )
            if status.validation_status != ValidationStatus.MISSING and not confirmed_unknown:
                raise BackendError("review_required", "Unknown fee status needs a typed correction.")
            return "needs_shipping_confirmation"
        raise BackendError(
            "review_required",
            "One or more quotes require a correction before comparison.",
            quote_ids=sorted(set(not_ready) | set(state.get("blocking_unknown_quote_ids", []))),
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
            question="请确认该报价 PDF/CSV 没有提供可用于计算的运费金额。",
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
        shipping_status = next(
            candidate
            for candidate in batch.candidates
            if candidate.field_name == "shipping_fee_status"
        )
        confirmation_field = "shipping_fee_status"
        if (
            shipping_status.validation_status
            in {ValidationStatus.EXTRACTED, ValidationStatus.VERIFIED}
            and shipping_status.normalized_value == "UNKNOWN"
        ):
            confirmation_field = "shipping_fee_amount"
        working_batch = batch
        status_corrections = tuple(
            CorrectionEvent.model_validate(payload)
            for payload in self.service.correction_event_payloads_for_batch(batch_id)
        )
        if shipping_status.validation_status == ValidationStatus.MISSING:
            working_batch, status_event = apply_candidate_correction(
                batch,
                field_name='shipping_fee_status',
                action=CorrectionAction.USER_INPUT,
                raw_value='UNKNOWN',
                normalized_value='UNKNOWN',
                unit=None,
                reason_code='DOCUMENT_DOES_NOT_STATE_VALUE',
                reason='Buyer confirmed that the document does not state shipping.',
                reviewer_id=issue['answered_by'],
                reviewed_at=reviewed_at,
            )
            batch_artifact = self.service.append_artifact(
                task_id=state['task_id'],
                task_revision=state['task_revision'],
                artifact_type='EXTRACTION_BATCH',
                schema_version=working_batch.schema_version,
                payload=working_batch.model_dump(mode='json'),
                parent_artifact_id=batch_id,
                quote_id=quote_id,
                graph_run_id=state['graph_run_id'],
            )
            self.service.append_artifact(
                task_id=state['task_id'],
                task_revision=state['task_revision'],
                artifact_type='CORRECTION_EVENT',
                payload=status_event.model_dump(mode='json'),
                parent_artifact_id=batch_artifact['artifact_id'],
                quote_id=quote_id,
                graph_run_id=state['graph_run_id'],
            )
            status_corrections = (*status_corrections, status_event)
            confirmation_field = 'shipping_fee_amount'
        event = create_review_event(
            working_batch,
            field_name=confirmation_field,
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
            working_batch,
            requirement,
            document["is_synthetic"],
            reviewed_at=self._state_evaluated_at(state),
            review_events=(event,),
            corrections=status_corrections,
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
        context = self.service.workflow_context(state["graph_run_id"])
        requirement = ProcurementRequirement.model_validate(context["requirement"])
        currency = requirement.currency
        issue = self.service.open_issue(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            issue_type="SHIPPING_AMOUNT",
            quote_id=state["target_quote_id"],
            field_name="shipping_fee_amount",
            question=f"请提供该供应商的 {currency} 运费金额。",
            answer_schema={
                "answer_type": "SHIPPING_AMOUNT",
                "amount": "decimal-string",
                "currency": currency,
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
        current_shipping_status = next(
            candidate
            for candidate in prior_envelope.batch.candidates
            if candidate.field_name == 'shipping_fee_status'
        )
        status_action = (
            CorrectionAction.USER_INPUT
            if current_shipping_status.validation_status == ValidationStatus.MISSING
            else CorrectionAction.USER_CORRECTION
        )
        with_status, status_event = apply_candidate_correction(
            prior_envelope.batch,
            field_name="shipping_fee_status",
            action=status_action,
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
            corrections=(
                *prior_envelope.corrections,
                status_event,
                amount_event,
            ),
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
            batch_artifact_id=batch_artifact["artifact_id"],
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
        snapshot_id, result_id = self._freeze_compare_artifacts(state)
        return {"snapshot_id": snapshot_id, "comparison_result_id": result_id}

    def _freeze_compare_publish(self, state: WorkflowState) -> tuple[str, str]:
        snapshot_id, result_id = self._freeze_compare_artifacts(state)
        self.service.publish_result(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            snapshot_id=snapshot_id,
            result_id=result_id,
        )
        return snapshot_id, result_id

    def _freeze_compare_artifacts(self, state: WorkflowState) -> tuple[str, str]:
        context = self.service.workflow_context(state["graph_run_id"])
        evaluated_at = self._state_evaluated_at(state)
        report = self._impact_report(state)
        result = report.comparison
        impact_artifact = self.service.append_artifact(
            task_id=state["task_id"], task_revision=state["task_revision"],
            artifact_type="DECISION_IMPACT_RESULT", schema_version=report.schema_version,
            payload=report.model_dump(mode="json"), graph_run_id=state["graph_run_id"],
        )
        snapshot = {
            "task_id": state["task_id"],
            "task_revision": state["task_revision"],
            "graph_run_id": state["graph_run_id"],
            "requirement": context["requirement"],
            "decision_profile": context["decision_profile"],
            "batch_artifact_ids": state["batch_artifact_ids"],
            "review_artifact_ids": state["review_artifact_ids"],
            "decision_impact_artifact_id": impact_artifact["artifact_id"],
            "dictionary_version": self.dictionary.version,
            "dictionary_sha256": self.dictionary_sha256,
            "rule_version": RULE_VERSION,
            "policy_set_version": context["policy_set_version"],
            "policy_index_version": context["policy_index_version"],
            "policy_category": context["policy_category"],
            "policy_region": context["policy_region"],
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
        return snapshot_artifact["artifact_id"], result_artifact["artifact_id"]

    def _retrieve_policies(self, state: WorkflowState) -> WorkflowState:
        context = self.service.workflow_context(state["graph_run_id"])
        comparison = ComparisonResult.model_validate(
            self.service.artifact_payload(state["comparison_result_id"])
        )
        if not comparison.final_recommendation_allowed:
            return {"policy_retrieval_artifact_ids": {}}
        binding = (
            context["policy_set_version"],
            context["policy_index_version"],
            context["policy_category"],
            context["policy_region"],
        )
        if not any(binding):
            return {"policy_retrieval_artifact_ids": {}}
        if not all(binding):
            raise BackendError(
                "policy_binding_incomplete",
                "The task policy binding is incomplete.",
            )

        artifacts: dict[str, str] = {}
        for control_code, query in POLICY_RETRIEVAL_QUERIES.items():
            request = RetrievalRequest(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                snapshot_id=state["snapshot_id"],
                policy_set_version=context["policy_set_version"],
                policy_index_version=context["policy_index_version"],
                query=query,
                required_control_codes=[control_code],
                category=context["policy_category"],
                region=context["policy_region"],
                evaluated_at=self._state_evaluated_at(state),
            )
            result = self._safe_policy_retrieval(request, control_code)
            artifact = self.service.append_artifact(
                task_id=state["task_id"],
                task_revision=state["task_revision"],
                artifact_type="POLICY_RETRIEVAL_RESULT",
                schema_version="policy-retrieval/1.0.0",
                payload=result.model_dump(mode="json"),
                parent_artifact_id=state["comparison_result_id"],
                graph_run_id=state["graph_run_id"],
            )
            artifacts[control_code] = artifact["artifact_id"]
        return {"policy_retrieval_artifact_ids": artifacts}

    def _safe_policy_retrieval(
        self, request: RetrievalRequest, control_code: str
    ) -> RetrievalResult:
        if self.policy_retriever is None:
            return self._policy_error_result(
                request, control_code, "policy_retriever_not_configured"
            )
        try:
            result = self.policy_retriever.retrieve(request)
        except Exception as exc:
            return self._policy_error_result(
                request, control_code, "policy_transport_transient" if is_transient_model_error(exc) else "policy_retriever_failed"
            )
        try:
            # Revalidate copies too: adapters must not release a gate with a
            # model_copy() that bypassed citation/result shape validation.
            result = RetrievalResult.model_validate(result.model_dump(mode='python') if isinstance(result, RetrievalResult) else result)
        except (TypeError, ValueError):
            return self._policy_error_result(request, control_code, 'policy_retrieval_contract_invalid')
        if (
            result.policy_set_version != request.policy_set_version
            or result.policy_index_version != request.policy_index_version
            or any(c.policy_set_version != request.policy_set_version
                   or hashlib.sha256(c.text.encode('utf-8')).hexdigest() != c.content_sha256 for c in result.citations)
            or (
                result.status == RetrievalStatus.OK
                and (
                    control_code not in result.covered_control_codes
                    or not any(
                        citation.control_code == control_code
                        and citation.policy_set_version == request.policy_set_version
                        and citation.retrieval_id == result.retrieval_id
                        for citation in result.citations
                    )
                )
            )
        ):
            return self._policy_error_result(
                request, control_code, "policy_retrieval_contract_invalid"
            )
        return result

    def _investigate_policies(self, state: WorkflowState) -> WorkflowState:
        artifacts = state.get('policy_retrieval_artifact_ids', {})
        if self.investigator is None or not artifacts:
            return {}
        context = self.service.workflow_context(state['graph_run_id'])
        requests = {code: RetrievalRequest(
            task_id=state['task_id'], task_revision=state['task_revision'], snapshot_id=state['snapshot_id'],
            policy_set_version=context['policy_set_version'], policy_index_version=context['policy_index_version'],
            query=POLICY_RETRIEVAL_QUERIES[code], required_control_codes=[code],
            category=context['policy_category'], region=context['policy_region'],
            evaluated_at=self._state_evaluated_at(state),
        ) for code in artifacts}

        def retry(request, code):
            result = self._safe_policy_retrieval(request, code)
            # Bind the frozen control scope in the audit trace even for adapters
            # that return minimal test/legacy filter metadata.
            payload = result.model_dump(mode='json')
            payload['filters'] = dict(payload['filters']) | {'control_codes': [code]}
            artifact = self.service.append_artifact(
                task_id=state['task_id'], task_revision=state['task_revision'], graph_run_id=state['graph_run_id'],
                artifact_type='POLICY_RETRIEVAL_RESULT', schema_version='policy-retrieval/1.0.0',
                parent_artifact_id=state['comparison_result_id'], payload=payload,
            )
            return artifact['artifact_id']

        tools = ScopedPolicyInvestigationTools(
            self.service, task_id=state['task_id'], task_revision=state['task_revision'],
            graph_run_id=state['graph_run_id'], comparison_result_id=state['comparison_result_id'],
            requests=requests, artifact_ids=artifacts, retry=retry, max_retries=self.policy_max_retries,
        )
        cases = self.investigator.run(tools.cases(), tools, tools.save)
        if any(c.status == CaseStatus.STALE for c in cases) or not tools.current():
            raise ConflictError('investigation_input_changed', 'Policy investigation inputs changed.')
        return {'policy_retrieval_artifact_ids': tools.artifact_ids}

    @staticmethod
    def _policy_error_result(
        request: RetrievalRequest, control_code: str, error_code: str
    ) -> RetrievalResult:
        return RetrievalResult(
            retrieval_id=new_id("retrieval"),
            status=RetrievalStatus.ERROR,
            policy_set_version=request.policy_set_version,
            policy_index_version=request.policy_index_version,
            embedding_model="unavailable",
            rerank_model="unavailable",
            filters={
                "control_codes": [control_code],
                "category": request.category,
                "region": request.region,
                "evaluated_at": request.evaluated_at.isoformat(),
            },
            covered_control_codes=[],
            missing_control_codes=[control_code],
            citations=[],
            candidates=[],
            latency_ms={"total": 0.0},
            attempts={"embedding": 0, "rerank": 0},
            error_code=error_code,
        )

    def _route_after_policy_retrieval(self, state: WorkflowState) -> str:
        artifact_ids = state.get("policy_retrieval_artifact_ids", {})
        if not artifact_ids:
            return "ready"
        for artifact_id in artifact_ids.values():
            result = RetrievalResult.model_validate(
                self.service.artifact_payload(artifact_id)
            )
            if result.status != RetrievalStatus.OK:
                return "review_required"
        return "ready"

    def _await_policy_evidence_review(self, state: WorkflowState) -> WorkflowState:
        statuses = {
            control_code: RetrievalResult.model_validate(
                self.service.artifact_payload(artifact_id)
            ).status.value
            for control_code, artifact_id in state["policy_retrieval_artifact_ids"].items()
        }
        issue = self.service.open_issue(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            issue_type="POLICY_EVIDENCE_REVIEW",
            quote_id=None,
            field_name=f"policy_retrieval:{state['task_revision']}",
            question=(
                "采购制度证据缺失、冲突或检索失败。修复临时故障后可重试；"
                "制度内容或索引发生变化时，应使用新发布版本创建新任务。"
            ),
            answer_schema={
                "answer_type": "RETRY_POLICY_RETRIEVAL",
                "changed_policy_requires": "NEW_TASK_WITH_NEW_POLICY_BINDING",
                "retrieval_statuses": statuses,
                'diagnoses': {code: diagnose_policy(RetrievalResult.model_validate(self.service.artifact_payload(aid)))
                              for code, aid in state['policy_retrieval_artifact_ids'].items()},
                'investigations': [record for record in self.service.list_investigations(state['task_id'])
                                   if record['is_current'] and record.get('kind') == 'POLICY'],
            },
        )
        resumed = interrupt(self._safe_interrupt(issue))
        if not isinstance(resumed, dict) or resumed.get("issue_id") != issue["issue_id"]:
            raise ConflictError(
                "resume_issue_mismatch", "Resume token does not match the issue."
            )
        resolved = self.service.get_issue(issue["issue_id"])
        if resolved["status"] != "RESOLVED":
            raise ConflictError("issue_not_resolved", "Issue must be resolved before resume.")
        return {
            "task_revision": resolved["resolved_revision"],
            "policy_retrieval_artifact_ids": {},
        }

    def _publish_final_result(self, state: WorkflowState) -> WorkflowState:
        self.service.publish_result(
            task_id=state["task_id"],
            graph_run_id=state["graph_run_id"],
            task_revision=state["task_revision"],
            snapshot_id=state["snapshot_id"],
            result_id=state["comparison_result_id"],
        )
        return {"final_result_id": state["comparison_result_id"]}

    def _missing_shipping_quote(self, state: WorkflowState) -> str:
        matches: list[str] = []
        for quote_id, artifact_id in state["review_artifact_ids"].items():
            if quote_id in state.get("nonblocking_unknown_quote_ids", []):
                continue
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
