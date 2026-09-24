from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from supplier_comparison.rules import (
    DecisionPreferences,
    ProcurementRequirement,
    RankingCriterion,
    RankingMode,
    RequirementChanges,
    analyze_decision_impact,
    ranking_mode_for,
    ranking_pair,
    simulate_requirement_change,
)
from supplier_comparison.extraction import (
    CorrectionAction,
    CorrectionEvent,
    CriticalityContext,
    ExtractionBatch,
    HumanReviewAction,
    Origin,
    ReviewEnvelope,
    ReviewSeverity,
    ValidationStatus,
    apply_candidate_correction,
    create_review_event,
    review_extraction_batch,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.files import stable_id
from supplier_comparison.extraction.review import HUMAN_RESOLVABLE_CODES, IDENTITY_CODES
from supplier_comparison.extraction.quote_field_rules import select_document_unit_price
from supplier_comparison.extraction.review_contracts import (
    CandidateValueSnapshot,
    EffectiveCriticality,
    FieldReviewDecision,
    ReviewStatus,
    REVIEW_POLICY_VERSION,
    REVIEW_SCHEMA_VERSION,
)

from .models import (
    DecisionConversation,
    DecisionConversationEvent,
    DecisionIntent,
    DecisionMessage,
    DecisionProfile,
    DecisionScenario,
    Document,
    DocumentAccessEvent,
    DocumentExecution,
    GraphRun,
    IdempotencyRecord,
    Issue,
    Job,
    Quote,
    QuoteDraft,
    RequirementDraft,
    RequirementRecord,
    SummaryReport,
    Task,
    TaskHistoryBinding,
    TaskRevision,
    WorkflowArtifact,
)
from .conversations import (
    CONVERSATION_PROMPT_VERSION,
    decision_fact_catalog,
    normalize_conversation_turn,
    render_conversation_turn,
    validate_conversation_turn,
)
from .decision_intents import DecisionIntentParser, confirmation_text
from .quote_review_schema import (
    GROUP_IDS,
    QUOTE_REVIEW_SCHEMA_VERSION,
    build_quote_field_schema,
)
from .supplier_history import history_inputs
from supplier_comparison.rag.clients import ModelClientError
from supplier_comparison.supplier_history import (
    SupplierHistoryLoader,
    SupplierHistoryLoadError,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


HISTORY_RANKING_CRITERIA = {
    RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
    RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE,
    RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE,
}

SUPPLIER_COMPLIANCE_EVIDENCE_SCHEMA = "supplier-compliance-evidence/1.0.0"
# WorkflowArtifact.schema_version is VARCHAR(32); keep the storage envelope version compact.
SUPPLIER_COMPLIANCE_EVIDENCE_ARTIFACT_SCHEMA = "supplier-evidence/1.0.0"


def _requires_history(requirement: ProcurementRequirement) -> bool:
    return bool(
        {requirement.ranking_preference, requirement.secondary_preference}
        & HISTORY_RANKING_CRITERIA
    )


def _preferences_require_history(preferences: DecisionPreferences) -> bool:
    return bool(
        {preferences.primary_criterion, preferences.secondary_criterion}
        & HISTORY_RANKING_CRITERIA
    )


QUOTE_REVIEW_ERROR_MESSAGES = {
    "CANDIDATE_AUTHORITY_MISMATCH": "字段身份与当前报价不一致，请重新解析原文件。",
    "CANDIDATE_FIELD_SET_INVALID": "候选字段集合不完整或重复，请重新解析原文件。",
    "CORRECTION_AUDIT_MISSING": "人工修改缺少对应审计记录，请重新确认该字段。",
    "CORRECTION_EVENT_INVALID": "人工修改记录与当前字段版本不一致，请刷新后重试。",
    "CRITICAL_FIELD_MISSING": "必填字段在报价中缺失，请核对后填写真实值。",
    "CRITICAL_FIELD_CONFLICT": "报价中该字段有多个冲突值，请核对后填写最终值。",
    "CURRENT_UNIT_PRICE_MISMATCH": "当前单价与原件中标记为 CURRENT 的价格不一致。",
    "DICTIONARY_VERSION_MISMATCH": "字段字典版本不一致，请重新解析报价。",
    "DOCUMENT_ABSENCE_MISREAD_AS_FEE_VALUE": "原文只说明费用未提供，不能据此填写费用状态或金额。",
    "FEE_STATUS_UNKNOWN": "费用仍为待确认状态，取得真实费用结论后才能提交。",
    "FEE_STATUS_AMOUNT_CONFLICT": "费用状态与金额不一致。",
    "HUMAN_ORIGIN_REQUIRES_VERIFIED": "人工填写或修改后的字段必须通过结构校验。",
    "HUMAN_REVIEW_EVENT_INVALID": "人工确认记录与当前字段或文件版本不一致。",
    "MONEY_VALUE_INVALID": "金额必须是大于或等于 0 的十进制字符串。",
    "MONEY_UNIT_MISMATCH": "金额币种必须与报价币种一致。",
    "NORMALIZED_ENUM_INVALID": "字段值不在允许的标准选项中。",
    "NORMALIZED_PRICE_NOT_IN_EVIDENCE": "提取价格无法由当前报价证据支持，请核对原件。",
    "NORMALIZED_TYPE_INVALID": "字段值的类型或范围不正确。",
    "NORMALIZED_VALUE_REQUIRED": "该字段需要一个可用的标准值。",
    "ISO_DATE_REQUIRED": "日期必须是有效的 YYYY-MM-DD。",
    "PRICE_GROUP_INCOMPLETE": "单价、计价数量和计价单位必须同时填写。",
    "PACKAGING_CONVERSION_INCOMPLETE": "MOQ 不按颗时，必须同时填写包装方式和每包数量。",
    "MOQ_PACKAGING_UNIT_MISMATCH": "MOQ 单位与包装单位不一致。",
    "LEAD_TIME_GROUP_INCOMPLETE": "交期天数、日历口径、交付语义和起算事件必须同时填写。",
    "QUOTE_DATE_AFTER_VALID_UNTIL": "报价日期不能晚于有效截止日。",
    "SOURCE_IDENTITY_MISMATCH": "证据不属于当前报价文件或版本，请重新解析。",
    "SOURCE_QUOTE_MISMATCH": "引用片段不在绑定的原文中，请重新解析后核对。",
    "SOURCE_REF_UNKNOWN": "字段引用了当前文件中不存在的证据。",
    "SOURCE_SEMANTIC_MISMATCH": "证据原文不能支持该字段含义，请人工核对。",
    "START_EVENT_DOCUMENT_CONFLICT": "原件存在多个交期起算事件，请确认最终适用条件。",
    "START_EVENT_UNSUPPORTED": "当前自动计算只支持明确从下单日开始的相对交期。",
    "SUPPLIER_NAME_CONTAINS_SUPPLIER_ID": "供应商名称混入了系统编号，请分开填写。",
    "REQUIRED_FIELD_UNAVAILABLE": "必填字段缺少可用值，UNKNOWN、MISSING 或 CONFLICT 不能正式提交。",
}

# Comparison rules may discover either an incorrect/unknown quote fact or a
# real commercial difference that the current calculator cannot yet convert.
# Only the first category may be written back as a field correction.  A valid
# supplier term must never be overwritten merely to match the requirement.
COMPARISON_FACT_CORRECTION_CODES = frozenset(
    {
        "DECIMAL_STRING_REQUIRED",
        "FEE_AMOUNT_UNKNOWN",
        "FEE_CURRENCY_MISMATCH",
        "FEE_STATUS_UNSUPPORTED",
        "FIELD_CONFLICT",
        "FIELD_MISSING",
        "FIELD_NON_NEGATIVE_INTEGER_REQUIRED",
        "FIELD_POSITIVE_INTEGER_REQUIRED",
        "FIELD_STRING_REQUIRED",
        "FIELD_VALUE_INVALID",
        "ISO_DATE_REQUIRED",
        "MONEY_VALUE_INVALID",
        "MOQ_PACKAGING_UNIT_MISMATCH",
        "UNIT_PRICE_CURRENCY_MISMATCH",
        "ZERO_FEE_STATUS_AMOUNT_CONFLICT",
    }
)


def _quote_draft_submission_ready(envelope: ReviewEnvelope) -> bool:
    """Return the authoritative 1.1 submission gate result.

    ``downstream_ready`` only describes deterministic extraction review.  It
    must never bypass the separate all-field human-review admission gate.
    """

    return envelope.submission_ready


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ConflictError(BackendError):
    pass


class NotFoundError(BackendError):
    pass


class BackendService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage_root: str | Path,
        *,
        actor_id: str,
        quote_dictionary_path: str | Path = "data/contracts/quote_data_field.csv",
        conversation_job_stale_seconds: int = 120,
        supplier_history_root: str | Path = "data/generated/supplier_history/mcu9",
        supplier_history_dataset_version: str = "2026-08-06-v1",
    ) -> None:
        self.session_factory = session_factory
        self.storage_root = Path(storage_root)
        self.actor_id = actor_id
        self.quote_dictionary_path = Path(quote_dictionary_path)
        self.quote_dictionary = QuoteDictionary.load(self.quote_dictionary_path)
        self.conversation_job_stale_seconds = conversation_job_stale_seconds
        self.supplier_history_root = Path(supplier_history_root)
        self.supplier_history_dataset_version = supplier_history_dataset_version

    def _published_history_release(
        self, dataset_version: str
    ) -> tuple[Any, Any, str]:
        try:
            return SupplierHistoryLoader(self.supplier_history_root).load(
                dataset_version=dataset_version
            )
        except SupplierHistoryLoadError as exc:
            raise BackendError(
                exc.code,
                "Supplier history dataset could not be loaded safely.",
                dataset_version=dataset_version,
            ) from exc

    @staticmethod
    def _history_binding_response(
        binding: TaskHistoryBinding | None,
    ) -> dict[str, Any] | None:
        if binding is None:
            return None
        return {
            "history_binding_id": binding.history_binding_id,
            "task_revision": binding.task_revision,
            "binding_status": binding.binding_status,
            "dataset_id": binding.dataset_id,
            "dataset_version": binding.dataset_version,
            "content_sha256": binding.content_sha256,
            "manifest_sha256": binding.manifest_sha256,
            "rating_method_version": binding.rating_method_version,
            "identity_matcher_version": binding.identity_matcher_version,
            "alias_allowlist_sha256": binding.alias_allowlist_sha256,
            "scope": binding.scope,
            "as_of_date": binding.as_of_date,
            "is_synthetic": bool(binding.payload.get("is_synthetic", False)),
        }

    def _new_history_binding(
        self,
        *,
        task_id: str,
        task_revision: int,
        requirement: ProcurementRequirement,
        dataset_version: str | None = None,
    ) -> TaskHistoryBinding:
        dataset, manifest, manifest_sha256 = self._published_history_release(
            dataset_version or self.supplier_history_dataset_version
        )
        mapping = requirement.manufacturer_part_number
        applicable = mapping in dataset.context.scope.task_product_mappings
        payload = {
            "dataset_context": dataset.context.model_dump(mode="json"),
            "binding_status": "AVAILABLE" if applicable else "OUT_OF_SCOPE",
            "task_product_mapping": mapping,
            "is_synthetic": dataset.context.is_synthetic,
        }
        return TaskHistoryBinding(
            history_binding_id=new_id("history_binding"),
            task_id=task_id,
            task_revision=task_revision,
            binding_status=payload["binding_status"],
            dataset_id=manifest.dataset_id,
            dataset_version=manifest.dataset_version,
            content_sha256=manifest.content_sha256,
            manifest_sha256=manifest_sha256,
            rating_method_version=dataset.context.rating_method_version,
            identity_matcher_version=dataset.context.identity_matcher_version,
            alias_allowlist_sha256=manifest.alias_allowlist_sha256,
            scope=dataset.context.scope.model_dump(mode="json"),
            as_of_date=dataset.context.as_of_date.isoformat(),
            payload=payload,
        )

    @staticmethod
    def _binding_at_revision(
        session: Session, task_id: str, revision: int
    ) -> TaskHistoryBinding | None:
        return session.scalar(
            select(TaskHistoryBinding)
            .where(
                TaskHistoryBinding.task_id == task_id,
                TaskHistoryBinding.task_revision <= revision,
            )
            .order_by(TaskHistoryBinding.task_revision.desc())
        )

    @classmethod
    def _copy_history_binding(
        cls,
        session: Session,
        *,
        task_id: str,
        from_revision: int,
        to_revision: int,
    ) -> TaskHistoryBinding | None:
        source = cls._binding_at_revision(session, task_id, from_revision)
        if source is None:
            return None
        copied = TaskHistoryBinding(
            history_binding_id=new_id("history_binding"),
            task_id=task_id,
            task_revision=to_revision,
            binding_status=source.binding_status,
            dataset_id=source.dataset_id,
            dataset_version=source.dataset_version,
            content_sha256=source.content_sha256,
            manifest_sha256=source.manifest_sha256,
            rating_method_version=source.rating_method_version,
            identity_matcher_version=source.identity_matcher_version,
            alias_allowlist_sha256=source.alias_allowlist_sha256,
            scope=dict(source.scope),
            as_of_date=source.as_of_date,
            payload=dict(source.payload),
        )
        session.add(copied)
        return copied

    def quote_field_schema(self) -> dict[str, Any]:
        """Return the versioned, backend-owned quote review form contract."""

        return build_quote_field_schema(
            self.quote_dictionary,
            self.quote_dictionary_path,
        )

    @staticmethod
    def _supersede_current_graph(session: Session, task: Task) -> GraphRun | None:
        """Invalidate execution state when an external input changes."""

        for scenario in session.scalars(
            select(DecisionScenario).where(
                DecisionScenario.task_id == task.task_id,
                DecisionScenario.status == "READY",
            )
        ):
            scenario.status = "STALE"
        for intent in session.scalars(
            select(DecisionIntent).where(
                DecisionIntent.task_id == task.task_id,
                DecisionIntent.status.in_(("PROCESSING", "READY")),
            )
        ):
            intent.status = "STALE"
        for conversation in session.scalars(
            select(DecisionConversation).where(
                DecisionConversation.task_id == task.task_id,
                DecisionConversation.status == "ACTIVE",
            )
        ):
            conversation.status = "STALE"
            for message in session.scalars(
                select(DecisionMessage).where(
                    DecisionMessage.conversation_id == conversation.conversation_id,
                    DecisionMessage.status.in_(("PENDING", "RUNNING")),
                )
            ):
                message.status = "STALE"
            for conversation_job in session.scalars(
                select(Job).where(
                    Job.conversation_id == conversation.conversation_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )
            ):
                conversation_job.status = "SUPERSEDED"
        for report in session.scalars(
            select(SummaryReport).where(
                SummaryReport.task_id == task.task_id,
                SummaryReport.status != "STALE",
            )
        ):
            report.status = "STALE"
        for summary_job in session.scalars(
            select(Job).where(
                Job.task_id == task.task_id,
                Job.summary_id.is_not(None),
                Job.status.in_(("PENDING", "RUNNING", "WAITING_INPUT")),
            )
        ):
            summary_job.status = "SUPERSEDED"
        if task.current_graph_run_id is None:
            task.current_snapshot_id = None
            task.current_result_id = None
            return None
        graph = session.get(GraphRun, task.current_graph_run_id)
        if graph is not None:
            graph.status = "SUPERSEDED"
            for issue in session.scalars(
                select(Issue).where(
                    Issue.graph_run_id == graph.graph_run_id,
                    Issue.status == "OPEN",
                )
            ):
                issue.status = "SUPERSEDED"
            for job in session.scalars(
                select(Job).where(
                    Job.graph_run_id == graph.graph_run_id,
                    Job.status.in_(("PENDING", "RUNNING", "WAITING_INPUT")),
                )
            ):
                job.status = "SUPERSEDED"
        task.current_graph_run_id = None
        task.current_snapshot_id = None
        task.current_result_id = None
        return graph

    @staticmethod
    def _latest_decision_profile(
        session: Session,
        task_id: str,
        *,
        max_revision: int | None = None,
    ) -> DecisionProfile | None:
        query = select(DecisionProfile).where(DecisionProfile.task_id == task_id)
        if max_revision is not None:
            query = query.where(DecisionProfile.task_revision <= max_revision)
        return session.scalar(query.order_by(DecisionProfile.profile_version.desc()))

    @classmethod
    def _decision_preferences(
        cls,
        session: Session,
        task: Task,
        requirement: ProcurementRequirement,
        *,
        max_revision: int | None = None,
    ) -> tuple[DecisionPreferences, DecisionProfile | None]:
        profile = cls._latest_decision_profile(
            session,
            task.task_id,
            max_revision=max_revision,
        )
        if profile is not None:
            return DecisionPreferences.model_validate(profile.payload), profile
        return DecisionPreferences(ranking_mode=ranking_mode_for(requirement)), None

    @staticmethod
    def _decision_profile_response(
        preferences: DecisionPreferences,
        profile: DecisionProfile | None,
    ) -> dict[str, Any]:
        return {
            "decision_profile_id": profile.decision_profile_id if profile else None,
            "task_revision": profile.task_revision if profile else None,
            "profile_version": profile.profile_version if profile else 0,
            "preferences": preferences.model_dump(mode="json"),
            "source_scenario_id": profile.source_scenario_id if profile else None,
        }

    def _existing_idempotent(
        self,
        session: Session,
        *,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == self.actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if record is None:
            return None
        if record.request_sha256 != request_sha256:
            raise ConflictError(
                "idempotency_key_reused",
                "Idempotency-Key was already used with a different request.",
            )
        return dict(record.response_payload)

    def _save_idempotent(
        self,
        session: Session,
        *,
        operation: str,
        key: str,
        request_sha256: str,
        response_status: int,
        response: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecord(
                idempotency_id=new_id("idem"),
                actor_id=self.actor_id,
                operation=operation,
                idempotency_key=key,
                request_sha256=request_sha256,
                response_status=response_status,
                response_payload=response,
            )
        )

    def create_task(
        self,
        requirement: ProcurementRequirement,
        *,
        idempotency_key: str,
        scenario_id: str | None = None,
        policy_set_version: str | None = None,
        policy_index_version: str | None = None,
        policy_category: str | None = None,
        policy_region: str | None = None,
        requirement_draft_id: str | None = None,
        expected_requirement_draft_revision: int | None = None,
    ) -> dict[str, Any]:
        policy_binding = (
            policy_set_version,
            policy_index_version,
            policy_category,
            policy_region,
        )
        if any(value is not None for value in policy_binding) and not all(
            isinstance(value, str) and value.strip() for value in policy_binding
        ):
            raise BackendError(
                "policy_binding_incomplete",
                "Policy set, index, category, and region must be supplied together.",
            )
        normalized_policy_binding = tuple(
            value.strip() if isinstance(value, str) else None for value in policy_binding
        )
        (
            policy_set_version,
            policy_index_version,
            policy_category,
            policy_region,
        ) = normalized_policy_binding
        request = {
            "requirement": requirement.model_dump(mode="json"),
            "scenario_id": scenario_id,
            "policy_set_version": policy_set_version,
            "policy_index_version": policy_index_version,
            "policy_category": policy_category,
            "policy_region": policy_region,
            "requirement_draft_id": requirement_draft_id,
            "expected_requirement_draft_revision": expected_requirement_draft_revision,
        }
        request_sha = content_hash(request)
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation="create_task",
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            requirement_draft = None
            if requirement_draft_id is not None:
                requirement_draft = session.scalar(
                    select(RequirementDraft).where(
                        RequirementDraft.requirement_draft_id == requirement_draft_id
                    ).with_for_update()
                )
                if requirement_draft is None or requirement_draft.actor_id != self.actor_id:
                    raise NotFoundError("requirement_draft_not_found", "Requirement draft was not found.")
                if expected_requirement_draft_revision != requirement_draft.revision:
                    raise ConflictError(
                        "requirement_draft_revision_conflict",
                        "Requirement draft revision has changed.",
                        expected=expected_requirement_draft_revision,
                        actual=requirement_draft.revision,
                    )
                if requirement_draft.status != "READY" or requirement_draft.submitted_task_id:
                    raise ConflictError(
                        "requirement_draft_not_ready",
                        "Requirement draft is not ready to create a task.",
                        status=requirement_draft.status,
                    )
            task_id = new_id("task")
            task = Task(
                task_id=task_id,
                owner_id=self.actor_id,
                scenario_id=scenario_id,
                current_revision=1,
                status="DRAFT",
                policy_set_version=policy_set_version,
                policy_index_version=policy_index_version,
                policy_category=policy_category,
                policy_region=policy_region,
            )
            session.add(task)
            # SQLAlchemy cannot infer object dependency ordering from scalar FK
            # values alone when no ORM relationship is assigned.
            session.flush()
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=1,
                    change_type="TASK_CREATED",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                    details={"requirement_draft_id": requirement_draft_id},
                )
            )
            requirement_payload = requirement.model_dump(mode="json")
            source_artifact_id = None
            if requirement_draft is not None:
                source_artifact_id = new_id("artifact")
                extracted = {
                    row["field_name"]: row.get("normalized_value")
                    for row in (requirement_draft.candidates or {}).get("candidates", [])
                }
                session.add(WorkflowArtifact(
                    artifact_id=source_artifact_id,
                    task_id=task_id,
                    task_revision=1,
                    artifact_type="REQUIREMENT_INTAKE",
                    schema_version="requirement-intake/1.0.0",
                    payload={
                        "requirement_draft_id": requirement_draft.requirement_draft_id,
                        "document_sha256": requirement_draft.sha256,
                        "original_filename": requirement_draft.original_filename,
                        "parsed": requirement_draft.parsed_payload,
                        "candidates": requirement_draft.candidates,
                        "confirmed_requirement": requirement_payload,
                        "origins": {
                            name: (
                                "DOCUMENT" if name in extracted and extracted[name] == value
                                else "USER_CORRECTION" if name in extracted
                                else "USER_INPUT"
                            )
                            for name, value in requirement_payload.items()
                        },
                    },
                    content_sha256=content_hash({
                        "draft": requirement_draft.requirement_draft_id,
                        "confirmed": requirement_payload,
                    }),
                ))
                requirement_draft.status = "USED"
                requirement_draft.submitted_task_id = task_id
                requirement_draft.revision += 1
            session.add(
                RequirementRecord(
                    requirement_id=new_id("req"),
                    task_id=task_id,
                    task_revision=1,
                    requirement_version=1,
                    payload=requirement_payload,
                    content_sha256=content_hash(requirement_payload),
                    source_artifact_id=source_artifact_id,
                )
            )
            history_binding = self._new_history_binding(
                task_id=task_id,
                task_revision=1,
                requirement=requirement,
            )
            if history_binding.binding_status != "AVAILABLE" and _requires_history(requirement):
                raise BackendError(
                    "ranking_criterion_not_applicable",
                    "Historical ranking criteria are not applicable to this requirement scope.",
                )
            session.add(history_binding)
            response = {
                "task_id": task_id,
                "task_revision": 1,
                "status": "DRAFT",
                "policy_binding": self._policy_binding_response(task),
                "decision_profile": self._decision_profile_response(
                    DecisionPreferences(ranking_mode=ranking_mode_for(requirement)), None
                ),
                "supplier_history_binding": self._history_binding_response(
                    history_binding
                ),
            }
            self._save_idempotent(
                session,
                operation="create_task",
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=201,
                response=response,
            )
            return response

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            requirement_contract = (
                ProcurementRequirement.model_validate(requirement.payload)
                if requirement is not None else None
            )
            decision_preferences, decision_profile = (
                self._decision_preferences(session, task, requirement_contract)
                if requirement_contract is not None
                else (DecisionPreferences(), None)
            )
            graph = (
                session.get(GraphRun, task.current_graph_run_id)
                if task.current_graph_run_id
                else None
            )
            issue = (
                session.get(Issue, graph.current_interrupt_issue_id)
                if graph is not None and graph.current_interrupt_issue_id
                else None
            )
            job = (
                session.scalar(
                    select(Job)
                    .where(Job.graph_run_id == graph.graph_run_id)
                    .order_by(Job.created_at.desc(), Job.job_id.desc())
                )
                if graph is not None
                else None
            )
            graph_has_corrections = bool(
                graph is not None
                and session.scalar(
                    select(WorkflowArtifact.artifact_id).where(
                        WorkflowArtifact.graph_run_id == graph.graph_run_id,
                        WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                    )
                )
            )
            correction_batch_incomplete = False
            if graph is not None and graph_has_corrections:
                correction_artifacts = session.scalars(
                    select(WorkflowArtifact).where(
                        WorkflowArtifact.graph_run_id == graph.graph_run_id,
                        WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                    )
                ).all()
                corrected_targets = {
                    (artifact.quote_id, str(artifact.payload.get("field_name") or ""))
                    for artifact in correction_artifacts
                    if artifact.quote_id and artifact.payload.get("field_name")
                }
                required_targets: set[tuple[str, str]] = set()
                active_quotes = session.scalars(
                    select(Quote).where(
                        Quote.task_id == task_id,
                        Quote.active.is_(True),
                    )
                ).all()
                for active_quote in active_quotes:
                    review_artifacts = session.scalars(
                        select(WorkflowArtifact)
                        .where(
                            WorkflowArtifact.task_id == task_id,
                            WorkflowArtifact.quote_id == active_quote.quote_id,
                            WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
                        )
                        .order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    ).all()
                    prior_review = next(
                        (
                            artifact
                            for artifact in review_artifacts
                            if artifact.graph_run_id != graph.graph_run_id
                        ),
                        None,
                    )
                    if prior_review is None:
                        continue
                    batch_query = select(WorkflowArtifact).where(
                        WorkflowArtifact.task_id == task_id,
                        WorkflowArtifact.quote_id == active_quote.quote_id,
                        WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                    )
                    current_batch_artifact = session.scalar(
                        batch_query.where(
                            WorkflowArtifact.graph_run_id == graph.graph_run_id
                        ).order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    )
                    if current_batch_artifact is None:
                        current_batch_artifact = session.scalar(
                            batch_query.order_by(
                                WorkflowArtifact.task_revision.desc(),
                                WorkflowArtifact.created_at.desc(),
                            )
                        )
                    candidate_by_name = {}
                    if current_batch_artifact is not None:
                        current_batch = ExtractionBatch.model_validate(
                            current_batch_artifact.payload
                        )
                        candidate_by_name = {
                            candidate.field_name: candidate
                            for candidate in current_batch.candidates
                        }
                    for finding in prior_review.payload.get("review", {}).get(
                        "findings", []
                    ):
                        if (
                            finding.get("resolved")
                            or finding.get("severity") != "BLOCKING"
                        ):
                            continue
                        field_name = str(finding.get("field_name") or "")
                        candidate = candidate_by_name.get(field_name)
                        if (
                            candidate is not None
                            and candidate.validation_status.value == "VERIFIED"
                            and candidate.origin is not None
                            and candidate.origin.value
                            in {"USER_INPUT", "USER_CORRECTION"}
                        ):
                            continue
                        if field_name:
                            required_targets.add(
                                (active_quote.quote_id, field_name)
                            )
                correction_batch_incomplete = bool(
                    required_targets - corrected_targets
                )
            documents = session.execute(
                select(Document, Quote)
                .join(Quote, Quote.quote_id == Document.quote_id)
                .where(
                    Document.task_id == task_id,
                    Quote.active.is_(True),
                    Document.quote_version == Quote.current_version,
                )
                .order_by(Quote.created_at, Quote.quote_id)
            ).all()
            summary_completed = bool(
                task.current_result_id
                and session.scalar(
                    select(SummaryReport.summary_id).where(
                        SummaryReport.task_id == task_id,
                        SummaryReport.task_revision == task.current_revision,
                        SummaryReport.result_id == task.current_result_id,
                        SummaryReport.status == "SUCCEEDED",
                    )
                )
            )
            quote_review_completed = bool(task.current_result_id)
            if documents and not quote_review_completed:
                quote_review_completed = True
                requirement_revision = requirement.task_revision if requirement is not None else task.current_revision
                for document, quote in documents:
                    review_artifact = None
                    if graph is not None:
                        execution = session.scalar(
                            select(DocumentExecution).where(
                                DocumentExecution.graph_run_id == graph.graph_run_id,
                                DocumentExecution.document_id == document.document_id,
                            )
                        )
                        if execution is not None and execution.review_artifact_id:
                            review_artifact = session.get(
                                WorkflowArtifact, execution.review_artifact_id
                            )
                    if review_artifact is not None:
                        document_ready = (
                            review_artifact.payload.get("review_status")
                            == "READY_FOR_DOWNSTREAM"
                        )
                    else:
                        submitted_draft = session.scalar(
                            select(QuoteDraft).where(
                                QuoteDraft.task_id == task_id,
                                (
                                    (QuoteDraft.proposed_quote_id == quote.quote_id)
                                    | (QuoteDraft.replacement_quote_id == quote.quote_id)
                                ),
                                QuoteDraft.proposed_document_id == document.document_id,
                                QuoteDraft.status == "SUBMITTED",
                                QuoteDraft.base_task_revision >= requirement_revision,
                            )
                        )
                        document_ready = submitted_draft is not None
                    if not document_ready:
                        quote_review_completed = False
                        break
            progress = {
                "requirement_completed": True,
                "quote_review_completed": quote_review_completed,
                "decision_completed": task.current_result_id is not None,
                "summary_completed": summary_completed,
            }
            history_binding = self._binding_at_revision(
                session, task.task_id, task.current_revision
            )
            return {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "task_revision": task.current_revision,
                "status": task.status,
                "current_graph_run_id": task.current_graph_run_id,
                "current_snapshot_id": task.current_snapshot_id,
                "current_result_id": task.current_result_id,
                "summary_completed": summary_completed,
                "progress": progress,
                "policy_binding": self._policy_binding_response(task),
                "decision_profile": self._decision_profile_response(
                    decision_preferences, decision_profile
                ),
                "supplier_history_binding": self._history_binding_response(
                    history_binding
                ),
                "current_issue": self._issue_response(issue) if issue is not None else None,
                "current_job": (
                    {
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "job_status": job.status,
                        "task_revision": job.task_revision,
                        "error_code": job.error_code,
                        "error_message": job.error_message,
                        "has_corrections": graph_has_corrections,
                        "correction_batch_incomplete": correction_batch_incomplete,
                        "created_at": job.created_at.isoformat(),
                        "started_at": (
                            job.started_at.isoformat()
                            if job.started_at is not None
                            else None
                        ),
                    }
                    if job is not None
                    else None
                ),
                "quotes": [
                    {
                        "quote_id": quote.quote_id,
                        "quote_version": quote.current_version,
                        "supplier_id": quote.supplier_id,
                        "document_id": document.document_id,
                        "document_version": document.document_version,
                        "original_filename": document.original_filename,
                    }
                    for document, quote in documents
                ],
                "requirement": dict(requirement.payload) if requirement else None,
            }

    def list_quotes(self, task_id: str) -> dict[str, Any]:
        """Return every logical quote and immutable uploaded document version."""

        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            quotes = session.scalars(
                select(Quote)
                .where(Quote.task_id == task_id)
                .order_by(Quote.created_at.desc(), Quote.quote_id.desc())
            ).all()
            documents = session.scalars(
                select(Document)
                .where(Document.task_id == task_id)
                .order_by(
                    Document.quote_id,
                    Document.quote_version.desc(),
                    Document.document_version.desc(),
                )
            ).all()
            documents_by_quote: dict[str, list[Document]] = {}
            for document in documents:
                documents_by_quote.setdefault(document.quote_id, []).append(document)
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [
                    {
                        "quote_id": quote.quote_id,
                        "supplier_id": quote.supplier_id,
                        "current_version": quote.current_version,
                        "active": quote.active,
                        "created_at": quote.created_at.isoformat(),
                        "versions": [
                            {
                                "quote_version": document.quote_version,
                                "document_id": document.document_id,
                                "document_version": document.document_version,
                                "original_filename": document.original_filename,
                                "media_type": document.media_type,
                                "size_bytes": document.size_bytes,
                                "document_sha256": document.sha256,
                                "is_synthetic": document.is_synthetic,
                                "is_current": (
                                    quote.active
                                    and document.quote_version == quote.current_version
                                ),
                                "created_at": document.created_at.isoformat(),
                            }
                            for document in documents_by_quote.get(quote.quote_id, [])
                        ],
                    }
                    for quote in quotes
                ],
            }

    def deactivate_quote(
        self,
        task_id: str,
        quote_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Soft-disable a quote while preserving every document version."""

        request = {
            "task_id": task_id,
            "quote_id": quote_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"deactivate_quote:{quote_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            quote = session.scalar(
                select(Quote)
                .where(Quote.quote_id == quote_id, Quote.task_id == task_id)
                .with_for_update()
            )
            if quote is None:
                raise NotFoundError("quote_not_found", "Quote was not found.")
            if not quote.active:
                raise ConflictError(
                    "quote_already_inactive", "Quote has already been disabled."
                )
            active_replacement = session.scalar(
                select(QuoteDraft).where(
                    QuoteDraft.task_id == task_id,
                    QuoteDraft.replacement_quote_id == quote_id,
                    QuoteDraft.status.in_(
                        ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                    ),
                )
            )
            if active_replacement is not None:
                raise ConflictError(
                    "quote_replacement_in_progress",
                    "Discard or submit the replacement draft before disabling this quote.",
                    quote_draft_id=active_replacement.quote_draft_id,
                )

            self._supersede_current_graph(session, task)
            quote.active = False
            next_revision = task.current_revision + 1
            task.current_revision = next_revision
            history_binding = self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=next_revision - 1,
                to_revision=next_revision,
            )
            task.status = "DRAFT"
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=next_revision,
                    change_type="QUOTE_DEACTIVATED",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                    details={
                        "quote_id": quote.quote_id,
                        "supplier_id": quote.supplier_id,
                        "quote_version": quote.current_version,
                    },
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "status": task.status,
                "quote_id": quote.quote_id,
                "supplier_id": quote.supplier_id,
                "active": False,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def reactivate_quote(
        self,
        task_id: str,
        quote_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Re-enable a soft-disabled quote without uploading its document again."""

        request = {
            "task_id": task_id,
            "quote_id": quote_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"reactivate_quote:{quote_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            quote = session.scalar(
                select(Quote)
                .where(Quote.quote_id == quote_id, Quote.task_id == task_id)
                .with_for_update()
            )
            if quote is None:
                raise NotFoundError("quote_not_found", "Quote was not found.")
            if quote.active:
                raise ConflictError(
                    "quote_already_active", "Quote is already active."
                )

            self._supersede_current_graph(session, task)
            quote.active = True
            next_revision = task.current_revision + 1
            task.current_revision = next_revision
            history_binding = self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=next_revision - 1,
                to_revision=next_revision,
            )
            task.status = "DRAFT"
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=next_revision,
                    change_type="QUOTE_REACTIVATED",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                    details={
                        "quote_id": quote.quote_id,
                        "supplier_id": quote.supplier_id,
                        "quote_version": quote.current_version,
                    },
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "status": task.status,
                "quote_id": quote.quote_id,
                "supplier_id": quote.supplier_id,
                "active": True,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def create_quote_revision_draft(
        self,
        task_id: str,
        quote_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        """Re-open the current immutable quote document as a new review draft."""

        request = {
            "task_id": task_id,
            "quote_id": quote_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"create_quote_revision_draft:{quote_id}"
        copied_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(
                    select(Task).where(Task.task_id == task_id).with_for_update()
                )
                if task is None or task.owner_id != self.actor_id:
                    raise NotFoundError("task_not_found", "Task was not found.")
                self._require_revision(task, expected_task_revision)
                quote = session.scalar(
                    select(Quote)
                    .where(Quote.quote_id == quote_id, Quote.task_id == task_id)
                    .with_for_update()
                )
                if quote is None:
                    raise NotFoundError("quote_not_found", "Quote was not found.")
                if not quote.active:
                    raise ConflictError(
                        "quote_inactive", "A disabled quote cannot be updated."
                    )
                active = session.scalar(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.in_(
                            ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                        ),
                    )
                )
                if active is not None:
                    raise ConflictError(
                        "active_quote_draft_exists",
                        "Discard or submit the active quote draft before editing another.",
                        quote_draft_id=active.quote_draft_id,
                    )
                document = session.scalar(
                    select(Document).where(
                        Document.quote_id == quote_id,
                        Document.quote_version == quote.current_version,
                    )
                )
                if document is None:
                    raise ConflictError(
                        "quote_document_missing",
                        "The current quote document is unavailable.",
                    )
                source_path = Path(document.storage_path)
                if not source_path.exists():
                    raise ConflictError(
                        "quote_file_missing", "The current quote file is unavailable."
                    )

                draft_id = new_id("draft")
                proposed_quote_id = new_id("quote")
                proposed_document_id = new_id("doc")
                job_id = new_id("job")
                extension = ".pdf" if document.media_type == "application/pdf" else ".csv"
                copied_path = (
                    self.storage_root
                    / ".drafts"
                    / task_id
                    / draft_id
                    / f"source{extension}"
                )
                copied_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, copied_path)
                dictionary_sha = hashlib.sha256(
                    self.quote_dictionary_path.read_bytes()
                ).hexdigest()
                carried_review = self._carry_forward_quote_review(
                    session,
                    task=task,
                    quote=quote,
                    document=document,
                    proposed_document_id=proposed_document_id,
                    dictionary_sha=dictionary_sha,
                )
                draft = QuoteDraft(
                    quote_draft_id=draft_id,
                    task_id=task_id,
                    actor_id=self.actor_id,
                    base_task_revision=task.current_revision,
                    revision=2 if carried_review is not None else 1,
                    status="READY_TO_SUBMIT" if carried_review is not None else "PROCESSING",
                    proposed_quote_id=proposed_quote_id,
                    replacement_quote_id=quote.quote_id,
                    proposed_document_id=proposed_document_id,
                    supplier_id=quote.supplier_id,
                    original_filename=document.original_filename,
                    media_type=document.media_type,
                    size_bytes=document.size_bytes,
                    sha256=document.sha256,
                    storage_path=str(copied_path),
                    is_synthetic=document.is_synthetic,
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                    dictionary_sha256=dictionary_sha,
                    parsed_artifact_id=(
                        carried_review["parsed_artifact_id"]
                        if carried_review is not None
                        else None
                    ),
                    batch_artifact_id=(
                        carried_review["batch_artifact_id"]
                        if carried_review is not None
                        else None
                    ),
                    review_artifact_id=(
                        carried_review["review_artifact_id"]
                        if carried_review is not None
                        else None
                    ),
                )
                session.add(draft)
                session.flush([draft])
                if carried_review is None:
                    session.add(
                        Job(
                            job_id=job_id,
                            task_id=task_id,
                            graph_run_id=None,
                            quote_draft_id=draft_id,
                            job_type="DRAFT_REVIEW",
                            status="PENDING",
                            task_revision=task.current_revision,
                        )
                    )
                session.flush()
                response = self._quote_draft_response(session, draft)
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=202,
                    response=response,
                )
                return response
        except Exception:
            if copied_path is not None and copied_path.exists():
                copied_path.unlink()
            raise

    def _carry_forward_quote_review(
        self,
        session: Session,
        *,
        task: Task,
        quote: Quote,
        document: Document,
        proposed_document_id: str,
        dictionary_sha: str,
    ) -> dict[str, str] | None:
        """Seed an edit draft from its submitted, fully reviewed quote version."""

        source_draft = session.scalar(
            select(QuoteDraft)
            .where(
                QuoteDraft.task_id == task.task_id,
                QuoteDraft.proposed_document_id == document.document_id,
                QuoteDraft.status == "SUBMITTED",
                QuoteDraft.dictionary_sha256 == dictionary_sha,
                QuoteDraft.batch_artifact_id.is_not(None),
                QuoteDraft.review_artifact_id.is_not(None),
            )
            .order_by(QuoteDraft.submitted_at.desc(), QuoteDraft.created_at.desc())
        )
        if source_draft is None:
            return None
        batch_artifact = session.get(WorkflowArtifact, source_draft.batch_artifact_id)
        review_artifact = session.get(WorkflowArtifact, source_draft.review_artifact_id)
        if batch_artifact is None or review_artifact is None:
            return None
        source_batch = ExtractionBatch.model_validate(batch_artifact.payload)
        source_review = ReviewEnvelope.model_validate(review_artifact.payload)
        # Same-file edits must retain answers supplied after initial submission.
        executions = session.scalars(select(DocumentExecution).join(
            GraphRun, GraphRun.graph_run_id == DocumentExecution.graph_run_id
        ).where(
            DocumentExecution.document_id == document.document_id,
            DocumentExecution.batch_artifact_id.is_not(None),
            GraphRun.task_id == task.task_id,
        ).order_by(GraphRun.effective_revision.desc(), GraphRun.created_at.desc()))
        for execution in executions:
            latest = session.get(WorkflowArtifact, execution.batch_artifact_id)
            if latest is None:
                continue
            candidate_batch = ExtractionBatch.model_validate(latest.payload)
            if candidate_batch.parsed_input.context.document_id != document.document_id:
                continue
            source_batch = candidate_batch
            source_review = source_review.model_copy(update={
                "batch": source_batch,
                "corrections": tuple(CorrectionEvent.model_validate(p) for p in
                                     self.correction_event_payloads_for_batch(latest.artifact_id)),
                "review_events": self.review_confirmations_for_batch(latest.artifact_id),
            })
            break
        if (
            source_batch.parsed_input.document_sha256 != document.sha256
            or not _quote_draft_submission_ready(source_review)
            or source_review.schema_version != REVIEW_SCHEMA_VERSION
            or source_review.review_policy_version != REVIEW_POLICY_VERSION
        ):
            return None

        next_quote_version = quote.current_version + 1
        now = datetime.now(timezone.utc)
        source_id_map = {
            source.source_id: stable_id(
                "src",
                {
                    "carried_from": source.source_id,
                    "document_id": proposed_document_id,
                    "document_version": 1,
                    "document_sha256": document.sha256,
                },
            )
            for source in source_batch.parsed_input.sources
        }
        context = source_batch.parsed_input.context.model_copy(
            update={
                "task_revision": task.current_revision,
                "quote_id": quote.quote_id,
                "quote_version": next_quote_version,
                "document_id": proposed_document_id,
                "document_version": 1,
            }
        )
        sources = tuple(
            source.model_copy(
                update={
                    "source_id": source_id_map[source.source_id],
                    "document_id": proposed_document_id,
                    "document_version": 1,
                }
            )
            for source in source_batch.parsed_input.sources
        )
        context_groups = tuple(
            group.model_copy(
                update={
                    "context_group_id": stable_id(
                        "context",
                        {
                            "carried_from": group.context_group_id,
                            "document_id": proposed_document_id,
                        },
                    ),
                    "source_ids": tuple(source_id_map[item] for item in group.source_ids),
                }
            )
            for group in source_batch.parsed_input.context_groups
        )
        parsed_input = source_batch.parsed_input.model_copy(
            update={
                "context": context,
                "sources": sources,
                "context_groups": context_groups,
            }
        )
        candidates = tuple(
            candidate.model_copy(
                update={
                    "field_id": stable_id(
                        "fld",
                        {
                            "quote_id": quote.quote_id,
                            "quote_version": next_quote_version,
                            "field_name": candidate.field_name,
                            "field_version": candidate.field_version,
                        },
                    ),
                    "quote_id": quote.quote_id,
                    "quote_version": next_quote_version,
                    "source_refs": tuple(
                        source_ref.model_copy(
                            update={"source_id": source_id_map[source_ref.source_id]}
                        )
                        for source_ref in candidate.source_refs
                    ),
                }
            )
            for candidate in source_batch.candidates
        )
        carried_batch = ExtractionBatch.model_validate(
            source_batch.model_copy(
                update={
                    "parsed_input": parsed_input,
                    "candidates": candidates,
                    "run": None,
                    "created_at": now,
                }
            ).model_dump(mode="python")
        )

        source_corrections = {
            correction.field_name: correction
            for correction in source_review.corrections
        }
        carried_corrections: list[CorrectionEvent] = []
        for candidate in carried_batch.candidates:
            if candidate.origin not in {Origin.USER_INPUT, Origin.USER_CORRECTION}:
                continue
            source_correction = source_corrections.get(candidate.field_name)
            if source_correction is None:
                return None
            before = source_correction.before.model_copy(
                update={
                    "field_id": stable_id(
                        "fld",
                        {
                            "quote_id": quote.quote_id,
                            "quote_version": next_quote_version,
                            "field_name": candidate.field_name,
                            "field_version": source_correction.before.field_version,
                        },
                    ),
                    "source_ids": tuple(
                        source_id_map[source_id]
                        for source_id in source_correction.before.source_ids
                    ),
                }
            )
            after = CandidateValueSnapshot.from_candidate(candidate)
            carried_corrections.append(
                source_correction.model_copy(
                    update={
                        "correction_id": stable_id(
                            "correction",
                            {
                                "carried_from": source_correction.correction_id,
                                "document_id": proposed_document_id,
                                "reviewed_at": now.isoformat(),
                            },
                        ),
                        "before": before,
                        "after": after,
                        "reason_code": "CARRIED_FORWARD_FROM_CURRENT_QUOTE",
                        "reason": "从当前已确认报价版本继承。",
                        "basis_source_ids": tuple(
                            source_id_map[source_id]
                            for source_id in source_correction.basis_source_ids
                        ),
                        "reviewer_id": self.actor_id,
                        "reviewed_at": now,
                        "task_revision": task.current_revision,
                        "draft_revision": 1,
                        "quote_id": quote.quote_id,
                        "quote_version": next_quote_version,
                        "document_id": proposed_document_id,
                        "document_version": 1,
                        "document_sha256": document.sha256,
                    }
                )
            )

        review_events = tuple(
            create_review_event(
                carried_batch,
                field_name=candidate.field_name,
                action={
                    ValidationStatus.MISSING: HumanReviewAction.CONFIRM_MISSING,
                    ValidationStatus.CONFLICT: HumanReviewAction.CONFIRM_CONFLICT,
                }.get(candidate.validation_status, HumanReviewAction.CONFIRM_VALUE),
                reviewer_id=self.actor_id,
                reviewed_at=now,
                reason_code="CARRIED_FORWARD_FROM_CURRENT_QUOTE",
                draft_revision=1,
            )
            for candidate in carried_batch.candidates
        )
        requirement_record = session.scalar(
            select(RequirementRecord)
            .where(RequirementRecord.task_id == task.task_id)
            .order_by(RequirementRecord.requirement_version.desc())
        )
        if requirement_record is None:
            return None
        requirement = ProcurementRequirement.model_validate(requirement_record.payload)
        carried_review = review_extraction_batch(
            carried_batch,
            self.quote_dictionary,
            CriticalityContext(
                required_revision=requirement.revision,
                base_unit=requirement.base_unit,
                payment_terms_required=(
                    requirement.ranking_preference
                    == "LONGEST_CONFIRMED_PAYMENT_TERM"
                    or requirement.secondary_preference
                    == "LONGEST_CONFIRMED_PAYMENT_TERM"
                ),
            ),
            input_is_synthetic=document.is_synthetic,
            reviewed_at=now,
            environment=source_review.environment,
            review_events=review_events,
            corrections=tuple(carried_corrections),
        )
        if not _quote_draft_submission_ready(carried_review):
            return None

        parsed_artifact_id = new_id("artifact")
        batch_artifact_id = new_id("artifact")
        review_artifact_id = new_id("artifact")

        def add_artifact(
            *,
            artifact_id: str,
            artifact_type: str,
            payload: dict[str, Any],
            parent_id: str | None,
            schema_version: str | None = None,
        ) -> None:
            session.add(
                WorkflowArtifact(
                    artifact_id=artifact_id,
                    task_id=task.task_id,
                    task_revision=task.current_revision,
                    artifact_type=artifact_type,
                    schema_version=schema_version,
                    parent_artifact_id=parent_id,
                    quote_id=quote.quote_id,
                    document_id=proposed_document_id,
                    graph_run_id=None,
                    payload=payload,
                    content_sha256=content_hash(payload),
                )
            )

        add_artifact(
            artifact_id=parsed_artifact_id,
            artifact_type="PARSED_INPUT",
            schema_version=carried_batch.schema_version,
            payload=carried_batch.parsed_input.model_dump(mode="json"),
            parent_id=None,
        )
        add_artifact(
            artifact_id=batch_artifact_id,
            artifact_type="EXTRACTION_BATCH",
            schema_version=carried_batch.schema_version,
            payload=carried_batch.model_dump(mode="json"),
            parent_id=parsed_artifact_id,
        )
        parent_artifact_id = batch_artifact_id
        for correction in carried_corrections:
            correction_artifact_id = new_id("artifact")
            add_artifact(
                artifact_id=correction_artifact_id,
                artifact_type="CORRECTION_EVENT",
                payload=correction.model_dump(mode="json"),
                parent_id=parent_artifact_id,
            )
            parent_artifact_id = correction_artifact_id
        add_artifact(
            artifact_id=review_artifact_id,
            artifact_type="REVIEW_ENVELOPE",
            schema_version=carried_review.schema_version,
            payload=carried_review.model_dump(mode="json"),
            parent_id=parent_artifact_id,
        )
        return {
            "parsed_artifact_id": parsed_artifact_id,
            "batch_artifact_id": batch_artifact_id,
            "review_artifact_id": review_artifact_id,
        }

    def _quote_revision_has_material_changes(
        self,
        session: Session,
        *,
        draft: QuoteDraft,
        batch: ExtractionBatch,
    ) -> bool:
        """Return whether an edit draft differs from the current submitted quote."""

        if draft.replacement_quote_id is None:
            return True
        quote = session.scalar(
            select(Quote).where(
                Quote.quote_id == draft.replacement_quote_id,
                Quote.task_id == draft.task_id,
            )
        )
        if quote is None:
            return True
        document = session.scalar(
            select(Document).where(
                Document.quote_id == quote.quote_id,
                Document.quote_version == quote.current_version,
            )
        )
        if document is None or document.sha256 != draft.sha256:
            return True
        submitted_draft = session.scalar(
            select(QuoteDraft)
            .where(
                QuoteDraft.task_id == draft.task_id,
                QuoteDraft.proposed_document_id == document.document_id,
                QuoteDraft.status == "SUBMITTED",
                QuoteDraft.batch_artifact_id.is_not(None),
            )
            .order_by(QuoteDraft.submitted_at.desc(), QuoteDraft.created_at.desc())
        )
        if submitted_draft is None or submitted_draft.batch_artifact_id is None:
            return True
        submitted_artifact = session.get(
            WorkflowArtifact, submitted_draft.batch_artifact_id
        )
        if submitted_artifact is None:
            return True
        submitted_batch = ExtractionBatch.model_validate(submitted_artifact.payload)
        return self._quote_field_snapshot(batch) != self._quote_field_snapshot(
            submitted_batch
        )

    @staticmethod
    def _quote_field_snapshot(batch: ExtractionBatch) -> str:
        """Hash business field state while ignoring version-specific identities."""

        fields = [
            {
                "field_name": candidate.field_name,
                "raw_value": candidate.raw_value,
                "normalized_value": candidate.normalized_value,
                "unit": candidate.unit,
                "validation_status": candidate.validation_status,
            }
            for candidate in sorted(
                batch.candidates, key=lambda candidate: candidate.field_name
            )
        ]
        return content_hash(fields)

    def upload_quote_draft_stream(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        supplier_id: str,
        original_filename: str,
        media_type: str,
        stream: BinaryIO,
        idempotency_key: str,
        is_synthetic: bool = False,
        replacement_quote_id: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
        max_bytes: int = 5 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> dict[str, Any]:
        """Persist a review draft without changing authoritative task inputs."""

        if media_type not in {"application/pdf", "text/csv"}:
            raise BackendError(
                "unsupported_media_type",
                "Only application/pdf and text/csv quote files are supported.",
            )
        if max_bytes < 1 or chunk_size < 1:
            raise ValueError("upload limits must be positive")

        staging_directory = self.storage_root / ".staging"
        staging_directory.mkdir(parents=True, exist_ok=True)
        staged_path = staging_directory / f"{new_id('draft_upload')}.tmp"
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with staged_path.open("xb") as handle:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise BackendError(
                            "upload_stream_invalid",
                            "Quote draft upload stream must produce bytes.",
                        )
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise BackendError(
                            "file_too_large",
                            "Quote file exceeds the 5 MiB limit.",
                            max_file_size_bytes=max_bytes,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size_bytes == 0:
                raise BackendError("empty_file", "Quote draft file is empty.")
        except Exception:
            if staged_path.exists():
                staged_path.unlink()
            raise

        file_sha = digest.hexdigest()
        dictionary_sha = hashlib.sha256(self.quote_dictionary_path.read_bytes()).hexdigest()
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "supplier_id": supplier_id,
            "original_filename": Path(original_filename).name,
            "media_type": media_type,
            "content_sha256": file_sha,
            "is_synthetic": is_synthetic,
            "replacement_quote_id": replacement_quote_id,
            "provider": provider,
            "model_id": model_id,
            "environment": environment,
            "prompt_version": prompt_version,
        }
        request_sha = content_hash(request)
        operation = f"upload_quote_draft:{task_id}"
        final_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(
                    select(Task).where(Task.task_id == task_id).with_for_update()
                )
                if task is None or task.owner_id != self.actor_id:
                    raise NotFoundError("task_not_found", "Task was not found.")
                self._require_revision(task, expected_task_revision)
                replacement_quote = None
                if replacement_quote_id is not None:
                    replacement_quote = session.scalar(
                        select(Quote)
                        .where(
                            Quote.quote_id == replacement_quote_id,
                            Quote.task_id == task_id,
                        )
                        .with_for_update()
                    )
                    if replacement_quote is None:
                        raise NotFoundError(
                            "quote_not_found", "Quote was not found."
                        )
                    if not replacement_quote.active:
                        raise ConflictError(
                            "quote_inactive",
                            "A disabled quote cannot be updated.",
                        )
                    if replacement_quote.supplier_id != supplier_id.strip():
                        raise BackendError(
                            "quote_supplier_mismatch",
                            "The replacement must keep the original supplier ID.",
                        )
                for stale in session.scalars(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.in_(
                            ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                        ),
                        QuoteDraft.base_task_revision != task.current_revision,
                    )
                ):
                    stale.status = "STALE"
                    stale.revision += 1
                self._raise_if_duplicate_quote(session, task_id, file_sha)
                active = session.scalar(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.in_(
                            ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                        ),
                    )
                )
                if active is not None:
                    raise ConflictError(
                        "active_quote_draft_exists",
                        "Discard or submit the active quote draft before uploading another.",
                        quote_draft_id=active.quote_draft_id,
                    )
                draft_id = new_id("draft")
                quote_id = new_id("quote")
                document_id = new_id("doc")
                job_id = new_id("job")
                extension = ".pdf" if media_type == "application/pdf" else ".csv"
                final_path = (
                    self.storage_root
                    / ".drafts"
                    / task_id
                    / draft_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.rename(final_path)
                draft = QuoteDraft(
                    quote_draft_id=draft_id,
                    task_id=task_id,
                    actor_id=self.actor_id,
                    base_task_revision=task.current_revision,
                    revision=1,
                    status="PROCESSING",
                    proposed_quote_id=quote_id,
                    replacement_quote_id=(
                        replacement_quote.quote_id
                        if replacement_quote is not None
                        else None
                    ),
                    proposed_document_id=document_id,
                    supplier_id=supplier_id.strip(),
                    original_filename=Path(original_filename).name,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    sha256=file_sha,
                    storage_path=str(final_path),
                    is_synthetic=is_synthetic,
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                    dictionary_sha256=dictionary_sha,
                )
                session.add(draft)
                # Persist the parent row before scheduling the job that references it.
                # Without this explicit flush SQLAlchemy may emit the jobs INSERT first
                # because no ORM relationship links these two pending objects.
                session.flush([draft])
                session.add(
                    Job(
                        job_id=job_id,
                        task_id=task_id,
                        graph_run_id=None,
                        quote_draft_id=draft_id,
                        job_type="DRAFT_REVIEW",
                        status="PENDING",
                        task_revision=task.current_revision,
                    )
                )
                session.flush()
                response = self._quote_draft_response(session, draft)
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=202,
                    response=response,
                )
                return response
        except Exception:
            if final_path is not None and final_path.exists():
                final_path.unlink()
            raise
        finally:
            if staged_path.exists():
                staged_path.unlink()

    def list_quote_drafts(self, task_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            for stale in session.scalars(
                select(QuoteDraft).where(
                    QuoteDraft.task_id == task_id,
                    QuoteDraft.status.in_(
                        ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                    ),
                    QuoteDraft.base_task_revision != task.current_revision,
                )
            ):
                stale.status = "STALE"
                stale.revision += 1
            drafts = session.scalars(
                select(QuoteDraft)
                .where(QuoteDraft.task_id == task_id)
                .order_by(QuoteDraft.created_at.desc(), QuoteDraft.quote_draft_id.desc())
            ).all()
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [self._quote_draft_response(session, draft) for draft in drafts],
            }

    def get_quote_draft(self, task_id: str, draft_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            draft = self._owned_quote_draft(session, task_id, draft_id)
            task = session.get(Task, task_id)
            if (
                task is not None
                and task.current_revision != draft.base_task_revision
                and draft.status in {"UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT"}
            ):
                draft.status = "STALE"
                draft.revision += 1
            return self._quote_draft_response(session, draft)

    def quote_draft_content(self, task_id: str, draft_id: str) -> dict[str, Any]:
        """Return a scoped draft file without exposing its storage path in JSON."""

        with self.session_factory() as session:
            draft = self._owned_quote_draft(session, task_id, draft_id)
            root = self.storage_root.resolve()
            path = Path(draft.storage_path).resolve()
            if root not in path.parents or not path.is_file():
                raise NotFoundError(
                    "quote_draft_content_not_found",
                    "Quote draft content was not found.",
                )
            return {
                "path": path,
                "filename": draft.original_filename,
                "media_type": draft.media_type,
                "sha256": draft.sha256,
            }

    def quote_draft_job_context(self, job_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != "DRAFT_REVIEW" or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.get(QuoteDraft, job.quote_draft_id)
            task = session.get(Task, job.task_id)
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == job.task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if draft is None or task is None or requirement is None:
                raise NotFoundError("draft_job_context_missing", "Quote draft job context is missing.")
            quote_id = draft.proposed_quote_id
            quote_version = 1
            if draft.replacement_quote_id is not None:
                replacement_quote = session.get(Quote, draft.replacement_quote_id)
                if replacement_quote is None:
                    raise NotFoundError(
                        "quote_not_found", "Replacement quote was not found."
                    )
                quote_id = replacement_quote.quote_id
                quote_version = replacement_quote.current_version + 1
            return {
                "job_id": job.job_id,
                "task_id": task.task_id,
                "task_revision": draft.base_task_revision,
                "scenario_id": task.scenario_id,
                "quote_draft_id": draft.quote_draft_id,
                "quote_id": quote_id,
                "quote_version": quote_version,
                "document_id": draft.proposed_document_id,
                "supplier_id": draft.supplier_id,
                "media_type": draft.media_type,
                "storage_path": draft.storage_path,
                "document_sha256": draft.sha256,
                "is_synthetic": draft.is_synthetic,
                "calls_used": draft.calls_used,
                "max_calls": draft.max_calls,
                "requirement": dict(requirement.payload),
            }

    def claim_quote_draft_job(self, job_id: str) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or job.job_type != "DRAFT_REVIEW" or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.scalar(
                select(QuoteDraft)
                .where(QuoteDraft.quote_draft_id == job.quote_draft_id)
                .with_for_update()
            )
            task = session.scalar(select(Task).where(Task.task_id == job.task_id).with_for_update())
            if draft is None or task is None:
                raise NotFoundError("draft_job_context_missing", "Quote draft job context is missing.")
            if job.status != "PENDING":
                raise ConflictError("job_not_pending", "Job is not pending.")
            if task.current_revision != draft.base_task_revision:
                draft.status = "STALE"
                draft.revision += 1
                job.status = "SUPERSEDED"
                raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
            if job.attempts >= 3:
                raise BackendError("job_attempt_budget_exceeded", "Job attempt budget was exceeded.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = utc_now()
            draft.status = "PROCESSING"
            return self._job_response(job)

    def record_quote_draft_calls(self, draft_id: str, calls_after: int) -> None:
        with self.session_factory.begin() as session:
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == draft_id).with_for_update()
            )
            if draft is not None:
                draft.calls_used = calls_after

    def complete_quote_draft_job(
        self,
        job_id: str,
        *,
        parsed_artifact_id: str,
        batch_artifact_id: str,
        review_artifact_id: str,
        downstream_ready: bool,
    ) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == job.quote_draft_id).with_for_update()
            )
            if draft is None:
                raise NotFoundError("quote_draft_not_found", "Quote draft was not found.")
            draft.parsed_artifact_id = parsed_artifact_id
            draft.batch_artifact_id = batch_artifact_id
            draft.review_artifact_id = review_artifact_id
            review_artifact = session.get(WorkflowArtifact, review_artifact_id)
            if review_artifact is None:
                raise ConflictError(
                    "quote_draft_review_missing",
                    "Quote draft review artifact is missing.",
                )
            envelope = ReviewEnvelope.model_validate(review_artifact.payload)
            # Successful extraction is only a deterministic pre-check.  A new
            # draft always remains in review until all 30 business fields have
            # a current, version-bound human disposition.
            draft.status = "REVIEW_REQUIRED"
            draft.error_code = None
            draft.error_message = None
            job.status = "SUCCEEDED"
            job.finished_at = utc_now()
            return self._quote_draft_response(session, draft)

    def fail_quote_draft_job(self, job_id: str, *, code: str, message: str) -> None:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.quote_draft_id:
                return
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == job.quote_draft_id).with_for_update()
            )
            job.status = "FAILED"
            job.error_code = code
            job.error_message = message[:1000]
            job.finished_at = utc_now()
            if draft is not None:
                draft.status = "FAILED"
                draft.error_code = code
                draft.error_message = message[:1000]

    def review_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_draft_revision: int,
        schema_version: str,
        actions: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Atomically confirm or edit all 30 quote fields and re-run admission."""

        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_draft_revision": expected_draft_revision,
            "schema_version": schema_version,
            "actions": actions,
        }
        request_sha = content_hash(request)
        operation = f"review_quote_draft:{draft_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated

            draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
            artifact_quote_id = (
                draft.replacement_quote_id or draft.proposed_quote_id
            )
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if task.current_revision != draft.base_task_revision:
                draft.status = "STALE"
                draft.revision += 1
                raise ConflictError(
                    "quote_draft_stale",
                    "Task changed while the quote draft was open.",
                )
            if draft.revision != expected_draft_revision:
                raise ConflictError(
                    "quote_draft_revision_conflict",
                    "Quote draft revision has changed.",
                    expected=expected_draft_revision,
                    actual=draft.revision,
                )
            if schema_version != QUOTE_REVIEW_SCHEMA_VERSION:
                raise ConflictError(
                    "quote_review_schema_conflict",
                    "Quote review schema has changed; refresh before confirming fields.",
                    expected=schema_version,
                    actual=QUOTE_REVIEW_SCHEMA_VERSION,
                )
            if draft.status not in {"REVIEW_REQUIRED", "READY_TO_SUBMIT"}:
                raise ConflictError(
                    "quote_draft_not_reviewable",
                    "Quote draft is not awaiting human review.",
                    status=draft.status,
                )
            if not draft.batch_artifact_id or not draft.review_artifact_id:
                raise ConflictError(
                    "quote_draft_review_missing",
                    "Quote draft review artifacts are missing.",
                )
            current_dictionary_sha = hashlib.sha256(
                self.quote_dictionary_path.read_bytes()
            ).hexdigest()
            if draft.dictionary_sha256 != current_dictionary_sha:
                raise ConflictError(
                    "quote_draft_review_stale",
                    "The quote field dictionary changed; reprocess this draft before review.",
                    expected=draft.dictionary_sha256,
                    actual=current_dictionary_sha,
                )

            batch_artifact = session.get(WorkflowArtifact, draft.batch_artifact_id)
            review_artifact = session.get(WorkflowArtifact, draft.review_artifact_id)
            if batch_artifact is None or review_artifact is None:
                raise ConflictError(
                    "quote_draft_review_missing",
                    "Quote draft review artifacts are missing.",
                )
            batch = ExtractionBatch.model_validate(batch_artifact.payload)
            prior_envelope = ReviewEnvelope.model_validate(review_artifact.payload)
            candidates = {candidate.field_name: candidate for candidate in batch.candidates}
            expected_fields = {
                definition.field_name
                for definition in self.quote_dictionary.extractable_fields
            }
            targets = [str(item.get("field_name", "")) for item in actions]
            target_set = set(targets)
            coverage_errors: list[dict[str, Any]] = []
            if len(targets) != len(target_set):
                coverage_errors.append(
                    {
                        "code": "DUPLICATE_FIELD_ACTION",
                        "field_names": sorted(
                            {name for name in target_set if targets.count(name) > 1}
                        ),
                        "group_id": None,
                        "message": "同一字段不能重复提交审核动作。",
                    }
                )
            missing_fields = sorted(expected_fields - target_set)
            unexpected_fields = sorted(target_set - expected_fields)
            if missing_fields:
                coverage_errors.append(
                    {
                        "code": "FULL_FIELD_REVIEW_REQUIRED",
                        "field_names": missing_fields,
                        "group_id": None,
                        "message": "正式提交前必须核对全部 30 个报价字段。",
                    }
                )
            if unexpected_fields:
                coverage_errors.append(
                    {
                        "code": "SYSTEM_FIELD_NOT_EDITABLE",
                        "field_names": unexpected_fields,
                        "group_id": None,
                        "message": "请求包含不可编辑的系统字段。",
                    }
                )
            if coverage_errors:
                raise BackendError(
                    "quote_draft_review_invalid",
                    "Quote draft review is incomplete or invalid.",
                    errors=coverage_errors,
                )

            version_errors: list[dict[str, Any]] = []
            for item in actions:
                field_name = str(item["field_name"])
                candidate = candidates.get(field_name)
                if (
                    candidate is None
                    or candidate.field_id != item.get("expected_field_id")
                    or candidate.field_version != item.get("expected_field_version")
                ):
                    version_errors.append(
                        {
                            "code": "FIELD_VERSION_CONFLICT",
                            "field_names": [field_name],
                            "group_id": self._quote_field_group_id(field_name),
                            "message": "字段已被更新，请刷新后重新核对。",
                            "expected_field_id": item.get("expected_field_id"),
                            "actual_field_id": candidate.field_id if candidate else None,
                            "expected_field_version": item.get("expected_field_version"),
                            "actual_field_version": candidate.field_version if candidate else None,
                        }
                    )
            if version_errors:
                raise ConflictError(
                    "quote_draft_field_conflict",
                    "One or more quote fields changed during review.",
                    errors=version_errors,
                )

            reviewed_at = datetime.now(timezone.utc)
            working_batch = batch
            new_corrections: list[CorrectionEvent] = []
            deferred_confirmations: list[dict[str, Any]] = []
            action_errors: list[dict[str, Any]] = []
            for item in actions:
                field_name = str(item["field_name"])
                action = str(item["action"])
                candidate = next(
                    item_candidate
                    for item_candidate in working_batch.candidates
                    if item_candidate.field_name == field_name
                )
                if action in {
                    "CONFIRM_VALUE",
                    "CONFIRM_MISSING",
                    "CONFIRM_CONFLICT",
                }:
                    deferred_confirmations.append(item)
                    continue
                try:
                    if action == "SET_VALUE":
                        correction_action = (
                            CorrectionAction.USER_INPUT
                            if candidate.validation_status == ValidationStatus.MISSING
                            else CorrectionAction.USER_CORRECTION
                        )
                        working_batch, correction = apply_candidate_correction(
                            working_batch,
                            field_name=field_name,
                            action=correction_action,
                            raw_value=str(item["raw_value"]),
                            normalized_value=item.get("normalized_value"),
                            unit=item.get("unit"),
                            reason_code="PRE_SUBMISSION_HUMAN_REVIEW",
                            reason=(
                                str(item.get("reason") or "用户在正式提交前核对并修改字段。")
                            ),
                            reviewer_id=self.actor_id,
                            reviewed_at=reviewed_at,
                            draft_revision=expected_draft_revision,
                        )
                    elif action == "MARK_MISSING":
                        working_batch, correction = apply_candidate_correction(
                            working_batch,
                            field_name=field_name,
                            action=CorrectionAction.MARK_MISSING,
                            raw_value=None,
                            normalized_value=None,
                            unit=None,
                            reason_code="PRE_SUBMISSION_MARKED_MISSING",
                            reason=str(item.get("reason") or "用户确认模型误提取了该字段。"),
                            reviewer_id=self.actor_id,
                            reviewed_at=reviewed_at,
                            draft_revision=expected_draft_revision,
                        )
                    else:
                        raise ValueError("unsupported quote review action")
                    new_corrections.append(correction)
                except (ValueError, TypeError, KeyError) as exc:
                    action_errors.append(
                        {
                            "code": "FIELD_REVIEW_ACTION_INVALID",
                            "field_names": [field_name],
                            "group_id": self._quote_field_group_id(field_name),
                            "message": "当前字段操作与提取状态不匹配。",
                        }
                    )
            if action_errors:
                raise BackendError(
                    "quote_draft_review_invalid",
                    "Quote draft review contains invalid field actions.",
                    errors=action_errors,
                )

            review_events = []
            for item in deferred_confirmations:
                field_name = str(item["field_name"])
                try:
                    review_events.append(
                        create_review_event(
                            working_batch,
                            field_name=field_name,
                            action=HumanReviewAction(str(item["action"])),
                            reviewer_id=self.actor_id,
                            reviewed_at=reviewed_at,
                            draft_revision=expected_draft_revision,
                            reason_code=(
                                str(item["reason"])
                                if item.get("reason")
                                else None
                            ),
                        )
                    )
                except (ValueError, TypeError) as exc:
                    action_errors.append(
                        {
                            "code": "FIELD_REVIEW_ACTION_INVALID",
                            "field_names": [field_name],
                            "group_id": self._quote_field_group_id(field_name),
                            "message": "确认动作与当前字段状态不匹配。",
                        }
                    )
            if action_errors:
                raise BackendError(
                    "quote_draft_review_invalid",
                    "Quote draft review contains invalid field actions.",
                    errors=action_errors,
                )

            requirement_record = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if requirement_record is None:
                raise ConflictError(
                    "requirement_missing",
                    "The task has no procurement requirement.",
                )
            requirement = ProcurementRequirement.model_validate(requirement_record.payload)
            corrections = tuple(prior_envelope.corrections) + tuple(new_corrections)
            reviewed = review_extraction_batch(
                working_batch,
                self.quote_dictionary,
                CriticalityContext(
                    required_revision=requirement.revision,
                    base_unit=requirement.base_unit,
                    payment_terms_required=(
                        requirement.ranking_preference
                        == "LONGEST_CONFIRMED_PAYMENT_TERM"
                        or requirement.secondary_preference
                        == "LONGEST_CONFIRMED_PAYMENT_TERM"
                    ),
                ),
                input_is_synthetic=draft.is_synthetic,
                reviewed_at=reviewed_at,
                review_events=tuple(review_events),
                corrections=corrections,
            )
            # Invalid provenance/authority still rejects the operation. Ordinary
            # field issues must not roll back the user's review progress.
            if reviewed.review_status == ReviewStatus.REJECTED:
                raise BackendError(
                    "quote_draft_review_failed",
                    "Quote draft did not pass authoritative backend review.",
                    errors=self._quote_review_errors(reviewed),
                    unconfirmed_fields=list(reviewed.unconfirmed_fields),
                    submission_blocking_fields=list(
                        reviewed.submission_blocking_fields
                    ),
                )

            parent_artifact_id = batch_artifact.artifact_id
            if new_corrections:
                batch_payload = working_batch.model_dump(mode="json")
                next_batch_artifact = WorkflowArtifact(
                    artifact_id=new_id("artifact"),
                    task_id=task_id,
                    task_revision=draft.base_task_revision,
                    artifact_type="EXTRACTION_BATCH",
                    schema_version=working_batch.schema_version,
                    parent_artifact_id=parent_artifact_id,
                    quote_id=artifact_quote_id,
                    document_id=draft.proposed_document_id,
                    payload=batch_payload,
                    content_sha256=content_hash(batch_payload),
                )
                session.add(next_batch_artifact)
                parent_artifact_id = next_batch_artifact.artifact_id
                draft.batch_artifact_id = next_batch_artifact.artifact_id
            for event in new_corrections:
                payload = event.model_dump(mode="json")
                artifact = WorkflowArtifact(
                    artifact_id=new_id("artifact"),
                    task_id=task_id,
                    task_revision=draft.base_task_revision,
                    artifact_type="CORRECTION_EVENT",
                    parent_artifact_id=parent_artifact_id,
                    quote_id=artifact_quote_id,
                    document_id=draft.proposed_document_id,
                    payload=payload,
                    content_sha256=content_hash(payload),
                )
                session.add(artifact)
                parent_artifact_id = artifact.artifact_id
            for event in review_events:
                payload = event.model_dump(mode="json")
                artifact = WorkflowArtifact(
                    artifact_id=new_id("artifact"),
                    task_id=task_id,
                    task_revision=draft.base_task_revision,
                    artifact_type="REVIEW_EVENT",
                    parent_artifact_id=parent_artifact_id,
                    quote_id=artifact_quote_id,
                    document_id=draft.proposed_document_id,
                    payload=payload,
                    content_sha256=content_hash(payload),
                )
                session.add(artifact)
                parent_artifact_id = artifact.artifact_id
            review_payload = reviewed.model_dump(mode="json")
            next_review_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"),
                task_id=task_id,
                task_revision=draft.base_task_revision,
                artifact_type="REVIEW_ENVELOPE",
                schema_version=reviewed.schema_version,
                parent_artifact_id=parent_artifact_id,
                quote_id=artifact_quote_id,
                document_id=draft.proposed_document_id,
                payload=review_payload,
                content_sha256=content_hash(review_payload),
            )
            session.add(next_review_artifact)
            draft.review_artifact_id = next_review_artifact.artifact_id
            draft.status = "READY_TO_SUBMIT" if reviewed.submission_ready else "REVIEW_REQUIRED"
            draft.revision += 1
            draft.error_code = None
            draft.error_message = None
            session.flush()
            response = self._quote_draft_response(session, draft)
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def correct_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_draft_revision: int,
        corrections: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_draft_revision": expected_draft_revision,
            "corrections": corrections,
        }
        request_sha = content_hash(request)
        operation = f"correct_quote_draft:{draft_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
            artifact_quote_id = (
                draft.replacement_quote_id or draft.proposed_quote_id
            )
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if task.current_revision != draft.base_task_revision:
                draft.status = "STALE"
                draft.revision += 1
                raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
            if draft.revision != expected_draft_revision:
                raise ConflictError(
                    "quote_draft_revision_conflict",
                    "Quote draft revision has changed.",
                    expected=expected_draft_revision,
                    actual=draft.revision,
                )
            if (
                draft.status not in {"REVIEW_REQUIRED", "READY_TO_SUBMIT"}
                or not draft.batch_artifact_id
                or not draft.review_artifact_id
            ):
                raise ConflictError("quote_draft_not_reviewable", "Quote draft is not awaiting field corrections.")
            targets_list = [str(item.get("field_name", "")) for item in corrections]
            targets = set(targets_list)
            editable_fields = {
                definition.field_name
                for definition in self.quote_dictionary.extractable_fields
            }
            if (
                not corrections
                or len(targets_list) != len(targets)
                or not targets.issubset(editable_fields)
            ):
                raise BackendError(
                    "draft_correction_scope_invalid",
                    "旧版修正接口只允许对不重复的报价业务字段执行 SET_VALUE。",
                    editable_fields=sorted(editable_fields),
                )
            batch_artifact = session.get(WorkflowArtifact, draft.batch_artifact_id)
            if batch_artifact is None:
                raise ConflictError("draft_batch_missing", "Quote draft extraction batch is missing.")
            batch = ExtractionBatch.model_validate(batch_artifact.payload)
            prior_event_artifacts = session.scalars(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.task_revision == draft.base_task_revision,
                    WorkflowArtifact.quote_id == artifact_quote_id,
                    WorkflowArtifact.document_id == draft.proposed_document_id,
                    WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                )
                .order_by(WorkflowArtifact.created_at, WorkflowArtifact.artifact_id)
            ).all()
            prior_events = tuple(
                CorrectionEvent.model_validate(artifact.payload)
                for artifact in prior_event_artifacts
            )
            events: list[CorrectionEvent] = []
            reviewed_at = datetime.now(timezone.utc)
            for item in corrections:
                field_name = str(item["field_name"])
                candidate = next((c for c in batch.candidates if c.field_name == field_name), None)
                definition = self.quote_dictionary.fields.get(field_name)
                allowed_values = (
                    definition.allowed_normalized_values
                    if definition is not None
                    else None
                )
                if (
                    allowed_values is not None
                    and item["normalized_value"] not in allowed_values
                ):
                    raise BackendError(
                        "field_correction_value_invalid",
                        "请选择该字段允许的标准值，不要输入 N/A、NO 等自由文本。",
                        field_name=field_name,
                        allowed_values=list(allowed_values),
                    )
                action = (
                    CorrectionAction.USER_INPUT
                    if candidate is not None and candidate.validation_status == ValidationStatus.MISSING
                    else CorrectionAction.USER_CORRECTION
                )
                try:
                    batch, event = apply_candidate_correction(
                        batch,
                        field_name=field_name,
                        action=action,
                        raw_value=str(item["raw_value"]),
                        normalized_value=item["normalized_value"],
                        unit=item.get("unit"),
                        reason_code="QUOTE_DRAFT_FIELD_CORRECTION",
                        reason=str(item["reason"]),
                        reviewer_id=self.actor_id,
                        reviewed_at=reviewed_at,
                        draft_revision=expected_draft_revision,
                    )
                except (ValueError, TypeError) as exc:
                    raise BackendError(
                        "field_correction_invalid",
                        "Quote draft field correction is invalid.",
                        field_name=field_name,
                    ) from exc
                events.append(event)
            requirement_record = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if requirement_record is None:
                raise ConflictError("requirement_missing", "The task has no procurement requirement.")
            requirement = ProcurementRequirement.model_validate(requirement_record.payload)
            corrected_payload = batch.model_dump(mode="json")
            corrected_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"),
                task_id=task_id,
                task_revision=draft.base_task_revision,
                artifact_type="EXTRACTION_BATCH",
                schema_version=batch.schema_version,
                parent_artifact_id=batch_artifact.artifact_id,
                quote_id=artifact_quote_id,
                document_id=draft.proposed_document_id,
                payload=corrected_payload,
                content_sha256=content_hash(corrected_payload),
            )
            session.add(corrected_artifact)
            for event in events:
                payload = event.model_dump(mode="json")
                session.add(
                    WorkflowArtifact(
                        artifact_id=new_id("artifact"),
                        task_id=task_id,
                        task_revision=draft.base_task_revision,
                        artifact_type="CORRECTION_EVENT",
                        parent_artifact_id=corrected_artifact.artifact_id,
                        quote_id=artifact_quote_id,
                        document_id=draft.proposed_document_id,
                        payload=payload,
                        content_sha256=content_hash(payload),
                    )
                )
            reviewed = review_extraction_batch(
                batch,
                self.quote_dictionary,
                CriticalityContext(
                    required_revision=requirement.revision,
                    base_unit=requirement.base_unit,
                    payment_terms_required=(
                        requirement.ranking_preference
                        == "LONGEST_CONFIRMED_PAYMENT_TERM"
                        or requirement.secondary_preference
                        == "LONGEST_CONFIRMED_PAYMENT_TERM"
                    ),
                ),
                input_is_synthetic=draft.is_synthetic,
                reviewed_at=reviewed_at,
                corrections=prior_events + tuple(events),
            )
            review_payload = reviewed.model_dump(mode="json")
            review_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"),
                task_id=task_id,
                task_revision=draft.base_task_revision,
                artifact_type="REVIEW_ENVELOPE",
                schema_version=reviewed.schema_version,
                parent_artifact_id=corrected_artifact.artifact_id,
                quote_id=artifact_quote_id,
                document_id=draft.proposed_document_id,
                payload=review_payload,
                content_sha256=content_hash(review_payload),
            )
            session.add(review_artifact)
            draft.batch_artifact_id = corrected_artifact.artifact_id
            draft.review_artifact_id = review_artifact.artifact_id
            draft.revision += 1
            draft.status = (
                "READY_TO_SUBMIT"
                if _quote_draft_submission_ready(reviewed)
                else "REVIEW_REQUIRED"
            )
            session.flush()
            response = self._quote_draft_response(session, draft)
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def submit_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_task_revision: int,
        expected_draft_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        from .models import utc_now

        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_task_revision": expected_task_revision,
            "expected_draft_revision": expected_draft_revision,
        }
        request_sha = content_hash(request)
        operation = f"submit_quote_draft:{draft_id}"
        source_path: Path | None = None
        final_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session, operation=operation, key=idempotency_key, request_sha256=request_sha
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
                draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
                if task is None:
                    raise NotFoundError("task_not_found", "Task was not found.")
                self._require_revision(task, expected_task_revision)
                if task.current_revision != draft.base_task_revision:
                    draft.status = "STALE"
                    draft.revision += 1
                    raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
                if draft.revision != expected_draft_revision:
                    raise ConflictError(
                        "quote_draft_revision_conflict",
                        "Quote draft revision has changed.",
                        expected=expected_draft_revision,
                        actual=draft.revision,
                    )
                if (
                    draft.status != "READY_TO_SUBMIT"
                    or not draft.batch_artifact_id
                    or not draft.review_artifact_id
                ):
                    raise ConflictError(
                        "quote_draft_not_ready",
                        "请先完成全部字段人工确认并通过后端复核。",
                    )

                current_dictionary_sha = hashlib.sha256(
                    self.quote_dictionary_path.read_bytes()
                ).hexdigest()
                if draft.dictionary_sha256 != current_dictionary_sha:
                    raise ConflictError(
                        "quote_draft_review_stale",
                        "字段字典已更新，请重新处理并确认报价。",
                        expected=draft.dictionary_sha256,
                        actual=current_dictionary_sha,
                    )
                batch_artifact = session.get(
                    WorkflowArtifact, draft.batch_artifact_id
                )
                review_artifact = session.get(
                    WorkflowArtifact, draft.review_artifact_id
                )
                if batch_artifact is None or review_artifact is None:
                    raise ConflictError(
                        "quote_draft_review_missing",
                        "Quote draft review artifacts are missing.",
                    )
                batch = ExtractionBatch.model_validate(batch_artifact.payload)
                prior_review = ReviewEnvelope.model_validate(review_artifact.payload)
                if (
                    prior_review.schema_version != REVIEW_SCHEMA_VERSION
                    or prior_review.review_policy_version != REVIEW_POLICY_VERSION
                ):
                    raise ConflictError(
                        "quote_draft_review_stale",
                        "审核规则已更新，请重新确认全部字段。",
                        expected_review_policy=REVIEW_POLICY_VERSION,
                        actual_review_policy=prior_review.review_policy_version,
                    )
                requirement_record = session.scalar(
                    select(RequirementRecord)
                    .where(RequirementRecord.task_id == task_id)
                    .order_by(RequirementRecord.requirement_version.desc())
                )
                if requirement_record is None:
                    raise ConflictError(
                        "requirement_missing",
                        "The task has no procurement requirement.",
                    )
                requirement = ProcurementRequirement.model_validate(
                    requirement_record.payload
                )
                authoritative_review = review_extraction_batch(
                    batch,
                    self.quote_dictionary,
                    CriticalityContext(
                        required_revision=requirement.revision,
                        base_unit=requirement.base_unit,
                        payment_terms_required=(
                            requirement.ranking_preference
                            == "LONGEST_CONFIRMED_PAYMENT_TERM"
                            or requirement.secondary_preference
                            == "LONGEST_CONFIRMED_PAYMENT_TERM"
                        ),
                    ),
                    input_is_synthetic=draft.is_synthetic,
                    reviewed_at=datetime.now(timezone.utc),
                    review_events=tuple(prior_review.review_events),
                    corrections=tuple(prior_review.corrections),
                )
                if not _quote_draft_submission_ready(authoritative_review):
                    raise ConflictError(
                        "quote_draft_revalidation_required",
                        "报价在正式提交前复核未通过，请重新确认有变化的字段。",
                        errors=self._quote_review_errors(authoritative_review),
                        unconfirmed_fields=list(
                            authoritative_review.unconfirmed_fields
                        ),
                        submission_blocking_fields=list(
                            authoritative_review.submission_blocking_fields
                        ),
                    )
                reviewed_draft_revision = draft.revision - 1
                revision_bound_fields = {
                    event.field_name
                    for event in prior_review.review_events
                    if event.draft_revision == reviewed_draft_revision
                    and any(
                        candidate.field_name == event.field_name
                        and candidate.field_id == event.candidate_field_id
                        and candidate.field_version
                        == event.candidate_field_version
                        for candidate in batch.candidates
                    )
                }
                revision_bound_fields.update(
                    event.field_name
                    for event in prior_review.corrections
                    if event.draft_revision == reviewed_draft_revision
                    and any(
                        candidate.field_name == event.field_name
                        and candidate.field_id == event.after.field_id
                        and candidate.field_version == event.after.field_version
                        for candidate in batch.candidates
                    )
                )
                unbound_fields = sorted(
                    {
                        candidate.field_name for candidate in batch.candidates
                    }
                    - revision_bound_fields
                )
                if unbound_fields:
                    raise ConflictError(
                        "quote_draft_review_stale",
                        "部分字段没有绑定当前草稿版本，请重新确认全部字段。",
                        errors=[
                            {
                                "code": "DRAFT_REVISION_CONFIRMATION_MISSING",
                                "field_names": unbound_fields,
                                "group_id": None,
                                "message": "字段确认记录不属于当前草稿版本。",
                            }
                        ],
                    )
                if not self._quote_revision_has_material_changes(
                    session, draft=draft, batch=batch
                ):
                    raise ConflictError(
                        "quote_revision_unchanged",
                        "No quote changes were detected; a new version is not required.",
                    )
                self._raise_if_duplicate_quote(
                    session,
                    task_id,
                    draft.sha256,
                    exclude_draft_id=draft.quote_draft_id,
                    allow_quote_id=draft.replacement_quote_id,
                )
                self._supersede_current_graph(session, task)
                replacement_quote = None
                quote_id = draft.proposed_quote_id
                quote_version = 1
                if draft.replacement_quote_id is not None:
                    replacement_quote = session.scalar(
                        select(Quote)
                        .where(
                            Quote.quote_id == draft.replacement_quote_id,
                            Quote.task_id == task_id,
                        )
                        .with_for_update()
                    )
                    if replacement_quote is None:
                        raise NotFoundError(
                            "quote_not_found", "Quote was not found."
                        )
                    if not replacement_quote.active:
                        raise ConflictError(
                            "quote_inactive",
                            "A disabled quote cannot be updated.",
                        )
                    if replacement_quote.supplier_id != draft.supplier_id:
                        raise ConflictError(
                            "quote_supplier_mismatch",
                            "The replacement must keep the original supplier ID.",
                        )
                    quote_id = replacement_quote.quote_id
                    quote_version = replacement_quote.current_version + 1
                extension = ".pdf" if draft.media_type == "application/pdf" else ".csv"
                source_path = Path(draft.storage_path)
                final_path = (
                    self.storage_root
                    / task_id
                    / quote_id
                    / f"v{quote_version}"
                    / draft.proposed_document_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if not source_path.exists():
                    raise ConflictError("draft_file_missing", "Quote draft file is missing.")
                if final_path.exists():
                    raise ConflictError("immutable_storage_conflict", "Quote storage location already exists.")
                source_path.rename(final_path)
                if replacement_quote is None:
                    session.add(
                        Quote(
                            quote_id=quote_id,
                            task_id=task_id,
                            supplier_id=draft.supplier_id,
                            current_version=quote_version,
                        )
                    )
                    session.flush()
                else:
                    replacement_quote.current_version = quote_version
                session.add(
                    Document(
                        document_id=draft.proposed_document_id,
                        task_id=task_id,
                        quote_id=quote_id,
                        quote_version=quote_version,
                        document_version=1,
                        original_filename=draft.original_filename,
                        media_type=draft.media_type,
                        size_bytes=draft.size_bytes,
                        sha256=draft.sha256,
                        storage_path=str(final_path),
                        is_synthetic=draft.is_synthetic,
                    )
                )
                task.current_revision += 1
                self._copy_history_binding(
                    session,
                    task_id=task_id,
                    from_revision=task.current_revision - 1,
                    to_revision=task.current_revision,
                )
                task.status = "DRAFT"
                draft.status = "SUBMITTED"
                draft.revision += 1
                draft.submitted_at = utc_now()
                draft.storage_path = str(final_path)
                session.add(
                    TaskRevision(
                        revision_id=new_id("rev"),
                        task_id=task_id,
                        revision=task.current_revision,
                        change_type=(
                            "QUOTE_REPLACEMENT_SUBMITTED"
                            if replacement_quote is not None
                            else "QUOTE_DRAFT_SUBMITTED"
                        ),
                        actor_id=self.actor_id,
                        request_sha256=request_sha,
                        details={
                            "quote_draft_id": draft.quote_draft_id,
                            "quote_id": quote_id,
                            "quote_version": quote_version,
                            "replaced_quote_version": (
                                quote_version - 1
                                if replacement_quote is not None
                                else None
                            ),
                            "supplier_id": draft.supplier_id,
                            "original_filename": draft.original_filename,
                            "document_sha256": draft.sha256,
                        },
                    )
                )
                response = {
                    "task_id": task_id,
                    "task_revision": task.current_revision,
                    "quote_draft_id": draft.quote_draft_id,
                    "draft_revision": draft.revision,
                    "status": draft.status,
                    "quote_id": quote_id,
                    "quote_version": quote_version,
                    "document_id": draft.proposed_document_id,
                    "document_version": 1,
                    "document_sha256": draft.sha256,
                }
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=201,
                    response=response,
                )
                return response
        except Exception:
            if final_path is not None and final_path.exists() and source_path is not None:
                source_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.rename(source_path)
            raise

    def discard_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_draft_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_draft_revision": expected_draft_revision,
        }
        request_sha = content_hash(request)
        operation = f"discard_quote_draft:{draft_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if draft.revision != expected_draft_revision:
                raise ConflictError(
                    "quote_draft_revision_conflict",
                    "Quote draft revision has changed.",
                    expected=expected_draft_revision,
                    actual=draft.revision,
                )
            if draft.status in {"SUBMITTED", "DISCARDED"}:
                raise ConflictError("quote_draft_terminal", "Quote draft is already closed.")
            draft.status = "DISCARDED"
            draft.revision += 1
            for job in session.scalars(
                select(Job).where(
                    Job.quote_draft_id == draft_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )
            ):
                job.status = "SUPERSEDED"
            session.flush()
            response = self._quote_draft_response(session, draft)
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def job_type(self, job_id: str) -> str:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            return job.job_type

    def upload_requirement_draft_stream(
        self,
        *,
        original_filename: str,
        media_type: str,
        stream: BinaryIO,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> dict[str, Any]:
        from .intake import safe_filename_extension

        try:
            extension = safe_filename_extension(original_filename, media_type)
        except ValueError as exc:
            raise BackendError(
                "unsupported_requirement_media_type",
                "Only PDF, TXT, and Markdown requirement files are supported.",
            ) from exc
        staging = self.storage_root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / f"{new_id('requirement_upload')}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with staged.open("xb") as handle:
                while chunk := stream.read(64 * 1024):
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise BackendError("upload_stream_invalid", "Requirement upload stream must produce bytes.")
                    size += len(chunk)
                    if size > max_bytes:
                        raise BackendError("file_too_large", "Requirement file exceeds the 10 MiB limit.", max_file_size_bytes=max_bytes)
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise BackendError("empty_file", "Requirement file is empty.")
        except Exception:
            if staged.exists():
                staged.unlink()
            raise
        file_sha = digest.hexdigest()
        request = {
            "filename": Path(original_filename).name,
            "media_type": media_type,
            "sha256": file_sha,
            "provider": provider,
            "model_id": model_id,
            "environment": environment,
            "prompt_version": prompt_version,
        }
        request_sha = content_hash(request)
        final_path = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session, operation="upload_requirement_draft", key=idempotency_key, request_sha256=request_sha
                )
                if repeated is not None:
                    return repeated
                draft_id, job_id = new_id("reqdraft"), new_id("job")
                final_path = self.storage_root / ".requirements" / draft_id / f"source{extension}"
                final_path.parent.mkdir(parents=True, exist_ok=True)
                staged.rename(final_path)
                draft = RequirementDraft(
                    requirement_draft_id=draft_id,
                    actor_id=self.actor_id,
                    revision=1,
                    status="PROCESSING",
                    original_filename=Path(original_filename).name,
                    media_type=media_type,
                    size_bytes=size,
                    sha256=file_sha,
                    storage_path=str(final_path),
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                )
                session.add(draft)
                # Persist the parent row before scheduling the job that references it.
                # No ORM relationship links these pending objects, so PostgreSQL may
                # otherwise emit the jobs INSERT before the requirement draft INSERT.
                session.flush([draft])
                session.add(Job(
                    job_id=job_id,
                    task_id=None,
                    graph_run_id=None,
                    requirement_draft_id=draft_id,
                    job_type="REQUIREMENT_DRAFT_PARSE",
                    status="PENDING",
                    task_revision=1,
                ))
                session.flush()
                response = self._requirement_draft_response(session, draft)
                self._save_idempotent(
                    session, operation="upload_requirement_draft", key=idempotency_key,
                    request_sha256=request_sha, response_status=202, response=response,
                )
                return response
        except Exception:
            if final_path is not None and final_path.exists():
                final_path.unlink()
            raise
        finally:
            if staged.exists():
                staged.unlink()

    def get_requirement_draft(self, draft_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            draft = session.get(RequirementDraft, draft_id)
            if draft is None or draft.actor_id != self.actor_id:
                raise NotFoundError("requirement_draft_not_found", "Requirement draft was not found.")
            return self._requirement_draft_response(session, draft)

    def discard_requirement_draft(
        self, draft_id: str, *, expected_revision: int, idempotency_key: str
    ) -> dict[str, Any]:
        request = {"draft_id": draft_id, "expected_revision": expected_revision}
        request_sha = content_hash(request)
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=f"discard_requirement_draft:{draft_id}", key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            draft = session.scalar(select(RequirementDraft).where(
                RequirementDraft.requirement_draft_id == draft_id
            ).with_for_update())
            if draft is None or draft.actor_id != self.actor_id:
                raise NotFoundError("requirement_draft_not_found", "Requirement draft was not found.")
            if draft.revision != expected_revision:
                raise ConflictError("requirement_draft_revision_conflict", "Requirement draft revision has changed.")
            if draft.status == "USED":
                raise ConflictError("requirement_draft_used", "A used requirement draft cannot be discarded.")
            draft.status = "DISCARDED"
            draft.revision += 1
            for job in session.scalars(select(Job).where(
                Job.requirement_draft_id == draft_id, Job.status.in_(("PENDING", "RUNNING"))
            )):
                job.status = "SUPERSEDED"
            response = self._requirement_draft_response(session, draft)
            self._save_idempotent(
                session, operation=f"discard_requirement_draft:{draft_id}", key=idempotency_key,
                request_sha256=request_sha, response_status=200, response=response,
            )
            return response

    def requirement_draft_job_context(self, job_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or job.job_type != "REQUIREMENT_DRAFT_PARSE" or not job.requirement_draft_id:
                raise NotFoundError("job_not_found", "Requirement draft job was not found.")
            draft = session.scalar(select(RequirementDraft).where(
                RequirementDraft.requirement_draft_id == job.requirement_draft_id
            ).with_for_update())
            if draft is None or draft.actor_id != self.actor_id:
                raise NotFoundError("requirement_draft_not_found", "Requirement draft was not found.")
            if job.status != "PENDING" or draft.status in {"DISCARDED", "USED"}:
                raise ConflictError("job_not_pending", "Requirement draft job is not pending.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = datetime.now(timezone.utc)
            job.error_code = None
            job.error_message = None
            draft.status = "PROCESSING"
            return {
                "job_id": job_id,
                "draft_id": draft.requirement_draft_id,
                "storage_path": draft.storage_path,
                "media_type": draft.media_type,
            }

    def complete_requirement_draft_job(
        self, job_id: str, *, parsed: dict[str, Any], candidates: dict[str, Any], calls_used: int
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.requirement_draft_id:
                raise NotFoundError("job_not_found", "Requirement draft job was not found.")
            draft = session.scalar(select(RequirementDraft).where(
                RequirementDraft.requirement_draft_id == job.requirement_draft_id
            ).with_for_update())
            if draft is None:
                raise NotFoundError("requirement_draft_not_found", "Requirement draft was not found.")
            if job.status != "RUNNING" or draft.status == "DISCARDED":
                job.status = "SUPERSEDED"
                raise ConflictError("job_superseded", "Requirement draft job was superseded.")
            draft.parsed_payload = parsed
            draft.candidates = candidates
            draft.calls_used = calls_used
            draft.status = "READY"
            draft.error_code = draft.error_message = None
            draft.revision += 1
            job.status = "SUCCEEDED"
            job.finished_at = datetime.now(timezone.utc)
            return self._requirement_draft_response(session, draft)

    def fail_requirement_draft_job(self, job_id: str, *, code: str, message: str, calls_used: int = 0) -> None:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.requirement_draft_id:
                return
            draft = session.get(RequirementDraft, job.requirement_draft_id)
            job.status = "FAILED"
            job.error_code, job.error_message = code, message[:1000]
            job.finished_at = datetime.now(timezone.utc)
            if draft is not None and draft.status != "DISCARDED":
                draft.status = "FAILED"
                draft.error_code, draft.error_message = code, message[:1000]
                draft.calls_used = calls_used

    def list_tasks(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        sort: str = "updated_desc",
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            tasks = session.scalars(
                select(Task)
                .where(Task.owner_id == self.actor_id)
            ).all()
            items = []
            for task in tasks:
                requirement = session.scalar(
                    select(RequirementRecord)
                    .where(RequirementRecord.task_id == task.task_id)
                    .order_by(RequirementRecord.requirement_version.desc())
                )
                payload = dict(requirement.payload) if requirement else {}
                items.append(
                    {
                        "task_id": task.task_id,
                        "scenario_id": task.scenario_id,
                        "task_revision": task.current_revision,
                        "status": task.status,
                        "current_result_id": task.current_result_id,
                        "manufacturer": payload.get("manufacturer"),
                        "manufacturer_part_number": payload.get(
                            "manufacturer_part_number"
                        ),
                        "planned_order_date": payload.get("planned_order_date"),
                        "created_at": task.created_at.isoformat(),
                        "updated_at": task.updated_at.isoformat(),
                    }
                )
            term = (query or "").strip().casefold()
            if term:
                items = [item for item in items if term in " ".join(str(item.get(key) or "") for key in (
                    "task_id", "scenario_id", "manufacturer", "manufacturer_part_number"
                )).casefold()]
            if status:
                items = [item for item in items if item["status"] == status]
            status_counts: dict[str, int] = {}
            for task in tasks:
                status_counts[task.status] = status_counts.get(task.status, 0) + 1
            if sort == "planned_asc":
                items.sort(key=lambda item: (item["planned_order_date"] is None, item["planned_order_date"] or "", item["task_id"]))
            elif sort == "planned_desc":
                items.sort(key=lambda item: (item["planned_order_date"] is not None, item["planned_order_date"] or "", item["task_id"]), reverse=True)
            elif sort == "created_desc":
                items.sort(key=lambda item: (item["created_at"], item["task_id"]), reverse=True)
            else:
                items.sort(key=lambda item: (item["updated_at"], item["task_id"]), reverse=True)
            total = len(items)
            return {"items": items[offset:offset + limit], "total": total, "limit": limit, "offset": offset, "status_counts": status_counts}

    def update_requirement(
        self,
        task_id: str,
        requirement: ProcurementRequirement,
        *,
        expected_task_revision: int,
        idempotency_key: str,
        update_policy_binding: bool = False,
        policy_set_version: str | None = None,
        policy_index_version: str | None = None,
        policy_category: str | None = None,
        policy_region: str | None = None,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        payload = requirement.model_dump(mode="json")
        requested_binding = (
            policy_set_version,
            policy_index_version,
            policy_category,
            policy_region,
        )
        if update_policy_binding and any(value is not None for value in requested_binding) and not all(
            isinstance(value, str) and value.strip() for value in requested_binding
        ):
            raise BackendError(
                "policy_binding_incomplete",
                "Policy set, index, category, and region must be supplied together.",
            )
        normalized_binding = tuple(
            value.strip() if isinstance(value, str) else None
            for value in requested_binding
        )
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "requirement": payload,
            "update_policy_binding": update_policy_binding,
            "policy_binding": normalized_binding if update_policy_binding else None,
        }
        request_sha = content_hash(request)
        operation = f"update_requirement:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            current = session.scalar(select(RequirementRecord).where(
                RequirementRecord.task_id == task_id
            ).order_by(RequirementRecord.requirement_version.desc()))
            old_payload = dict(current.payload) if current else {}
            old_binding = (
                task.policy_set_version,
                task.policy_index_version,
                task.policy_category,
                task.policy_region,
            )
            requirement_changed = old_payload != payload
            binding_changed = update_policy_binding and old_binding != normalized_binding
            if not requirement_changed and not binding_changed:
                raise BackendError(
                    "requirement_unchanged",
                    "Task configuration contains no changes.",
                )
            self._supersede_current_graph(session, task)
            for draft in session.scalars(select(QuoteDraft).where(
                QuoteDraft.task_id == task_id,
                QuoteDraft.status.in_(("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")),
            )):
                draft.status = "STALE"
                draft.revision += 1
                for draft_job in session.scalars(select(Job).where(
                    Job.quote_draft_id == draft.quote_draft_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )):
                    draft_job.status = "SUPERSEDED"
            next_revision = task.current_revision + 1
            changed_fields = sorted(name for name in set(old_payload) | set(payload) if old_payload.get(name) != payload.get(name))
            change_type = (
                "TASK_CONFIGURATION_UPDATED"
                if requirement_changed and binding_changed
                else "POLICY_BINDING_UPDATED"
                if binding_changed
                else "REQUIREMENT_UPDATED"
            )
            session.add(TaskRevision(
                revision_id=new_id("rev"), task_id=task_id, revision=next_revision,
                change_type=change_type, actor_id=self.actor_id,
                request_sha256=request_sha, details={
                    "changed_fields": changed_fields,
                    "policy_binding_changed": binding_changed,
                    **({
                        "previous_policy_binding": old_binding,
                        "policy_binding": normalized_binding,
                    } if binding_changed else {}),
                },
            ))
            session.add(RequirementRecord(
                requirement_id=new_id("req"), task_id=task_id, task_revision=next_revision,
                requirement_version=(current.requirement_version + 1 if current else 1),
                payload=payload, content_sha256=content_hash(payload), source_artifact_id=None,
            ))
            task.current_revision = next_revision
            if update_policy_binding:
                (
                    task.policy_set_version,
                    task.policy_index_version,
                    task.policy_category,
                    task.policy_region,
                ) = normalized_binding
            previous_binding = self._binding_at_revision(session, task_id, next_revision - 1)
            history_binding = self._new_history_binding(
                task_id=task_id,
                task_revision=next_revision,
                requirement=requirement,
                dataset_version=(previous_binding.dataset_version if previous_binding else None),
            )
            if history_binding.binding_status != "AVAILABLE" and _requires_history(requirement):
                raise BackendError(
                    "ranking_criterion_not_applicable",
                    "Historical ranking criteria are not applicable to this requirement scope.",
                )
            session.add(history_binding)
            active_documents = session.scalars(select(Document).join(
                Quote, Quote.quote_id == Document.quote_id
            ).where(Document.task_id == task_id, Quote.active.is_(True), Document.quote_version == Quote.current_version)).all()
            graph_run_id = job_id = None
            if active_documents:
                graph_run_id, job_id = new_id("graph"), new_id("job")
                session.add(GraphRun(
                    graph_run_id=graph_run_id, task_id=task_id, thread_id=graph_run_id,
                    started_revision=next_revision, effective_revision=next_revision, status="PENDING",
                    history_binding_id=(history_binding.history_binding_id if history_binding else None),
                    provider=provider, model_id=model_id, environment=environment, prompt_version=prompt_version,
                ))
                session.flush()
                session.add(Job(
                    job_id=job_id, task_id=task_id, graph_run_id=graph_run_id,
                    job_type="START", status="PENDING", task_revision=next_revision,
                    history_binding_id=(history_binding.history_binding_id if history_binding else None),
                ))
                task.current_graph_run_id = graph_run_id
                task.status = "QUEUED"
            else:
                task.status = "DRAFT"
            if graph_run_id:
                self._seed_submitted_extractions(
                    session, task_id=task_id, graph_run_id=graph_run_id,
                    provider=provider, model_id=model_id, environment=environment,
                    prompt_version=prompt_version,
                )
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "status": task.status,
                "changed_fields": changed_fields,
                "policy_binding_changed": binding_changed,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_status": "PENDING" if job_id else None,
            }
            self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha, response_status=202, response=response)
            return response

    def abandon_task(
        self, task_id: str, *, expected_task_revision: int, reason: str, idempotency_key: str
    ) -> dict[str, Any]:
        request = {"task_id": task_id, "expected_task_revision": expected_task_revision, "reason": reason.strip()}
        request_sha = content_hash(request)
        operation = f"abandon_task:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Task has already been abandoned.")
            if task.current_revision != expected_task_revision:
                raise ConflictError("task_revision_conflict", "Task revision has changed.", expected=expected_task_revision, actual=task.current_revision)
            self._supersede_current_graph(session, task)
            for job in session.scalars(select(Job).where(
                Job.task_id == task_id, Job.status.in_(("PENDING", "RUNNING", "WAITING_INPUT"))
            )):
                job.status = "SUPERSEDED"
            for report in session.scalars(select(SummaryReport).where(
                SummaryReport.task_id == task_id,
                SummaryReport.status.in_(("PENDING", "RUNNING")),
            )):
                report.status = "STALE"
            for draft in session.scalars(select(QuoteDraft).where(
                QuoteDraft.task_id == task_id,
                QuoteDraft.status.in_(("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")),
            )):
                draft.status = "STALE"
                draft.revision += 1
            next_revision = task.current_revision + 1
            session.add(TaskRevision(
                revision_id=new_id("rev"), task_id=task_id, revision=next_revision,
                change_type="TASK_ABANDONED", actor_id=self.actor_id,
                request_sha256=request_sha, details={"reason": reason.strip()},
            ))
            task.current_revision = next_revision
            self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=next_revision - 1,
                to_revision=next_revision,
            )
            task.status = "ABANDONED"
            task.abandoned_at = datetime.now(timezone.utc)
            response = {"task_id": task_id, "task_revision": next_revision, "status": "ABANDONED", "reason": reason.strip()}
            self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha, response_status=200, response=response)
            return response

    def task_audit(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            revisions = session.scalars(select(TaskRevision).where(
                TaskRevision.task_id == task_id
            ).order_by(TaskRevision.revision)).all()
            accesses = session.scalars(select(DocumentAccessEvent).where(
                DocumentAccessEvent.task_id == task_id
            ).order_by(DocumentAccessEvent.created_at.desc())).all()
            return {
                "task_id": task_id,
                "revisions": [{
                    "revision": item.revision,
                    "change_type": item.change_type,
                    "actor_id": item.actor_id,
                    "details": item.details or {},
                    "created_at": self._aware_datetime(item.created_at).isoformat(),
                } for item in revisions],
                "document_accesses": [{
                    "access_event_id": item.access_event_id,
                    "document_id": item.document_id,
                    "action": item.action,
                    "actor_id": item.actor_id,
                    "request_id": item.request_id,
                    "created_at": self._aware_datetime(item.created_at).isoformat(),
                } for item in accesses],
            }

    def document_content(
        self, task_id: str, document_id: str, *, action: str, request_id: str | None
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            task = session.get(Task, task_id)
            document = session.get(Document, document_id)
            if task is None or task.owner_id != self.actor_id or document is None or document.task_id != task_id:
                raise NotFoundError("document_not_found", "Document was not found.")
            root = self.storage_root.resolve()
            path = Path(document.storage_path).resolve()
            if root not in path.parents or not path.is_file():
                raise NotFoundError("document_content_not_found", "Document content was not found.")
            session.add(DocumentAccessEvent(
                access_event_id=new_id("access"), task_id=task_id, document_id=document_id,
                actor_id=self.actor_id, action=action, request_id=request_id,
            ))
            return {
                "path": path,
                "filename": document.original_filename,
                "media_type": document.media_type,
                "sha256": document.sha256,
            }

    def create_summary(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        result_id: str,
        idempotency_key: str,
        provider: str | None,
        model_id: str | None,
        environment: str | None,
        prompt_version: str,
    ) -> dict[str, Any]:
        request = {"task_id": task_id, "expected_task_revision": expected_task_revision, "result_id": result_id, "prompt_version": prompt_version, "model_id": model_id}
        request_sha = content_hash(request)
        operation = f"create_summary:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            if task.current_result_id != result_id:
                raise ConflictError("summary_result_stale", "Summary requires the current result.")
            result = session.get(WorkflowArtifact, result_id)
            if result is None or result.task_id != task_id or result.artifact_type != "COMPARISON_RESULT":
                raise NotFoundError("result_not_found", "Result was not found.")
            facts = self._summary_facts(session, task, result)
            input_sha = content_hash(facts)
            existing = session.scalar(select(SummaryReport).where(
                SummaryReport.task_id == task_id,
                SummaryReport.input_sha256 == input_sha,
                SummaryReport.prompt_version == prompt_version,
                SummaryReport.model_id == model_id,
            ))
            if existing is not None:
                response = self._summary_response(session, task, existing)
                self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha, response_status=200, response=response)
                return response
            for previous in session.scalars(select(SummaryReport).where(
                SummaryReport.task_id == task_id,
                SummaryReport.task_revision == task.current_revision,
                SummaryReport.result_id == result_id,
                SummaryReport.status != "STALE",
            )):
                previous.status = "STALE"
                for previous_job in session.scalars(select(Job).where(
                    Job.summary_id == previous.summary_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )):
                    previous_job.status = "SUPERSEDED"
            summary_id, job_id = new_id("summary"), new_id("job")
            report = SummaryReport(
                summary_id=summary_id, task_id=task_id, task_revision=task.current_revision,
                result_id=result_id, status="PENDING", input_sha256=input_sha, facts=facts,
                provider=provider, model_id=model_id, environment=environment,
                prompt_version=prompt_version, max_calls=4,
            )
            session.add(report)
            session.flush()
            session.add(Job(
                job_id=job_id, task_id=task_id, graph_run_id=None, summary_id=summary_id,
                job_type="SUMMARY_GENERATION", status="PENDING", task_revision=task.current_revision,
            ))
            session.flush()
            response = self._summary_response(session, task, report)
            self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha, response_status=202, response=response)
            return response

    def list_summaries(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            reports = session.scalars(select(SummaryReport).where(
                SummaryReport.task_id == task_id
            ).order_by(SummaryReport.created_at.desc())).all()
            return {"task_id": task_id, "task_revision": task.current_revision, "items": [self._summary_response(session, task, item) for item in reports]}

    def get_summary(self, task_id: str, summary_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            report = session.get(SummaryReport, summary_id)
            if task is None or task.owner_id != self.actor_id or report is None or report.task_id != task_id:
                raise NotFoundError("summary_not_found", "Summary was not found.")
            return self._summary_response(session, task, report)

    def retry_summary(
        self, task_id: str, summary_id: str, *, expected_task_revision: int, idempotency_key: str
    ) -> dict[str, Any]:
        request = {"summary_id": summary_id, "expected_task_revision": expected_task_revision}
        request_sha = content_hash(request)
        operation = f"retry_summary:{summary_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha)
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            report = session.scalar(select(SummaryReport).where(SummaryReport.summary_id == summary_id).with_for_update())
            if task is None or task.owner_id != self.actor_id or report is None or report.task_id != task_id:
                raise NotFoundError("summary_not_found", "Summary was not found.")
            self._require_revision(task, expected_task_revision)
            if report.task_revision != task.current_revision or report.result_id != task.current_result_id:
                raise ConflictError("summary_stale", "A stale summary cannot be retried.")
            if report.status != "FAILED" or report.calls_used >= report.max_calls:
                raise ConflictError("summary_not_retryable", "Summary is not retryable.")
            job_id = new_id("job")
            report.status = "PENDING"
            report.error_code = report.error_message = None
            session.add(Job(
                job_id=job_id, task_id=task_id, graph_run_id=None, summary_id=summary_id,
                job_type="SUMMARY_GENERATION", status="PENDING", task_revision=task.current_revision,
            ))
            session.flush()
            response = self._summary_response(session, task, report)
            self._save_idempotent(session, operation=operation, key=idempotency_key, request_sha256=request_sha, response_status=202, response=response)
            return response

    def summary_job_context(self, job_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or job.job_type != "SUMMARY_GENERATION" or not job.summary_id:
                raise NotFoundError("job_not_found", "Summary job was not found.")
            report = session.scalar(select(SummaryReport).where(SummaryReport.summary_id == job.summary_id).with_for_update())
            task = session.get(Task, report.task_id) if report else None
            if report is None or task is None or task.owner_id != self.actor_id:
                raise NotFoundError("summary_not_found", "Summary was not found.")
            if job.status != "PENDING" or report.task_revision != task.current_revision or report.result_id != task.current_result_id:
                job.status = "SUPERSEDED"
                report.status = "STALE"
                raise ConflictError("summary_stale", "Summary inputs are stale.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = datetime.now(timezone.utc)
            report.status = "RUNNING"
            return {"summary_id": report.summary_id, "facts": dict(report.facts), "calls_used": report.calls_used, "max_calls": report.max_calls}

    def complete_summary_job(self, job_id: str, *, narrative: dict[str, Any], calls_used: int) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            report = session.get(SummaryReport, job.summary_id) if job and job.summary_id else None
            task = session.get(Task, report.task_id) if report else None
            if job is None or report is None or task is None:
                raise NotFoundError("summary_not_found", "Summary was not found.")
            if task.current_revision != report.task_revision or task.current_result_id != report.result_id:
                job.status, report.status = "SUPERSEDED", "STALE"
                raise ConflictError("summary_stale", "Summary inputs changed during generation.")
            report.narrative = narrative
            report.calls_used = calls_used
            report.status = "SUCCEEDED"
            report.error_code = report.error_message = None
            job.status = "SUCCEEDED"
            job.finished_at = datetime.now(timezone.utc)
            return self._summary_response(session, task, report)

    def fail_summary_job(self, job_id: str, *, code: str, message: str, calls_used: int) -> None:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            report = session.get(SummaryReport, job.summary_id) if job and job.summary_id else None
            if job is None or report is None:
                return
            job.status = "FAILED"
            job.error_code, job.error_message = code, message[:1000]
            job.finished_at = datetime.now(timezone.utc)
            report.status = "FAILED"
            report.error_code, report.error_message = code, message[:1000]
            report.calls_used = calls_used

    def list_issues(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            issues = session.scalars(
                select(Issue)
                .where(Issue.task_id == task_id)
                .order_by(Issue.created_at, Issue.issue_id)
            ).all()
            return [
                {
                    **self._issue_response(issue),
                    "answer": issue.answer_payload,
                    "resolved_revision": issue.resolved_revision,
                    "answered_by": issue.answered_by,
                    "answered_at": (
                        issue.answered_at.isoformat() if issue.answered_at else None
                    ),
                }
                for issue in issues
            ]

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            issue = session.get(Issue, issue_id)
            if issue is None:
                raise NotFoundError("issue_not_found", "Issue was not found.")
            response = self._issue_response(issue)
            response.update(
                answer=issue.answer_payload,
                resolved_revision=issue.resolved_revision,
                answered_by=issue.answered_by,
                answered_at=(
                    self._aware_datetime(issue.answered_at).isoformat()
                    if issue.answered_at
                    else None
                ),
            )
            return response

    def current_issue(self, graph_run_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            graph = session.get(GraphRun, graph_run_id)
            if graph is None or graph.current_interrupt_issue_id is None:
                return None
            issue = session.get(Issue, graph.current_interrupt_issue_id)
            return self._issue_response(issue) if issue is not None else None

    def workflow_context(self, graph_run_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            graph = session.get(GraphRun, graph_run_id)
            if graph is None:
                raise NotFoundError("graph_run_not_found", "Graph run was not found.")
            task = session.get(Task, graph.task_id)
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task.task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            requirement_contract = (
                ProcurementRequirement.model_validate(requirement.payload)
                if requirement is not None else None
            )
            decision_preferences, decision_profile = (
                self._decision_preferences(
                    session,
                    task,
                    requirement_contract,
                    max_revision=graph.effective_revision,
                )
                if requirement_contract is not None
                else (DecisionPreferences(), None)
            )
            documents = session.execute(
                select(Document, Quote)
                .join(Quote, Quote.quote_id == Document.quote_id)
                .where(Document.task_id == task.task_id, Quote.active.is_(True),
                       Document.quote_version == Quote.current_version)
                .order_by(Quote.quote_id)
            ).all()
            history_binding = (
                session.get(TaskHistoryBinding, graph.history_binding_id)
                if graph.history_binding_id
                else self._binding_at_revision(
                    session, task.task_id, graph.effective_revision
                )
            )
            history_dataset_payload = None
            if history_binding is not None:
                if history_binding.task_id != task.task_id:
                    raise ConflictError(
                        "history_binding_task_mismatch",
                        "Graph history binding belongs to another task.",
                    )
                # Exact hashes make a mutable or replaced release fail closed.
                try:
                    dataset, _manifest, _manifest_hash = SupplierHistoryLoader(
                        self.supplier_history_root
                    ).load(
                        dataset_version=history_binding.dataset_version,
                        expected_content_sha256=history_binding.content_sha256,
                        expected_manifest_sha256=history_binding.manifest_sha256,
                    )
                except SupplierHistoryLoadError as exc:
                    raise BackendError(
                        exc.code,
                        "Bound supplier history dataset failed validation.",
                        history_binding_id=history_binding.history_binding_id,
                    ) from exc
                history_dataset_payload = dataset.model_dump(mode="json")
            payment_supplements = {
                issue.quote_id: {
                    "payment_start_event": issue.answer_payload.get("payment_start_event"),
                    "evidence_ref": f"ISSUE:{issue.issue_id}",
                    "note": issue.answer_payload.get("note"),
                    "source_type": issue.answer_payload.get("source_type"),
                    "source_refs": issue.answer_payload.get("source_refs", []),
                }
                for issue in session.scalars(
                    select(Issue).where(
                        Issue.graph_run_id == graph_run_id,
                        Issue.issue_type == "PAYMENT_INFORMATION",
                        Issue.status == "RESOLVED",
                    )
                ).all()
                if issue.quote_id and issue.answer_payload
            }
            return {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "task_revision": task.current_revision,
                "started_revision": graph.started_revision,
                "effective_revision": graph.effective_revision,
                "graph_run_id": graph.graph_run_id,
                "thread_id": graph.thread_id,
                "policy_set_version": task.policy_set_version,
                "policy_index_version": task.policy_index_version,
                "policy_category": task.policy_category,
                "policy_region": task.policy_region,
                "requirement": dict(requirement.payload) if requirement else None,
                "decision_profile": self._decision_profile_response(
                    decision_preferences, decision_profile
                ),
                "supplier_history_binding": self._history_binding_response(
                    history_binding
                ),
                "supplier_history_dataset": history_dataset_payload,
                "payment_supplements": payment_supplements,
                "documents": [
                    {
                        "document_id": document.document_id,
                        "document_version": document.document_version,
                        "quote_id": document.quote_id,
                        "quote_version": document.quote_version,
                        "supplier_id": quote.supplier_id,
                        "media_type": document.media_type,
                        "storage_path": document.storage_path,
                        "document_sha256": document.sha256,
                        "is_synthetic": document.is_synthetic,
                    }
                    for document, quote in documents
                ],
            }

    def artifact_payload(self, artifact_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            artifact = session.get(WorkflowArtifact, artifact_id)
            if artifact is None:
                raise NotFoundError("artifact_not_found", "Workflow artifact was not found.")
            return dict(artifact.payload)

    def link_document_artifacts(
        self,
        document_execution_id: str,
        *,
        parsed_artifact_id: str | None = None,
        batch_artifact_id: str | None = None,
        review_artifact_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            execution = session.get(DocumentExecution, document_execution_id)
            if execution is None:
                raise NotFoundError(
                    "document_execution_not_found", "Document execution was not found."
                )
            if parsed_artifact_id is not None:
                execution.parsed_artifact_id = parsed_artifact_id
            if batch_artifact_id is not None:
                execution.batch_artifact_id = batch_artifact_id
            if review_artifact_id is not None:
                execution.review_artifact_id = review_artifact_id
            if status is not None:
                execution.status = status
            return self._document_execution_response(execution)

    def claim_job(self, job_id: str) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if task is None or graph is None:
                raise NotFoundError("job_context_missing", "Job context was not found.")
            if job.status != "PENDING":
                raise ConflictError("job_not_pending", "Job is not pending.")
            if task.current_graph_run_id != graph.graph_run_id:
                job.status = "SUPERSEDED"
                graph.status = "SUPERSEDED"
                raise ConflictError("graph_run_superseded", "Graph run was superseded.")
            if job.attempts >= 3:
                raise BackendError("job_attempt_budget_exceeded", "Job attempt budget was exceeded.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = utc_now()
            graph.status = "RUNNING"
            task.status = "RUNNING"
            return self._job_response(job)

    def next_pending_job_id(self) -> str | None:
        """Return the oldest pending job for the single background worker."""

        with self.session_factory.begin() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(
                seconds=self.conversation_job_stale_seconds
            )
            stale_jobs = session.scalars(
                select(Job)
                .where(
                    Job.job_type == "DECISION_CONVERSATION",
                    Job.status == "RUNNING",
                    Job.started_at.is_not(None),
                    Job.started_at <= cutoff,
                )
                .order_by(Job.started_at, Job.job_id)
                .with_for_update()
            ).all()
            for job in stale_jobs:
                conversation = session.get(DecisionConversation, job.conversation_id)
                task = session.get(Task, job.task_id)
                if (
                    conversation is None
                    or task is None
                    or conversation.status != "ACTIVE"
                    or conversation.base_task_revision != task.current_revision
                    or conversation.base_result_id != task.current_result_id
                ):
                    job.status = "SUPERSEDED"
                    if conversation is not None:
                        conversation.status = "STALE"
                    continue
                if job.attempts < 3:
                    job.status = "PENDING"
                    job.started_at = None
                    job.error_code = "conversation_worker_interrupted"
                    job.error_message = (
                        "The previous conversation worker stopped before completion."
                    )
                    self._append_conversation_event(
                        session,
                        conversation.conversation_id,
                        "assistant.retrying",
                        {
                            "job_id": job.job_id,
                            "reply_to_message_id": job.conversation_message_id,
                            "attempts": job.attempts,
                        },
                    )
                else:
                    latest = session.scalar(
                        select(DecisionMessage)
                        .where(
                            DecisionMessage.conversation_id
                            == conversation.conversation_id
                        )
                        .order_by(DecisionMessage.sequence.desc())
                    )
                    failed = DecisionMessage(
                        message_id=new_id("dmessage"),
                        conversation_id=conversation.conversation_id,
                        task_id=conversation.task_id,
                        sequence=(latest.sequence + 1 if latest else 1),
                        role="ASSISTANT",
                        status="FAILED",
                        reply_to_message_id=job.conversation_message_id,
                        attempts=job.attempts,
                        error_code="conversation_retry_exhausted",
                        error_message=(
                            "The conversation worker stopped repeatedly before completion."
                        ),
                    )
                    session.add(failed)
                    session.flush()
                    job.status = "FAILED"
                    job.error_code = failed.error_code
                    job.error_message = failed.error_message
                    job.finished_at = datetime.now(timezone.utc)
                    self._append_conversation_event(
                        session,
                        conversation.conversation_id,
                        "assistant.failed",
                        {"message": self._decision_message_response(failed)},
                    )
            return session.scalar(
                select(Job.job_id)
                .where(Job.status == "PENDING")
                .order_by(Job.created_at.asc(), Job.job_id.asc())
                .limit(1)
            )

    def finish_job(self, job_id: str, *, waiting_input: bool) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if graph is None or task is None:
                raise NotFoundError("job_context_missing", "Job context was not found.")
            job.status = "WAITING_INPUT" if waiting_input else "SUCCEEDED"
            job.finished_at = utc_now()
            is_current = (
                task.current_graph_run_id == graph.graph_run_id
                and task.current_revision == graph.effective_revision
            )
            if not is_current:
                graph.status = "SUPERSEDED"
                job.status = "SUPERSEDED"
            elif waiting_input:
                graph.status = "INTERRUPTED"
                task.status = "NEEDS_INPUT"
            else:
                graph.status = "SUCCEEDED"
                task.status = "COMPLETED"
            return self._job_response(job)

    def fail_job(self, job_id: str, *, code: str, message: str) -> None:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                return
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            job.finished_at = utc_now()
            if (
                graph is not None
                and task is not None
                and task.current_graph_run_id == graph.graph_run_id
            ):
                job.status = "FAILED"
                job.error_code = code
                job.error_message = message[:1000]
                graph.status = "FAILED"
                task.status = "FAILED"
            else:
                job.status = "SUPERSEDED"
                if graph is not None:
                    graph.status = "SUPERSEDED"

    def publish_result(
        self,
        *,
        task_id: str,
        graph_run_id: str,
        task_revision: int,
        snapshot_id: str,
        result_id: str,
    ) -> None:
        with self.session_factory.begin() as session:
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == graph_run_id)
                .with_for_update()
            )
            if task is None or graph is None:
                raise NotFoundError("publish_context_missing", "Publish context was not found.")
            if task.current_graph_run_id != graph_run_id or task.current_revision != task_revision:
                raise ConflictError(
                    "stale_result_publish",
                    "A stale graph run cannot replace the current result.",
                    current_revision=task.current_revision,
                    result_revision=task_revision,
                )
            task.current_snapshot_id = snapshot_id
            task.current_result_id = result_id

    def list_results(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            artifacts = session.scalars(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.artifact_type == "COMPARISON_RESULT",
                )
                .order_by(
                    WorkflowArtifact.task_revision.desc(),
                    WorkflowArtifact.created_at.desc(),
                )
            ).all()
            return [
                self._comparison_result_response(session, task, artifact)
                for artifact in artifacts
            ]

    def get_result(self, task_id: str, result_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            artifact = session.get(WorkflowArtifact, result_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or artifact is None
                or artifact.task_id != task_id
                or artifact.artifact_type != "COMPARISON_RESULT"
            ):
                raise NotFoundError("result_not_found", "Result was not found.")
            return self._comparison_result_response(session, task, artifact)

    def get_supplier_compliance_evidence(
        self, task_id: str, *, result_id: str | None = None
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            selected_result_id = result_id or task.current_result_id
            if selected_result_id is None:
                return {
                    "schema_version": SUPPLIER_COMPLIANCE_EVIDENCE_SCHEMA,
                    "task_id": task_id,
                    "task_revision": task.current_revision,
                    "result_id": None,
                    "evidence": [],
                }
            result = session.get(WorkflowArtifact, selected_result_id)
            if (
                result is None
                or result.task_id != task_id
                or result.artifact_type != "COMPARISON_RESULT"
            ):
                raise NotFoundError("result_not_found", "Result was not found.")
            artifact = session.scalar(
                select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.parent_artifact_id == selected_result_id,
                    WorkflowArtifact.artifact_type == "SUPPLIER_COMPLIANCE_EVIDENCE",
                ).order_by(WorkflowArtifact.created_at.desc()).limit(1)
            )
            if artifact is None:
                return {
                    "schema_version": SUPPLIER_COMPLIANCE_EVIDENCE_SCHEMA,
                    "task_id": task_id,
                    "task_revision": result.task_revision,
                    "result_id": selected_result_id,
                    "evidence": [],
                }
            return dict(artifact.payload) | {"evidence_artifact_id": artifact.artifact_id}

    def check_supplier_compliance(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        result_id: str,
        evidence: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "result_id": result_id,
            "evidence": evidence,
        }
        request_sha = content_hash(request)
        operation = f"check_supplier_compliance:{task_id}:{result_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            if task.current_result_id != result_id:
                raise ConflictError(
                    "result_stale", "Supplier compliance can only be checked against the current result."
                )
            result = session.get(WorkflowArtifact, result_id)
            if result is None or result.task_id != task_id or result.artifact_type != "COMPARISON_RESULT":
                raise NotFoundError("result_not_found", "Result was not found.")
            snapshot = session.get(WorkflowArtifact, result.parent_artifact_id) if result.parent_artifact_id else None
            if snapshot is None or snapshot.artifact_type != "INPUT_SNAPSHOT":
                raise ConflictError("result_snapshot_missing", "The result input snapshot is unavailable.")

            documents = {
                row.get("quote_id"): row for row in snapshot.payload.get("documents", [])
                if row.get("quote_id") and row.get("supplier_id")
            }
            expected_suppliers = {
                str(row["supplier_id"]) for row in documents.values()
            }
            supplied_ids = [str(row.get("supplier_id", "")).strip() for row in evidence]
            if len(supplied_ids) != len(set(supplied_ids)):
                raise BackendError("supplier_evidence_duplicate", "Supplier evidence contains duplicate supplier IDs.")
            unknown = sorted(set(supplied_ids) - expected_suppliers)
            if unknown:
                raise BackendError(
                    "supplier_evidence_out_of_scope",
                    "Supplier evidence contains suppliers outside the current result.",
                    supplier_ids=unknown,
                )

            evidence_payload = {
                "schema_version": SUPPLIER_COMPLIANCE_EVIDENCE_SCHEMA,
                "task_id": task_id,
                "task_revision": task.current_revision,
                "result_id": result_id,
                "evidence": evidence,
            }
            evidence_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"), task_id=task_id,
                task_revision=task.current_revision,
                artifact_type="SUPPLIER_COMPLIANCE_EVIDENCE",
                schema_version=SUPPLIER_COMPLIANCE_EVIDENCE_ARTIFACT_SCHEMA,
                parent_artifact_id=result_id, graph_run_id=result.graph_run_id,
                payload=evidence_payload, content_sha256=content_hash(evidence_payload),
            )
            session.add(evidence_artifact)

            retrievals = self._policy_retrieval_payloads(session, result_id)
            compliance = self._evaluate_supplier_compliance(
                comparison=dict(result.payload),
                snapshot=dict(snapshot.payload),
                retrievals=retrievals,
                evidence=evidence,
            )
            compliance["evidence_artifact_id"] = evidence_artifact.artifact_id
            check_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"), task_id=task_id,
                task_revision=task.current_revision,
                artifact_type="SUPPLIER_COMPLIANCE_CHECK",
                schema_version="policy-compliance/2.0.0",
                parent_artifact_id=result_id, graph_run_id=result.graph_run_id,
                payload=compliance, content_sha256=content_hash(compliance),
            )
            session.add(check_artifact)
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "result_id": result_id,
                "evidence_artifact_id": evidence_artifact.artifact_id,
                "check_artifact_id": check_artifact.artifact_id,
                "policy_compliance": compliance,
            }
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=200, response=response,
            )
            return response

    def supplier_information(
        self, task_id: str, *, result_id: str | None = None
    ) -> dict[str, Any]:
        """Return a task-scoped, read-only supplier view without live historical fallback."""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")

            selected_result_id = result_id or task.current_result_id
            if selected_result_id is None:
                quotes = session.scalars(
                    select(Quote).where(Quote.task_id == task_id, Quote.active.is_(True))
                    .order_by(Quote.quote_id)
                ).all()
                documents = {
                    row.quote_id: row
                    for row in session.scalars(
                        select(Document).join(
                            Quote, Document.quote_id == Quote.quote_id
                        ).where(
                            Document.task_id == task_id,
                            Quote.active.is_(True),
                            Document.quote_version == Quote.current_version,
                        )
                    ).all()
                }
                binding = self._binding_at_revision(session, task_id, task.current_revision)
                history_context = None
                snapshots_by_quote: dict[str, dict[str, Any]] = {}
                if binding is not None and documents:
                    try:
                        dataset, _manifest, _manifest_sha = SupplierHistoryLoader(
                            self.supplier_history_root
                        ).load(
                            dataset_version=binding.dataset_version,
                            expected_content_sha256=binding.content_sha256,
                            expected_manifest_sha256=binding.manifest_sha256,
                        )
                    except SupplierHistoryLoadError as exc:
                        raise BackendError(
                            exc.code,
                            "Supplier history dataset could not be loaded safely.",
                            dataset_version=binding.dataset_version,
                        ) from exc
                    submitted_drafts = session.scalars(
                        select(QuoteDraft)
                        .where(
                            QuoteDraft.task_id == task_id,
                            QuoteDraft.status == "SUBMITTED",
                            QuoteDraft.proposed_document_id.in_(
                                [document.document_id for document in documents.values()]
                            ),
                            QuoteDraft.review_artifact_id.is_not(None),
                        )
                        .order_by(
                            QuoteDraft.submitted_at.desc(),
                            QuoteDraft.created_at.desc(),
                        )
                    ).all()
                    envelopes: list[ReviewEnvelope] = []
                    seen_documents: set[str] = set()
                    for draft in submitted_drafts:
                        if draft.proposed_document_id in seen_documents:
                            continue
                        artifact = session.get(WorkflowArtifact, draft.review_artifact_id)
                        if artifact is None or artifact.task_id != task_id:
                            continue
                        try:
                            envelopes.append(ReviewEnvelope.model_validate(artifact.payload))
                        except ValidationError as exc:
                            raise BackendError(
                                "supplier_history_review_invalid",
                                "Submitted quote review could not be matched safely.",
                            ) from exc
                        seen_documents.add(draft.proposed_document_id)
                    history_context_model, snapshots = history_inputs(
                        {
                            "supplier_history_binding": self._history_binding_response(binding),
                            "supplier_history_dataset": dataset.model_dump(mode="json"),
                        },
                        tuple(envelopes),
                    )
                    history_context = (
                        history_context_model.model_dump(mode="json")
                        if history_context_model is not None else None
                    )
                    snapshots_by_quote = {
                        item.quote_id: item.model_dump(mode="json") for item in snapshots
                    }

                grouped: dict[str, dict[str, Any]] = {}
                unresolved: list[dict[str, Any]] = []
                for quote in quotes:
                    document = documents.get(quote.quote_id)
                    history = snapshots_by_quote.get(quote.quote_id)
                    identity_status = (history or {}).get(
                        "identity_match_status", "NOT_RECORDED"
                    )
                    supplier_id = (history or {}).get("supplier_id") or quote.supplier_id
                    display_name = (history or {}).get("supplier_name") or supplier_id
                    quote_view = {
                        "quote_id": quote.quote_id,
                        "quote_version": quote.current_version,
                        "active": True,
                        "in_scope_at_result": None,
                        "document_id": document.document_id if document else None,
                        "document_sha256": document.sha256 if document else None,
                        "is_synthetic": document.is_synthetic if document else None,
                        "evaluation": None,
                        "policy_assessment": None,
                        "decision_impact": None,
                    }
                    if identity_status == "MATCHED" and supplier_id:
                        group = grouped.setdefault(supplier_id, {
                            "supplier_identity_id": supplier_id,
                            "display_name": display_name,
                            "supplier_id": supplier_id,
                            "identity_match_status": identity_status,
                            "history_availability_status": (history or {}).get(
                                "history_availability_status", "NOT_RECORDED"
                            ),
                            "history_snapshot": history,
                            "quotes": [],
                        })
                        group["quotes"].append(quote_view)
                    else:
                        unresolved.append(quote_view | {
                            "supplier_id": supplier_id,
                            "display_name": display_name or "身份待核验候选",
                            "identity_match_status": identity_status,
                            "history_availability_status": (history or {}).get(
                                "history_availability_status", "NOT_RECORDED"
                            ),
                            "history_snapshot": history,
                        })
                payload = {
                    "schema_version": "supplier-information/1.0.0",
                    "task_id": task_id,
                    "snapshot_revision": task.current_revision,
                    "result_id": None,
                    "snapshot_id": None,
                    "view_state": "QUOTE_ONLY",
                    "is_current": True,
                    "data_availability": "DETERMINISTIC_COMPARISON_NOT_RUN",
                    "dataset_status": binding.binding_status if binding else "NOT_RECORDED",
                    "history_dataset_context": history_context or (
                        binding.payload.get("dataset_context") if binding else None
                    ),
                    "history_binding": self._history_binding_response(binding),
                    "effective_preferences": None,
                    "ranking_trace": None,
                    "quote_count": len(quotes),
                    "matched_supplier_count": len(grouped),
                    "unresolved_identity_quote_count": len(unresolved),
                    "draft_count": len(session.scalars(select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.notin_(("SUBMITTED", "DISCARDED")),
                    )).all()),
                    "inactive_quote_count": len(session.scalars(select(Quote).where(
                        Quote.task_id == task_id, Quote.active.is_(False),
                    )).all()),
                    "suppliers": list(grouped.values()),
                    "unresolved_identity_quotes": unresolved,
                }
                payload["context_sha256"] = content_hash(payload)
                return payload

            artifact = session.get(WorkflowArtifact, selected_result_id)
            if (
                artifact is None
                or artifact.task_id != task_id
                or artifact.artifact_type != "COMPARISON_RESULT"
            ):
                raise NotFoundError("result_not_found", "Result was not found.")
            snapshot = (
                session.get(WorkflowArtifact, artifact.parent_artifact_id)
                if artifact.parent_artifact_id else None
            )
            frozen = snapshot.payload if snapshot and snapshot.artifact_type == "INPUT_SNAPSHOT" else {}
            result = dict(artifact.payload)
            docs = {row.get("quote_id"): row for row in frozen.get("documents", [])}
            impact = self._decision_impact_payload(session, artifact.artifact_id) or {}
            impacts = {row.get("quote_id"): row for row in impact.get("quote_impacts", [])}
            compliance = self._policy_compliance_payload(
                result, self._policy_retrieval_payloads(session, artifact.artifact_id)
            )
            policies = {row.get("quote_id"): row for row in compliance.get("assessments", [])}
            excluded = set((result.get("ranking_trace") or {}).get("excluded_quote_ids", []))
            grouped: dict[str, dict[str, Any]] = {}
            unresolved: list[dict[str, Any]] = []
            for evaluation in result.get("supplier_results", []):
                quote_id = evaluation["quote_id"]
                history = evaluation.get("history_snapshot")
                identity_status = (history or {}).get("identity_match_status", "NOT_RECORDED")
                supplier_id = (history or {}).get("supplier_id") or docs.get(quote_id, {}).get("supplier_id")
                quote_view = {
                    "quote_id": quote_id,
                    "quote_version": evaluation.get("quote_version"),
                    "active": None,
                    "in_scope_at_result": quote_id not in excluded,
                    "document_id": docs.get(quote_id, {}).get("document_id"),
                    "document_sha256": docs.get(quote_id, {}).get("document_sha256"),
                    "is_synthetic": docs.get(quote_id, {}).get("is_synthetic"),
                    "evaluation": evaluation,
                    "policy_assessment": policies.get(quote_id),
                    "decision_impact": impacts.get(quote_id),
                }
                if identity_status == "MATCHED" and supplier_id:
                    group = grouped.setdefault(supplier_id, {
                        "supplier_identity_id": supplier_id,
                        "display_name": evaluation.get("supplier_name") or supplier_id,
                        "supplier_id": supplier_id,
                        "identity_match_status": identity_status,
                        "history_availability_status": (history or {}).get(
                            "history_availability_status", "NOT_RECORDED"
                        ),
                        "history_snapshot": history,
                        "quotes": [],
                    })
                    group["quotes"].append(quote_view)
                else:
                    unresolved.append(quote_view | {
                        "supplier_id": supplier_id,
                        "display_name": evaluation.get("supplier_name") or supplier_id or "身份待核验候选",
                        "identity_match_status": identity_status,
                        "history_availability_status": (history or {}).get(
                            "history_availability_status", "NOT_RECORDED"
                        ),
                        "history_snapshot": history,
                    })
            is_current = artifact.artifact_id == task.current_result_id
            payload = {
                "schema_version": "supplier-information/1.0.0",
                "task_id": task_id,
                "snapshot_revision": artifact.task_revision,
                "result_id": artifact.artifact_id,
                "snapshot_id": snapshot.artifact_id if snapshot else None,
                "view_state": "CURRENT_RESULT" if is_current else "HISTORICAL_RESULT",
                "is_current": is_current,
                "data_availability": "RECORDED",
                "dataset_status": (
                    (frozen.get("supplier_history_binding") or {}).get("binding_status", "NOT_RECORDED")
                ),
                "history_dataset_context": frozen.get("supplier_history_dataset_context"),
                "history_binding": frozen.get("supplier_history_binding"),
                "effective_preferences": (frozen.get("decision_profile") or {}).get("preferences"),
                "ranking_trace": result.get("ranking_trace"),
                "quote_count": len(result.get("supplier_results", [])),
                "matched_supplier_count": len(grouped),
                "unresolved_identity_quote_count": len(unresolved),
                "draft_count": None,
                "inactive_quote_count": None,
                "suppliers": list(grouped.values()),
                "unresolved_identity_quotes": unresolved,
            }
            payload["context_sha256"] = content_hash(payload)
            return payload

    def _comparison_result_response(
        self,
        session: Session,
        task: Task,
        artifact: WorkflowArtifact,
    ) -> dict[str, Any]:
        result = dict(artifact.payload)
        snapshot = session.get(WorkflowArtifact, artifact.parent_artifact_id) if artifact.parent_artifact_id else None
        if snapshot and (snapshot.task_id != task.task_id or snapshot.artifact_type != "INPUT_SNAPSHOT"):
            snapshot = None
        frozen = snapshot.payload if snapshot else {}
        # Expose only presentation inputs; internal artifact maps stay server-side.
        input_snapshot = {
            key: frozen.get(key)
            for key in (
                "requirement", "decision_profile", "policy_set_version",
                "policy_index_version", "policy_category", "policy_region",
                "supplier_history_binding", "supplier_history_dataset_context",
                "supplier_history_snapshots", "documents", "evaluated_at",
            )
        } if snapshot else None
        retrievals = self._policy_retrieval_payloads(session, artifact.artifact_id)
        compliance_artifact = session.scalar(
            select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task.task_id,
                WorkflowArtifact.parent_artifact_id == artifact.artifact_id,
                WorkflowArtifact.artifact_type == "SUPPLIER_COMPLIANCE_CHECK",
            ).order_by(WorkflowArtifact.created_at.desc()).limit(1)
        )
        return {
            "result_id": artifact.artifact_id,
            "snapshot_id": snapshot.artifact_id if snapshot else None,
            "input_snapshot": input_snapshot,
            "task_revision": artifact.task_revision,
            "graph_run_id": artifact.graph_run_id,
            "is_current": artifact.artifact_id == task.current_result_id,
            "result": result,
            "decision_impact": self._decision_impact_payload(session, artifact.artifact_id),
            "policy_retrievals": retrievals,
            "policy_compliance": (
                dict(compliance_artifact.payload)
                if compliance_artifact is not None
                else self._policy_compliance_payload(result, retrievals)
            ),
        }

    @staticmethod
    def _evaluate_supplier_compliance(
        *,
        comparison: dict[str, Any],
        snapshot: dict[str, Any],
        retrievals: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        requirement = snapshot.get("requirement") or {}
        evaluated_raw = comparison.get("evaluated_at") or snapshot.get("evaluated_at")
        try:
            evaluated_on = datetime.fromisoformat(str(evaluated_raw).replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            evaluated_on = datetime.now(timezone.utc).date()
        documents = {
            row.get("quote_id"): row for row in snapshot.get("documents", [])
            if row.get("quote_id")
        }
        evidence_by_supplier = {
            str(row.get("supplier_id")): row for row in evidence if row.get("supplier_id")
        }
        retrieval_by_control: dict[str, dict[str, Any]] = {}
        for retrieval in retrievals:
            codes = {
                *retrieval.get("covered_control_codes", []),
                *retrieval.get("missing_control_codes", []),
                *(citation.get("control_code") for citation in retrieval.get("citations", [])),
            }
            for code in codes:
                retrieval_by_control[str(code)] = retrieval

        def citation_ids(control_code: str) -> list[str]:
            retrieval = retrieval_by_control.get(control_code) or {}
            return [
                str(item["citation_id"])
                for item in retrieval.get("citations", [])
                if item.get("control_code") == control_code and item.get("citation_id")
            ]

        def policy_ready(control_code: str) -> bool:
            retrieval = retrieval_by_control.get(control_code)
            return bool(retrieval and retrieval.get("status") == "OK")

        assessments: list[dict[str, Any]] = []
        for supplier in comparison.get("supplier_results", []):
            quote_id = supplier.get("quote_id")
            supplier_id = (documents.get(quote_id) or {}).get("supplier_id")
            record = evidence_by_supplier.get(str(supplier_id))
            checks: list[dict[str, Any]] = []

            if supplier.get("status") != "FEASIBLE":
                for control_code in ("APPROVED_SUPPLIER", "ROHS_COMPLIANCE"):
                    checks.append({
                        "control_code": control_code,
                        "status": "NOT_EVALUATED",
                        "reason_code": "QUOTE_NOT_FEASIBLE",
                        "message": "该报价未通过采购要求，不进入供应商制度检查。",
                        "citation_ids": citation_ids(control_code),
                    })
                overall_status = "NOT_EVALUATED"
            else:
                if not policy_ready("APPROVED_SUPPLIER"):
                    approved_check = ("REVIEW_REQUIRED", "POLICY_EVIDENCE_INCOMPLETE", "供应商准入制度依据缺失或存在冲突。")
                elif record is None:
                    approved_check = ("REVIEW_REQUIRED", "SUPPLIER_REGISTRY_EVIDENCE_MISSING", "尚未录入该供应商的准入名单记录。")
                elif not record.get("approved_supplier"):
                    approved_check = ("FAIL", "SUPPLIER_NOT_APPROVED", "供应商不在当前有效准入名单中。")
                else:
                    try:
                        registry_until = date.fromisoformat(str(record.get("supplier_registry_valid_until")))
                    except (TypeError, ValueError):
                        registry_until = None
                    approved_check = (
                        ("PASS", "SUPPLIER_APPROVED", "供应商准入记录有效。")
                        if registry_until is not None and registry_until >= evaluated_on
                        else ("FAIL", "SUPPLIER_REGISTRY_EXPIRED", "供应商准入记录已过期或缺少有效期。")
                    )
                checks.append({
                    "control_code": "APPROVED_SUPPLIER", "status": approved_check[0],
                    "reason_code": approved_check[1], "message": approved_check[2],
                    "citation_ids": citation_ids("APPROVED_SUPPLIER"),
                })

                if not policy_ready("ROHS_COMPLIANCE"):
                    rohs_check = ("REVIEW_REQUIRED", "POLICY_EVIDENCE_INCOMPLETE", "RoHS 制度依据缺失或存在冲突。")
                elif record is None or not record.get("rohs_certificate_number"):
                    rohs_check = ("REVIEW_REQUIRED", "ROHS_EVIDENCE_MISSING", "尚未录入该供应商的 RoHS 证书。")
                elif record.get("rohs_part_number") != requirement.get("manufacturer_part_number"):
                    rohs_check = ("FAIL", "ROHS_PART_MISMATCH", "RoHS 证书料号与采购需求不一致。")
                elif requirement.get("revision") and record.get("rohs_revision") != requirement.get("revision"):
                    rohs_check = ("FAIL", "ROHS_REVISION_MISMATCH", "RoHS 证书版本与采购需求不一致。")
                else:
                    try:
                        rohs_until = date.fromisoformat(str(record.get("rohs_valid_until")))
                    except (TypeError, ValueError):
                        rohs_until = None
                    rohs_check = (
                        ("PASS", "ROHS_CERTIFICATE_VALID", "RoHS 证书与供应商、料号和版本匹配且在有效期内。")
                        if rohs_until is not None and rohs_until >= evaluated_on
                        else ("FAIL", "ROHS_CERTIFICATE_EXPIRED", "RoHS 证书已过期或缺少有效期。")
                    )
                checks.append({
                    "control_code": "ROHS_COMPLIANCE", "status": rohs_check[0],
                    "reason_code": rohs_check[1], "message": rohs_check[2],
                    "citation_ids": citation_ids("ROHS_COMPLIANCE"),
                })
                overall_status = (
                    "NON_COMPLIANT" if any(item["status"] == "FAIL" for item in checks)
                    else "COMPLIANT" if all(item["status"] == "PASS" for item in checks)
                    else "REVIEW_REQUIRED"
                )

            assessments.append({
                "quote_id": quote_id,
                "quote_version": supplier.get("quote_version"),
                "supplier_id": supplier_id,
                "supplier_name": supplier.get("supplier_name"),
                "status": overall_status,
                "checks": checks,
            })

        counts = {
            status: sum(item["status"] == status for item in assessments)
            for status in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED", "NOT_EVALUATED")
        }
        return {
            "schema_version": "policy-compliance/2.0.0",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "disposition": (
                "COMPLIANT_SUPPLIERS_AVAILABLE" if counts["COMPLIANT"]
                else "NO_CONFIRMED_COMPLIANT_SUPPLIER" if assessments else "NO_SUPPLIERS"
            ),
            "recommendation_scope": "COMPLIANCE_VERIFIED" if counts["COMPLIANT"] else "PROCUREMENT_COMPARISON_ONLY",
            "requires_human_review": counts["REVIEW_REQUIRED"] > 0,
            "counts": counts,
            "assessments": assessments,
        }

    @staticmethod
    def _policy_compliance_payload(
        comparison: dict[str, Any],
        retrievals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        control_definitions = {
            "APPROVED_SUPPLIER": (
                "SUPPLIER_REGISTRY_EVIDENCE_MISSING",
                "缺少当前供应商注册表记录，无法确认供应商准入状态。",
            ),
            "ROHS_COMPLIANCE": (
                "ROHS_EVIDENCE_MISSING",
                "缺少与供应商及料号匹配的有效 RoHS 证明。",
            ),
            "AMOUNT_APPROVAL": (
                "AMOUNT_APPROVAL_NOT_EVALUATED",
                "金额审批条款已找到，但尚未执行阈值判断或核对审批记录。",
            ),
        }
        retrieval_by_control: dict[str, dict[str, Any]] = {}
        for retrieval in retrievals:
            codes = {
                *retrieval.get("covered_control_codes", []),
                *retrieval.get("missing_control_codes", []),
                *(citation.get("control_code") for citation in retrieval.get("citations", [])),
            }
            for code in codes:
                if code in control_definitions:
                    retrieval_by_control[code] = retrieval

        assessments: list[dict[str, Any]] = []
        for supplier in comparison.get("supplier_results", []):
            quote_feasible = supplier.get("status") == "FEASIBLE"
            checks: list[dict[str, Any]] = []
            for control_code, (missing_fact_code, missing_fact_message) in control_definitions.items():
                retrieval = retrieval_by_control.get(control_code)
                citation_ids = [
                    citation["citation_id"]
                    for citation in (retrieval or {}).get("citations", [])
                    if citation.get("control_code") == control_code
                    and citation.get("citation_id")
                ]
                if not quote_feasible:
                    status = "NOT_EVALUATED"
                    reason_code = "QUOTE_NOT_FEASIBLE"
                    message = "该报价未通过采购要求，不进入供应商制度核验。"
                elif retrieval is None:
                    status = "REVIEW_REQUIRED"
                    reason_code = "POLICY_EVIDENCE_NOT_RETRIEVED"
                    message = "当前结果没有该控制项的制度检索记录。"
                elif retrieval.get("status") != "OK":
                    status = "REVIEW_REQUIRED"
                    reason_code = "POLICY_EVIDENCE_INCOMPLETE"
                    message = "该控制项的制度依据缺失或存在冲突。"
                else:
                    status = "REVIEW_REQUIRED"
                    reason_code = missing_fact_code
                    message = missing_fact_message
                checks.append({
                    "control_code": control_code,
                    "status": status,
                    "reason_code": reason_code,
                    "message": message,
                    "citation_ids": citation_ids,
                })
            if not quote_feasible:
                overall_status = "NOT_EVALUATED"
            elif all(check["status"] == "PASS" for check in checks):
                overall_status = "COMPLIANT"
            elif any(check["status"] == "FAIL" for check in checks):
                overall_status = "NON_COMPLIANT"
            else:
                overall_status = "REVIEW_REQUIRED"
            assessments.append({
                "quote_id": supplier.get("quote_id"),
                "quote_version": supplier.get("quote_version"),
                "supplier_name": supplier.get("supplier_name"),
                "status": overall_status,
                "checks": checks,
            })

        counts = {
            status: sum(item["status"] == status for item in assessments)
            for status in ("COMPLIANT", "NON_COMPLIANT", "REVIEW_REQUIRED", "NOT_EVALUATED")
        }
        if counts["COMPLIANT"]:
            disposition = "COMPLIANT_SUPPLIERS_AVAILABLE"
        elif assessments:
            disposition = "NO_CONFIRMED_COMPLIANT_SUPPLIER"
        else:
            disposition = "NO_SUPPLIERS"
        return {
            "schema_version": "policy-compliance/1.0.0",
            "disposition": disposition,
            "recommendation_scope": (
                "COMPLIANCE_VERIFIED"
                if counts["COMPLIANT"]
                else "PROCUREMENT_COMPARISON_ONLY"
            ),
            "requires_human_review": counts["REVIEW_REQUIRED"] > 0,
            "counts": counts,
            "assessments": assessments,
        }

    @staticmethod
    def _decision_impact_payload(session: Session, result_id: str) -> dict[str, Any] | None:
        result = session.get(WorkflowArtifact, result_id)
        snapshot = session.get(WorkflowArtifact, result.parent_artifact_id) if result and result.parent_artifact_id else None
        impact_id = snapshot.payload.get("decision_impact_artifact_id") if snapshot else None
        artifact = session.get(WorkflowArtifact, impact_id) if impact_id else None
        return dict(artifact.payload) if artifact is not None else None

    def list_quote_fields(self, task_id: str, quote_id: str, result_id: str | None = None) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            quote = session.get(Quote, quote_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or quote is None
                or quote.task_id != task_id
            ):
                raise NotFoundError("quote_not_found", "Quote was not found.")
            frozen_supplier = None
            frozen_review = None
            if result_id is not None:
                result = session.get(WorkflowArtifact, result_id)
                if not result or result.task_id != task_id or result.artifact_type != "COMPARISON_RESULT":
                    raise NotFoundError("result_not_found", "Result was not found.")
                frozen_supplier = next((row for row in result.payload.get("supplier_results", [])
                                        if row.get("quote_id") == quote_id), None)
                snapshot = session.get(WorkflowArtifact, result.parent_artifact_id) if result.parent_artifact_id else None
                if (not frozen_supplier or not snapshot or snapshot.task_id != task_id
                        or snapshot.artifact_type != "INPUT_SNAPSHOT"):
                    raise NotFoundError("result_evidence_unavailable", "Frozen evidence is unavailable for this result.")
                batch_id = snapshot.payload.get("batch_artifact_ids", {}).get(quote_id)
                review_id = snapshot.payload.get("review_artifact_ids", {}).get(quote_id)
                batch_artifact = session.get(WorkflowArtifact, batch_id) if batch_id else None
                frozen_review = session.get(WorkflowArtifact, review_id) if review_id else None
                for item, kind in ((batch_artifact, "EXTRACTION_BATCH"), (frozen_review, "REVIEW_ENVELOPE")):
                    if not item or item.task_id != task_id or item.quote_id != quote_id or item.artifact_type != kind:
                        raise NotFoundError("result_evidence_unavailable", "Frozen evidence is unavailable for this result.")
            else:
                batch_artifact = session.scalar(
                    select(WorkflowArtifact)
                    .where(
                        WorkflowArtifact.task_id == task_id,
                        WorkflowArtifact.quote_id == quote_id,
                        WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                    )
                    .order_by(
                        WorkflowArtifact.task_revision.desc(),
                        WorkflowArtifact.created_at.desc(),
                    )
                )
            if batch_artifact is None:
                return {
                    "quote_id": quote_id,
                    "quote_version": frozen_supplier["quote_version"] if frozen_supplier else quote.current_version,
                    "review_status": None,
                    "review_findings": [],
                    "fields": [],
                }
            batch = dict(batch_artifact.payload)
            sources = {
                source["source_id"]: source
                for source in batch.get("parsed_input", {}).get("sources", [])
            }
            fields = []
            for candidate in batch.get("candidates", []):
                evidence = []
                for citation in candidate.get("source_refs", []):
                    source = sources.get(citation.get("source_id"), {})
                    evidence.append(
                        {
                            "source_id": citation.get("source_id"),
                            "quoted_text": citation.get("quoted_text"),
                            "kind": source.get("kind"),
                            "page_number": source.get("page_number"),
                            "row_number": source.get("row_number"),
                            "column_name": source.get("column_name"),
                            "bbox": source.get("bbox"),
                            "coordinate_space": source.get("coordinate_space"),
                        }
                    )
                fields.append(
                    {
                        key: candidate.get(key)
                        for key in (
                            "field_name",
                            "field_version",
                            "raw_value",
                            "normalized_value",
                            "unit",
                            "validation_status",
                            "origin",
                        )
                    }
                    | {"evidence": evidence}
                )
            if result_id is not None:
                review_artifact = frozen_review
            else:
                review_query = select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.quote_id == quote_id,
                    WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
                )
                review_artifact = session.scalar(
                    review_query.where(
                        WorkflowArtifact.graph_run_id
                        == (task.current_graph_run_id or batch_artifact.graph_run_id)
                    ).order_by(
                        WorkflowArtifact.task_revision.desc(),
                        WorkflowArtifact.created_at.desc(),
                    )
                )
                if review_artifact is None:
                    review_artifact = session.scalar(
                        review_query.order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    )
            review_status = (
                review_artifact.payload.get("review_status")
                if review_artifact is not None
                else None
            )
            review_findings = (
                review_artifact.payload.get("review", {}).get("findings", [])
                if review_artifact is not None
                else []
            )
            review_is_current = bool(
                review_artifact is not None
                and review_artifact.graph_run_id == task.current_graph_run_id
            )
            if result_id is None and not review_is_current:
                corrected_fields = {
                    field["field_name"]
                    for field in fields
                    if field.get("validation_status") == "VERIFIED"
                    and field.get("origin") in {"USER_INPUT", "USER_CORRECTION"}
                }
                review_findings = [
                    finding
                    for finding in review_findings
                    if finding.get("field_name") not in corrected_fields
                    or finding.get("resolved")
                ]
            return {
                "quote_id": quote_id,
                "quote_version": frozen_supplier["quote_version"] if frozen_supplier else quote.current_version,
                "review_status": review_status,
                "batch_artifact_id": batch_artifact.artifact_id,
                "review_findings": review_findings,
                "fields": fields,
            }

    def list_review_problems(self, task_id: str) -> dict[str, Any]:
        """Read all current findings together without inventing human approvals."""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            quotes = session.scalars(select(Quote).where(
                Quote.task_id == task_id, Quote.active.is_(True)
            ).order_by(Quote.quote_id)).all()
            reports = []
            problems = []
            impact = session.scalar(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id,
                WorkflowArtifact.graph_run_id == task.current_graph_run_id,
                WorkflowArtifact.task_revision == task.current_revision,
                WorkflowArtifact.artifact_type == "DECISION_IMPACT_RESULT",
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
            nonblocking = set(impact.payload.get("nonblocking_unknown_quote_ids", [])) if impact else set()
            for quote in quotes:
                execution = session.scalar(select(DocumentExecution).join(
                    Document, Document.document_id == DocumentExecution.document_id
                ).where(
                    DocumentExecution.graph_run_id == task.current_graph_run_id,
                    Document.quote_id == quote.quote_id,
                    Document.quote_version == quote.current_version,
                ).order_by(DocumentExecution.document_execution_id))
                batch_artifact = session.get(WorkflowArtifact, execution.batch_artifact_id) if execution and execution.batch_artifact_id else None
                review_artifact = session.get(WorkflowArtifact, execution.review_artifact_id) if execution and execution.review_artifact_id else None
                # Corrections queue a new batch, but do not reuse an old review.
                batch = batch_artifact.payload if batch_artifact else {}
                candidates = {c["field_name"]: c for c in batch.get("candidates", [])}
                document = session.get(Document, execution.document_id) if execution else None
                current_review = bool(
                    review_artifact and batch_artifact
                    and review_artifact.graph_run_id == task.current_graph_run_id
                    and review_artifact.task_revision <= task.current_revision
                    and review_artifact.payload.get("batch") == batch_artifact.payload
                )
                envelope = review_artifact.payload if current_review else {}
                report = {
                    "quote_id": quote.quote_id, "supplier_id": quote.supplier_id,
                    "quote_version": quote.current_version,
                    "document_id": document.document_id if document else None,
                    "original_filename": document.original_filename if document else None,
                    "batch_artifact_id": batch_artifact.artifact_id if batch_artifact else None,
                    "review_artifact_id": review_artifact.artifact_id if current_review else None,
                    "review_status": envelope.get("review_status"),
                    "review_pending": not current_review,
                    "fields": list(candidates.values()),
                    "evidence_sources": [
                        {key: source.get(key) for key in (
                            "source_id", "kind", "raw_text", "page_number", "row_number", "column_name"
                        )}
                        for source in batch.get("parsed_input", {}).get("sources", [])
                    ],
                }
                reports.append(report)
                for finding in (envelope.get("review") or {}).get("findings", []):
                    if finding.get("resolved") or finding.get("decision") == "PASS":
                        continue
                    candidate = candidates.get(finding["field_name"], {})
                    is_blocking = finding.get("severity") == "BLOCKING" or finding.get("decision") == "REJECTED"
                    # Dominance proof applies only to audited missing fees.
                    needs_resolution = is_blocking and quote.quote_id not in nonblocking
                    problems.append(dict(finding) | {
                        "quote_id": quote.quote_id, "quote_version": quote.current_version,
                        "field_version": candidate.get("field_version"),
                        "raw_value": candidate.get("raw_value"),
                        "normalized_value": candidate.get("normalized_value"),
                        "unit": candidate.get("unit"),
                        "document_id": report["document_id"],
                        "original_filename": report["original_filename"],
                        "needs_resolution": needs_resolution,
                        "resolution": "FIELD_CORRECTION" if candidate and finding.get("review_reason") != "SYSTEM_IDENTITY_ERROR" else "REEXTRACT_OR_SYSTEM_REPAIR",
                    })
                # The comparison boundary can detect issues beyond extraction
                # findings (for example FREE shipping with a positive amount).
                comparison_rows = (impact.payload.get("comparison") or {}).get("supplier_results", []) if impact else []
                comparison_row = next((row for row in comparison_rows if row["quote_id"] == quote.quote_id), {})
                for issue in comparison_row.get("pending_reasons", []):
                    for field_name in issue.get("fields", []):
                        if any(p["quote_id"] == quote.quote_id and p["field_name"] == field_name
                               and issue["code"] in p["codes"] for p in problems):
                            continue
                        candidate = candidates.get(field_name, {})
                        correctable_fact = issue["code"] in COMPARISON_FACT_CORRECTION_CODES
                        needs_field_resolution = (
                            quote.quote_id not in nonblocking and correctable_fact
                        )
                        problems.append({
                            "finding_id": f"comparison:{quote.quote_id}:{field_name}:{issue['code']}",
                            "field_name": field_name, "codes": [issue["code"]],
                            "message": issue["message"], "decision": "REVIEW_REQUIRED",
                            "severity": "BLOCKING", "review_reason": "COMPARISON_INPUT_ISSUE",
                            "quote_id": quote.quote_id, "quote_version": quote.current_version,
                            "field_version": candidate.get("field_version"),
                            "raw_value": candidate.get("raw_value"),
                            "normalized_value": candidate.get("normalized_value"), "unit": candidate.get("unit"),
                            "document_id": report["document_id"], "original_filename": report["original_filename"],
                            # Valid commercial terms that the current calculator
                            # cannot evaluate must remain visible as PENDING, but
                            # they are not quote fields the operator must rewrite.
                            "needs_resolution": needs_field_resolution,
                            "resolution": (
                                "FIELD_CORRECTION"
                                if candidate and correctable_fact
                                else "ADDITIONAL_INFORMATION_REQUIRED"
                                if candidate
                                else "REEXTRACT_OR_SYSTEM_REPAIR"
                            ),
                        })
            return {
                "task_id": task_id, "task_revision": task.current_revision,
                "graph_run_id": task.current_graph_run_id, "task_status": task.status,
                "review_pending": not reports or any(report["review_pending"] for report in reports),
                "quotes": reports, "problems": problems,
                "blocking_problem_count": sum(p["needs_resolution"] for p in problems),
                "problem_count": len(problems),
            }

    def selection_analysis_input(self, task_id: str, *, expected_task_revision: int,
                                 evaluated_at: datetime | None = None,
                                 changes: RequirementChanges | None = None):
        """Current audited quote scope only; never turn form values into reviewed facts."""
        from supplier_comparison.extraction import ReviewEnvelope
        from supplier_comparison.extraction.errors import DownstreamNotReadyError
        from supplier_comparison.rules.integration import reviewed_decision_impact_request

        task = self.get_task(task_id)
        review = self.list_review_problems(task_id)
        if task['task_revision'] != expected_task_revision or review['task_revision'] != expected_task_revision:
            raise ConflictError('task_revision_conflict', 'Analysis requires the current task revision.')
        if review['review_pending'] or not task['current_graph_run_id']:
            raise ConflictError('selection_review_required', 'Run extraction and review before selection analysis.')
        context = self.workflow_context(task['current_graph_run_id'])
        documents = {d['quote_id']: d for d in context['documents']}
        envelopes = []
        try:
            for row in review['quotes']:
                envelope = ReviewEnvelope.model_validate(self.artifact_payload(row['review_artifact_id']))
                batch = envelope.batch
                document = documents[row['quote_id']]
                if batch is None:
                    raise ConflictError('selection_review_required', 'A model-failed quote cannot be analyzed.')
                parsed = batch.parsed_input
                identity = parsed.context
                if (identity.task_id != task_id or identity.quote_id != row['quote_id']
                        or identity.quote_version != document['quote_version']
                        or identity.document_id != document['document_id']
                        or identity.document_version != document['document_version']
                        or parsed.document_sha256 != document['document_sha256']):
                    raise ConflictError('selection_input_stale', 'Reviewed input identity no longer matches the quote.')
                envelopes.append(envelope)
            if set(documents) != {r['quote_id'] for r in review['quotes']}:
                raise ConflictError('selection_input_stale', 'Analysis must cover every active quote.')
        except DownstreamNotReadyError as exc:
            raise ConflictError('selection_review_required', 'Resolve unsafe review findings before analysis.') from exc
        # Freeze the workflow evaluation instant instead of silently changing quote validity.
        if evaluated_at is None:
            with self.session_factory() as session:
                report = session.scalar(select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.graph_run_id == task['current_graph_run_id'],
                    WorkflowArtifact.task_revision == expected_task_revision,
                    WorkflowArtifact.artifact_type == 'DECISION_IMPACT_RESULT',
                ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
                stamp = report.payload.get('comparison', {}).get('evaluated_at') if report else None
            if not stamp:
                raise ConflictError('selection_review_required', 'A frozen preliminary comparison is required.')
            evaluated_at = datetime.fromisoformat(stamp)
        history_context, history_snapshots = history_inputs(context, tuple(envelopes))
        preferences = DecisionPreferences.model_validate(task['decision_profile']['preferences'])
        scope_preferences = preferences
        if changes is not None and 'excluded_supplier_ids' in changes.model_fields_set:
            requested = set(changes.excluded_supplier_ids or ())
            available = {row['supplier_id'] for row in review['quotes']}
            if requested - available:
                raise BackendError('simulation_change_invalid', 'Excluded supplier is not in this task.')
            # Re-included suppliers must pass the same authoritative review again.
            # Keep currently included suppliers available for simulation exclusion.
            scope_preferences = preferences.model_copy(update={
                'excluded_supplier_ids': tuple(sorted(set(preferences.excluded_supplier_ids) & requested)),
            })
        try:
            result = reviewed_decision_impact_request(
                ProcurementRequirement.model_validate(task['requirement']), tuple(envelopes),
                task_id=task_id, task_revision=expected_task_revision,
                evaluated_at=evaluated_at,
                supplier_history_snapshots=history_snapshots,
                history_dataset_context=history_context,
                payment_supplements=context.get('payment_supplements', {}),
                policy_binding={key: context[key] for key in (
                    'policy_set_version', 'policy_index_version', 'policy_category', 'policy_region')},
                decision_preferences=scope_preferences,
            ).model_copy(update={'decision_preferences': preferences})
        except DownstreamNotReadyError as exc:
            raise ConflictError(
                'selection_review_required',
                '参与本次比较的报价仍有待审核字段，请到集中审核确认后重新分析。',
            ) from exc
        latest = self.get_task(task_id)
        if latest['task_revision'] != expected_task_revision or latest['current_graph_run_id'] != task['current_graph_run_id']:
            raise ConflictError('selection_input_stale', 'Input changed during analysis.')
        return result

    def selection_gaps(self, task_id: str, *, expected_task_revision: int,
                       expected_result_id: str | None = None):
        from supplier_comparison.rules import analyze_selection_gap, draft_clarification
        before = self.get_task(task_id)
        if expected_result_id is not None and before['current_result_id'] != expected_result_id:
            raise ConflictError('selection_input_stale', '当前结果已更新，请刷新决策页面后重试。')
        result = analyze_selection_gap(self.selection_analysis_input(task_id, expected_task_revision=expected_task_revision))
        latest = self.get_task(task_id)
        if (latest['task_revision'] != expected_task_revision
                or latest['current_graph_run_id'] != before['current_graph_run_id']
                or latest['current_result_id'] != before['current_result_id']):
            raise ConflictError('selection_input_stale', 'Input changed during analysis.')
        return result.model_dump(mode='json') | {'clarification_drafts': [draft_clarification(gap) for gap in result.gaps]}

    def requirement_simulation(self, task_id: str, *, expected_task_revision: int, changes, user_authorized: bool):
        before = self.get_task(task_id)
        if before["status"] == "ABANDONED":
            raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
        request = self.selection_analysis_input(task_id, expected_task_revision=expected_task_revision, changes=changes)
        try:
            result = simulate_requirement_change(request, changes, user_authorized=user_authorized)
        except ValueError as exc:
            raise BackendError('simulation_change_invalid', 'Authorized changes must satisfy the requirement contract.') from exc
        if (
            _preferences_require_history(result.decision_preferences)
            and (before.get("supplier_history_binding") or {}).get("binding_status") != "AVAILABLE"
        ):
            raise BackendError(
                "ranking_criterion_not_applicable",
                "Historical ranking criteria are not applicable to this task scope.",
            )
        latest = self.get_task(task_id)
        if latest['task_revision'] != expected_task_revision or latest['current_graph_run_id'] != before['current_graph_run_id']:
            raise ConflictError('selection_input_stale', 'Input changed during simulation.')
        return {'task_id': task_id, 'task_revision': expected_task_revision,
                'result': result.model_dump(mode='json')}

    @staticmethod
    def _scenario_delta(baseline: dict[str, Any], simulated: dict[str, Any]) -> dict[str, Any]:
        before = {row["quote_id"]: row for row in baseline.get("supplier_results", [])}
        after = {row["quote_id"]: row for row in simulated.get("supplier_results", [])}
        excluded_ids = set(
            (simulated.get("ranking_trace") or {}).get("excluded_quote_ids", [])
        )
        rows = []
        for quote_id in sorted(set(before) | set(after)):
            old = before.get(quote_id)
            new = after.get(quote_id)
            old_cost = old.get("total_cost") if old else None
            new_cost = new.get("total_cost") if new else None
            cost_delta = None
            if old_cost is not None and new_cost is not None:
                from decimal import Decimal
                cost_delta = str(Decimal(new_cost) - Decimal(old_cost))
            rows.append({
                "quote_id": quote_id,
                "baseline_status": old.get("status") if old else None,
                "simulated_status": new.get("status") if new else None,
                "baseline_total_cost": old_cost,
                "simulated_total_cost": new_cost,
                "total_cost_delta": cost_delta,
                "baseline_arrival_date": old.get("estimated_arrival_date") if old else None,
                "simulated_arrival_date": new.get("estimated_arrival_date") if new else None,
                "excluded": quote_id in excluded_ids,
            })
        baseline_ids = tuple(baseline.get("recommended_quote_ids", []))
        simulated_ids = tuple(simulated.get("recommended_quote_ids", []))
        return {
            "recommendation_changed": baseline_ids != simulated_ids,
            "baseline_disposition": baseline.get("disposition"),
            "simulated_disposition": simulated.get("disposition"),
            "baseline_recommended_quote_ids": list(baseline_ids),
            "simulated_recommended_quote_ids": list(simulated_ids),
            "added_recommended_quote_ids": sorted(set(simulated_ids) - set(baseline_ids)),
            "removed_recommended_quote_ids": sorted(set(baseline_ids) - set(simulated_ids)),
            "supplier_deltas": rows,
        }

    def create_decision_scenario(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        changes: RequirementChanges,
        idempotency_key: str,
    ) -> dict[str, Any]:
        task_view = self.get_task(task_id)
        if task_view["status"] == "ABANDONED":
            raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
        if task_view["task_revision"] != expected_task_revision:
            raise ConflictError(
                "task_revision_conflict", "Task revision has changed.",
                expected=expected_task_revision, actual=task_view["task_revision"],
            )
        if not task_view["current_result_id"]:
            raise ConflictError(
                "scenario_result_required",
                "A current frozen comparison result is required before creating a scenario.",
            )
        analysis_input = self.selection_analysis_input(
            task_id, expected_task_revision=expected_task_revision
        )
        baseline = analyze_decision_impact(analysis_input).comparison.model_dump(mode="json")
        frozen_baseline = self.get_result(
            task_id, task_view["current_result_id"]
        )["result"]
        if baseline != frozen_baseline:
            changed_sections = sorted(
                key
                for key in set(baseline) | set(frozen_baseline)
                if baseline.get(key) != frozen_baseline.get(key)
            )
            raise ConflictError(
                "scenario_baseline_stale",
                "冻结结果与当前确定性计算不一致，请先按当前代码重新分析。",
                frozen_rule_version=frozen_baseline.get("rule_version"),
                current_rule_version=baseline.get("rule_version"),
                changed_sections=changed_sections,
            )
        try:
            analysis_input = self.selection_analysis_input(
                task_id, expected_task_revision=expected_task_revision, changes=changes
            )
            trial = simulate_requirement_change(
                analysis_input, changes, user_authorized=True
            )
        except ValueError as exc:
            raise BackendError(
                "simulation_change_invalid",
                "Authorized changes must satisfy the requirement and decision contracts.",
            ) from exc
        if (
            _preferences_require_history(trial.decision_preferences)
            and (task_view.get("supplier_history_binding") or {}).get("binding_status") != "AVAILABLE"
        ):
            raise BackendError(
                "ranking_criterion_not_applicable",
                "Historical ranking criteria are not applicable to this task scope.",
            )
        hard_change = any(
            field in changes.model_fields_set
            and getattr(changes, field) != getattr(analysis_input.comparison.requirement, field)
            for field in ("budget_amount", "delivery_deadline")
        )
        if not hard_change and trial.decision_preferences == analysis_input.decision_preferences:
            raise BackendError(
                "decision_scenario_no_effect",
                "The proposed scenario does not change the current requirement or decision preferences.",
            )
        simulated = trial.model_dump(mode="json")
        delta = self._scenario_delta(baseline, simulated["comparison"])
        changes_payload = changes.model_dump(mode="json", exclude_unset=True)
        request_payload = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "base_result_id": task_view["current_result_id"],
            "changes": changes_payload,
        }
        request_sha = content_hash(request_payload)
        operation = f"create_decision_scenario:{task_id}"
        scenario_id = new_id("scenario")
        input_sha = content_hash({
            "analysis_input": analysis_input.model_dump(mode="json"),
            "base_result_id": task_view["current_result_id"],
            "changes": changes_payload,
        })
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            if task.current_result_id != task_view["current_result_id"]:
                raise ConflictError("scenario_input_stale", "The current result changed during simulation.")
            scenario = DecisionScenario(
                decision_scenario_id=scenario_id,
                task_id=task_id,
                actor_id=self.actor_id,
                base_task_revision=expected_task_revision,
                base_result_id=task.current_result_id,
                input_sha256=input_sha,
                status="READY",
                changes=changes_payload,
                baseline=baseline,
                simulated=simulated,
                delta=delta,
            )
            session.add(scenario)
            session.flush()
            response = self._decision_scenario_response(task, scenario)
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=201, response=response,
            )
            return response

    def list_decision_scenarios(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            scenarios = session.scalars(select(DecisionScenario).where(
                DecisionScenario.task_id == task_id,
                DecisionScenario.actor_id == self.actor_id,
            ).order_by(DecisionScenario.created_at.desc(), DecisionScenario.decision_scenario_id.desc())).all()
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [self._decision_scenario_response(task, item) for item in scenarios],
            }

    def get_decision_scenario(self, task_id: str, scenario_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            scenario = session.get(DecisionScenario, scenario_id)
            if (
                task is None or task.owner_id != self.actor_id or scenario is None
                or scenario.task_id != task_id or scenario.actor_id != self.actor_id
            ):
                raise NotFoundError("decision_scenario_not_found", "Decision scenario was not found.")
            return self._decision_scenario_response(task, scenario)

    def apply_decision_scenario(
        self,
        task_id: str,
        scenario_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "scenario_id": scenario_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"apply_decision_scenario:{task_id}:{scenario_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            scenario = session.scalar(select(DecisionScenario).where(
                DecisionScenario.decision_scenario_id == scenario_id
            ).with_for_update())
            if (
                task is None or task.owner_id != self.actor_id or scenario is None
                or scenario.task_id != task_id or scenario.actor_id != self.actor_id
            ):
                raise NotFoundError("decision_scenario_not_found", "Decision scenario was not found.")
            self._require_revision(task, expected_task_revision)
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if (
                scenario.status != "READY"
                or scenario.base_task_revision != task.current_revision
                or scenario.base_result_id != task.current_result_id
            ):
                if scenario.status == "READY":
                    scenario.status = "STALE"
                raise ConflictError("decision_scenario_stale", "Only a current READY scenario can be applied.")
            current = session.scalar(select(RequirementRecord).where(
                RequirementRecord.task_id == task_id
            ).order_by(RequirementRecord.requirement_version.desc()))
            if current is None:
                raise ConflictError("requirement_missing", "The task has no procurement requirement.")
            changes = RequirementChanges.model_validate(scenario.changes)
            old_requirement = ProcurementRequirement.model_validate(current.payload)
            requirement_values = old_requirement.model_dump(mode="python")
            for field in ("budget_amount", "delivery_deadline"):
                if field in changes.model_fields_set:
                    requirement_values[field] = getattr(changes, field)
            new_requirement = ProcurementRequirement.model_validate(requirement_values)
            old_preferences, old_profile = self._decision_preferences(
                session, task, old_requirement
            )
            new_preferences = DecisionPreferences.model_validate(
                scenario.simulated["decision_preferences"]
            )
            requirement_changed = old_requirement != new_requirement
            preferences_changed = old_preferences != new_preferences
            if not requirement_changed and not preferences_changed:
                raise ConflictError("decision_scenario_no_effect", "Scenario does not change current inputs.")

            self._supersede_current_graph(session, task)
            for draft in session.scalars(select(QuoteDraft).where(
                QuoteDraft.task_id == task_id,
                QuoteDraft.status.in_(("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")),
            )):
                draft.status = "STALE"
                draft.revision += 1
                for draft_job in session.scalars(select(Job).where(
                    Job.quote_draft_id == draft.quote_draft_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )):
                    draft_job.status = "SUPERSEDED"
            next_revision = task.current_revision + 1
            changed_requirement_fields = sorted(
                name for name in set(current.payload) | set(new_requirement.model_dump(mode="json"))
                if current.payload.get(name) != new_requirement.model_dump(mode="json").get(name)
            )
            changed_preference_fields = sorted(
                name for name in set(old_preferences.model_dump(mode="json")) | set(new_preferences.model_dump(mode="json"))
                if old_preferences.model_dump(mode="json").get(name) != new_preferences.model_dump(mode="json").get(name)
            )
            session.add(TaskRevision(
                revision_id=new_id("rev"), task_id=task_id, revision=next_revision,
                change_type="DECISION_SCENARIO_APPLIED", actor_id=self.actor_id,
                request_sha256=request_sha, details={
                    "decision_scenario_id": scenario_id,
                    "changed_requirement_fields": changed_requirement_fields,
                    "changed_decision_preference_fields": changed_preference_fields,
                },
            ))
            if requirement_changed:
                requirement_payload = new_requirement.model_dump(mode="json")
                session.add(RequirementRecord(
                    requirement_id=new_id("req"), task_id=task_id,
                    task_revision=next_revision,
                    requirement_version=current.requirement_version + 1,
                    payload=requirement_payload,
                    content_sha256=content_hash(requirement_payload),
                    source_artifact_id=None,
                ))
            decision_profile_id = old_profile.decision_profile_id if old_profile else None
            if preferences_changed:
                profile_payload = new_preferences.model_dump(mode="json")
                decision_profile_id = new_id("dprofile")
                session.add(DecisionProfile(
                    decision_profile_id=decision_profile_id,
                    task_id=task_id,
                    task_revision=next_revision,
                    profile_version=(old_profile.profile_version + 1 if old_profile else 1),
                    payload=profile_payload,
                    content_sha256=content_hash(profile_payload),
                    source_scenario_id=scenario_id,
                ))
            task.current_revision = next_revision
            history_binding = self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=next_revision - 1,
                to_revision=next_revision,
            )
            active_documents = session.scalars(select(Document).join(
                Quote, Quote.quote_id == Document.quote_id
            ).where(
                Document.task_id == task_id,
                Quote.active.is_(True),
                Document.quote_version == Quote.current_version,
            )).all()
            graph_run_id = job_id = None
            if active_documents:
                graph_run_id, job_id = new_id("graph"), new_id("job")
                session.add(GraphRun(
                    graph_run_id=graph_run_id, task_id=task_id, thread_id=graph_run_id,
                    started_revision=next_revision, effective_revision=next_revision,
                    status="PENDING",
                    history_binding_id=(history_binding.history_binding_id if history_binding else None),
                    provider=provider, model_id=model_id,
                    environment=environment, prompt_version=prompt_version,
                ))
                session.flush()
                session.add(Job(
                    job_id=job_id, task_id=task_id, graph_run_id=graph_run_id,
                    job_type="START", status="PENDING", task_revision=next_revision,
                    history_binding_id=(history_binding.history_binding_id if history_binding else None),
                ))
                task.current_graph_run_id = graph_run_id
                task.status = "QUEUED"
            else:
                task.status = "DRAFT"
            if graph_run_id:
                self._seed_submitted_extractions(
                    session, task_id=task_id, graph_run_id=graph_run_id,
                    provider=provider, model_id=model_id, environment=environment,
                    prompt_version=prompt_version,
                )
            scenario.status = "APPLIED"
            scenario.applied_task_revision = next_revision
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "status": task.status,
                "decision_scenario_id": scenario_id,
                "decision_profile_id": decision_profile_id,
                "changed_requirement_fields": changed_requirement_fields,
                "changed_decision_preference_fields": changed_preference_fields,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_status": "PENDING" if job_id else None,
            }
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=202, response=response,
            )
            return response

    def _decision_scenario_response(
        self, task: Task, scenario: DecisionScenario
    ) -> dict[str, Any]:
        stale = (
            scenario.status == "STALE"
            or (
                scenario.status == "READY"
                and (
                    scenario.base_task_revision != task.current_revision
                    or scenario.base_result_id != task.current_result_id
                )
            )
        )
        status = "STALE" if stale else scenario.status
        return {
            "decision_scenario_id": scenario.decision_scenario_id,
            "task_id": scenario.task_id,
            "base_task_revision": scenario.base_task_revision,
            "base_result_id": scenario.base_result_id,
            "input_sha256": scenario.input_sha256,
            "status": status,
            "is_current": status == "READY",
            "changes": dict(scenario.changes),
            "baseline": dict(scenario.baseline),
            "simulated": dict(scenario.simulated),
            "delta": dict(scenario.delta),
            "applied_task_revision": scenario.applied_task_revision,
            "created_at": self._aware_datetime(scenario.created_at).isoformat(),
            "updated_at": self._aware_datetime(scenario.updated_at).isoformat(),
        }

    @staticmethod
    def _replay_decision_intent_response(response: dict[str, Any]) -> dict[str, Any]:
        error = response.get("_decision_intent_error")
        if error is None:
            return response
        error_type = ConflictError if error.get("conflict") else BackendError
        raise error_type(error["code"], error["message"])

    def parse_decision_intent(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        message: str,
        idempotency_key: str,
        parser: DecisionIntentParser,
        provider: str | None,
        model_id: str | None,
        prompt_version: str,
    ) -> dict[str, Any]:
        """Parse one utterance and persist a confirmable, non-authoritative patch."""

        request_sha = content_hash({
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "message": message,
            "prompt_version": prompt_version,
            "model_id": model_id,
        })
        operation = f"parse_decision_intent:{task_id}"
        with self.session_factory() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return self._replay_decision_intent_response(repeated)
        task_view = self.get_task(task_id)
        if task_view["status"] == "ABANDONED":
            raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
        if task_view["task_revision"] != expected_task_revision:
            raise ConflictError(
                "task_revision_conflict",
                "Task revision has changed.",
                expected=expected_task_revision,
                actual=task_view["task_revision"],
            )
        if not task_view["current_result_id"]:
            raise ConflictError(
                "decision_intent_result_required",
                "A current frozen comparison result is required before interpreting an intent.",
            )
        analysis_input = self.selection_analysis_input(
            task_id, expected_task_revision=expected_task_revision
        )
        context = {
            "currency": analysis_input.comparison.requirement.currency,
            "current_requirement": {
                "budget_amount": str(analysis_input.comparison.requirement.budget_amount),
                "delivery_deadline": analysis_input.comparison.requirement.delivery_deadline.isoformat(),
            },
            "current_decision_preferences": analysis_input.decision_preferences.model_dump(
                mode="json"
            ),
            "available_supplier_ids": sorted(set(analysis_input.supplier_bindings.values())),
            "allowed_ranking_criteria": [criterion.value for criterion in RankingCriterion],
        }
        intent_id = new_id("dintent")
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return self._replay_decision_intent_response(repeated)
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            if task.current_result_id != task_view["current_result_id"]:
                raise ConflictError(
                    "decision_intent_input_stale",
                    "The current result changed before intent parsing began.",
                )
            session.add(
                DecisionIntent(
                    decision_intent_id=intent_id,
                    task_id=task_id,
                    actor_id=self.actor_id,
                    base_task_revision=expected_task_revision,
                    base_result_id=task.current_result_id,
                    source_text=message,
                    source_sha256=content_hash({"message": message}),
                    status="PROCESSING",
                    provider=provider,
                    model_id=model_id,
                    prompt_version=prompt_version,
                )
            )

        attempts = 0
        try:
            parsed, attempts = parser(message, context)
            changes = RequirementChanges.model_validate(parsed)
            trial = simulate_requirement_change(
                analysis_input, changes, user_authorized=True
            )
            hard_change = any(
                field in changes.model_fields_set
                and getattr(changes, field)
                != getattr(analysis_input.comparison.requirement, field)
                for field in ("budget_amount", "delivery_deadline")
            )
            if not hard_change and trial.decision_preferences == analysis_input.decision_preferences:
                raise ValueError("intent has no effect")
        except ModelClientError as exc:
            attempts = exc.attempts
            error_code = exc.error_code
            error_message = "The decision intent model could not produce a valid structured change."
        except (ValidationError, ValueError) as exc:
            error_code = "decision_intent_model_output_invalid"
            error_message = "The decision intent model could not produce a valid structured change."
        else:
            error_code = error_message = None

        if error_code is not None:
            replay = {
                "_decision_intent_error": {
                    "code": error_code,
                    "message": error_message,
                    "conflict": False,
                }
            }
            with self.session_factory.begin() as session:
                intent = session.get(DecisionIntent, intent_id)
                if intent is not None:
                    intent.status = "FAILED"
                    intent.attempts = attempts
                    intent.error_code = error_code
                    intent.error_message = error_message
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=422,
                    response=replay,
                )
            raise BackendError(error_code, error_message)

        confirmation = confirmation_text(
            changes, currency=analysis_input.comparison.requirement.currency
        )
        stale = False
        response: dict[str, Any] | None = None
        with self.session_factory.begin() as session:
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            intent = session.scalar(
                select(DecisionIntent)
                .where(DecisionIntent.decision_intent_id == intent_id)
                .with_for_update()
            )
            if task is None or intent is None or task.owner_id != self.actor_id:
                raise NotFoundError("decision_intent_not_found", "Decision intent was not found.")
            intent.attempts = attempts
            if (
                task.current_revision != expected_task_revision
                or task.current_result_id != task_view["current_result_id"]
            ):
                stale = True
                intent.status = "STALE"
                intent.error_code = "decision_intent_input_stale"
                intent.error_message = "Task inputs changed while the intent was being parsed."
                replay = {
                    "_decision_intent_error": {
                        "code": intent.error_code,
                        "message": intent.error_message,
                        "conflict": True,
                    }
                }
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=409,
                    response=replay,
                )
            else:
                intent.status = "READY"
                intent.parsed_changes = changes.model_dump(mode="json", exclude_unset=True)
                intent.confirmation_text = confirmation
                session.flush()
                response = self._decision_intent_response(task, intent)
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=201,
                    response=response,
                )
        if stale:
            raise ConflictError(
                "decision_intent_input_stale",
                "Task inputs changed while the intent was being parsed.",
            )
        assert response is not None
        return response

    def list_decision_intents(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            intents = session.scalars(
                select(DecisionIntent)
                .where(
                    DecisionIntent.task_id == task_id,
                    DecisionIntent.actor_id == self.actor_id,
                )
                .order_by(
                    DecisionIntent.created_at.desc(),
                    DecisionIntent.decision_intent_id.desc(),
                )
            ).all()
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [self._decision_intent_response(task, item) for item in intents],
            }

    def get_decision_intent(self, task_id: str, intent_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            intent = session.get(DecisionIntent, intent_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or intent is None
                or intent.task_id != task_id
                or intent.actor_id != self.actor_id
            ):
                raise NotFoundError("decision_intent_not_found", "Decision intent was not found.")
            return self._decision_intent_response(task, intent)

    def confirm_decision_intent(
        self,
        task_id: str,
        intent_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "decision_intent_id": intent_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"confirm_decision_intent:{task_id}:{intent_id}"
        with self.session_factory() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.get(Task, task_id)
            intent = session.get(DecisionIntent, intent_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or intent is None
                or intent.task_id != task_id
                or intent.actor_id != self.actor_id
            ):
                raise NotFoundError("decision_intent_not_found", "Decision intent was not found.")
            self._require_revision(task, expected_task_revision)
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if (
                intent.status != "READY"
                or intent.base_task_revision != task.current_revision
                or intent.base_result_id != task.current_result_id
            ):
                raise ConflictError(
                    "decision_intent_stale",
                    "Only a current READY decision intent can be confirmed.",
                )
            changes = RequirementChanges.model_validate(intent.parsed_changes)

        scenario = self.create_decision_scenario(
            task_id,
            expected_task_revision=expected_task_revision,
            changes=changes,
            idempotency_key=content_hash(
                {"decision_intent_id": intent_id, "confirmation_key": idempotency_key}
            ),
        )
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            intent = session.scalar(
                select(DecisionIntent)
                .where(DecisionIntent.decision_intent_id == intent_id)
                .with_for_update()
            )
            if task is None or intent is None or task.owner_id != self.actor_id:
                raise NotFoundError("decision_intent_not_found", "Decision intent was not found.")
            self._require_revision(task, expected_task_revision)
            if intent.status != "READY":
                raise ConflictError(
                    "decision_intent_stale",
                    "Only a current READY decision intent can be confirmed.",
                )
            intent.status = "CONFIRMED"
            intent.decision_scenario_id = scenario["decision_scenario_id"]
            response = {
                "decision_intent_id": intent_id,
                "status": "CONFIRMED",
                "scenario": scenario,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=201,
                response=response,
            )
            return response

    def _decision_intent_response(
        self, task: Task, intent: DecisionIntent
    ) -> dict[str, Any]:
        stale = intent.status == "STALE" or (
            intent.status in {"PROCESSING", "READY"}
            and (
                intent.base_task_revision != task.current_revision
                or intent.base_result_id != task.current_result_id
            )
        )
        status = "STALE" if stale else intent.status
        return {
            "decision_intent_id": intent.decision_intent_id,
            "task_id": intent.task_id,
            "base_task_revision": intent.base_task_revision,
            "base_result_id": intent.base_result_id,
            "source_text": intent.source_text,
            "source_sha256": intent.source_sha256,
            "status": status,
            "is_current": status == "READY",
            "parsed_changes": dict(intent.parsed_changes) if intent.parsed_changes else None,
            "confirmation_text": intent.confirmation_text,
            "provider": intent.provider,
            "model_id": intent.model_id,
            "prompt_version": intent.prompt_version,
            "attempts": intent.attempts,
            "error_code": intent.error_code,
            "error_message": intent.error_message,
            "decision_scenario_id": intent.decision_scenario_id,
            "created_at": self._aware_datetime(intent.created_at).isoformat(),
            "updated_at": self._aware_datetime(intent.updated_at).isoformat(),
        }

    @staticmethod
    def _append_conversation_event(
        session: Session,
        conversation_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> DecisionConversationEvent:
        latest = session.scalar(
            select(DecisionConversationEvent)
            .where(DecisionConversationEvent.conversation_id == conversation_id)
            .order_by(DecisionConversationEvent.sequence.desc())
        )
        event = DecisionConversationEvent(
            event_id=new_id("cevent"),
            conversation_id=conversation_id,
            sequence=(latest.sequence + 1 if latest else 1),
            event_type=event_type,
            payload=payload,
        )
        session.add(event)
        session.flush()
        return event

    def create_decision_conversation(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        title: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "title": title,
        }
        request_sha = content_hash(request)
        operation = f"create_decision_conversation:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            self._require_revision(task, expected_task_revision)
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if not task.current_result_id:
                raise ConflictError(
                    "conversation_result_required",
                    "A current frozen comparison result is required before starting a conversation.",
                )
            conversation = DecisionConversation(
                conversation_id=new_id("conversation"),
                task_id=task_id,
                actor_id=self.actor_id,
                base_task_revision=task.current_revision,
                base_result_id=task.current_result_id,
                status="ACTIVE",
                title=title or "决策分析对话",
            )
            session.add(conversation)
            session.flush()
            self._append_conversation_event(
                session,
                conversation.conversation_id,
                "conversation.created",
                {"conversation_id": conversation.conversation_id},
            )
            response = self._decision_conversation_response(session, task, conversation)
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=201, response=response,
            )
            return response

    def list_decision_conversations(
        self, task_id: str, *, result_id: str | None = None
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            query = select(DecisionConversation).where(
                DecisionConversation.task_id == task_id,
                DecisionConversation.actor_id == self.actor_id,
            )
            if result_id is not None:
                query = query.where(DecisionConversation.base_result_id == result_id)
            rows = session.scalars(
                query.order_by(DecisionConversation.created_at.desc())
            ).all()
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [self._decision_conversation_response(session, task, row) for row in rows],
            }

    def get_decision_conversation(
        self, task_id: str, conversation_id: str
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            conversation = session.get(DecisionConversation, conversation_id)
            if (
                task is None or task.owner_id != self.actor_id or conversation is None
                or conversation.task_id != task_id or conversation.actor_id != self.actor_id
            ):
                raise NotFoundError("conversation_not_found", "Decision conversation was not found.")
            return self._decision_conversation_response(session, task, conversation)

    def send_decision_conversation_message(
        self,
        task_id: str,
        conversation_id: str,
        *,
        expected_task_revision: int,
        content: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "conversation_id": conversation_id,
            "expected_task_revision": expected_task_revision,
            "content": content,
        }
        request_sha = content_hash(request)
        operation = f"send_decision_message:{conversation_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            conversation = session.scalar(
                select(DecisionConversation)
                .where(DecisionConversation.conversation_id == conversation_id)
                .with_for_update()
            )
            if (
                task is None or task.owner_id != self.actor_id or conversation is None
                or conversation.task_id != task_id or conversation.actor_id != self.actor_id
            ):
                raise NotFoundError("conversation_not_found", "Decision conversation was not found.")
            self._require_revision(task, expected_task_revision)
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            if (
                conversation.status != "ACTIVE"
                or conversation.base_task_revision != task.current_revision
                or conversation.base_result_id != task.current_result_id
            ):
                conversation.status = "STALE"
                raise ConflictError("conversation_stale", "The conversation inputs are stale.")
            active_job = session.scalar(
                select(Job).where(
                    Job.conversation_id == conversation_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )
            )
            if active_job is not None:
                raise ConflictError(
                    "conversation_turn_in_progress",
                    "Wait for the current assistant turn before sending another message.",
                )
            latest = session.scalar(
                select(DecisionMessage)
                .where(DecisionMessage.conversation_id == conversation_id)
                .order_by(DecisionMessage.sequence.desc())
            )
            message = DecisionMessage(
                message_id=new_id("dmessage"),
                conversation_id=conversation_id,
                task_id=task_id,
                sequence=(latest.sequence + 1 if latest else 1),
                role="USER",
                status="SUCCEEDED",
                content=content,
            )
            job = Job(
                job_id=new_id("job"),
                task_id=task_id,
                graph_run_id=None,
                conversation_id=conversation_id,
                conversation_message_id=message.message_id,
                job_type="DECISION_CONVERSATION",
                status="PENDING",
                task_revision=task.current_revision,
            )
            session.add_all((message, job))
            session.flush()
            self._append_conversation_event(
                session, conversation_id, "user.message",
                {"message": self._decision_message_response(message)},
            )
            self._append_conversation_event(
                session, conversation_id, "assistant.pending",
                {"job_id": job.job_id, "reply_to_message_id": message.message_id},
            )
            response = {
                "conversation_id": conversation_id,
                "message": self._decision_message_response(message),
                "job": self._job_response(job),
            }
            self._save_idempotent(
                session, operation=operation, key=idempotency_key,
                request_sha256=request_sha, response_status=202, response=response,
            )
            return response

    def _conversation_frozen_references(
        self,
        session: Session,
        *,
        task: Task,
        result: WorkflowArtifact,
    ) -> tuple[dict[str, Any], set[str]]:
        comparison = dict(result.payload)
        comparison["decision_fact_catalog"] = decision_fact_catalog(comparison)
        references: dict[str, Any] = {f"RESULT:{result.artifact_id}": comparison}
        quotes = session.scalars(
            select(Quote).where(Quote.task_id == task.task_id, Quote.active.is_(True))
        ).all()
        # Identity scope includes excluded active quotes so users can re-include
        # them. This does not grant those quotes factual or review authority.
        supplier_ids: set[str] = {quote.supplier_id for quote in quotes}
        supplier_by_quote = {quote.quote_id: quote.supplier_id for quote in quotes}
        confirmed_fields_by_quote: dict[str, dict[str, Any]] = {}
        snapshot = (
            session.get(WorkflowArtifact, result.parent_artifact_id)
            if result.parent_artifact_id
            else None
        )
        if snapshot is not None and snapshot.artifact_type == "INPUT_SNAPSHOT":
            references[f"REQUIREMENT:{snapshot.artifact_id}"] = {
                "requirement": snapshot.payload.get("requirement", {}),
                "decision_profile": snapshot.payload.get("decision_profile", {}),
            }
            for quote_id, artifact_id in dict(
                snapshot.payload.get("review_artifact_ids", {})
            ).items():
                artifact = session.get(WorkflowArtifact, artifact_id)
                if artifact is None or artifact.task_id != task.task_id:
                    continue
                try:
                    envelope = ReviewEnvelope.model_validate(artifact.payload)
                except ValidationError:
                    continue
                if not envelope.submission_ready or envelope.batch is None:
                    continue
                confirmed_fields_by_quote[str(quote_id)] = {
                    candidate.field_name: candidate.normalized_value
                    for candidate in envelope.batch.candidates
                    if candidate.validation_status == ValidationStatus.VERIFIED
                }
        for row in comparison.get("supplier_results", []):
            quote_id = row.get("quote_id")
            if quote_id:
                references[f"QUOTE:{quote_id}"] = {
                    **row,
                    "confirmed_quote_fields": confirmed_fields_by_quote.get(
                        str(quote_id), {}
                    ),
                }
                if quote_id in supplier_by_quote:
                    supplier_ids.add(supplier_by_quote[quote_id])

        retrievals = self._policy_retrieval_payloads(session, result.artifact_id)
        references[f"COMPLIANCE:{result.artifact_id}"] = self._policy_compliance_payload(
            comparison, retrievals
        )
        for retrieval in retrievals:
            for citation in retrieval.get("citations", []):
                citation_id = citation.get("citation_id")
                if citation_id:
                    references[f"POLICY:{citation_id}"] = {
                        **citation,
                        "retrieval_id": retrieval.get("retrieval_id"),
                        "retrieval_status": retrieval.get("status"),
                        "policy_index_version": retrieval.get("policy_index_version"),
                    }

        investigation_rows = session.scalars(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.task_id == task.task_id,
                WorkflowArtifact.task_revision == task.current_revision,
                WorkflowArtifact.graph_run_id == result.graph_run_id,
                WorkflowArtifact.artifact_type == "INVESTIGATION_CASE",
            )
            .order_by(
                WorkflowArtifact.created_at.desc(),
                WorkflowArtifact.artifact_id.desc(),
            )
        ).all()
        seen_cases: set[str] = set()
        for artifact in investigation_rows:
            case_id = str(artifact.payload.get("case_id", artifact.artifact_id))
            if case_id in seen_cases:
                continue
            seen_cases.add(case_id)
            references[f"INVESTIGATION:{artifact.artifact_id}"] = {
                key: artifact.payload.get(key)
                for key in (
                    "case_id",
                    "kind",
                    "status",
                    "stop_reason",
                    "goal",
                    "unknown_fields",
                    "clarification",
                    "observations",
                )
            }
        return references, supplier_ids

    def conversation_job_context(self, job_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if (
                job is None or job.job_type != "DECISION_CONVERSATION"
                or not job.conversation_id or not job.conversation_message_id
            ):
                raise NotFoundError("conversation_job_not_found", "Conversation job was not found.")
            task = session.get(Task, job.task_id)
            conversation = session.scalar(
                select(DecisionConversation)
                .where(DecisionConversation.conversation_id == job.conversation_id)
                .with_for_update()
            )
            user_message = session.get(DecisionMessage, job.conversation_message_id)
            if task is None or conversation is None or user_message is None:
                raise NotFoundError("conversation_job_context_missing", "Conversation job context is missing.")
            if (
                task.owner_id != self.actor_id or job.status != "PENDING"
                or conversation.status != "ACTIVE"
                or conversation.base_task_revision != task.current_revision
                or conversation.base_result_id != task.current_result_id
            ):
                job.status = "SUPERSEDED"
                conversation.status = "STALE"
                raise ConflictError("conversation_stale", "Conversation inputs are stale.")
            if job.attempts >= 3:
                raise BackendError("job_attempt_budget_exceeded", "Job attempt budget was exceeded.")
            result = session.get(WorkflowArtifact, task.current_result_id)
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task.task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if result is None or requirement is None:
                raise ConflictError("conversation_context_missing", "Conversation facts are missing.")
            preferences, _profile = self._decision_preferences(
                session, task, ProcurementRequirement.model_validate(requirement.payload)
            )
            messages = list(session.scalars(
                select(DecisionMessage)
                .where(
                    DecisionMessage.conversation_id == conversation.conversation_id,
                    DecisionMessage.status == "SUCCEEDED",
                )
                .order_by(DecisionMessage.sequence.desc())
                .limit(12)
            ).all())
            messages.reverse()
            bounded_messages: list[DecisionMessage] = []
            remaining_history_characters = 12_000
            for row in reversed(messages):
                size = len(row.content or "")
                if size > remaining_history_characters:
                    break
                bounded_messages.append(row)
                remaining_history_characters -= size
            messages = list(reversed(bounded_messages))
            prior_user_messages = list(
                session.scalars(
                    select(DecisionMessage)
                    .join(
                        DecisionConversation,
                        DecisionMessage.conversation_id
                        == DecisionConversation.conversation_id,
                    )
                    .where(
                        DecisionConversation.task_id == task.task_id,
                        DecisionConversation.actor_id == self.actor_id,
                        DecisionConversation.conversation_id
                        != conversation.conversation_id,
                        DecisionConversation.created_at < conversation.created_at,
                        DecisionMessage.role == "USER",
                        DecisionMessage.status == "SUCCEEDED",
                    )
                    .order_by(DecisionMessage.created_at.desc())
                    .limit(8)
                ).all()
            )
            prior_user_messages.reverse()
            references, supplier_ids = self._conversation_frozen_references(
                session,
                task=task,
                result=result,
            )
            supplier_directory = []
            names_by_id = {
                str(row.get("history_snapshot", {}).get("supplier_id")): row.get("supplier_name")
                for row in result.payload.get("supplier_results", [])
                if isinstance(row.get("history_snapshot"), dict)
            }
            snapshot = session.get(WorkflowArtifact, result.parent_artifact_id) if result.parent_artifact_id else None
            quote_ids = {q.quote_id: q.supplier_id for q in session.scalars(
                select(Quote).where(Quote.task_id == task.task_id, Quote.active.is_(True))
            ).all()}
            if snapshot is not None and snapshot.artifact_type == "INPUT_SNAPSHOT":
                for quote_id, review_id in snapshot.payload.get("review_artifact_ids", {}).items():
                    review = session.get(WorkflowArtifact, review_id)
                    if review is None or review.task_id != task.task_id or quote_id not in quote_ids:
                        continue
                    try:
                        envelope = ReviewEnvelope.model_validate(review.payload)
                    except ValidationError:
                        continue
                    if envelope.batch:
                        for candidate in envelope.batch.candidates:
                            if (candidate.field_name == "supplier_name"
                                and candidate.validation_status == ValidationStatus.VERIFIED
                                and isinstance(candidate.normalized_value, str)):
                                names_by_id[quote_ids[quote_id]] = candidate.normalized_value
            for supplier_id in sorted(supplier_ids):
                supplier_directory.append({"supplier_id": supplier_id,
                                           "name": names_by_id.get(supplier_id) or supplier_id})
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = datetime.now(timezone.utc)
            self._append_conversation_event(
                session, conversation.conversation_id, "assistant.started",
                {"job_id": job.job_id, "reply_to_message_id": user_message.message_id},
            )
            return {
                "job_id": job.job_id,
                "task_id": task.task_id,
                "task_revision": task.current_revision,
                "result_id": task.current_result_id,
                "conversation_id": conversation.conversation_id,
                "user_message_id": user_message.message_id,
                "current_requirement": dict(requirement.payload),
                "current_decision_preferences": preferences.model_dump(mode="json"),
                "frozen_references": references,
                "allowed_reference_ids": sorted(references),
                "available_supplier_ids": sorted(supplier_ids),
                "supplier_directory": supplier_directory,
                "prior_user_context": [
                    {
                        "content": row.content,
                        "trust": "UNTRUSTED_USER_INTENT_ONLY",
                    }
                    for row in prior_user_messages
                    if row.content
                ],
                "recent_messages": [
                    {
                        "role": row.role,
                        "content": row.content,
                        "reference_ids": list(row.reference_ids or []),
                    }
                    for row in messages
                    if row.content
                ],
            }

    def complete_conversation_job(
        self,
        job_id: str,
        *,
        turn: dict[str, Any],
        attempts: int,
        provider: str,
        model_id: str,
        prompt_version: str = CONVERSATION_PROMPT_VERSION,
    ) -> dict[str, Any]:
        try:
            turn = normalize_conversation_turn(turn).model_dump(mode="json", exclude_unset=True)
        except (ValidationError, ValueError, TypeError) as exc:
            raise BackendError(
                "conversation_model_output_invalid",
                "Conversation proposal does not satisfy the output contract.",
            ) from exc
        text = str(turn["assistant_text"])
        reference_ids = list(turn.get("reference_ids", []))
        if text.strip() and not re.search(r"[\u4e00-\u9fff]", text):
            raise BackendError(
                "conversation_model_output_invalid",
                "Conversation output must contain validated Chinese narration.",
            )
        if len(reference_ids) != len(set(reference_ids)):
            raise BackendError(
                "conversation_model_output_invalid",
                "Conversation output contains duplicate references.",
            )
        raw_changes = turn.get("changes")
        changes = RequirementChanges.model_validate(raw_changes) if raw_changes is not None else None
        with self.session_factory() as session:
            job_view = session.get(Job, job_id)
            if job_view is None or not job_view.task_id:
                raise NotFoundError("conversation_job_not_found", "Conversation job was not found.")
            task_id = job_view.task_id
            task_revision = job_view.task_revision
        trial = analysis_input = None
        no_effect = False
        if changes is not None:
            analysis_input = self.selection_analysis_input(
                task_id, expected_task_revision=task_revision, changes=changes
            )
            trial = simulate_requirement_change(
                analysis_input, changes, user_authorized=True
            )
            hard_change = any(
                field in changes.model_fields_set
                and getattr(changes, field)
                != getattr(analysis_input.comparison.requirement, field)
                for field in ("budget_amount", "delivery_deadline")
            )
            if not hard_change and trial.decision_preferences == analysis_input.decision_preferences:
                no_effect = True
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            conversation = session.scalar(
                select(DecisionConversation)
                .where(DecisionConversation.conversation_id == job.conversation_id)
                .with_for_update()
            ) if job and job.conversation_id else None
            task = session.get(Task, job.task_id) if job and job.task_id else None
            user_message = session.get(DecisionMessage, job.conversation_message_id) if job and job.conversation_message_id else None
            if job is None or conversation is None or task is None or user_message is None:
                raise NotFoundError("conversation_job_not_found", "Conversation job was not found.")
            if (
                job.status != "RUNNING" or conversation.status != "ACTIVE"
                or task.current_revision != conversation.base_task_revision
                or task.current_result_id != conversation.base_result_id
            ):
                job.status = "SUPERSEDED"
                conversation.status = "STALE"
                raise ConflictError("conversation_stale", "Conversation inputs changed during generation.")
            result = session.get(WorkflowArtifact, task.current_result_id)
            if result is None:
                raise ConflictError(
                    "conversation_context_missing", "Conversation facts are missing."
                )
            frozen_references, _supplier_ids = self._conversation_frozen_references(
                session,
                task=task,
                result=result,
            )
            allowed_references = set(frozen_references)
            if set(reference_ids) - allowed_references:
                raise BackendError(
                    "conversation_model_output_invalid",
                    "Conversation output contains references outside the frozen result.",
                )
            try:
                validated_turn = validate_conversation_turn(
                    turn,
                    {
                        "frozen_references": frozen_references,
                        "allowed_reference_ids": sorted(allowed_references),
                        "available_supplier_ids": sorted(_supplier_ids),
                        "recent_messages": [{"role": "USER", "content": user_message.content}],
                    },
                )
            except (ValidationError, ValueError, TypeError) as exc:
                raise BackendError(
                    "conversation_model_output_invalid",
                    "Conversation output failed persistence validation: "
                    + str(exc)[:500],
                ) from exc
            text = render_conversation_turn(validated_turn)
            if no_effect:
                changes = None
                text = "\n\n".join(part for part in (
                    validated_turn.assistant_text.strip(),
                    "这项偏好与当前设置一致，无需生成新的模拟情景。您可以继续调整其他条件。",
                ) if part)
            reference_ids = list(validated_turn.reference_ids)
            if changes is not None and not no_effect:
                from .decision_narrative import render_decision_preview

                preview_id = new_id("artifact")
                preview_payload = {
                    "base_result_id": task.current_result_id,
                    "base_task_revision": task.current_revision,
                    "changes": changes.model_dump(mode="json", exclude_unset=True),
                    "simulation": trial.model_dump(mode="json"),
                    "supplier_bindings": analysis_input.supplier_bindings,
                    "source": "DETERMINISTIC_SIMULATION",
                }
                session.add(WorkflowArtifact(
                    artifact_id=preview_id, task_id=task.task_id,
                    task_revision=task.current_revision,
                    artifact_type="DECISION_PREVIEW",
                    parent_artifact_id=task.current_result_id,
                    payload=preview_payload, content_sha256=content_hash(preview_payload),
                ))
                # This is a derived hypothetical reference, not evidence that the
                # current frozen recommendation has already changed.
                reference_ids = ["SIMULATION:" + preview_id]
                text = render_decision_preview(
                    trial, currency=analysis_input.comparison.requirement.currency,
                    supplier_bindings=analysis_input.supplier_bindings,
                    reference=reference_ids[0],
                )
            intent_id = None
            confirmation = None
            if changes is not None:
                intent_id = new_id("dintent")
                confirmation = confirmation_text(
                    changes, currency=analysis_input.comparison.requirement.currency
                )
                session.add(DecisionIntent(
                    decision_intent_id=intent_id,
                    task_id=task.task_id,
                    actor_id=self.actor_id,
                    base_task_revision=task.current_revision,
                    base_result_id=task.current_result_id,
                    source_text=user_message.content or "",
                    source_sha256=content_hash({"message": user_message.content or ""}),
                    status="READY",
                    parsed_changes=changes.model_dump(mode="json", exclude_unset=True),
                    confirmation_text=confirmation,
                    provider=provider,
                    model_id=model_id,
                    prompt_version=prompt_version,
                    attempts=attempts,
                ))
            latest = session.scalar(
                select(DecisionMessage)
                .where(DecisionMessage.conversation_id == conversation.conversation_id)
                .order_by(DecisionMessage.sequence.desc())
            )
            assistant = DecisionMessage(
                message_id=new_id("dmessage"),
                conversation_id=conversation.conversation_id,
                task_id=task.task_id,
                sequence=latest.sequence + 1,
                role="ASSISTANT",
                status="SUCCEEDED",
                content=text,
                reference_ids=reference_ids,
                proposed_changes=(changes.model_dump(mode="json", exclude_unset=True) if changes else None),
                decision_intent_id=intent_id,
                reply_to_message_id=user_message.message_id,
                provider=provider,
                model_id=model_id,
                prompt_version=prompt_version,
                attempts=attempts,
            )
            session.add(assistant)
            session.flush()
            response = self._decision_message_response(assistant)
            if confirmation:
                response["confirmation_text"] = confirmation
            self._append_conversation_event(
                session, conversation.conversation_id, "assistant.completed",
                {"message": response},
            )
            job.status = "SUCCEEDED"
            job.finished_at = datetime.now(timezone.utc)
            return {
                "conversation_id": conversation.conversation_id,
                "message": response,
                "job_id": job.job_id,
                "job_status": job.status,
            }

    def record_conversation_stage(
        self, job_id: str, *, stage: str, calls_used: int, elapsed_seconds: float,
        provider: str, model_id: str, prompt_version: str,
    ) -> None:
        """Persist bounded diagnostics without holding a transaction during model calls."""
        if stage not in {"intent", "simulation", "narration", "persist"}:
            raise ValueError("unknown conversation stage")
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id)
            if job is None or job.status != "RUNNING" or not job.conversation_id:
                return
            conversation = session.scalar(select(DecisionConversation).where(
                DecisionConversation.conversation_id == job.conversation_id
            ).with_for_update())
            if conversation is None:
                return
            self._append_conversation_event(session, conversation.conversation_id, "assistant.stage", {
                "job_id": job_id, "reply_to_message_id": job.conversation_message_id,
                "stage": stage, "calls_used": calls_used,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "task_revision": job.task_revision, "result_id": conversation.base_result_id,
                "provider": provider, "model_id": model_id, "prompt_version": prompt_version,
            })

    def fail_conversation_job(
        self, job_id: str, *, code: str, message: str, attempts: int,
        diagnostic: str | None = None,
    ) -> None:
        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            conversation = session.scalar(
                select(DecisionConversation)
                .where(DecisionConversation.conversation_id == job.conversation_id)
                .with_for_update()
            ) if job and job.conversation_id else None
            user_message = session.get(DecisionMessage, job.conversation_message_id) if job and job.conversation_message_id else None
            if job is None or conversation is None or user_message is None:
                return
            latest = session.scalar(
                select(DecisionMessage)
                .where(DecisionMessage.conversation_id == conversation.conversation_id)
                .order_by(DecisionMessage.sequence.desc())
            )
            failed = DecisionMessage(
                message_id=new_id("dmessage"),
                conversation_id=conversation.conversation_id,
                task_id=conversation.task_id,
                sequence=latest.sequence + 1,
                role="ASSISTANT",
                status="FAILED",
                reply_to_message_id=user_message.message_id,
                attempts=attempts,
                error_code=code,
                error_message=message[:1000],
            )
            session.add(failed)
            job.status = "FAILED"
            job.error_code = code
            job.error_message = (diagnostic or message)[:1000]
            job.finished_at = datetime.now(timezone.utc)
            session.flush()
            self._append_conversation_event(
                session, conversation.conversation_id, "assistant.failed",
                {"message": self._decision_message_response(failed)},
            )

    def decision_conversation_events(
        self, task_id: str, conversation_id: str, *, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            conversation = session.get(DecisionConversation, conversation_id)
            if (
                task is None or task.owner_id != self.actor_id or conversation is None
                or conversation.task_id != task_id or conversation.actor_id != self.actor_id
            ):
                raise NotFoundError("conversation_not_found", "Decision conversation was not found.")
            events = session.scalars(
                select(DecisionConversationEvent)
                .where(
                    DecisionConversationEvent.conversation_id == conversation_id,
                    DecisionConversationEvent.sequence > after_sequence,
                )
                .order_by(DecisionConversationEvent.sequence)
            ).all()
            return [
                {
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "payload": dict(event.payload),
                    "created_at": self._aware_datetime(event.created_at).isoformat(),
                }
                for event in events
            ]

    def _decision_conversation_response(
        self, session: Session, task: Task, conversation: DecisionConversation
    ) -> dict[str, Any]:
        stale = conversation.status == "STALE" or (
            conversation.status == "ACTIVE"
            and (
                conversation.base_task_revision != task.current_revision
                or conversation.base_result_id != task.current_result_id
            )
        )
        messages = session.scalars(
            select(DecisionMessage)
            .where(DecisionMessage.conversation_id == conversation.conversation_id)
            .order_by(DecisionMessage.sequence)
        ).all()
        return {
            "conversation_id": conversation.conversation_id,
            "task_id": conversation.task_id,
            "base_task_revision": conversation.base_task_revision,
            "base_result_id": conversation.base_result_id,
            "status": "STALE" if stale else conversation.status,
            "title": conversation.title,
            "messages": [self._decision_message_response(row) for row in messages],
            "created_at": self._aware_datetime(conversation.created_at).isoformat(),
            "updated_at": self._aware_datetime(conversation.updated_at).isoformat(),
        }

    @staticmethod
    def _decision_message_response(message: DecisionMessage) -> dict[str, Any]:
        return {
            "message_id": message.message_id,
            "sequence": message.sequence,
            "role": message.role,
            "status": message.status,
            "content": message.content,
            "reference_ids": list(message.reference_ids or []),
            "proposed_changes": dict(message.proposed_changes) if message.proposed_changes else None,
            "decision_intent_id": message.decision_intent_id,
            "reply_to_message_id": message.reply_to_message_id,
            "provider": message.provider,
            "model_id": message.model_id,
            "prompt_version": message.prompt_version,
            "attempts": message.attempts,
            "error_code": message.error_code,
            "error_message": message.error_message,
            "created_at": BackendService._aware_datetime(message.created_at).isoformat(),
        }

    def reserve_policy_retry(self, task_id: str, *, graph_run_id: str, task_revision: int,
                             max_attempts: int, payload: dict) -> bool:
        """Reserve a durable graph-wide retry BEFORE external IO (including replay)."""
        with self.session_factory.begin() as session:
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError('task_not_found', 'Task was not found.')
            if task.current_revision != task_revision or task.current_graph_run_id != graph_run_id:
                raise ConflictError('investigation_input_changed', 'Policy retry inputs are stale.')
            attempts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id, WorkflowArtifact.graph_run_id == graph_run_id,
                WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT',
            )).all()
            if len(attempts) >= max_attempts:
                return False
            session.add(WorkflowArtifact(
                artifact_id=new_id('artifact'), task_id=task_id, task_revision=task_revision,
                graph_run_id=graph_run_id, artifact_type='POLICY_RETRY_ATTEMPT',
                payload=payload, content_sha256=content_hash(payload), schema_version='policy-investigation/1.0.0',
            ))
        return True

    def list_investigations(self, task_id: str) -> list[dict[str, Any]]:
        """Latest public snapshot per case; historical records never become authority."""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            artifacts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id,
                WorkflowArtifact.artifact_type == "INVESTIGATION_CASE",
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
            records = {}
            for artifact in artifacts:
                case_id = artifact.payload["case_id"]
                if case_id in records:
                    continue
                current = artifact.graph_run_id == task.current_graph_run_id and artifact.task_revision == task.current_revision
                records[case_id] = dict(artifact.payload) | {
                    "artifact_id": artifact.artifact_id, "is_current": current,
                    "stored_status": artifact.payload["status"],
                    "status": artifact.payload["status"] if current else "STALE",
                    "stop_reason": artifact.payload["stop_reason"] if current else "INPUT_CHANGED",
                }
            return list(records.values())

    def review_confirmations_for_batch(self, batch_artifact_id: str):
        """Recover human decisions for these exact candidate versions, including
        confirmations embedded in carried review envelopes. Artifact columns in
        older correction runs can be null; the typed payload owns the identity.
        """
        from supplier_comparison.extraction.review_contracts import ReviewEvent
        from supplier_comparison.extraction.submission import _review_event_matches_current

        with self.session_factory() as session:
            artifact = session.get(WorkflowArtifact, batch_artifact_id)
            if artifact is None or artifact.artifact_type != "EXTRACTION_BATCH":
                return ()
            batch = ExtractionBatch.model_validate(artifact.payload)
            candidates = {c.field_name: c for c in batch.candidates}
            records = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == artifact.task_id,
                WorkflowArtifact.quote_id == artifact.quote_id,
                WorkflowArtifact.artifact_type.in_(("REVIEW_EVENT", "REVIEW_ENVELOPE")),
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
            matched = {}
            for record in records:
                payloads = ([record.payload] if record.artifact_type == "REVIEW_EVENT"
                            else record.payload.get("review_events", []))
                for payload in payloads:
                    event = ReviewEvent.model_validate(payload)
                    candidate = candidates.get(event.field_name)
                    if (candidate is not None and event.field_name not in matched
                            and _review_event_matches_current(batch, candidate, event)):
                        matched[event.field_name] = event
            return tuple(matched.values())

    def correction_event_payloads_for_batch(
        self, batch_artifact_id: str
    ) -> list[dict[str, Any]]:
        from supplier_comparison.extraction.submission import _correction_matches_current

        with self.session_factory() as session:
            batch_artifact = session.get(WorkflowArtifact, batch_artifact_id)
            if batch_artifact is None or batch_artifact.artifact_type != "EXTRACTION_BATCH":
                raise NotFoundError(
                    "extraction_batch_artifact_not_found",
                    "Extraction batch artifact was not found.",
                )
            batch = ExtractionBatch.model_validate(batch_artifact.payload)
            candidates = {c.field_name: c for c in batch.candidates}
            artifacts = session.scalars(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.task_id == batch_artifact.task_id,
                    WorkflowArtifact.quote_id == batch_artifact.quote_id,
                    WorkflowArtifact.artifact_type.in_(("CORRECTION_EVENT", "REVIEW_ENVELOPE")),
                )
                .order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())
            ).all()
            matched = {}
            # Graph/task revisions can change without changing a quotation.
            # Match the complete immutable field/source identity, not nullable
            # artifact columns or only the most recent graph's audit records.
            for artifact in artifacts:
                payloads = ([artifact.payload] if artifact.artifact_type == 'CORRECTION_EVENT'
                            else artifact.payload.get('corrections', []))
                for payload in payloads:
                    event = CorrectionEvent.model_validate(payload)
                    candidate = candidates.get(event.field_name)
                    if (candidate is not None and event.field_name not in matched
                            and _correction_matches_current(batch, candidate, event)):
                        matched[event.field_name] = event.model_dump(mode='json')
            return list(matched.values())

    def correct_field(self, *, task_id: str, quote_id: str, field_name: str,
                      expected_task_revision: int, raw_value: str,
                      normalized_value: str | int | bool, unit: str | None,
                      reason: str, idempotency_key: str) -> dict[str, Any]:
        return self.correct_fields(task_id=task_id, expected_task_revision=expected_task_revision,
            corrections=[dict(quote_id=quote_id, field_name=field_name, raw_value=raw_value,
                              normalized_value=normalized_value, unit=unit, reason=reason)],
            idempotency_key=idempotency_key, _single=True)

    def correct_fields(self, *, task_id: str, expected_task_revision: int,
                       corrections: list[dict[str, Any]], idempotency_key: str,
                       _single: bool = False) -> dict[str, Any]:
        from .models import utc_now

        if not corrections or len(corrections) > 100:
            raise BackendError("field_correction_invalid", "Submit between 1 and 100 corrections.")
        pairs = [(c.get("quote_id"), c.get("field_name")) for c in corrections]
        if any(not q or not n for q, n in pairs) or len(set(pairs)) != len(pairs):
            raise BackendError("field_correction_invalid", "Duplicate or empty correction fields.")
        request = {"task_id": task_id, "expected_task_revision": expected_task_revision,
                   "corrections": corrections}
        if _single:
            request = {"task_id": task_id, "expected_task_revision": expected_task_revision, **corrections[0]}
        request_sha = content_hash(request)
        operation = (f"correct_field:{task_id}:{pairs[0][0]}:{pairs[0][1]}" if _single
                     else f"correct_fields:{task_id}")
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("quote_not_found", "Quote was not found.")
            for quote_id, _field in pairs:
                quote = session.get(Quote, quote_id)
                if quote is None or quote.task_id != task_id or not quote.active:
                    raise NotFoundError("quote_not_found", "Active quote was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            latest_batches = {}
            current_documents = {}
            active_quotes = session.scalars(
                select(Quote).where(Quote.task_id == task_id, Quote.active.is_(True))
            ).all()
            for active_quote in active_quotes:
                execution = session.scalar(
                    select(DocumentExecution)
                    .join(Document, Document.document_id == DocumentExecution.document_id)
                    .where(
                        DocumentExecution.graph_run_id == task.current_graph_run_id,
                        Document.task_id == task_id,
                        Document.quote_id == active_quote.quote_id,
                        Document.quote_version == active_quote.current_version,
                    )
                    .order_by(DocumentExecution.document_execution_id)
                )
                artifact = (
                    session.get(WorkflowArtifact, execution.batch_artifact_id)
                    if execution is not None and execution.batch_artifact_id
                    else None
                )
                if artifact is None:
                    previous_batch = session.scalar(
                        select(WorkflowArtifact.artifact_id).where(
                            WorkflowArtifact.task_id == task_id,
                            WorkflowArtifact.quote_id == active_quote.quote_id,
                            WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                        )
                    )
                    if previous_batch is not None:
                        raise ConflictError(
                            "extraction_batch_stale",
                            "Re-extract the current quote before correction.",
                            quote_id=active_quote.quote_id,
                        )
                    raise ConflictError(
                        "extraction_batch_missing",
                        "Every active quote must have an extraction batch before correction.",
                        quote_id=active_quote.quote_id,
                    )
                parsed = ExtractionBatch.model_validate(artifact.payload).parsed_input
                document = session.get(Document, parsed.context.document_id)
                if (
                    parsed.context.task_id != task_id
                    or parsed.context.quote_id != active_quote.quote_id
                    or parsed.context.quote_version != active_quote.current_version
                    or document is None or document.task_id != task_id
                    or document.quote_id != active_quote.quote_id
                    or document.quote_version != active_quote.current_version
                    or parsed.context.document_version != document.document_version
                    or parsed.document_sha256 != document.sha256
                ):
                    raise ConflictError("extraction_batch_stale", "Re-extract the current quote before correction.", quote_id=active_quote.quote_id)
                latest_batches[active_quote.quote_id] = artifact
                current_documents[active_quote.quote_id] = document
            reviewed_at = utc_now()
            corrected_batches = {}
            events = []
            errors = []
            for item in corrections:
                quote_id = item["quote_id"]
                source_artifact = latest_batches[quote_id]
                source_batch = corrected_batches.get(quote_id) or ExtractionBatch.model_validate(source_artifact.payload)
                candidate = next((c for c in source_batch.candidates if c.field_name == item["field_name"]), None)
                if "expected_field_version" in item and (
                    candidate is None or candidate.field_version != item["expected_field_version"]
                ):
                    errors.append({"quote_id": quote_id, "field_name": item["field_name"],
                                   "code": "field_version_conflict", "actual": candidate.field_version if candidate else None})
                    continue
                try:
                    corrected_batch, correction = apply_candidate_correction(
                        source_batch, field_name=item["field_name"], action=CorrectionAction.USER_CORRECTION,
                        raw_value=item["raw_value"], normalized_value=item["normalized_value"],
                        unit=item.get("unit"), reason_code="AUTHORIZED_FIELD_CORRECTION",
                        reason=item["reason"], reviewer_id=self.actor_id, reviewed_at=reviewed_at)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append({"quote_id": quote_id, "field_name": item["field_name"],
                                   "code": "field_correction_invalid"})
                    continue
                corrected_batches[quote_id] = corrected_batch
                events.append((quote_id, correction))
            if errors:
                if _single and errors[0]["code"] == "field_correction_invalid":
                    raise BackendError("field_correction_invalid", "Field correction is invalid.",
                                       quote_id=errors[0]["quote_id"], field_name=errors[0]["field_name"])
                error_type = ConflictError if any(e["code"] == "field_version_conflict" for e in errors) else BackendError
                raise error_type("field_correction_batch_invalid", "No corrections were saved; resolve all reported errors.", errors=errors)
            next_revision = task.current_revision + 1
            old_graph = (
                session.get(GraphRun, task.current_graph_run_id)
                if task.current_graph_run_id
                else None
            )
            self._supersede_current_graph(session, task)
            graph_run_id = new_id("graph")
            job_id = new_id("job")
            graph = GraphRun(
                graph_run_id=graph_run_id,
                task_id=task_id,
                thread_id=graph_run_id,
                started_revision=next_revision,
                effective_revision=next_revision,
                status="PENDING",
                provider=old_graph.provider if old_graph is not None else None,
                model_id=old_graph.model_id if old_graph is not None else None,
                environment=old_graph.environment if old_graph is not None else None,
                prompt_version=(
                    old_graph.prompt_version if old_graph is not None else None
                ),
            )
            session.add(graph)
            # PostgreSQL enforces the DocumentExecution -> GraphRun foreign key
            # immediately. Persist the parent before a later query-triggered
            # autoflush attempts to insert the carried document executions.
            session.flush()
            parents = {qid: artifact.artifact_id for qid, artifact in latest_batches.items()}
            for quote_id, correction in events:
                payload = correction.model_dump(mode="json")
                artifact = WorkflowArtifact(artifact_id=new_id("artifact"), task_id=task_id,
                    task_revision=next_revision, artifact_type="CORRECTION_EVENT", quote_id=quote_id,
                    graph_run_id=graph_run_id, parent_artifact_id=parents[quote_id],
                    payload=payload, content_sha256=content_hash(payload))
                session.add(artifact)
                parents[quote_id] = artifact.artifact_id
            for quote_id, corrected_batch in corrected_batches.items():
                payload = corrected_batch.model_dump(mode="json")
                artifact = WorkflowArtifact(artifact_id=new_id("artifact"), task_id=task_id,
                    task_revision=next_revision, artifact_type="EXTRACTION_BATCH",
                    schema_version=corrected_batch.schema_version, quote_id=quote_id,
                    graph_run_id=graph_run_id, parent_artifact_id=parents[quote_id],
                    payload=payload, content_sha256=content_hash(payload))
                session.add(artifact)
                latest_batches[quote_id] = artifact
            # Carry only the document version that produced each current batch.
            # Binding every historical document for a quote to the new graph makes
            # the next review read stale quote versions and can create duplicate,
            # contradictory review cards.
            for quote_id, document in current_documents.items():
                selected_batch = latest_batches[quote_id]
                session.add(
                    DocumentExecution(
                        document_execution_id=new_id("docexec"),
                        graph_run_id=graph_run_id,
                        document_id=document.document_id,
                        status="EXTRACTED",
                        calls_used=0,
                        max_calls=8,
                        batch_artifact_id=selected_batch.artifact_id,
                    )
                )
            task.current_revision = next_revision
            history_binding = self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=next_revision - 1,
                to_revision=next_revision,
            )
            # This binding is referenced by both the persisted graph and the
            # queued job. Flush it first so strict FK databases never attempt
            # the graph update before inserting its new parent row.
            if history_binding is not None:
                session.flush()
            graph.history_binding_id = (
                history_binding.history_binding_id if history_binding else None
            )
            task.current_graph_run_id = graph_run_id
            task.current_snapshot_id = None
            task.current_result_id = None
            task.status = "QUEUED"
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=next_revision,
                    change_type=(f"FIELD_CORRECTED:{pairs[0][1]}" if _single else "FIELDS_CORRECTED_BATCH"),
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                )
            )
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph_run_id,
                    job_type="START",
                    status="PENDING",
                    task_revision=next_revision,
                    history_binding_id=(
                        history_binding.history_binding_id if history_binding else None
                    ),
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_type": "START",
                "job_status": "PENDING",
                "correction_count": len(corrections),
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def upload_quote(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        supplier_id: str,
        original_filename: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        is_synthetic: bool = False,
    ) -> dict[str, Any]:
        return self.upload_quote_stream(
            task_id,
            expected_task_revision=expected_task_revision,
            supplier_id=supplier_id,
            original_filename=original_filename,
            media_type=media_type,
            stream=BytesIO(content),
            idempotency_key=idempotency_key,
            is_synthetic=is_synthetic,
        )

    def upload_quote_stream(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        supplier_id: str,
        original_filename: str,
        media_type: str,
        stream: BinaryIO,
        idempotency_key: str,
        is_synthetic: bool = False,
        max_bytes: int = 5 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> dict[str, Any]:
        if media_type not in {"application/pdf", "text/csv"}:
            raise BackendError(
                "unsupported_media_type",
                "Only application/pdf and text/csv quote files are supported.",
            )
        if max_bytes < 1 or chunk_size < 1:
            raise ValueError("upload limits must be positive")

        staging_directory = self.storage_root / ".staging"
        staging_directory.mkdir(parents=True, exist_ok=True)
        staged_path = staging_directory / f"{new_id('upload')}.tmp"
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with staged_path.open("xb") as handle:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise BackendError(
                            "upload_stream_invalid",
                            "Quote upload stream must produce bytes.",
                        )
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise BackendError(
                            "file_too_large",
                            "Quote file exceeds the 5 MiB limit.",
                            max_file_size_bytes=max_bytes,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        except Exception:
            if staged_path.exists():
                staged_path.unlink()
            raise

        file_sha = digest.hexdigest()
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "supplier_id": supplier_id,
            "original_filename": original_filename,
            "media_type": media_type,
            "content_sha256": file_sha,
            "is_synthetic": is_synthetic,
        }
        request_sha = content_hash(request)
        operation = f"upload_quote:{task_id}"
        final_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(
                    select(Task).where(Task.task_id == task_id).with_for_update()
                )
                if task is None or task.owner_id != self.actor_id:
                    raise NotFoundError("task_not_found", "Task was not found.")
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                if task.current_revision != expected_task_revision:
                    raise ConflictError(
                        "task_revision_conflict",
                        "Task revision has changed.",
                        expected=expected_task_revision,
                        actual=task.current_revision,
                    )
                self._raise_if_duplicate_quote(session, task_id, file_sha)
                self._supersede_current_graph(session, task)
                quote_id = new_id("quote")
                document_id = new_id("doc")
                extension = ".pdf" if media_type == "application/pdf" else ".csv"
                final_path = (
                    self.storage_root
                    / task_id
                    / quote_id
                    / "v1"
                    / document_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if final_path.exists():
                    raise ConflictError(
                        "immutable_storage_conflict",
                        "Generated quote storage location already exists.",
                    )
                staged_path.rename(final_path)
                session.add(
                    Quote(
                        quote_id=quote_id,
                        task_id=task_id,
                        supplier_id=supplier_id,
                        current_version=1,
                    )
                )
                session.flush()
                session.add(
                    Document(
                        document_id=document_id,
                        task_id=task_id,
                        quote_id=quote_id,
                        quote_version=1,
                        document_version=1,
                        original_filename=Path(original_filename).name,
                        media_type=media_type,
                        size_bytes=size_bytes,
                        sha256=file_sha,
                        storage_path=str(final_path),
                        is_synthetic=is_synthetic,
                    )
                )
                task.current_revision += 1
                self._copy_history_binding(
                    session,
                    task_id=task_id,
                    from_revision=task.current_revision - 1,
                    to_revision=task.current_revision,
                )
                task.status = "DRAFT"
                session.add(
                    TaskRevision(
                        revision_id=new_id("rev"),
                        task_id=task_id,
                        revision=task.current_revision,
                        change_type="QUOTE_UPLOADED",
                        actor_id=self.actor_id,
                        request_sha256=request_sha,
                        details={
                            "quote_id": quote_id,
                            "supplier_id": supplier_id,
                            "original_filename": Path(original_filename).name,
                            "document_sha256": file_sha,
                        },
                    )
                )
                response = {
                    "task_id": task_id,
                    "task_revision": task.current_revision,
                    "quote_id": quote_id,
                    "quote_version": 1,
                    "document_id": document_id,
                    "document_version": 1,
                    "document_sha256": file_sha,
                    "storage_path": str(final_path),
                }
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=201,
                    response=response,
                )
                return response
        except Exception:
            if final_path is not None and final_path.exists():
                final_path.unlink()
            raise
        finally:
            if staged_path.exists():
                staged_path.unlink()

    def append_artifact(
        self,
        *,
        task_id: str,
        task_revision: int,
        artifact_type: str,
        payload: dict[str, Any],
        schema_version: str | None = None,
        parent_artifact_id: str | None = None,
        quote_id: str | None = None,
        document_id: str | None = None,
        graph_run_id: str | None = None,
    ) -> dict[str, Any]:
        artifact_id = new_id("artifact")
        payload_sha = content_hash(payload)
        with self.session_factory.begin() as session:
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            # Windows can produce equal clock ticks for successive saves. Random
            # artifact UUIDs must not decide which investigation snapshot is latest.
            latest_stamp = session.scalar(select(WorkflowArtifact.created_at).where(
                WorkflowArtifact.task_id == task_id,
            ).order_by(WorkflowArtifact.created_at.desc()).limit(1))
            stamp = datetime.now(timezone.utc)
            if latest_stamp is not None:
                latest_stamp = latest_stamp.replace(tzinfo=timezone.utc) if latest_stamp.tzinfo is None else latest_stamp
                if latest_stamp >= stamp:
                    stamp = latest_stamp + timedelta(microseconds=1)
            session.add(
                WorkflowArtifact(
                    artifact_id=artifact_id,
                    created_at=stamp,
                    task_id=task_id,
                    task_revision=task_revision,
                    artifact_type=artifact_type,
                    schema_version=schema_version,
                    parent_artifact_id=parent_artifact_id,
                    quote_id=quote_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    payload=payload,
                    content_sha256=payload_sha,
                )
            )
        return {"artifact_id": artifact_id, "content_sha256": payload_sha}

    def refresh_supplier_history(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        dataset_version: str,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "dataset_version": dataset_version,
        }
        request_sha = content_hash(request)
        operation = f"refresh_supplier_history:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.status == "ABANDONED":
                raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
            self._require_revision(task, expected_task_revision)
            requirement_record = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if requirement_record is None:
                raise ConflictError(
                    "requirement_missing", "The task has no procurement requirement."
                )
            old_binding = self._binding_at_revision(
                session, task_id, task.current_revision
            )
            next_revision = task.current_revision + 1
            new_binding = self._new_history_binding(
                task_id=task_id,
                task_revision=next_revision,
                requirement=ProcurementRequirement.model_validate(
                    requirement_record.payload
                ),
                dataset_version=dataset_version,
            )
            if (
                old_binding is not None
                and old_binding.dataset_version == new_binding.dataset_version
                and old_binding.content_sha256 == new_binding.content_sha256
                and old_binding.manifest_sha256 == new_binding.manifest_sha256
            ):
                raise BackendError(
                    "supplier_history_unchanged",
                    "The requested supplier history version is already bound.",
                )
            self._supersede_current_graph(session, task)
            session.add(new_binding)
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=next_revision,
                    change_type="SUPPLIER_HISTORY_REFRESHED",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                    details={
                        "old_dataset_version": (
                            old_binding.dataset_version if old_binding else None
                        ),
                        "old_content_sha256": (
                            old_binding.content_sha256 if old_binding else None
                        ),
                        "new_dataset_version": new_binding.dataset_version,
                        "new_content_sha256": new_binding.content_sha256,
                        "new_manifest_sha256": new_binding.manifest_sha256,
                    },
                )
            )
            task.current_revision = next_revision
            active_documents = session.scalars(
                select(Document)
                .join(Quote, Quote.quote_id == Document.quote_id)
                .where(
                    Document.task_id == task_id,
                    Quote.active.is_(True),
                    Document.quote_version == Quote.current_version,
                )
            ).all()
            graph_run_id = job_id = None
            if active_documents:
                graph_run_id, job_id = new_id("graph"), new_id("job")
                session.add(
                    GraphRun(
                        graph_run_id=graph_run_id,
                        task_id=task_id,
                        thread_id=graph_run_id,
                        started_revision=next_revision,
                        effective_revision=next_revision,
                        history_binding_id=new_binding.history_binding_id,
                        status="PENDING",
                        provider=provider,
                        model_id=model_id,
                        environment=environment,
                        prompt_version=prompt_version,
                    )
                )
                session.flush()
                session.add(
                    Job(
                        job_id=job_id,
                        task_id=task_id,
                        graph_run_id=graph_run_id,
                        job_type="START",
                        status="PENDING",
                        task_revision=next_revision,
                        history_binding_id=new_binding.history_binding_id,
                    )
                )
                task.current_graph_run_id = graph_run_id
                task.status = "QUEUED"
            else:
                task.status = "DRAFT"
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "status": task.status,
                "supplier_history_binding": self._history_binding_response(
                    new_binding
                ),
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_status": "PENDING" if job_id else None,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def _seed_submitted_extractions(
        self, session, *, task_id, graph_run_id, provider, model_id,
        environment, prompt_version,
    ) -> None:
        """Reuse immutable submitted input; re-review with current corrections and rules.

        Both scenario application and explicit reruns must use this same boundary.
        Never reuse model sessions across provider/configuration or file changes.
        """
        dictionary_sha = hashlib.sha256(self.quote_dictionary_path.read_bytes()).hexdigest()
        dictionary_version = QuoteDictionary.load(self.quote_dictionary_path).version
        documents = session.scalars(select(Document).join(
            Quote, Quote.quote_id == Document.quote_id
        ).where(
            Document.task_id == task_id, Quote.active.is_(True),
            Document.quote_version == Quote.current_version,
        )).all()
        for document in documents:
            # Prefer the most recent reviewed workflow batch, which may contain
            # centralized human corrections made after the original submission.
            prior = session.scalar(select(DocumentExecution).join(
                GraphRun, GraphRun.graph_run_id == DocumentExecution.graph_run_id
            ).where(
                DocumentExecution.document_id == document.document_id,
                DocumentExecution.graph_run_id != graph_run_id,
                DocumentExecution.status == "REVIEWED",
                DocumentExecution.batch_artifact_id.is_not(None),
                GraphRun.task_id == task_id,
                GraphRun.provider == provider, GraphRun.model_id == model_id,
                GraphRun.environment == environment, GraphRun.prompt_version == prompt_version,
            ).order_by(GraphRun.effective_revision.desc(), GraphRun.created_at.desc()))
            if prior is not None:
                artifact = session.get(WorkflowArtifact, prior.batch_artifact_id)
                batch = ExtractionBatch.model_validate(artifact.payload) if artifact else None
                if (batch is not None and batch.dictionary_version == dictionary_version
                        and batch.parsed_input.document_sha256 == document.sha256
                        and batch.parsed_input.context.document_id == document.document_id):
                    session.add(DocumentExecution(
                        document_execution_id=new_id("docexec"), graph_run_id=graph_run_id,
                        document_id=document.document_id, status="EXTRACTED",
                        calls_used=prior.calls_used, max_calls=prior.max_calls,
                        parsed_artifact_id=prior.parsed_artifact_id,
                        batch_artifact_id=prior.batch_artifact_id,
                    ))
                    continue
            draft = session.scalar(select(QuoteDraft).where(
                QuoteDraft.task_id == task_id,
                QuoteDraft.proposed_document_id == document.document_id,
                QuoteDraft.status == "SUBMITTED",
                QuoteDraft.sha256 == document.sha256,
                QuoteDraft.provider == provider, QuoteDraft.model_id == model_id,
                QuoteDraft.environment == environment,
                QuoteDraft.prompt_version == prompt_version,
                QuoteDraft.dictionary_sha256 == dictionary_sha,
                QuoteDraft.batch_artifact_id.is_not(None),
            ))
            if draft is not None:
                session.add(DocumentExecution(
                    document_execution_id=new_id("docexec"),
                    graph_run_id=graph_run_id, document_id=document.document_id,
                    status="EXTRACTED", calls_used=draft.calls_used,
                    max_calls=draft.max_calls, parsed_artifact_id=draft.parsed_artifact_id,
                    batch_artifact_id=draft.batch_artifact_id, review_artifact_id=None,
                ))

    def start_run(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "provider": provider,
            "model_id": model_id,
            "environment": environment,
            "prompt_version": prompt_version,
        }
        request_sha = content_hash(request)
        operation = f"start_run:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            if task.current_graph_run_id is not None:
                current = session.get(GraphRun, task.current_graph_run_id)
                if current is not None and current.status in {
                    "PENDING",
                    "RUNNING",
                }:
                    raise ConflictError(
                        "graph_run_active", "The task already has an active graph run."
                    )
                if current is not None and current.status == "INTERRUPTED":
                    # A run waiting for user input is not actively computing. Allow an
                    # explicit rerun to replace it so updated rules or source data can
                    # clear an obsolete review interruption.
                    self._supersede_current_graph(session, task)
            history_binding = self._binding_at_revision(
                session, task_id, task.current_revision
            )
            if history_binding is None:
                requirement_record = session.scalar(
                    select(RequirementRecord)
                    .where(RequirementRecord.task_id == task_id)
                    .order_by(RequirementRecord.requirement_version.desc())
                )
                if requirement_record is None:
                    raise ConflictError(
                        "requirement_missing", "The task has no procurement requirement."
                    )
                history_binding = self._new_history_binding(
                    task_id=task_id,
                    task_revision=task.current_revision,
                    requirement=ProcurementRequirement.model_validate(
                        requirement_record.payload
                    ),
                )
                session.add(history_binding)
            graph_run_id = new_id("graph")
            job_id = new_id("job")
            session.add(
                GraphRun(
                    graph_run_id=graph_run_id,
                    task_id=task_id,
                    thread_id=graph_run_id,
                    started_revision=task.current_revision,
                    effective_revision=task.current_revision,
                    history_binding_id=history_binding.history_binding_id,
                    status="PENDING",
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                )
            )
            session.flush()
            self._seed_submitted_extractions(
                session, task_id=task_id, graph_run_id=graph_run_id,
                provider=provider, model_id=model_id, environment=environment,
                prompt_version=prompt_version,
            )
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph_run_id,
                    job_type="START",
                    status="PENDING",
                    task_revision=task.current_revision,
                    history_binding_id=history_binding.history_binding_id,
                )
            )
            task.current_graph_run_id = graph_run_id
            task.status = "QUEUED"
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_type": "START",
                "job_status": "PENDING",
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def retry_failed_resume_job(
        self,
        task_id: str,
        job_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Requeue a failed RESUME job without starting a new graph or model run."""

        request = {
            "task_id": task_id,
            "job_id": job_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"retry_resume_job:{job_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            if job is None or job.task_id != task_id:
                raise NotFoundError("job_not_found", "Job was not found.")
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if graph is None or task.current_graph_run_id != graph.graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "The job belongs to a superseded graph run."
                )
            self._require_revision(task, expected_task_revision)
            if job.job_type != "RESUME":
                raise ConflictError(
                    "job_retry_unsupported",
                    "Only a failed resume job can be retried from its checkpoint.",
                )
            if job.status != "FAILED":
                raise ConflictError("job_not_failed", "Job is not failed.")
            if job.attempts >= 3:
                raise BackendError(
                    "job_attempt_budget_exceeded", "Job attempt budget was exceeded."
                )
            job.status = "PENDING"
            job.error_code = None
            job.error_message = None
            job.started_at = None
            job.finished_at = None
            graph.status = "PENDING"
            task.status = "QUEUED"
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph.graph_run_id,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job_status": job.status,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def ensure_document_execution(
        self,
        *,
        graph_run_id: str,
        document_id: str,
        max_calls: int = 8,
    ) -> dict[str, Any]:
        if max_calls < 1:
            raise BackendError(
                "model_call_budget_invalid", "Model call budget must be positive."
            )
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(DocumentExecution).where(
                    DocumentExecution.graph_run_id == graph_run_id,
                    DocumentExecution.document_id == document_id,
                )
            )
            if existing is None:
                if session.get(GraphRun, graph_run_id) is None:
                    raise NotFoundError("graph_run_not_found", "Graph run was not found.")
                if session.get(Document, document_id) is None:
                    raise NotFoundError("document_not_found", "Document was not found.")
                existing = DocumentExecution(
                    document_execution_id=new_id("docexec"),
                    graph_run_id=graph_run_id,
                    document_id=document_id,
                    status="PENDING",
                    calls_used=0,
                    max_calls=max_calls,
                )
                session.add(existing)
                session.flush()
            return self._document_execution_response(existing)

    def record_document_calls(
        self,
        document_execution_id: str,
        *,
        expected_calls_used: int,
        calls_after: int,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            execution = session.get(DocumentExecution, document_execution_id)
            if execution is None:
                raise NotFoundError(
                    "document_execution_not_found", "Document execution was not found."
                )
            if execution.calls_used != expected_calls_used:
                raise ConflictError(
                    "model_call_count_conflict",
                    "Stored model call count has changed.",
                    expected=expected_calls_used,
                    actual=execution.calls_used,
                )
            if calls_after < expected_calls_used or calls_after > execution.max_calls:
                raise BackendError(
                    "model_call_budget_exceeded",
                    "Document model call budget was exceeded.",
                    calls_after=calls_after,
                    max_calls=execution.max_calls,
                )
            execution.calls_used = calls_after
            return self._document_execution_response(execution)

    def open_issue(
        self,
        *,
        task_id: str,
        graph_run_id: str,
        task_revision: int,
        issue_type: str,
        quote_id: str | None,
        field_name: str | None,
        question: str,
        answer_schema: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == graph_run_id)
                .with_for_update()
            )
            if graph is None or task is None or graph.task_id != task_id:
                raise NotFoundError("graph_run_not_found", "Graph run was not found.")
            if task.current_graph_run_id != graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "Graph run was superseded."
                )
            existing = session.scalar(
                select(Issue).where(
                    Issue.graph_run_id == graph_run_id,
                    Issue.issue_type == issue_type,
                    Issue.quote_id == quote_id,
                    Issue.field_name == field_name,
                )
            )
            if existing is not None:
                return self._issue_response(existing)
            if graph.effective_revision != task_revision:
                raise ConflictError(
                    "graph_revision_conflict",
                    "Graph run revision has changed.",
                    expected=task_revision,
                    actual=graph.effective_revision,
                )
            issue = Issue(
                issue_id=new_id("issue"),
                task_id=task_id,
                graph_run_id=graph_run_id,
                quote_id=quote_id,
                field_name=field_name,
                issue_type=issue_type,
                status="OPEN",
                question=question,
                answer_schema=answer_schema,
                created_revision=task_revision,
                created_by=self.actor_id,
            )
            session.add(issue)
            graph.status = "INTERRUPTED"
            graph.current_interrupt_issue_id = issue.issue_id
            task.status = "NEEDS_INPUT"
            session.flush()
            return self._issue_response(issue)

    def answer_issue(
        self,
        task_id: str,
        issue_id: str,
        *,
        expected_task_revision: int,
        answer: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "issue_id": issue_id,
            "expected_task_revision": expected_task_revision,
            "answer": answer,
        }
        request_sha = content_hash(request)
        operation = f"answer_issue:{issue_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            issue = session.get(Issue, issue_id)
            if issue is None or issue.task_id != task_id:
                raise NotFoundError("issue_not_found", "Issue was not found.")
            if issue.status != "OPEN":
                raise ConflictError("issue_not_open", "Issue is no longer open.")
            if issue.issue_type == "BATCH_FIELD_REVIEW":
                raise BackendError("batch_review_requires_correction", "Use the versioned batch field correction endpoint.")
            expected_answer_type = issue.answer_schema.get("answer_type")
            if answer.get("answer_type") != expected_answer_type:
                raise BackendError(
                    "answer_type_invalid",
                    "Answer type does not match the issue schema.",
                    expected=expected_answer_type,
                )
            expected_currency = issue.answer_schema.get("currency")
            if (
                expected_answer_type == "SHIPPING_AMOUNT"
                and expected_currency is not None
                and answer.get("currency") != expected_currency
            ):
                raise BackendError(
                    "answer_currency_invalid",
                    "Answer currency does not match the procurement requirement.",
                    expected=expected_currency,
                    actual=answer.get("currency"),
                )
            if expected_answer_type == "PAYMENT_INFORMATION":
                if answer.get("payment_start_event") not in {"INVOICE_DATE"}:
                    raise BackendError(
                        "payment_start_event_invalid",
                        "Payment start event is outside the supported deterministic contract.",
                    )
                if not str(answer.get("note") or "").strip() or answer.get("source_type") not in {
                    "SUPPLIER_CONFIRMATION", "DOCUMENT_CLARIFICATION", "USER_INPUT"
                }:
                    raise BackendError(
                        "payment_information_invalid",
                        "Payment information requires an audited note and source type.",
                    )
            graph = session.get(GraphRun, issue.graph_run_id)
            if graph is None or task.current_graph_run_id != graph.graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "The issue belongs to a superseded graph run."
                )
            task.current_revision += 1
            history_binding = self._copy_history_binding(
                session,
                task_id=task_id,
                from_revision=task.current_revision - 1,
                to_revision=task.current_revision,
            )
            task.status = "QUEUED"
            task.current_snapshot_id = None
            task.current_result_id = None
            graph.effective_revision = task.current_revision
            if history_binding is not None:
                session.flush([history_binding])
            graph.history_binding_id = (
                history_binding.history_binding_id if history_binding else None
            )
            graph.status = "PENDING"
            graph.current_interrupt_issue_id = None
            issue.status = "RESOLVED"
            issue.answer_payload = answer
            issue.resolved_revision = task.current_revision
            issue.answered_by = self.actor_id
            from .models import utc_now

            issue.answered_at = utc_now()
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=task.current_revision,
                    change_type=f"ISSUE_ANSWERED:{issue.issue_type}",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                )
            )
            job_id = new_id("job")
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph.graph_run_id,
                    issue_id=issue.issue_id,
                    job_type="RESUME",
                    status="PENDING",
                    task_revision=task.current_revision,
                    history_binding_id=(
                        history_binding.history_binding_id if history_binding else None
                    ),
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph.graph_run_id,
                "job_id": job_id,
                "job_type": "RESUME",
                "job_status": "PENDING",
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def _owned_quote_draft(
        self,
        session: Session,
        task_id: str,
        draft_id: str,
        *,
        lock: bool = False,
    ) -> QuoteDraft:
        query = select(QuoteDraft).where(
            QuoteDraft.quote_draft_id == draft_id,
            QuoteDraft.task_id == task_id,
            QuoteDraft.actor_id == self.actor_id,
        )
        if lock:
            query = query.with_for_update()
        draft = session.scalar(query)
        if draft is None:
            raise NotFoundError("quote_draft_not_found", "Quote draft was not found.")
        return draft

    def _quote_field_group_id(self, field_name: str) -> str | None:
        definition = self.quote_dictionary.fields.get(field_name)
        if definition is None:
            return None
        return GROUP_IDS.get(definition.field_group, definition.field_group or None)

    def _quote_review_errors(self, envelope: ReviewEnvelope) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        represented: set[str] = set()
        if envelope.review is not None:
            for finding in envelope.review.findings:
                if finding.resolved or finding.decision == FieldReviewDecision.PASS or (
                    finding.severity != ReviewSeverity.BLOCKING
                    and finding.field_name not in envelope.submission_blocking_fields
                ):
                    continue
                represented.add(finding.field_name)
                code = finding.codes[0] if finding.codes else "FIELD_REVIEW_FAILED"
                errors.append(
                    {
                        "code": code,
                        "field_names": [finding.field_name],
                        "group_id": self._quote_field_group_id(finding.field_name),
                        "message": QUOTE_REVIEW_ERROR_MESSAGES.get(
                            code,
                            finding.message,
                        ),
                    }
                )
        for field_name in envelope.submission_blocking_fields:
            if field_name in represented:
                continue
            candidate = (
                next(
                    (
                        item
                        for item in envelope.batch.candidates
                        if item.field_name == field_name
                    ),
                    None,
                )
                if envelope.batch is not None
                else None
            )
            if (
                field_name in {"shipping_fee_status", "other_fees_status"}
                and candidate is not None
                and candidate.normalized_value == "UNKNOWN"
            ):
                code = "FEE_STATUS_UNKNOWN"
            elif (
                candidate is not None
                and candidate.validation_status == ValidationStatus.MISSING
            ):
                code = "CRITICAL_FIELD_MISSING"
            elif (
                candidate is not None
                and candidate.validation_status == ValidationStatus.CONFLICT
            ):
                code = "CRITICAL_FIELD_CONFLICT"
            else:
                code = "REQUIRED_FIELD_UNAVAILABLE"
            errors.append(
                {
                    "code": code,
                    "field_names": [field_name],
                    "group_id": self._quote_field_group_id(field_name),
                    "message": QUOTE_REVIEW_ERROR_MESSAGES[code],
                }
            )
        if envelope.unconfirmed_fields:
            errors.append(
                {
                    "code": "FULL_FIELD_REVIEW_REQUIRED",
                    "field_names": list(envelope.unconfirmed_fields),
                    "group_id": None,
                    "message": "正式提交前必须核对全部 30 个报价字段。",
                }
            )
        for error in errors:
            code = error["code"]
            if code in IDENTITY_CODES:
                category, actions = "SOURCE_OR_VERSION_ERROR", ["RELOAD_OR_REPARSE"]
                next_action = "请刷新草稿版本；若仍提示文件或引用身份不一致，请重新解析原件。"
            elif code in HUMAN_RESOLVABLE_CODES:
                category, actions = "NEEDS_CONFIRMATION", ["VIEW_SOURCE", "CONFIRM_VALUE", "EDIT_VALUE", "MARK_UNKNOWN"]
                next_action = "请查看原文，核对后可直接采用当前值，也可修改或标记未知，再保存审核。"
            elif code in {"FEE_STATUS_UNKNOWN", "CRITICAL_FIELD_MISSING", "REQUIRED_FIELD_UNAVAILABLE", "NORMALIZED_VALUE_REQUIRED"}:
                category, actions = "MISSING_INFORMATION", ["EDIT_VALUE", "SAVE_REVIEW"]
                next_action = "可先保存。请依据原文或供应商确认补充；未知信息不会按零计算，相关比较暂不能完成。"
            elif code == "FULL_FIELD_REVIEW_REQUIRED":
                category, actions = "NEEDS_CONFIRMATION", ["SAVE_REVIEW"]
                next_action = "核对表单后点击保存并确认审核；有疑问的值请修改或标记未知。"
            else:
                category, actions = "INVALID_INPUT", ["EDIT_VALUE", "SAVE_REVIEW"]
                next_action = QUOTE_REVIEW_ERROR_MESSAGES.get(code, "请按字段格式和关联条件修正当前值。") + " 可以先保存，修正后再正式提交。"
            if code == "FEE_STATUS_AMOUNT_CONFLICT":
                next_action = "另有明确金额时请填写非负金额；已包含时请清空金额，避免重复计费；免费/不适用时请留空或填写 0；未知时请清空金额。可先保存。"
            error.update(category=category, next_action=next_action, actions=actions)
        return errors

    def _quote_draft_response(self, session: Session, draft: QuoteDraft) -> dict[str, Any]:
        batch_artifact = (
            session.get(WorkflowArtifact, draft.batch_artifact_id)
            if draft.batch_artifact_id
            else None
        )
        review_artifact = (
            session.get(WorkflowArtifact, draft.review_artifact_id)
            if draft.review_artifact_id
            else None
        )
        batch_payload = dict(batch_artifact.payload) if batch_artifact is not None else {}
        batch = (
            ExtractionBatch.model_validate(batch_payload)
            if batch_artifact is not None
            else None
        )
        sources = {
            source.get("source_id"): source
            for source in batch_payload.get("parsed_input", {}).get("sources", [])
        }
        review_payload = dict(review_artifact.payload) if review_artifact is not None else {}
        envelope = (
            ReviewEnvelope.model_validate(review_payload)
            if review_artifact is not None
            else None
        )
        review = envelope.review if envelope is not None else None
        always_fields = set(review.always_critical_fields) if review is not None else set()
        applicable_conditional_fields = (
            set(review.applicable_conditional_fields) if review is not None else set()
        )
        not_applicable_conditional_fields = (
            set(review.not_applicable_conditional_fields) if review is not None else set()
        )
        noncritical_fields = set(review.noncritical_fields) if review is not None else set()
        findings_by_field: dict[str, list[Any]] = {}
        if review is not None:
            for finding in review.findings:
                findings_by_field.setdefault(finding.field_name, []).append(finding)

        current_review_events = {}
        current_corrections = {}
        reviewed_action_revision = draft.revision - (
            2 if draft.status == "SUBMITTED" else 1
        )
        if envelope is not None and batch is not None:
            candidates_by_name = {
                candidate.field_name: candidate for candidate in batch.candidates
            }
            for event in envelope.review_events:
                candidate = candidates_by_name.get(event.field_name)
                if (
                    candidate is not None
                    and event.candidate_field_id == candidate.field_id
                    and event.candidate_field_version == candidate.field_version
                    and event.draft_revision == reviewed_action_revision
                ):
                    current_review_events[event.field_name] = event
            for event in envelope.corrections:
                candidate = candidates_by_name.get(event.field_name)
                if (
                    candidate is not None
                    and event.after.field_id == candidate.field_id
                    and event.after.field_version == candidate.field_version
                    and event.draft_revision == reviewed_action_revision
                ):
                    current_corrections[event.field_name] = event

        legacy_or_stale_review = bool(
            envelope is not None
            and (
                envelope.schema_version != REVIEW_SCHEMA_VERSION
                or envelope.review_policy_version != REVIEW_POLICY_VERSION
            )
        )
        envelope_unconfirmed = (
            set(envelope.unconfirmed_fields) if envelope is not None else set()
        )
        if legacy_or_stale_review and batch is not None:
            envelope_unconfirmed = {
                candidate.field_name for candidate in batch.candidates
            }

        fields: list[dict[str, Any]] = []
        reviewed_states: list[str] = []
        price_observation_ids = {
            source_id
            for observation in (select_document_unit_price(batch.parsed_input).observations if batch else ())
            for source_id in observation.source_ids
        }
        for candidate in batch_payload.get("candidates", []):
            field_name = str(candidate.get("field_name", ""))
            evidence = []
            for citation in candidate.get("source_refs", []):
                source = sources.get(citation.get("source_id"), {})
                evidence.append(
                    {
                        "source_id": citation.get("source_id"),
                        "quoted_text": citation.get("quoted_text"),
                        "kind": source.get("kind"),
                        "page_number": source.get("page_number"),
                        "row_number": source.get("row_number"),
                        "column_name": source.get("column_name"),
                        "bbox": source.get("bbox"),
                        "coordinate_space": source.get("coordinate_space"),
                    }
                )
            # Related source text is context for human review, not new evidence
            # asserted to support the selected value (e.g. superseded prices).
            cited_ids = {item["source_id"] for item in evidence}
            related_ids = {
                source_id for finding in findings_by_field.get(field_name, [])
                for source_id in finding.source_ids
            }
            if field_name == "unit_price":
                related_ids.update(price_observation_ids)
            review_evidence = [
                {
                    "source_id": source_id, "quoted_text": sources[source_id].get("raw_text"),
                    **{key: sources[source_id].get(key) for key in (
                        "kind", "page_number", "row_number", "column_name", "bbox", "coordinate_space"
                    )},
                }
                for source_id in sorted(related_ids - cited_ids) if source_id in sources
            ]
            if field_name in always_fields:
                criticality = EffectiveCriticality.ALWAYS.value
                applicable = True
            elif field_name in applicable_conditional_fields:
                criticality = EffectiveCriticality.CONDITIONAL_APPLICABLE.value
                applicable = True
            elif field_name in not_applicable_conditional_fields:
                criticality = EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE.value
                applicable = False
            elif field_name in noncritical_fields:
                criticality = EffectiveCriticality.NON_CRITICAL.value
                applicable = False
            else:
                definition = self.quote_dictionary.fields.get(field_name)
                criticality = {
                    "关键": EffectiveCriticality.ALWAYS.value,
                    "条件关键": EffectiveCriticality.CONDITIONAL_NOT_APPLICABLE.value,
                    "可选": EffectiveCriticality.NON_CRITICAL.value,
                }.get(
                    definition.required_level if definition is not None else "",
                    EffectiveCriticality.NON_CRITICAL.value,
                )
                applicable = criticality == EffectiveCriticality.ALWAYS.value

            current_correction = current_corrections.get(field_name)
            current_event = current_review_events.get(field_name)
            if field_name in envelope_unconfirmed or legacy_or_stale_review:
                review_state = "UNREVIEWED"
            elif current_correction is not None:
                review_state = (
                    "MISSING_CONFIRMED"
                    if current_correction.action == CorrectionAction.MARK_MISSING
                    else "CORRECTED"
                )
            elif current_event is not None:
                review_state = {
                    HumanReviewAction.CONFIRM_VALUE: "CONFIRMED",
                    HumanReviewAction.CONFIRM_MISSING: "MISSING_CONFIRMED",
                    HumanReviewAction.CONFIRM_CONFLICT: "CONFLICT_CONFIRMED",
                }[current_event.action]
            else:
                review_state = "UNREVIEWED"
            reviewed_states.append(review_state)

            candidate_findings = findings_by_field.get(field_name, [])
            unresolved_blocking = any(
                finding.severity == ReviewSeverity.BLOCKING and not finding.resolved
                for finding in candidate_findings
            )
            accepted_for_calculation = bool(
                any(finding.accepted_for_calculation for finding in candidate_findings)
                and not unresolved_blocking
            )
            validation_status = candidate.get("validation_status")
            if validation_status == ValidationStatus.MISSING.value:
                allowed_actions = ["CONFIRM_MISSING", "SET_VALUE"]
            elif validation_status == ValidationStatus.CONFLICT.value:
                allowed_actions = [
                    "SET_VALUE",
                    "CONFIRM_CONFLICT",
                    "MARK_MISSING",
                ]
            else:
                allowed_actions = ["CONFIRM_VALUE", "SET_VALUE", "MARK_MISSING"]
            fields.append(
                {
                    key: candidate.get(key)
                    for key in (
                        "field_id",
                        "field_name",
                        "field_version",
                        "raw_value",
                        "normalized_value",
                        "unit",
                        "validation_status",
                        "origin",
                    )
                }
                | {
                    "criticality": criticality,
                    "applicable": applicable,
                    "required_for_submission": criticality
                    in {
                        EffectiveCriticality.ALWAYS.value,
                        EffectiveCriticality.CONDITIONAL_APPLICABLE.value,
                    },
                    "review_state": review_state,
                    "accepted_for_calculation": accepted_for_calculation,
                    "allowed_actions": allowed_actions,
                    "findings": [
                        finding.model_dump(mode="json")
                        for finding in candidate_findings
                    ],
                    "evidence": evidence,
                    "review_evidence": review_evidence,
                }
            )
        job = session.scalar(
            select(Job)
            .where(Job.quote_draft_id == draft.quote_draft_id)
            .order_by(Job.created_at.desc(), Job.job_id.desc())
        )
        task = session.get(Task, draft.task_id)
        effective_status = draft.status
        if (
            task is not None
            and task.current_revision != draft.base_task_revision
            and draft.status in {"UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT"}
        ):
            effective_status = "STALE"
        if (
            effective_status == "READY_TO_SUBMIT"
            and (
                envelope is None
                or legacy_or_stale_review
                or not envelope.submission_ready
            )
        ):
            effective_status = "REVIEW_REQUIRED"

        total_fields = len(fields)
        reviewed_count = sum(state != "UNREVIEWED" for state in reviewed_states)
        effective_unconfirmed = tuple(
            sorted(
                field["field_name"]
                for field in fields
                if field["review_state"] == "UNREVIEWED"
            )
        )
        submission_ready = bool(
            envelope is not None
            and not legacy_or_stale_review
            and envelope.submission_ready
            and not effective_unconfirmed
        )
        if effective_status == "READY_TO_SUBMIT" and not submission_ready:
            effective_status = "REVIEW_REQUIRED"
        human_review_complete = bool(
            envelope is not None
            and not legacy_or_stale_review
            and envelope.human_review_complete
            and not effective_unconfirmed
        )
        calculation_ready = bool(
            envelope is not None
            and submission_ready
            and envelope.calculation_ready
        )
        submission_blocking_fields = (
            tuple(envelope.submission_blocking_fields)
            if envelope is not None
            else ()
        )
        return {
            "quote_draft_id": draft.quote_draft_id,
            "task_id": draft.task_id,
            "base_task_revision": draft.base_task_revision,
            "draft_revision": draft.revision,
            "status": effective_status,
            "proposed_quote_id": draft.proposed_quote_id,
            "replacement_quote_id": draft.replacement_quote_id,
            "proposed_document_id": draft.proposed_document_id,
            "supplier_id": draft.supplier_id,
            "original_filename": draft.original_filename,
            "media_type": draft.media_type,
            "size_bytes": draft.size_bytes,
            "document_sha256": draft.sha256,
            "is_synthetic": draft.is_synthetic,
            "schema_version": QUOTE_REVIEW_SCHEMA_VERSION,
            "review_envelope_schema_version": (
                envelope.schema_version if envelope is not None else None
            ),
            "review_policy_version": (
                envelope.review_policy_version
                if envelope is not None
                else REVIEW_POLICY_VERSION
            ),
            "human_review_complete": human_review_complete,
            "submission_ready": submission_ready,
            "calculation_ready": calculation_ready,
            "submission_blocking_fields": list(submission_blocking_fields),
            "unconfirmed_fields": list(effective_unconfirmed),
            "review_progress": {
                "total": total_fields,
                "reviewed": reviewed_count,
                "confirmed": sum(
                    state in {"CONFIRMED", "CONFLICT_CONFIRMED"}
                    for state in reviewed_states
                ),
                "corrected": sum(
                    state == "CORRECTED"
                    for state in reviewed_states
                ),
                "missing_confirmed": sum(
                    state == "MISSING_CONFIRMED"
                    for state in reviewed_states
                ),
            },
            "review_status": (
                envelope.review_status.value if envelope is not None else None
            ),
            "review_findings": (
                [finding.model_dump(mode="json") for finding in review.findings]
                if review is not None
                else []
            ),
            "review_errors": (
                self._quote_review_errors(envelope)
                if envelope is not None and not submission_ready
                else []
            ),
            "fields": fields,
            "calls_used": draft.calls_used,
            "max_calls": draft.max_calls,
            "error_code": draft.error_code,
            "error_message": draft.error_message,
            "job": (
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "job_status": job.status,
                    "attempts": job.attempts,
                    "created_at": self._aware_datetime(job.created_at).isoformat(),
                    "started_at": (
                        self._aware_datetime(job.started_at).isoformat()
                        if job.started_at is not None
                        else None
                    ),
                }
                if job is not None
                else None
            ),
            "created_at": self._aware_datetime(draft.created_at).isoformat(),
            "updated_at": self._aware_datetime(draft.updated_at).isoformat(),
            "submitted_at": (
                self._aware_datetime(draft.submitted_at).isoformat()
                if draft.submitted_at is not None
                else None
            ),
        }

    def _requirement_draft_response(self, session: Session, draft: RequirementDraft) -> dict[str, Any]:
        job = session.scalar(select(Job).where(
            Job.requirement_draft_id == draft.requirement_draft_id
        ).order_by(Job.created_at.desc(), Job.job_id.desc()))
        return {
            "requirement_draft_id": draft.requirement_draft_id,
            "draft_revision": draft.revision,
            "status": draft.status,
            "original_filename": draft.original_filename,
            "media_type": draft.media_type,
            "size_bytes": draft.size_bytes,
            "document_sha256": draft.sha256,
            "parsed": draft.parsed_payload,
            "candidates": (draft.candidates or {}).get("candidates", []),
            "calls_used": draft.calls_used,
            "max_calls": draft.max_calls,
            "provider": draft.provider,
            "model_id": draft.model_id,
            "environment": draft.environment,
            "prompt_version": draft.prompt_version,
            "error_code": draft.error_code,
            "error_message": draft.error_message,
            "submitted_task_id": draft.submitted_task_id,
            "job": ({
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job_status": job.status,
                "attempts": job.attempts,
            } if job else None),
            "created_at": self._aware_datetime(draft.created_at).isoformat(),
            "updated_at": self._aware_datetime(draft.updated_at).isoformat(),
        }

    def _summary_facts(self, session: Session, task: Task, result: WorkflowArtifact) -> dict[str, Any]:
        snapshot = (
            session.get(WorkflowArtifact, result.parent_artifact_id)
            if result.parent_artifact_id
            else None
        )
        if snapshot is not None and (
            snapshot.task_id != task.task_id
            or snapshot.artifact_type != "INPUT_SNAPSHOT"
        ):
            snapshot = None
        frozen = dict(snapshot.payload) if snapshot is not None else {}
        latest_requirement = session.scalar(select(RequirementRecord).where(
            RequirementRecord.task_id == task.task_id
        ).order_by(RequirementRecord.requirement_version.desc()))
        requirement_payload = frozen.get("requirement") or (
            dict(latest_requirement.payload) if latest_requirement is not None else {}
        )
        requirement_contract = (
            ProcurementRequirement.model_validate(requirement_payload)
            if requirement_payload else None
        )
        frozen_decision_profile = frozen.get("decision_profile")
        if frozen_decision_profile is not None:
            decision_profile_payload = dict(frozen_decision_profile)
        else:
            decision_preferences, decision_profile = (
                self._decision_preferences(
                    session, task, requirement_contract, max_revision=result.task_revision
                )
                if requirement_contract is not None
                else (DecisionPreferences(), None)
            )
            decision_profile_payload = self._decision_profile_response(
                decision_preferences, decision_profile
            )
        comparison = dict(result.payload)
        references: dict[str, Any] = {
            f"RESULT:{result.artifact_id}": {
                "type": "COMPARISON_RESULT",
                "task_revision": result.task_revision,
                "rule_version": comparison.get("rule_version"),
                "evaluated_at": comparison.get("evaluated_at"),
            }
        }
        for row in comparison.get("supplier_results", []):
            references[f"QUOTE:{row.get('quote_id')}"] = {"type": "QUOTE_RESULT", **row}
        document_contexts: list[tuple[Document, Quote, WorkflowArtifact | None]] = []
        for quote_id, batch_id in frozen.get("batch_artifact_ids", {}).items():
            batch = session.get(WorkflowArtifact, batch_id)
            document = session.get(Document, batch.document_id) if batch and batch.document_id else None
            quote = session.get(Quote, quote_id)
            if (
                batch is not None
                and batch.task_id == task.task_id
                and batch.artifact_type == "EXTRACTION_BATCH"
                and document is not None
                and quote is not None
                and document.task_id == task.task_id
                and quote.task_id == task.task_id
            ):
                document_contexts.append((document, quote, batch))
        if not document_contexts:
            documents = session.execute(select(Document, Quote).join(
                Quote, Quote.quote_id == Document.quote_id
            ).where(
                Document.task_id == task.task_id,
                Quote.active.is_(True),
                Document.quote_version == Quote.current_version,
            )).all()
            for document, quote in documents:
                execution = session.scalar(select(DocumentExecution).where(
                    DocumentExecution.graph_run_id == result.graph_run_id,
                    DocumentExecution.document_id == document.document_id,
                )) if result.graph_run_id else None
                batch = session.get(WorkflowArtifact, execution.batch_artifact_id) if execution and execution.batch_artifact_id else None
                document_contexts.append((document, quote, batch))
        for document, quote, batch in document_contexts:
            references[f"DOCUMENT:{document.document_id}"] = {
                "type": "QUOTE_DOCUMENT",
                "quote_id": quote.quote_id,
                "quote_version": document.quote_version,
                "supplier_id": quote.supplier_id,
                "original_filename": document.original_filename,
                "document_sha256": document.sha256,
            }
            for source in (batch.payload.get("parsed_input", {}).get("sources", []) if batch else []):
                source_id = source.get("source_id")
                if source_id:
                    references[f"EVIDENCE:{source_id}"] = {
                        "type": "QUOTE_EVIDENCE",
                        "document_id": document.document_id,
                        "quote_id": quote.quote_id,
                        **{key: source.get(key) for key in ("source_id", "kind", "raw_text", "page_number", "row_number", "column_name")},
                    }
        for retrieval in self._policy_retrieval_payloads(session, result.artifact_id):
            for citation in retrieval.get("citations", []):
                citation_id = citation.get("citation_id")
                if citation_id:
                    references[f"POLICY:{citation_id}"] = {"type": "POLICY_CITATION", **citation}
        policy_values = {
            "policy_set_version": frozen.get("policy_set_version", task.policy_set_version),
            "policy_index_version": frozen.get("policy_index_version", task.policy_index_version),
            "category": frozen.get("policy_category", task.policy_category),
            "region": frozen.get("policy_region", task.policy_region),
        }
        policy_binding = policy_values if all(policy_values.values()) else None
        return {
            "schema_version": "summary-facts/1.1.0",
            "task_id": task.task_id,
            "task_revision": result.task_revision,
            "result_id": result.artifact_id,
            "requirement": requirement_payload,
            "decision_profile": decision_profile_payload,
            "disposition": comparison.get("disposition"),
            "final_recommendation_allowed": comparison.get("final_recommendation_allowed", False),
            "recommended_quote_ids": comparison.get("recommended_quote_ids", []),
            "pending_quote_ids": comparison.get("pending_quote_ids", []),
            "comparison_reasons": comparison.get("comparison_reasons", []),
            "ranking_trace": comparison.get("ranking_trace"),
            "supplier_history_binding": frozen.get("supplier_history_binding"),
            "supplier_history_dataset_context": frozen.get("supplier_history_dataset_context"),
            "policy_binding": policy_binding,
            "references": references,
            "scope": "Explanatory summary only; no approval, order, payment, or supplier contact.",
        }

    def _summary_response(self, session: Session, task: Task, report: SummaryReport) -> dict[str, Any]:
        job = session.scalar(select(Job).where(Job.summary_id == report.summary_id).order_by(
            Job.created_at.desc(), Job.job_id.desc()
        ))
        stale = (
            report.status == "STALE"
            or report.task_revision != task.current_revision
            or report.result_id != task.current_result_id
        )
        return {
            "summary_id": report.summary_id,
            "task_id": report.task_id,
            "task_revision": report.task_revision,
            "result_id": report.result_id,
            "status": "STALE" if stale else report.status,
            "is_current": not stale,
            "input_sha256": report.input_sha256,
            "facts": dict(report.facts),
            "narrative": dict(report.narrative) if report.narrative else None,
            "provider": report.provider,
            "model_id": report.model_id,
            "environment": report.environment,
            "prompt_version": report.prompt_version,
            "calls_used": report.calls_used,
            "max_calls": report.max_calls,
            "error_code": report.error_code,
            "error_message": report.error_message,
            "job": ({
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job_status": job.status,
                "attempts": job.attempts,
            } if job else None),
            "created_at": self._aware_datetime(report.created_at).isoformat(),
            "updated_at": self._aware_datetime(report.updated_at).isoformat(),
        }

    @staticmethod
    def _require_revision(task: Task, expected: int) -> None:
        if task.status == "ABANDONED":
            raise ConflictError("task_abandoned", "Abandoned tasks are read-only.")
        if task.current_revision != expected:
            raise ConflictError(
                "task_revision_conflict",
                "Task revision has changed.",
                expected=expected,
                actual=task.current_revision,
            )

    @staticmethod
    def _raise_if_duplicate_quote(
        session: Session,
        task_id: str,
        document_sha256: str,
        *,
        exclude_draft_id: str | None = None,
        allow_quote_id: str | None = None,
    ) -> None:
        document_filters = [
            Document.task_id == task_id,
            Document.sha256 == document_sha256,
        ]
        if allow_quote_id is not None:
            document_filters.append(Document.quote_id != allow_quote_id)
        duplicate = session.execute(
            select(Document, Quote)
            .join(Quote, Quote.quote_id == Document.quote_id)
            .where(*document_filters)
        ).first()
        if duplicate is not None:
            document, quote = duplicate
            raise ConflictError(
                "duplicate_quote_uploaded",
                "该报价单已上传。",
                document_id=document.document_id,
                quote_id=quote.quote_id,
                supplier_id=quote.supplier_id,
                quote_active=quote.active,
            )
        draft_filters = [
            QuoteDraft.task_id == task_id,
            QuoteDraft.sha256 == document_sha256,
            QuoteDraft.status.in_(
                ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
            ),
        ]
        if exclude_draft_id is not None:
            draft_filters.append(QuoteDraft.quote_draft_id != exclude_draft_id)
        draft = session.scalar(select(QuoteDraft).where(*draft_filters))
        if draft is not None:
            raise ConflictError(
                "duplicate_quote_uploaded",
                "该报价单已上传。",
                quote_draft_id=draft.quote_draft_id,
            )

    @staticmethod
    def _issue_response(issue: Issue) -> dict[str, Any]:
        return {
            "issue_id": issue.issue_id,
            "task_id": issue.task_id,
            "graph_run_id": issue.graph_run_id,
            "quote_id": issue.quote_id,
            "field_name": issue.field_name,
            "issue_type": issue.issue_type,
            "status": issue.status,
            "question": issue.question,
            "answer_schema": dict(issue.answer_schema),
            "created_revision": issue.created_revision,
            "created_by": issue.created_by,
        }

    @staticmethod
    def _policy_binding_response(task: Task) -> dict[str, str] | None:
        values = (
            task.policy_set_version,
            task.policy_index_version,
            task.policy_category,
            task.policy_region,
        )
        if not all(values):
            return None
        return {
            "policy_set_version": task.policy_set_version,
            "policy_index_version": task.policy_index_version,
            "category": task.policy_category,
            "region": task.policy_region,
        }

    @staticmethod
    def _policy_retrieval_payloads(session, result_id: str) -> list[dict[str, Any]]:
        artifacts = session.scalars(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.parent_artifact_id == result_id,
                WorkflowArtifact.artifact_type == "POLICY_RETRIEVAL_RESULT",
            )
            .order_by(WorkflowArtifact.created_at, WorkflowArtifact.artifact_id)
        ).all()
        return [dict(artifact.payload) for artifact in artifacts]

    @staticmethod
    def _document_execution_response(execution: DocumentExecution) -> dict[str, Any]:
        return {
            "document_execution_id": execution.document_execution_id,
            "graph_run_id": execution.graph_run_id,
            "document_id": execution.document_id,
            "status": execution.status,
            "calls_used": execution.calls_used,
            "max_calls": execution.max_calls,
            "parsed_artifact_id": execution.parsed_artifact_id,
            "batch_artifact_id": execution.batch_artifact_id,
            "review_artifact_id": execution.review_artifact_id,
        }

    @staticmethod
    def _job_response(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "task_id": job.task_id,
            "graph_run_id": job.graph_run_id,
            "conversation_id": job.conversation_id,
            "conversation_message_id": job.conversation_message_id,
            "issue_id": job.issue_id,
            "job_type": job.job_type,
            "status": job.status,
            "task_revision": job.task_revision,
            "attempts": job.attempts,
        }

    @staticmethod
    def _aware_datetime(value):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value
