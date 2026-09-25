from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from supplier_comparison.rag.clients import EmbeddingConfig, SiliconFlowEmbeddingClient
from supplier_comparison.rag.importer import PolicyImporter
from supplier_comparison.rag.uploads import (
    PolicyDraftClauseInput,
    PolicyFileImportMetadata,
    PolicyFileImportService,
)
from supplier_comparison.rules import ProcurementRequirement, RequirementChanges

from .database import create_session_factory, readiness_probe
from .investigation import AgentConfig, AgentLimits, InvestigationRunner, LiveInvestigationPlanner
from .investigation_tools import ScopedInvestigationTools
from .decision_investigation import DecisionInvestigationTools
from .quote_identity import identify_supplier_id
from .decision_intents import (
    DECISION_INTENT_PROMPT_VERSION,
    DecisionIntentModelConfig,
    DecisionIntentParser,
    parse_decision_intent,
)
from .intake import REQUIREMENT_PROMPT_VERSION, RequirementModelConfig
from .service import BackendError, BackendService, ConflictError, NotFoundError
from .settings import settings
from .summaries import SUMMARY_PROMPT_VERSION, SummaryModelConfig
from .worker_health import worker_heartbeat_status
from .compliance import EvidenceInput
from .compliance_evidence_parser import parse_compliance_evidence


logger = logging.getLogger("uvicorn.error")


IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ComplianceConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_task_revision: int = Field(ge=1)
    expected_assessment_id: str = Field(min_length=1, max_length=64)
    acknowledged_missing_item_ids: list[str] = Field(default_factory=list, max_length=1000)
    acknowledge_no_policy: StrictBool = False


class PolicyBindingRequest(ApiModel):
    policy_set_version: str = Field(min_length=1, max_length=128)
    policy_index_version: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=64)


class CreateTaskRequest(ApiModel):
    requirement: ProcurementRequirement
    task_name: str | None = Field(default=None, max_length=128)
    scenario_id: str | None = Field(default=None, max_length=128)
    policy_binding: PolicyBindingRequest | None = None
    requirement_draft_id: str | None = Field(default=None, min_length=1, max_length=64)
    expected_requirement_draft_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def requirement_draft_pair(self):
        if (self.requirement_draft_id is None) != (self.expected_requirement_draft_revision is None):
            raise ValueError("requirement draft ID and revision must be supplied together")
        return self


class UpdateRequirementRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    requirement: ProcurementRequirement
    policy_binding: PolicyBindingRequest | None = None


class AbandonTaskRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class DiscardRequirementDraftRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: int = Field(ge=1)


class CreateSummaryRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    result_id: str = Field(min_length=1, max_length=64)


class RetrySummaryRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class StartRunRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)


class RefreshSupplierHistoryRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    dataset_version: str = Field(min_length=1, max_length=128)


class RetryJobRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)


class RequirementSimulationRequest(ApiModel):
    model_config = ConfigDict(extra='forbid')
    expected_task_revision: int = Field(ge=1)
    confirm_hypothetical: Literal[True]
    changes: RequirementChanges

    @field_validator('confirm_hypothetical', mode='before')
    @classmethod
    def explicit_authorization(cls, value):
        if value is not True:
            raise ValueError('explicit hypothetical authorization is required')
        return value


class RequestedInvestigationRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    quote_id: StrictStr = Field(min_length=1, max_length=64)
    intent: Literal[
        "ANALYZE_SELECTION_GAP",
        "DRAFT_CLARIFICATION",
        "SIMULATE_REQUIREMENT_CHANGE",
    ]
    confirm_hypothetical: StrictBool = False
    changes: RequirementChanges | None = None

    @model_validator(mode="after")
    def validate_authorization(self):
        simulation = self.intent == "SIMULATE_REQUIREMENT_CHANGE"
        submitted_changes = (
            self.changes.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            if self.changes is not None
            else {}
        )
        if simulation and (self.confirm_hypothetical is not True or not submitted_changes):
            raise ValueError("simulation requires explicit authorization and changes")
        if not simulation and (self.confirm_hypothetical or self.changes is not None):
            raise ValueError("only simulation accepts hypothetical requirement changes")
        return self


class DecisionInvestigationRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class CreateDecisionScenarioRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    confirm_hypothetical: Literal[True]
    changes: RequirementChanges

    @field_validator("confirm_hypothetical", mode="before")
    @classmethod
    def explicit_authorization(cls, value):
        if value is not True:
            raise ValueError("explicit hypothetical authorization is required")
        return value


class ApplyDecisionScenarioRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class ParseDecisionIntentRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    message: StrictStr = Field(min_length=3, max_length=4000)


class ConfirmDecisionIntentRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    confirm: Literal[True]

    @field_validator("confirm", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("explicit intent confirmation is required")
        return value


class CreateDecisionConversationRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=255)


class SendDecisionConversationMessageRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    message: StrictStr = Field(min_length=1, max_length=4000)


class ConfirmMissingAnswer(ApiModel):
    answer_type: Literal["CONFIRM_MISSING"]


class ShippingAmountAnswer(ApiModel):
    answer_type: Literal["SHIPPING_AMOUNT"]
    amount: Decimal = Field(ge=0)
    currency: StrictStr = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")

    @field_validator("amount", mode="before")
    @classmethod
    def reject_float_amount(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string")
        return value


class RetryPolicyRetrievalAnswer(ApiModel):
    answer_type: Literal["RETRY_POLICY_RETRIEVAL"]


class PaymentInformationAnswer(ApiModel):
    answer_type: Literal["PAYMENT_INFORMATION"]
    payment_start_event: Literal["INVOICE_DATE"]
    note: StrictStr = Field(min_length=1, max_length=1000)
    source_type: Literal["SUPPLIER_CONFIRMATION", "DOCUMENT_CLARIFICATION", "USER_INPUT"]
    source_refs: list[StrictStr] = Field(default_factory=list, max_length=20)


class IssueAnswerRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)
    answer: ConfirmMissingAnswer | ShippingAmountAnswer | RetryPolicyRetrievalAnswer | PaymentInformationAnswer = Field(
        discriminator="answer_type"
    )


class FieldCorrectionRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)
    raw_value: str = Field(min_length=1)
    normalized_value: StrictStr | StrictInt | StrictBool
    unit: str | None = None
    reason: str = Field(min_length=3, max_length=1000)


class QuoteDraftCorrectionItem(ApiModel):
    model_config = ConfigDict(extra="forbid")
    field_name: str = Field(min_length=1, max_length=128)
    raw_value: str = Field(min_length=1)
    normalized_value: StrictStr | StrictInt | StrictBool | None
    unit: str | None = None
    reason: str = Field(min_length=3, max_length=1000)


class QuoteDraftCorrectionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: int = Field(ge=1)
    corrections: list[QuoteDraftCorrectionItem] = Field(min_length=1, max_length=100)


class QuoteDraftReviewActionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "CONFIRM_VALUE",
        "SET_VALUE",
        "CONFIRM_MISSING",
        "MARK_MISSING",
        "CONFIRM_CONFLICT",
    ]
    field_name: str = Field(min_length=1, max_length=128)
    expected_field_id: str = Field(min_length=1, max_length=128)
    expected_field_version: int = Field(ge=1)
    raw_value: str | None = Field(default=None, max_length=10_000)
    normalized_value: StrictStr | StrictInt | StrictBool | None = None
    unit: str | None = Field(default=None, max_length=64)
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def action_payload_matches(self):
        if self.action == "SET_VALUE":
            if self.raw_value is None or not self.raw_value.strip():
                raise ValueError("SET_VALUE requires a non-empty raw_value")
            if self.normalized_value is None:
                raise ValueError("SET_VALUE requires normalized_value")
        elif self.action == "MARK_MISSING":
            if self.reason is None or len(self.reason.strip()) < 3:
                raise ValueError("MARK_MISSING requires a reason")
            if self.raw_value is not None or self.normalized_value is not None or self.unit is not None:
                raise ValueError("MARK_MISSING cannot carry a value")
        elif self.raw_value is not None or self.normalized_value is not None or self.unit is not None:
            raise ValueError(f"{self.action} cannot carry a replacement value")
        return self


class QuoteDraftReviewRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: int = Field(ge=1)
    schema_version: str = Field(min_length=1, max_length=64)
    actions: list[QuoteDraftReviewActionRequest] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def field_actions_are_unique(self):
        names = [item.field_name for item in self.actions]
        if len(names) != len(set(names)):
            raise ValueError("review actions cannot repeat a field")
        return self


class SubmitQuoteDraftRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    expected_draft_revision: int = Field(ge=1)


class DiscardQuoteDraftRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: int = Field(ge=1)


class DeactivateQuoteRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class ReactivateQuoteRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class CreateQuoteRevisionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)


class BatchFieldCorrection(ApiModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str = Field(min_length=1, max_length=64)
    field_name: str = Field(min_length=1, max_length=128)
    expected_field_version: int = Field(ge=1)
    raw_value: str = Field(min_length=1)
    normalized_value: StrictStr | StrictInt | StrictBool
    unit: str | None = None
    reason: str = Field(min_length=3, max_length=1000)


class BatchFieldCorrectionRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")
    expected_task_revision: int = Field(ge=1)
    corrections: list[BatchFieldCorrection] = Field(min_length=1, max_length=100)


class ReviewPolicyClausesRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    clauses: list[PolicyDraftClauseInput] = Field(min_length=1, max_length=200)


class PublishPolicyRequest(ApiModel):
    expected_revision: int = Field(ge=1)


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id is None:
        request_id = request.headers.get("X-Request-ID") or f"request_{uuid4().hex}"
        request.state.request_id = request_id
    return request_id


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": _request_id(request),
            }
        },
    )


