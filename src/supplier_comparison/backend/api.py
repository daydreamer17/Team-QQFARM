from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
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


IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PolicyBindingRequest(ApiModel):
    policy_set_version: str = Field(min_length=1, max_length=128)
    policy_index_version: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=128)
    region: str = Field(min_length=1, max_length=64)


class CreateTaskRequest(ApiModel):
    requirement: ProcurementRequirement
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


class IssueAnswerRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)
    answer: ConfirmMissingAnswer | ShippingAmountAnswer | RetryPolicyRetrievalAnswer = Field(
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
    return request.headers.get("X-Request-ID") or f"request_{uuid4().hex}"


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
    async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
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
    def ready(request: Request):
        if readiness_check():
            return {"status": "ready"}
        return _error_response(
            request,
            status_code=503,
            code="database_unavailable",
            message="Database readiness check failed.",
            details={},
        )

    @app.get("/api/v1/quote-field-schema")
    def quote_field_schema():
        return service.quote_field_schema()

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(
        body: CreateTaskRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.create_task(
            body.requirement,
            idempotency_key=idempotency_key,
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
        return service.update_requirement(
            task_id,
            body.requirement,
            expected_task_revision=body.expected_task_revision,
            idempotency_key=idempotency_key,
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

    @app.get('/api/v1/tasks/{task_id}/selection-gaps')
    def selection_gaps(task_id: str, expected_task_revision: int = Query(ge=1)):
        return service.selection_gaps(task_id, expected_task_revision=expected_task_revision)

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
            category: Annotated[
                str | None, Query(min_length=1, max_length=128)
            ] = None,
            region: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
            limit: Annotated[int, Query(ge=1, le=100)] = 50,
            offset: Annotated[int, Query(ge=0)] = 0,
        ):
            return policy_file_import_service.list_policy_sets(
                status=status,
                category=category,
                region=region,
                limit=limit,
                offset=offset,
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
            return policy_file_import_service.upload_stream(
                metadata=parsed_metadata,
                original_filename=file.filename or "policy",
                media_type=file.content_type or "application/octet-stream",
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
app = create_app(
    BackendService(
        _sessions,
        settings.quote_storage_path,
        actor_id=settings.test_user_id,
        quote_dictionary_path=settings.quote_dictionary_path,
    ),
    readiness_check=readiness_probe(_engine),
    policy_file_import_service=_policy_file_import_service,
    allow_legacy_direct_quote_upload=settings.allow_legacy_direct_quote_upload,
)
