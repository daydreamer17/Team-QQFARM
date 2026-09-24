from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    task_name: Mapped[str] = mapped_column(String(128), nullable=False)
    task_name_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scenario_id: Mapped[str | None] = mapped_column(String(128))
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    current_graph_run_id: Mapped[str | None] = mapped_column(String(64))
    current_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    current_result_id: Mapped[str | None] = mapped_column(String(64))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_set_version: Mapped[str | None] = mapped_column(String(128))
    policy_index_version: Mapped[str | None] = mapped_column(String(128))
    policy_category: Mapped[str | None] = mapped_column(String(128))
    policy_region: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "task_name_key", name="uq_tasks_owner_task_name_key"),
    )


class TaskRevision(Base):
    __tablename__ = "task_revisions"

    revision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON_VALUE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("task_id", "revision"),)


class RequirementRecord(Base):
    __tablename__ = "requirements"

    requirement_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    requirement_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_artifact_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TaskHistoryBinding(Base):
    __tablename__ = "task_history_bindings"

    history_binding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_status: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    rating_method_version: Mapped[str] = mapped_column(String(128), nullable=False)
    identity_matcher_version: Mapped[str] = mapped_column(String(128), nullable=False)
    alias_allowlist_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    as_of_date: Mapped[str] = mapped_column(String(10), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("task_id", "task_revision"),)


class DecisionProfile(Base):
    __tablename__ = "decision_profiles"

    decision_profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_scenarios.decision_scenario_id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("task_id", "profile_version"),)


class DecisionScenario(Base):
    __tablename__ = "decision_scenarios"

    decision_scenario_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_result_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    changes: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    baseline: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    simulated: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    delta: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    applied_task_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DecisionIntent(Base):
    __tablename__ = "decision_intents"

    decision_intent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_result_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROCESSING")
    parsed_changes: Mapped[dict | None] = mapped_column(JSON_VALUE)
    confirmation_text: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    decision_scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_scenarios.decision_scenario_id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DecisionConversation(Base):
    __tablename__ = "decision_conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    base_result_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DecisionMessage(Base):
    __tablename__ = "decision_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("decision_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    reference_ids: Mapped[list | None] = mapped_column(JSON_VALUE)
    proposed_changes: Mapped[dict | None] = mapped_column(JSON_VALUE)
    decision_intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_intents.decision_intent_id", ondelete="SET NULL"),
        index=True,
    )
    reply_to_message_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("conversation_id", "sequence"),)


class DecisionConversationEvent(Base):
    __tablename__ = "decision_conversation_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("decision_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("conversation_id", "sequence"),)


class Quote(Base):
    __tablename__ = "quotes"

    quote_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    supplier_id: Mapped[str] = mapped_column(String(128), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_id: Mapped[str] = mapped_column(
        ForeignKey("quotes.quote_id", ondelete="CASCADE"), nullable=False, index=True
    )
    quote_version: Mapped[int] = mapped_column(Integer, nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_synthetic: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class QuoteDraft(Base):
    __tablename__ = "quote_drafts"

    quote_draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    base_task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    proposed_quote_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    replacement_quote_id: Mapped[str | None] = mapped_column(
        ForeignKey("quotes.quote_id", ondelete="RESTRICT"), index=True
    )
    proposed_document_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_synthetic: Mapped[bool] = mapped_column(nullable=False, default=False)
    parsed_artifact_id: Mapped[str | None] = mapped_column(String(64))
    batch_artifact_id: Mapped[str | None] = mapped_column(String(64))
    review_artifact_id: Mapped[str | None] = mapped_column(String(64))
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    dictionary_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class RequirementDraft(Base):
    __tablename__ = "requirement_drafts"

    requirement_draft_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    parsed_payload: Mapped[dict | None] = mapped_column(JSON_VALUE)
    candidates: Mapped[dict | None] = mapped_column(JSON_VALUE)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    submitted_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class SummaryReport(Base):
    __tablename__ = "summary_reports"

    summary_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    facts: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    narrative: Mapped[dict | None] = mapped_column(JSON_VALUE)
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint("task_id", "input_sha256", "prompt_version", "model_id"),
    )


class DocumentAccessEvent(Base):
    __tablename__ = "document_access_events"

    access_event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class WorkflowArtifact(Base):
    __tablename__ = "workflow_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[str | None] = mapped_column(String(32))
    parent_artifact_id: Mapped[str | None] = mapped_column(String(64))
    quote_id: Mapped[str | None] = mapped_column(String(64), index=True)
    document_id: Mapped[str | None] = mapped_column(String(64), index=True)
    graph_run_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class GraphRun(Base):
    __tablename__ = "graph_runs"

    graph_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    started_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    history_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_history_bindings.history_binding_id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_interrupt_issue_id: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class Issue(Base):
    __tablename__ = "issues"

    issue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False, index=True
    )
    graph_run_id: Mapped[str] = mapped_column(
        ForeignKey("graph_runs.graph_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quote_id: Mapped[str | None] = mapped_column(String(64), index=True)
    field_name: Mapped[str | None] = mapped_column(String(128))
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_schema: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    answer_payload: Mapped[dict | None] = mapped_column(JSON_VALUE)
    created_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_revision: Mapped[int | None] = mapped_column(Integer)
    answered_by: Mapped[str | None] = mapped_column(String(128))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("graph_run_id", "issue_type", "quote_id", "field_name"),
    )


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=True, index=True
    )
    graph_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("graph_runs.graph_run_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    quote_draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("quote_drafts.quote_draft_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    requirement_draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("requirement_drafts.requirement_draft_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    summary_id: Mapped[str | None] = mapped_column(
        ForeignKey("summary_reports.summary_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_conversations.conversation_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    conversation_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("decision_messages.message_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    issue_id: Mapped[str | None] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    history_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_history_bindings.history_binding_id", ondelete="RESTRICT"),
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentExecution(Base):
    __tablename__ = "document_executions"

    document_execution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    graph_run_id: Mapped[str] = mapped_column(
        ForeignKey("graph_runs.graph_run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    parsed_artifact_id: Mapped[str | None] = mapped_column(String(64))
    batch_artifact_id: Mapped[str | None] = mapped_column(String(64))
    review_artifact_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (UniqueConstraint("graph_run_id", "document_id"),)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    idempotency_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key"),
    )