def create_app(
    service: BackendService,
    *,
    readiness_check,
    policy_file_import_service: PolicyFileImportService | None = None,
    allow_legacy_direct_quote_upload: bool = True,
    decision_intent_parser: DecisionIntentParser | None = None,
    decision_intent_provider: str | None = None,
    decision_intent_model_id: str | None = None,
    investigator: InvestigationRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="Supplier Comparison API", version="0.1.0")

    @app.exception_handler(BackendError)
    async def backend_error_handler(request: Request, exc: BackendError) -> JSONResponse:
        status = 409 if isinstance(exc, ConflictError) else 404 if isinstance(exc, NotFoundError) else 422
        return _error_response(
            request,
            status_code=status,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors()
        ]
        return _error_response(
            request,
            status_code=422,
            code="request_validation_failed",
            message="Request validation failed.",
            details={"errors": errors},
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled API error request_id=%s method=%s path=%s",
            _request_id(request),
            request.method,
            request.url.path,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_server_error",
            message="An unexpected server error occurred.",
            details={},
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready(request: Request, require_worker: bool = False):
        if readiness_check():
            if require_worker:
                worker = worker_heartbeat_status(
                    service.storage_root,
                    stale_after_seconds=settings.supplier_worker_heartbeat_stale_seconds,
                )
                if worker["status"] != "ready":
                    return _error_response(
                        request,
                        status_code=503,
                        code="worker_unavailable",
                        message="Background worker heartbeat is unavailable.",
                        details=worker,
                    )
            return {"status": "ready"}
        return _error_response(
            request,
            status_code=503,
            code="database_unavailable",
            message="Database readiness check failed.",
            details={},
        )

    @app.get("/health/worker")
    def worker_ready(request: Request):
        worker = worker_heartbeat_status(
            service.storage_root,
            stale_after_seconds=settings.supplier_worker_heartbeat_stale_seconds,
        )
        if worker["status"] == "ready":
            return worker
        return _error_response(
            request,
            status_code=503,
            code="worker_unavailable",
            message="Background worker heartbeat is unavailable.",
            details=worker,
        )

    @app.get("/api/v1/quote-field-schema")
    def quote_field_schema():
        return service.quote_field_schema()

    @app.get('/api/v1/tasks/{task_id}/compliance')
    def compliance_workspace(task_id: str):
        return service.compliance_workspace(task_id)

    @app.post('/api/v1/tasks/{task_id}/compliance/evidence/parse')
    def parse_evidence_file(task_id: str,
            control_code: Literal['APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL'] = Form(...),
            file: UploadFile = File(...)):
        # Enforce the task owner boundary before inspecting user-supplied content.
        service.get_task(task_id)
        content = file.file.read(10 * 1024 * 1024 + 1)
        try:
            return parse_compliance_evidence(
                content,
                filename=file.filename or 'evidence',
                media_type=file.content_type or 'application/octet-stream',
                control_code=control_code,
            )
        except ValueError as exc:
            code = str(exc)
            messages = {
                'empty_file': 'The evidence file is empty.',
                'file_too_large': 'The evidence file exceeds 10 MiB.',
                'unsupported_media_type': 'Only PDF, UTF-8 TXT, or Markdown evidence files are supported.',
                'invalid_text_encoding': 'TXT and Markdown evidence files must use UTF-8 encoding.',
                'invalid_pdf': 'The PDF evidence file is invalid or unreadable.',
                'pdf_page_limit_exceeded': 'A PDF evidence file cannot exceed 50 pages.',
                'text_unavailable': 'The file does not contain enough parseable text.',
            }
            raise BackendError(code, messages.get(code, 'Unable to parse the evidence file.'))

    def save_evidence(task_id, expected_task_revision, facts, idempotency_key, file, previous=None,
                      run_after_save=True):
        try:
            parsed = EvidenceInput.model_validate_json(facts)
        except ValidationError:
            raise BackendError('evidence_fields_invalid', 'Evidence fields are incomplete or incorrectly formatted. Check identity, dates, and sources.')
        return service.save_compliance_evidence(task_id, expected_task_revision=expected_task_revision,
            facts=parsed, idempotency_key=idempotency_key, previous_evidence_id=previous,
            file=file.file if file else None, filename=file.filename if file else None,
            run_after_save=run_after_save)

    @app.post('/api/v1/tasks/{task_id}/compliance/evidence', status_code=202)
    def create_compliance_evidence(task_id: str, idempotency_key: IdempotencyKey,
            expected_task_revision: int = Form(..., ge=1), facts: str = Form(...),
            run_after_save: bool = Form(True), file: UploadFile | None = File(None)):
        return save_evidence(task_id, expected_task_revision, facts, idempotency_key, file,
                             run_after_save=run_after_save)

    @app.post('/api/v1/tasks/{task_id}/compliance/evidence/{evidence_id}/revisions', status_code=202)
    def revise_compliance_evidence(task_id: str, evidence_id: str, idempotency_key: IdempotencyKey,
            expected_task_revision: int = Form(..., ge=1), facts: str = Form(...),
            run_after_save: bool = Form(True), file: UploadFile | None = File(None)):
        return save_evidence(task_id, expected_task_revision, facts, idempotency_key, file, evidence_id,
                             run_after_save=run_after_save)

    @app.get('/api/v1/tasks/{task_id}/compliance/evidence/{evidence_id}/files/{file_id}/content')
    def compliance_evidence_content(task_id: str, evidence_id: str, file_id: str, download: bool = False):
        item = service.compliance_evidence_content(task_id, evidence_id, file_id)
        return FileResponse(item['path'], media_type=item['media_type'], filename=item['filename'],
            content_disposition_type='attachment' if download else 'inline',
            headers={'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "sandbox; default-src 'none'", 'Cache-Control': 'no-store'})

    @app.post('/api/v1/tasks/{task_id}/compliance/confirm', status_code=202)
    def confirm_compliance(task_id: str, body: ComplianceConfirmationRequest, idempotency_key: IdempotencyKey):
        return service.confirm_compliance(task_id, **body.model_dump(), idempotency_key=idempotency_key)

    @app.post('/api/v1/tasks/{task_id}/compliance/start', status_code=202)
    def start_compliance(
        task_id: str,
        body: StartRunRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.start_compliance(
            task_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(
        body: CreateTaskRequest,
        idempotency_key: IdempotencyKey,
    ):
        if body.policy_binding is not None and policy_file_import_service is not None:
            policy_file_import_service.require_active_binding(
                policy_set_version=body.policy_binding.policy_set_version,
                policy_index_version=body.policy_binding.policy_index_version,
                category=body.policy_binding.category,
                region=body.policy_binding.region,
            )
        return service.create_task(
            body.requirement,
            idempotency_key=idempotency_key,
            task_name=body.task_name,
            scenario_id=body.scenario_id,
            policy_set_version=(
                body.policy_binding.policy_set_version if body.policy_binding else None
            ),
            policy_index_version=(
                body.policy_binding.policy_index_version if body.policy_binding else None
            ),
            policy_category=(body.policy_binding.category if body.policy_binding else None),
            policy_region=(body.policy_binding.region if body.policy_binding else None),
            requirement_draft_id=body.requirement_draft_id,
            expected_requirement_draft_revision=body.expected_requirement_draft_revision,
        )

    @app.post("/api/v1/requirement-drafts", status_code=202)
    def upload_requirement_draft(
        idempotency_key: IdempotencyKey,
        file: UploadFile = File(...),
    ):
        suffix = Path(file.filename or "").suffix.lower()
        media_type = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}.get(suffix)
        if media_type is None:
            raise BackendError(
                "unsupported_requirement_media_type",
                "Only PDF, TXT, and Markdown requirement files are supported.",
            )
        model_config = RequirementModelConfig.from_env()
        return service.upload_requirement_draft_stream(
            original_filename=file.filename or f"requirement{suffix}",
            media_type=media_type,
            stream=file.file,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=model_config.model_id if model_config else None,
            environment=settings.supplier_model_environment,
            prompt_version=REQUIREMENT_PROMPT_VERSION,
        )

    @app.get("/api/v1/requirement-drafts/{draft_id}")
    def get_requirement_draft(draft_id: str):
        return service.get_requirement_draft(draft_id)

    @app.post("/api/v1/requirement-drafts/{draft_id}/discard")
    def discard_requirement_draft(
        draft_id: str,
        body: DiscardRequirementDraftRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.discard_requirement_draft(
            draft_id,
            expected_revision=body.expected_draft_revision,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks")
    def list_tasks(
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        query: Annotated[str | None, Query(max_length=200)] = None,
        status: Annotated[str | None, Query(max_length=32)] = None,
        sort: Literal["updated_desc", "planned_asc", "planned_desc", "created_desc"] = "updated_desc",
    ):
        return service.list_tasks(limit=limit, offset=offset, query=query, status=status, sort=sort)

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        return service.get_task(task_id)

    @app.put("/api/v1/tasks/{task_id}/requirement", status_code=202)
    def update_requirement(
        task_id: str,
        body: UpdateRequirementRequest,
        idempotency_key: IdempotencyKey,
    ):
        if body.policy_binding is not None and policy_file_import_service is not None:
            current_binding = service.get_task(task_id).get("policy_binding")
            requested_binding = body.policy_binding.model_dump()
            if current_binding != requested_binding:
                policy_file_import_service.require_active_binding(
                    policy_set_version=body.policy_binding.policy_set_version,
                    policy_index_version=body.policy_binding.policy_index_version,
                    category=body.policy_binding.category,
                    region=body.policy_binding.region,
                )
        return service.update_requirement(
            task_id,
            body.requirement,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
            update_policy_binding="policy_binding" in body.model_fields_set,
            policy_set_version=(
                body.policy_binding.policy_set_version if body.policy_binding else None
            ),
            policy_index_version=(
                body.policy_binding.policy_index_version if body.policy_binding else None
            ),
            policy_category=(body.policy_binding.category if body.policy_binding else None),
            policy_region=(body.policy_binding.region if body.policy_binding else None),
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/abandon")
    def abandon_task(
        task_id: str,
        body: AbandonTaskRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.abandon_task(
            task_id,
            expected_task_revision=body.expected_task_revision,
            reason=body.reason,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/revisions")
    def task_revisions(task_id: str):
        return service.task_audit(task_id)

    @app.get("/api/v1/tasks/{task_id}/documents/{document_id}/content")
    def document_content(
        task_id: str,
        document_id: str,
        request: Request,
        disposition: Literal["inline", "attachment"] = "inline",
    ):
        item = service.document_content(
            task_id,
            document_id,
            action="DOWNLOAD" if disposition == "attachment" else "PREVIEW",
            request_id=_request_id(request),
        )
        return FileResponse(
            item["path"],
            media_type=item["media_type"],
            filename=item["filename"],
            content_disposition_type=disposition,
            headers={
                "ETag": f'"{item["sha256"]}"',
                "Cache-Control": "private, no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/v1/tasks/{task_id}/quotes", status_code=201, deprecated=True)
    def upload_quote(
        task_id: str,
        idempotency_key: IdempotencyKey,
        expected_task_revision: Annotated[int, Form(ge=1)],
        supplier_id: Annotated[str, Form(min_length=1, max_length=128)],
        is_synthetic: Annotated[bool, Form()] = False,
        file: UploadFile = File(...),
    ):
        if not allow_legacy_direct_quote_upload:
            raise BackendError(
                "quote_draft_required",
                "Upload and review a quote draft before formal submission.",
            )
        result = service.upload_quote_stream(
            task_id,
            expected_task_revision=expected_task_revision,
            supplier_id=supplier_id,
            original_filename=file.filename or "quote",
            media_type=file.content_type or "application/octet-stream",
            stream=file.file,
            idempotency_key=idempotency_key,
            is_synthetic=is_synthetic,
        )
        return {key: value for key, value in result.items() if key != "storage_path"}

    @app.post("/api/v1/tasks/{task_id}/quote-drafts", status_code=202)
    def upload_quote_draft(
        task_id: str,
        idempotency_key: IdempotencyKey,
        expected_task_revision: Annotated[int, Form(ge=1)],
        supplier_id: Annotated[str, Form(min_length=1, max_length=128)],
        is_synthetic: Annotated[bool, Form()] = False,
        replacement_quote_id: Annotated[
            str | None, Form(min_length=1, max_length=64)
        ] = None,
        file: UploadFile = File(...),
    ):
        return service.upload_quote_draft_stream(
            task_id,
            expected_task_revision=expected_task_revision,
            supplier_id=supplier_id,
            original_filename=file.filename or "quote",
            media_type=file.content_type or "application/octet-stream",
            stream=file.file,
            idempotency_key=idempotency_key,
            is_synthetic=is_synthetic,
            replacement_quote_id=replacement_quote_id,
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/quote-supplier-identification")
    def identify_quote_supplier(task_id: str, file: UploadFile = File(...)):
        # The task lookup enforces the same owner boundary as quote upload.
        service.get_task(task_id)
        content = file.file.read(5 * 1024 * 1024 + 1)
        if not content:
            raise BackendError("empty_file", "The uploaded quote file is empty.")
        if len(content) > 5 * 1024 * 1024:
            raise BackendError("file_too_large", "Quote file exceeds the 5 MiB limit.")
        try:
            return identify_supplier_id(
                content,
                filename=file.filename or "quote",
                media_type=file.content_type or "application/octet-stream",
            )
        except ValueError as exc:
            code = str(exc)
            messages = {
                "unsupported_media_type": "Only PDF and UTF-8 CSV quote files are supported.",
                "invalid_csv_encoding": "CSV quote files must use UTF-8 encoding.",
                "invalid_pdf": "The PDF quote is damaged or unreadable.",
                "pdf_page_limit_exceeded": "The PDF quote exceeds the 50-page preview limit.",
            }
            raise BackendError(code, messages.get(code, "Supplier identification failed.")) from exc

    @app.get("/api/v1/tasks/{task_id}/quote-drafts")
    def list_quote_drafts(task_id: str):
        return service.list_quote_drafts(task_id)

    @app.get("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}")
    def get_quote_draft(task_id: str, draft_id: str):
        return service.get_quote_draft(task_id, draft_id)

    @app.get("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/content")
    def quote_draft_content(
        task_id: str,
        draft_id: str,
        disposition: Literal["inline", "attachment"] = "inline",
    ):
        item = service.quote_draft_content(task_id, draft_id)
        return FileResponse(
            item["path"],
            media_type=item["media_type"],
            filename=item["filename"],
            content_disposition_type=disposition,
            headers={
                "ETag": f'"{item["sha256"]}"',
                "Cache-Control": "private, no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.put("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/review")
    def review_quote_draft(
        task_id: str,
        draft_id: str,
        body: QuoteDraftReviewRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.review_quote_draft(
            task_id,
            draft_id,
            expected_draft_revision=body.expected_draft_revision,
            schema_version=body.schema_version,
            actions=[item.model_dump(mode="json") for item in body.actions],
            idempotency_key=idempotency_key,
        )

    @app.put("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/corrections")
    def correct_quote_draft(
        task_id: str,
        draft_id: str,
        body: QuoteDraftCorrectionRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.correct_quote_draft(
            task_id,
            draft_id,
            expected_draft_revision=body.expected_draft_revision,
            corrections=[item.model_dump(mode="json") for item in body.corrections],
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/submit", status_code=201)
    def submit_quote_draft(
        task_id: str,
        draft_id: str,
        body: SubmitQuoteDraftRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.submit_quote_draft(
            task_id,
            draft_id,
            expected_task_revision=body.expected_task_revision,
            expected_draft_revision=body.expected_draft_revision,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/discard")
    def discard_quote_draft(
        task_id: str,
        draft_id: str,
        body: DiscardQuoteDraftRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.discard_quote_draft(
            task_id,
            draft_id,
            expected_draft_revision=body.expected_draft_revision,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/quotes")
    def list_quotes(task_id: str):
        return service.list_quotes(task_id)

    @app.post("/api/v1/tasks/{task_id}/quotes/{quote_id}/deactivate")
    def deactivate_quote(
        task_id: str,
        quote_id: str,
        body: DeactivateQuoteRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.deactivate_quote(
            task_id,
            quote_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/tasks/{task_id}/quotes/{quote_id}/reactivate")
    def reactivate_quote(
        task_id: str,
        quote_id: str,
        body: ReactivateQuoteRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.reactivate_quote(
            task_id,
            quote_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/api/v1/tasks/{task_id}/quotes/{quote_id}/revisions",
        status_code=202,
    )
    def create_quote_revision(
        task_id: str,
        quote_id: str,
        body: CreateQuoteRevisionRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.create_quote_revision_draft(
            task_id,
            quote_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/runs", status_code=202)
    def start_run(
        task_id: str,
        body: StartRunRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.start_run(
            task_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/supplier-history/refresh", status_code=202)
    def refresh_task_supplier_history(
        task_id: str,
        body: RefreshSupplierHistoryRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.refresh_supplier_history(
            task_id,
            expected_task_revision=body.expected_task_revision,
            dataset_version=body.dataset_version,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/jobs/{job_id}/retries", status_code=202)
    def retry_failed_job(
        task_id: str,
        job_id: str,
        body: RetryJobRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.retry_failed_resume_job(
            task_id,
            job_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/issues")
    def list_issues(task_id: str):
        return service.list_issues(task_id)

    @app.get("/api/v1/tasks/{task_id}/quotes/{quote_id}/fields")
    def list_quote_fields(task_id: str, quote_id: str, result_id: str | None = None):
        return service.list_quote_fields(task_id, quote_id, result_id=result_id)

    @app.get("/api/v1/tasks/{task_id}/review")
    def list_review_problems(task_id: str):
        return service.list_review_problems(task_id)

    @app.get("/api/v1/tasks/{task_id}/investigations")
    def list_investigations(task_id: str):
        return service.list_investigations(task_id)

    @app.post("/api/v1/tasks/{task_id}/decision-investigations", status_code=201)
    def request_decision_investigation(task_id: str, body: DecisionInvestigationRequest):
        if investigator is None:
            raise BackendError("investigation_agent_disabled", "The investigation Agent is not enabled.")
        context = service.requested_investigation_context(
            task_id, expected_task_revision=body.expected_task_revision, quote_id=None,
        )
        tools = DecisionInvestigationTools(service, task_id=task_id, context=context)
        case = tools.prepare_case(tools.requested_case(request_id=str(uuid4())))
        completed = investigator.run((case,), tools, tools.save)[0]
        return next(record for record in service.list_investigations(task_id)
                    if record["case_id"] == completed.case_id)

    @app.post("/api/v1/tasks/{task_id}/investigations", status_code=201)
    def request_investigation(task_id: str, body: RequestedInvestigationRequest):
        if investigator is None:
            raise BackendError(
                "investigation_agent_disabled",
                "The investigation Agent is not enabled for this environment.",
            )
        context = service.requested_investigation_context(
            task_id,
            expected_task_revision=body.expected_task_revision,
            quote_id=body.quote_id,
        )
        definitions = {
            "ANALYZE_SELECTION_GAP": {
                "goal": "Analyse this quotation's cost, delivery, and blocking gaps relative to the current feasible options and produce verifiable findings.",
                "required": ("analyze_selection_gap",),
                "allowed": (
                    "get_task_context", "analyze_decision_impact", "get_comparison_result",
                    "get_cost_breakdown", "analyze_selection_gap",
                ),
            },
            "DRAFT_CLARIFICATION": {
                "goal": "Analyse why this quotation was not selected or remains pending, then generate an unsent supplier clarification draft.",
                "required": ("analyze_selection_gap", "draft_clarification"),
                "allowed": (
                    "get_task_context", "analyze_decision_impact", "get_comparison_result",
                    "get_cost_breakdown", "analyze_selection_gap", "draft_clarification",
                ),
            },
            "SIMULATE_REQUIREMENT_CHANGE": {
                "goal": "Run a hypothetical simulation using only the budget or delivery changes explicitly authorised by the user, without changing official procurement requirements.",
                "required": ("simulate_requirement_change",),
                "allowed": (
                    "get_task_context", "analyze_decision_impact", "get_comparison_result",
                    "get_cost_breakdown", "simulate_requirement_change",
                ),
            },
        }
        definition = definitions[body.intent]
        tools = ScopedInvestigationTools(
            service,
            task_id=task_id,
            graph_run_id=context["graph_run_id"],
            task_revision=context["task_revision"],
            impact_artifact_id=context["impact_artifact_id"],
            evaluated_at=datetime.fromisoformat(context["evaluated_at"].replace("Z", "+00:00")),
            authorized_requirement_changes=(
                body.changes.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
                if body.changes is not None
                else None
            ),
        )
        case = tools.requested_case(
            quote_id=body.quote_id,
            goal=definition["goal"],
            required_tools=definition["required"],
            allowed_tools=definition["allowed"],
            request_id=str(uuid4()),
        )
        completed = investigator.run((case,), tools, tools.save)[0]
        return next(
            record
            for record in service.list_investigations(task_id)
            if record["case_id"] == completed.case_id
        )

    @app.get('/api/v1/tasks/{task_id}/selection-gaps')
    def selection_gaps(task_id: str, expected_task_revision: int = Query(ge=1),
                       expected_result_id: str | None = None):
        return service.selection_gaps(task_id, expected_task_revision=expected_task_revision,
                                      expected_result_id=expected_result_id)

    @app.post('/api/v1/tasks/{task_id}/requirement-simulations')
    def requirement_simulation(task_id: str, body: RequirementSimulationRequest):
        return service.requirement_simulation(task_id, expected_task_revision=body.expected_task_revision,
                                             changes=body.changes, user_authorized=body.confirm_hypothetical)

    @app.post("/api/v1/tasks/{task_id}/decision-scenarios", status_code=201)
    def create_decision_scenario(
        task_id: str,
        body: CreateDecisionScenarioRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.create_decision_scenario(
            task_id,
            expected_task_revision=body.expected_task_revision,
            changes=body.changes,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/decision-scenarios")
    def list_decision_scenarios(task_id: str):
        return service.list_decision_scenarios(task_id)

    @app.get("/api/v1/tasks/{task_id}/decision-scenarios/{scenario_id}")
    def get_decision_scenario(task_id: str, scenario_id: str):
        return service.get_decision_scenario(task_id, scenario_id)

    @app.post("/api/v1/tasks/{task_id}/decision-scenarios/{scenario_id}/apply", status_code=202)
    def apply_decision_scenario(
        task_id: str,
        scenario_id: str,
        body: ApplyDecisionScenarioRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.apply_decision_scenario(
            task_id,
            scenario_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=settings.supplier_model_model_id,
            environment=settings.supplier_model_environment,
            prompt_version=settings.supplier_prompt_version,
        )

    @app.post("/api/v1/tasks/{task_id}/decision-intents", status_code=201)
    def parse_task_decision_intent(
        task_id: str,
        body: ParseDecisionIntentRequest,
        idempotency_key: IdempotencyKey,
    ):
        parser = decision_intent_parser
        provider = decision_intent_provider
        model_id = decision_intent_model_id
        if parser is None:
            config = DecisionIntentModelConfig.from_env()
            if config is None:
                raise BackendError(
                    "decision_intent_model_unconfigured",
                    "Decision intent model is not configured.",
                )
            parser = lambda message, context: parse_decision_intent(
                message, context, config
            )
            provider = config.provider
            model_id = config.model_id
        else:
            provider = provider or "fixed"
            model_id = model_id or "fixed-output"
        return service.parse_decision_intent(
            task_id,
            expected_task_revision=body.expected_task_revision,
            message=body.message,
            idempotency_key=idempotency_key,
            parser=parser,
            provider=provider,
            model_id=model_id,
            prompt_version=DECISION_INTENT_PROMPT_VERSION,
        )

    @app.get("/api/v1/tasks/{task_id}/decision-intents")
    def list_task_decision_intents(task_id: str):
        return service.list_decision_intents(task_id)

    @app.get("/api/v1/tasks/{task_id}/decision-intents/{intent_id}")
    def get_task_decision_intent(task_id: str, intent_id: str):
        return service.get_decision_intent(task_id, intent_id)

    @app.post(
        "/api/v1/tasks/{task_id}/decision-intents/{intent_id}/confirm",
        status_code=201,
    )
    def confirm_task_decision_intent(
        task_id: str,
        intent_id: str,
        body: ConfirmDecisionIntentRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.confirm_decision_intent(
            task_id,
            intent_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v1/tasks/{task_id}/decision-conversations", status_code=201)
    def create_task_decision_conversation(
        task_id: str,
        body: CreateDecisionConversationRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.create_decision_conversation(
            task_id,
            expected_task_revision=body.expected_task_revision,
            title=body.title,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/decision-conversations")
    def list_task_decision_conversations(task_id: str, result_id: str | None = None):
        return service.list_decision_conversations(task_id, result_id=result_id)

    @app.get("/api/v1/tasks/{task_id}/decision-conversations/{conversation_id}")
    def get_task_decision_conversation(task_id: str, conversation_id: str):
        return service.get_decision_conversation(task_id, conversation_id)

    @app.post(
        "/api/v1/tasks/{task_id}/decision-conversations/{conversation_id}/messages",
        status_code=202,
    )
    def send_task_decision_conversation_message(
        task_id: str,
        conversation_id: str,
        body: SendDecisionConversationMessageRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.send_decision_conversation_message(
            task_id,
            conversation_id,
            expected_task_revision=body.expected_task_revision,
            content=body.message,
            idempotency_key=idempotency_key,
        )

    @app.get(
        "/api/v1/tasks/{task_id}/decision-conversations/{conversation_id}/events"
    )
    def stream_task_decision_conversation_events(
        task_id: str,
        conversation_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
        follow: bool = Query(default=True),
        timeout_seconds: float = Query(default=25.0, ge=0.1, le=30.0),
    ):
        # Authorize before starting a streaming response so errors retain the API envelope.
        service.get_decision_conversation(task_id, conversation_id)

        def event_stream():
            cursor = max(after, last_event_id or 0)
            deadline = time.monotonic() + timeout_seconds
            yield "retry: 1000\n\n"
            while True:
                events = service.decision_conversation_events(
                    task_id, conversation_id, after_sequence=cursor
                )
                for event in events:
                    cursor = event["sequence"]
                    data = json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {data}\n\n"
                if not follow or time.monotonic() >= deadline:
                    break
                if any(
                    event["event_type"] in {"assistant.completed", "assistant.failed"}
                    for event in events
                ):
                    break
                time.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, private",
                "X-Accel-Buffering": "no",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/v1/tasks/{task_id}/fields/corrections", status_code=202)
    def correct_fields(
        task_id: str,
        body: BatchFieldCorrectionRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.correct_fields(
            task_id=task_id,
            expected_task_revision=body.expected_task_revision,
            corrections=[item.model_dump(mode="json") for item in body.corrections],
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/api/v1/tasks/{task_id}/issues/{issue_id}/answers", status_code=202
    )
    def answer_issue(
        task_id: str,
        issue_id: str,
        body: IssueAnswerRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.answer_issue(
            task_id,
            issue_id,
            expected_task_revision=body.expected_task_revision,
            answer=body.answer.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )

    @app.post(
        "/api/v1/tasks/{task_id}/quotes/{quote_id}/fields/{field_name}/corrections",
        status_code=202,
    )
    def correct_field(
        task_id: str,
        quote_id: str,
        field_name: str,
        body: FieldCorrectionRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.correct_field(
            task_id=task_id,
            quote_id=quote_id,
            field_name=field_name,
            expected_task_revision=body.expected_task_revision,
            raw_value=body.raw_value,
            normalized_value=body.normalized_value,
            unit=body.unit,
            reason=body.reason,
            idempotency_key=idempotency_key,
        )

    @app.post(
        '/api/v1/tasks/{task_id}/corrections',
        status_code=202,
    )
    def correct_fields(
        task_id: str,
        body: BatchFieldCorrectionRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.correct_fields(
            task_id=task_id,
            expected_task_revision=body.expected_task_revision,
            corrections=[item.model_dump(mode='json') for item in body.corrections],
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v1/tasks/{task_id}/results")
    def list_results(task_id: str):
        return service.list_results(task_id)

    @app.get("/api/v1/tasks/{task_id}/results/{result_id}")
    def get_result(task_id: str, result_id: str):
        return service.get_result(task_id, result_id)

    @app.get("/api/v1/tasks/{task_id}/suppliers")
    def get_supplier_information(task_id: str, result_id: str | None = None):
        return service.supplier_information(task_id, result_id=result_id)

    @app.post("/api/v1/tasks/{task_id}/summaries", status_code=202)
    def create_summary(
        task_id: str,
        body: CreateSummaryRequest,
        idempotency_key: IdempotencyKey,
    ):
        model_config = SummaryModelConfig.from_env()
        return service.create_summary(
            task_id,
            expected_task_revision=body.expected_task_revision,
            result_id=body.result_id,
            idempotency_key=idempotency_key,
            provider=settings.supplier_model_provider,
            model_id=model_config.model_id if model_config else None,
            environment=settings.supplier_model_environment,
            prompt_version=SUMMARY_PROMPT_VERSION,
        )

    @app.get("/api/v1/tasks/{task_id}/summaries")
    def list_summaries(task_id: str):
        return service.list_summaries(task_id)

    @app.get("/api/v1/tasks/{task_id}/summaries/{summary_id}")
    def get_summary(task_id: str, summary_id: str):
        return service.get_summary(task_id, summary_id)

    @app.get("/api/v1/tasks/{task_id}/summaries/{summary_id}/exports")
    def export_summary(
        task_id: str,
        summary_id: str,
        format: Literal["md", "docx"] = Query(),
    ):
        exported = service.export_summary(
            task_id, summary_id, export_format=format
        )
        filename = exported["filename"]
        return StreamingResponse(
            iter((exported["content"],)),
            media_type=exported["media_type"],
            headers={
                "Content-Disposition": (
                    f'attachment; filename="procurement-summary.{format}"; '
                    f"filename*=UTF-8''{quote(filename)}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/v1/tasks/{task_id}/summaries/{summary_id}/retries", status_code=202)
    def retry_summary(
        task_id: str,
        summary_id: str,
        body: RetrySummaryRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.retry_summary(
            task_id,
            summary_id,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
        )

    if policy_file_import_service is not None:

        @app.get("/api/v1/policy-sets")
        def list_policy_sets(
            status: Literal["PUBLISHED"] = "PUBLISHED",
            include_inactive: bool = False,
            category: Annotated[
                str | None, Query(min_length=1, max_length=128)
            ] = None,
            region: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 50,
            offset: Annotated[int, Query(ge=0)] = 0,
        ):
            return policy_file_import_service.list_policy_sets(
                status=status,
                include_inactive=include_inactive,
                category=category,
                region=region,
                limit=limit,
                offset=offset,
            )

        @app.post(
            "/api/v1/policy-sets/{policy_set_id}/versions/"
            "{policy_set_version}/deactivate"
        )
        def deactivate_policy_set(
            policy_set_id: str,
            policy_set_version: str,
            idempotency_key: IdempotencyKey,
        ):
            return policy_file_import_service.deactivate_policy_set(
                policy_set_id,
                policy_set_version,
                idempotency_key=idempotency_key,
            )

        @app.get("/api/v1/policy-imports")
        def list_policy_imports(
            status: Literal[
                "REVIEW_REQUIRED",
                "READY_TO_PUBLISH",
                "PUBLISHING",
                "PUBLISHED",
            ]
            | None = None,
            policy_set_id: Annotated[
                str | None, Query(min_length=1, max_length=128)
            ] = None,
            policy_set_version: Annotated[
                str | None, Query(min_length=1, max_length=128)
            ] = None,
            category: Annotated[
                str | None, Query(min_length=1, max_length=128)
            ] = None,
            region: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 50,
            offset: Annotated[int, Query(ge=0)] = 0,
        ):
            return policy_file_import_service.list_imports(
                status=status,
                policy_set_id=policy_set_id,
                policy_set_version=policy_set_version,
                category=category,
                region=region,
                limit=limit,
                offset=offset,
            )

        @app.post("/api/v1/policy-imports", status_code=201)
        def upload_policy_file(
            idempotency_key: IdempotencyKey,
            metadata: Annotated[str, Form(min_length=2, max_length=32_768)],
            file: UploadFile = File(...),
        ):
            try:
                parsed_metadata = PolicyFileImportMetadata.model_validate_json(metadata)
            except ValidationError as exc:
                raise BackendError(
                    "policy_metadata_invalid",
                    "Policy upload metadata is invalid.",
                    errors=[
                        {
                            "location": list(item["loc"]),
                            "message": item["msg"],
                            "type": item["type"],
                        }
                        for item in exc.errors()
                    ],
                ) from exc
            suffix = Path(file.filename or "").suffix.lower()
            media_type = {
                ".pdf": "application/pdf",
                ".txt": "text/plain",
                ".md": "text/markdown",
            }.get(suffix, file.content_type or "application/octet-stream")
            return policy_file_import_service.upload_stream(
                metadata=parsed_metadata,
                original_filename=file.filename or "policy",
                media_type=media_type,
                stream=file.file,
                idempotency_key=idempotency_key,
            )

        @app.get("/api/v1/policy-imports/{policy_import_id}")
        def get_policy_import(policy_import_id: str):
            return policy_file_import_service.get(policy_import_id)

        @app.put("/api/v1/policy-imports/{policy_import_id}/clauses")
        def review_policy_clauses(
            policy_import_id: str,
            body: ReviewPolicyClausesRequest,
            idempotency_key: IdempotencyKey,
        ):
            return policy_file_import_service.replace_clauses(
                policy_import_id,
                expected_revision=body.expected_revision,
                clauses=body.clauses,
                idempotency_key=idempotency_key,
            )

        @app.post("/api/v1/policy-imports/{policy_import_id}/publish")
        def publish_policy_import(
            policy_import_id: str,
            body: PublishPolicyRequest,
            idempotency_key: IdempotencyKey,
        ):
            return policy_file_import_service.publish(
                policy_import_id,
                expected_revision=body.expected_revision,
                idempotency_key=idempotency_key,
            )

    return app


_engine, _sessions = create_session_factory(settings.database_url)
_embedding_config = EmbeddingConfig.from_env()
_embedding_client = SiliconFlowEmbeddingClient(_embedding_config)
_policy_importer = PolicyImporter(
    _sessions,
    _embedding_client,
    allowed_root=Path("data/policies").resolve(),
    provider=_embedding_config.provider,
)
_policy_file_import_service = PolicyFileImportService(
    _sessions,
    settings.policy_upload_storage_path,
    _policy_importer,
    actor_id=settings.test_user_id,
    max_bytes=settings.policy_upload_max_bytes,
    max_pdf_pages=settings.policy_upload_max_pdf_pages,
    max_extracted_characters=settings.policy_upload_max_extracted_characters,
)
_investigator = None
if settings.supplier_agent_enabled:
    try:
        _investigator = InvestigationRunner(
            LiveInvestigationPlanner(AgentConfig.from_env()),
            limits=AgentLimits.from_env(),
        )
    except ValidationError:
        logger.warning(
            "SUPPLIER_AGENT_ENABLED is true but the Agent model configuration is incomplete; "
            "on-demand investigations are disabled."
        )
app = create_app(
    BackendService(
        _sessions,
        settings.quote_storage_path,
        actor_id=settings.test_user_id,
        quote_dictionary_path=settings.quote_dictionary_path,
        supplier_history_root=settings.supplier_history_root,
        supplier_history_dataset_version=settings.supplier_history_dataset_version,
    ),
    readiness_check=readiness_probe(_engine),
    policy_file_import_service=_policy_file_import_service,
    allow_legacy_direct_quote_upload=settings.allow_legacy_direct_quote_upload,
    investigator=_investigator,
)
