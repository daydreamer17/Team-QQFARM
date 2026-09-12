from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from supplier_comparison.rules import ProcurementRequirement

from .database import create_session_factory, readiness_probe
from .service import BackendError, BackendService, ConflictError, NotFoundError
from .settings import settings


IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CreateTaskRequest(ApiModel):
    requirement: ProcurementRequirement
    scenario_id: str | None = Field(default=None, max_length=128)


class StartRunRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)


class ConfirmMissingAnswer(ApiModel):
    answer_type: Literal["CONFIRM_MISSING"]


class ShippingAmountAnswer(ApiModel):
    answer_type: Literal["SHIPPING_AMOUNT"]
    amount: Decimal = Field(ge=0)
    currency: Literal["SGD"]

    @field_validator("amount", mode="before")
    @classmethod
    def reject_float_amount(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("money must be supplied as a decimal string")
        return value


class IssueAnswerRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)
    answer: ConfirmMissingAnswer | ShippingAmountAnswer = Field(discriminator="answer_type")


class FieldCorrectionRequest(ApiModel):
    expected_task_revision: int = Field(ge=1)
    raw_value: str = Field(min_length=1)
    normalized_value: StrictStr | StrictInt | StrictBool
    unit: str | None = None
    reason: str = Field(min_length=3, max_length=1000)


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

    @app.post("/api/v1/tasks", status_code=201)
    def create_task(
        body: CreateTaskRequest,
        idempotency_key: IdempotencyKey,
    ):
        return service.create_task(
            body.requirement,
            idempotency_key=idempotency_key,
            scenario_id=body.scenario_id,
        )

    @app.get("/api/v1/tasks/{task_id}")
    def get_task(task_id: str):
        return service.get_task(task_id)

    @app.post("/api/v1/tasks/{task_id}/quotes", status_code=201)
    def upload_quote(
        task_id: str,
        idempotency_key: IdempotencyKey,
        expected_task_revision: Annotated[int, Form(ge=1)],
        supplier_id: Annotated[str, Form(min_length=1, max_length=128)],
        is_synthetic: Annotated[bool, Form()] = False,
        file: UploadFile = File(...),
    ):
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

    @app.get("/api/v1/tasks/{task_id}/issues")
    def list_issues(task_id: str):
        return service.list_issues(task_id)

    @app.get("/api/v1/tasks/{task_id}/quotes/{quote_id}/fields")
    def list_quote_fields(task_id: str, quote_id: str):
        return service.list_quote_fields(task_id, quote_id)

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

    @app.get("/api/v1/tasks/{task_id}/results")
    def list_results(task_id: str):
        return service.list_results(task_id)

    @app.get("/api/v1/tasks/{task_id}/results/{result_id}")
    def get_result(task_id: str, result_id: str):
        return service.get_result(task_id, result_id)

    return app


_engine, _sessions = create_session_factory(settings.database_url)
app = create_app(
    BackendService(_sessions, settings.quote_storage_path, actor_id=settings.test_user_id),
    readiness_check=readiness_probe(_engine),
)
