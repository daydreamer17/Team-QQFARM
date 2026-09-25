from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, Document, QuoteDraft, WorkflowArtifact
from supplier_comparison.backend.service import (
    BackendError,
    BackendService,
    ConflictError,
    content_hash,
)
from supplier_comparison.backend.workflow import DraftReviewRunner
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.contracts import DocumentContext, ExtractionBatch
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.criticality import POLICY_FIELDS
from supplier_comparison.rules import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DICTIONARY_PATH = ROOT / "data/contracts/quote_data_field.csv"
CANONICAL_QUOTES = ROOT / "data/generated/fixtures/extraction/canonical-quotes/quotes.csv"
def requirement() -> ProcurementRequirement:
    return ProcurementRequirement.model_validate(
        {
            "manufacturer": "QQ Demo Components",
            "manufacturer_part_number": "QW-MCU9-DEMO",
            "package": "QFN-32",
            "revision": "R1",
            "condition": "NEW",
            "allow_substitutes": False,
            "base_unit": "piece",
            "required_quantity": 1000,
            "quantity_unit": "piece",
            "budget_amount": "8000.00",
            "currency": "SGD",
            "includes_shipping": True,
            "tax_mode": "EXCLUDED",
            "other_fees_required": False,
            "planned_order_date": "2026-09-14",
            "delivery_deadline": "2026-09-19",
            "delivery_location": "Singapore",
            "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
        }
    )


class CanonicalProcessor:
    def __init__(self, work_dir: Path, supplier_id: str = "SUP-022") -> None:
        self.work_dir = work_dir
        self.supplier_id = supplier_id
        self.dictionary = QuoteDictionary.load(DICTIONARY_PATH)

    def process(
        self,
        *,
        path: Path,
        media_type: str,
        context: DocumentContext,
        budget: ModelCallBudget,
    ) -> ExtractionBatch:
        del path, media_type, budget
        with CANONICAL_QUOTES.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or ())
            row = next(item for item in reader if item["supplier_id"] == self.supplier_id)
        row.update(
            scenario_id=context.scenario_id or "",
            quote_id=context.quote_id,
            quote_version=str(context.quote_version),
            document_id=context.document_id,
            supplier_id=context.supplier_id or "",
        )
        generated = self.work_dir / f"{context.document_id}.csv"
        with generated.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)
        return FixedCsvQuoteParser(self.dictionary).parse_row(generated, context, 2)


@pytest.fixture
def service(tmp_path: Path) -> BackendService:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return BackendService(
        sessions,
        tmp_path / "quotes",
        actor_id="test-user",
        quote_dictionary_path=DICTIONARY_PATH,
    )


def _processed_canonical_draft(
    service: BackendService,
    tmp_path: Path,
    *,
    supplier_id: str = "SUP-022",
    processor: CanonicalProcessor | None = None,
    key: str = "canonical",
) -> tuple[dict, dict, dict]:
    task = service.create_task(
        requirement(),
        idempotency_key=f"create-{key}",
        scenario_id="MCU-DEMO-001",
    )
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id=supplier_id,
        original_filename=f"{key}.csv",
        media_type="text/csv",
        stream=CANONICAL_QUOTES.open("rb"),
        idempotency_key=f"upload-{key}",
        is_synthetic=True,
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    result = DraftReviewRunner(
        service,
        processor=processor or CanonicalProcessor(tmp_path, supplier_id=supplier_id),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    assert result["status"] == "REVIEW_REQUIRED"
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    return task, draft, current


def _field(current: dict, field_name: str) -> dict:
    return next(item for item in current["fields"] if item["field_name"] == field_name)


def _all_field_actions(
    current: dict,
    *,
    overrides: dict[str, dict] | None = None,
) -> list[dict]:
    overrides = overrides or {}
    assert len(current["fields"]) == 30
    assert {item["field_name"] for item in current["fields"]} == POLICY_FIELDS
    actions: list[dict] = []
    for field in current["fields"]:
        status = field["validation_status"]
        action = {
            "EXTRACTED": "CONFIRM_VALUE",
            "VERIFIED": "CONFIRM_VALUE",
            "MISSING": "CONFIRM_MISSING",
            "CONFLICT": "CONFIRM_CONFLICT",
        }[status]
        payload = {
            "action": action,
            "field_name": field["field_name"],
            "expected_field_id": field["field_id"],
            "expected_field_version": field["field_version"],
        }
        payload.update(overrides.get(field["field_name"], {}))
        actions.append(payload)
    return actions


def _review_all_fields(
    service: BackendService,
    task: dict,
    draft: dict,
    current: dict,
    *,
    key: str,
    overrides: dict[str, dict] | None = None,
) -> dict:
    return service.review_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_draft_revision=current["draft_revision"],
        schema_version=service.quote_field_schema()["schema_version"],
        actions=_all_field_actions(current, overrides=overrides),
        idempotency_key=key,
    )


def test_quote_field_schema_is_exactly_the_30_field_policy(
    service: BackendService,
) -> None:
    schema = service.quote_field_schema()
    fields = schema["fields"]

    assert schema["schema_version"] == "quote-review-schema/1.0.0"
    assert len(fields) == 30
    assert {item["field_name"] for item in fields} == POLICY_FIELDS
    assert sum(item["required_level"] == "关键" for item in fields) == 16
    assert sum(item["required_level"] == "条件关键" for item in fields) == 10
    assert sum(item["required_level"] == "可选" for item in fields) == 4
    assert "supplier_id" not in {item["field_name"] for item in fields}


def test_initial_draft_requires_full_human_review_before_formal_submit(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key="full-review"
    )

    assert current["status"] == "REVIEW_REQUIRED"
    assert len(current["fields"]) == 30
    assert all(
        {
            "field_id",
            "field_version",
            "criticality",
            "applicable",
            "required_for_submission",
            "review_state",
            "accepted_for_calculation",
            "allowed_actions",
        }
        <= set(field)
        for field in current["fields"]
    )
    assert current["schema_version"] == "quote-review-schema/1.0.0"
    assert current["human_review_complete"] is False
    assert current["submission_ready"] is False
    assert set(current["unconfirmed_fields"]) == POLICY_FIELDS
    assert current["review_progress"] == {
        "total": 30,
        "reviewed": 0,
        "confirmed": 0,
        "corrected": 0,
        "missing_confirmed": 0,
    }
    with service.session_factory() as session:
        stored = session.get(QuoteDraft, draft["quote_draft_id"])
        envelope = session.get(WorkflowArtifact, stored.review_artifact_id)
        assert envelope.payload["schema_version"] == "review-envelope/1.1.0"

    with pytest.raises(ConflictError) as premature:
        service.submit_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_task_revision=1,
            expected_draft_revision=current["draft_revision"],
            idempotency_key="submit-before-review",
        )
    assert premature.value.code == "quote_draft_not_ready"
    assert service.list_quotes(task["task_id"])["items"] == []

    reviewed = _review_all_fields(
        service,
        task,
        draft,
        current,
        key="review-all-30",
    )

    assert reviewed["status"] == "READY_TO_SUBMIT"
    assert reviewed["human_review_complete"] is True
    assert reviewed["submission_ready"] is True
    assert reviewed["calculation_ready"] is True
    assert reviewed["unconfirmed_fields"] == []
    assert reviewed["submission_blocking_fields"] == []
    assert reviewed["review_progress"]["reviewed"] == 30

    submitted = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=reviewed["draft_revision"],
        idempotency_key="submit-after-review",
    )

    assert submitted["status"] == "SUBMITTED"
    assert submitted["task_revision"] == 2
    assert len(service.list_quotes(task["task_id"])["items"]) == 1


def test_replacement_creates_new_version_and_deactivation_preserves_history(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key="replace-version-one"
    )
    reviewed = _review_all_fields(
        service,
        task,
        draft,
        current,
        key="review-version-one",
        overrides={
            "supplier_name": {
                "action": "SET_VALUE",
                "raw_value": "Reviewed Supplier",
                "normalized_value": "Reviewed Supplier",
                "unit": None,
                "reason": "人工确认供应商名称。",
            },
            "shipping_fee_status": {
                "action": "SET_VALUE",
                "raw_value": "KNOWN_AMOUNT",
                "normalized_value": "KNOWN_AMOUNT",
                "unit": None,
                "reason": "Shipping has a separately confirmed amount.",
            },
            "shipping_fee_amount": {
                "action": "SET_VALUE",
                "raw_value": "SGD 200.00",
                "normalized_value": "200.00",
                "unit": "SGD",
                "reason": "Shipping amount was confirmed from the quote.",
            },
        },
    )
    first = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=reviewed["draft_revision"],
        idempotency_key="submit-version-one",
    )

    # CanonicalProcessor rewrites authority columns into a generated fixture, so
    # align this test document with that reviewed fixture before exercising the
    # production same-document carry-forward path.
    with service.session_factory.begin() as session:
        submitted_draft = session.get(QuoteDraft, draft["quote_draft_id"])
        assert submitted_draft is not None and submitted_draft.batch_artifact_id
        submitted_batch = session.get(
            WorkflowArtifact, submitted_draft.batch_artifact_id
        )
        submitted_document = session.get(Document, first["document_id"])
        assert submitted_batch is not None and submitted_document is not None
        reviewed_sha = ExtractionBatch.model_validate(
            submitted_batch.payload
        ).parsed_input.document_sha256
        submitted_draft.sha256 = reviewed_sha
        submitted_document.sha256 = reviewed_sha

    replacement = service.create_quote_revision_draft(
        task["task_id"],
        first["quote_id"],
        expected_task_revision=2,
        idempotency_key="open-version-two",
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    assert replacement["replacement_quote_id"] == first["quote_id"]
    assert replacement["job"] is None
    assert replacement["status"] == "READY_TO_SUBMIT"
    assert replacement["review_progress"]["reviewed"] == 30
    assert replacement["unconfirmed_fields"] == []
    assert _field(replacement, "supplier_name")["normalized_value"] == "Reviewed Supplier"
    assert _field(replacement, "supplier_name")["review_state"] == "CORRECTED"
    assert _field(replacement, "shipping_fee_status")["normalized_value"] == "KNOWN_AMOUNT"
    assert _field(replacement, "shipping_fee_amount")["normalized_value"] == "200.00"

    with service.session_factory() as session:
        replacement_draft = session.get(
            QuoteDraft, replacement["quote_draft_id"]
        )
        assert replacement_draft is not None
        carried_artifacts = session.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task["task_id"],
                WorkflowArtifact.task_revision == 2,
                WorkflowArtifact.quote_id == first["quote_id"],
                WorkflowArtifact.document_id
                == replacement_draft.proposed_document_id,
                WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
            )
        ).all()
    assert {
        artifact.payload["field_name"] for artifact in carried_artifacts
    } >= {"supplier_name", "shipping_fee_status", "shipping_fee_amount"}

    # Simulate a replacement draft created before correction events were
    # persisted separately.  The immutable review envelope must remain a
    # usable audit source for those already-created drafts.
    with service.session_factory.begin() as session:
        legacy_artifacts = session.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.artifact_id.in_(
                    [artifact.artifact_id for artifact in carried_artifacts]
                )
            )
        ).all()
        for artifact in legacy_artifacts:
            artifact.artifact_type = "LEGACY_CORRECTION_EVENT"

    with pytest.raises(ConflictError) as unchanged:
        service.submit_quote_draft(
            task["task_id"],
            replacement["quote_draft_id"],
            expected_task_revision=2,
            expected_draft_revision=replacement["draft_revision"],
            idempotency_key="submit-unchanged-version-two",
        )
    assert unchanged.value.code == "quote_revision_unchanged"
    unchanged_history = service.list_quotes(task["task_id"])
    assert unchanged_history["items"][0]["current_version"] == 1
    assert len(unchanged_history["items"][0]["versions"]) == 1
    assert service.get_task(task["task_id"])["task_revision"] == 2

    replacement_current = service.get_quote_draft(
        task["task_id"], replacement["quote_draft_id"]
    )
    replacement_reviewed = _review_all_fields(
        service,
        task,
        replacement,
        replacement_current,
        key="review-version-two",
        overrides={
            "supplier_name": {
                "action": "SET_VALUE",
                "raw_value": "Updated Supplier",
                "normalized_value": "Updated Supplier",
                "unit": None,
                "reason": "Supplier name was updated for the replacement quote.",
            }
        },
    )
    with service.session_factory() as session:
        replacement_draft = session.get(
            QuoteDraft, replacement["quote_draft_id"]
        )
        assert replacement_draft is not None
        replacement_batch_artifact_id = replacement_draft.batch_artifact_id
    assert replacement_batch_artifact_id is not None
    correction_payloads = service.correction_event_payloads_for_batch(
        replacement_batch_artifact_id
    )
    assert {payload["field_name"] for payload in correction_payloads} >= {
        "supplier_name",
        "shipping_fee_status",
        "shipping_fee_amount",
    }
    second = service.submit_quote_draft(
        task["task_id"],
        replacement["quote_draft_id"],
        expected_task_revision=2,
        expected_draft_revision=replacement_reviewed["draft_revision"],
        idempotency_key="submit-version-two",
    )

    assert second["quote_id"] == first["quote_id"]
    assert second["quote_version"] == 2
    history = service.list_quotes(task["task_id"])
    assert len(history["items"]) == 1
    assert history["items"][0]["current_version"] == 2
    assert [item["quote_version"] for item in history["items"][0]["versions"]] == [2, 1]
    assert [item["is_current"] for item in history["items"][0]["versions"]] == [True, False]
    assert service.get_task(task["task_id"])["progress"]["quote_review_completed"] is True

    disabled = service.deactivate_quote(
        task["task_id"],
        first["quote_id"],
        expected_task_revision=3,
        idempotency_key="deactivate-replaced-quote",
    )

    assert disabled["active"] is False
    assert disabled["task_revision"] == 4
    assert service.get_task(task["task_id"])["quotes"] == []
    disabled_history = service.list_quotes(task["task_id"])["items"][0]
    assert disabled_history["active"] is False
    assert len(disabled_history["versions"]) == 2
    assert not any(item["is_current"] for item in disabled_history["versions"])
    change_types = [
        item["change_type"] for item in service.task_audit(task["task_id"])["revisions"]
    ]
    assert "QUOTE_REPLACEMENT_SUBMITTED" in change_types
    assert "QUOTE_DEACTIVATED" in change_types

    reactivated = service.reactivate_quote(
        task["task_id"],
        first["quote_id"],
        expected_task_revision=4,
        idempotency_key="reactivate-replaced-quote",
    )
    repeated = service.reactivate_quote(
        task["task_id"],
        first["quote_id"],
        expected_task_revision=4,
        idempotency_key="reactivate-replaced-quote",
    )

    assert repeated == reactivated
    assert reactivated["active"] is True
    assert reactivated["task_revision"] == 5
    assert service.get_task(task["task_id"])["quotes"][0]["quote_id"] == first["quote_id"]
    reactivated_history = service.list_quotes(task["task_id"])["items"][0]
    assert reactivated_history["active"] is True
    assert [item["is_current"] for item in reactivated_history["versions"]] == [True, False]
    change_types = [
        item["change_type"] for item in service.task_audit(task["task_id"])["revisions"]
    ]
    assert "QUOTE_REACTIVATED" in change_types

    with pytest.raises(ConflictError) as already_active:
        service.reactivate_quote(
            task["task_id"],
            first["quote_id"],
            expected_task_revision=5,
            idempotency_key="reactivate-already-active",
        )
    assert already_active.value.code == "quote_already_active"


def test_unknown_required_fee_can_be_submitted_but_not_calculated(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service,
        tmp_path,
        supplier_id="SUP-023",
        key="unknown-shipping",
    )
    initial_revision = current["draft_revision"]
    initial_status = _field(current, "shipping_fee_status")
    assert initial_status["validation_status"] == "MISSING"

    with service.session_factory() as session:
        artifact_ids_before = tuple(
            session.scalars(
                select(WorkflowArtifact.artifact_id).where(
                    WorkflowArtifact.task_id == task["task_id"]
                )
            )
        )

    saved = _review_all_fields(
        service,
        task,
        draft,
        current,
        key="review-unknown-shipping",
        overrides={
            "shipping_fee_status": {
                "action": "SET_VALUE",
                "raw_value": "Not stated in the quotation",
                "normalized_value": "UNKNOWN",
                "unit": None,
                "reason": "Supplier has not confirmed the shipping charge.",
            }
        },
    )

    assert "shipping_fee_status" not in saved[
        "submission_blocking_fields"
    ]
    assert saved["unconfirmed_fields"] == []
    assert saved["review_errors"] == []

    unchanged = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert unchanged["draft_revision"] == initial_revision + 1
    assert unchanged["status"] == "READY_TO_SUBMIT"
    assert _field(unchanged, "shipping_fee_status")["field_id"] != initial_status[
        "field_id"
    ]
    assert _field(unchanged, "shipping_fee_status")["normalized_value"] == "UNKNOWN"
    assert unchanged["human_review_complete"] is True
    assert unchanged["submission_ready"] is True
    assert unchanged["calculation_ready"] is False
    with service.session_factory() as session:
        artifact_ids_after = tuple(
            session.scalars(
                select(WorkflowArtifact.artifact_id).where(
                    WorkflowArtifact.task_id == task["task_id"]
                )
            )
        )
    assert set(artifact_ids_after) > set(artifact_ids_before)


@pytest.mark.parametrize(
    ("status_field", "amount_field", "status"),
    [
        ("shipping_fee_status", "shipping_fee_amount", "FREE"),
        ("other_fees_status", "other_fees_amount", "NOT_APPLICABLE"),
        ("shipping_fee_status", "shipping_fee_amount", "INCLUDED"),
        ("other_fees_status", "other_fees_amount", "UNKNOWN"),
    ],
)
def test_full_field_confirmation_cannot_bypass_fee_pair_rules(
    service: BackendService,
    tmp_path: Path,
    status_field: str,
    amount_field: str,
    status: str,
) -> None:
    key = f"fee-pair-{status.lower()}"
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key=key
    )
    overrides = {
        status_field: {
            "action": "SET_VALUE",
            "raw_value": status,
            "normalized_value": status,
            "unit": None,
            "reason": "Test the authoritative fee-pair validation.",
        },
        amount_field: {
            "action": "SET_VALUE",
            "raw_value": "SGD 1.00",
            "normalized_value": "1.00",
            "unit": "SGD",
            "reason": "Test the authoritative fee-pair validation.",
        },
    }

    saved = _review_all_fields(
        service,
        task,
        draft,
        current,
        key=f"review-{key}",
        overrides=overrides,
    )

    assert saved["submission_ready"] is False
    assert saved["unconfirmed_fields"] == []
    assert any(
        error["code"] == "FEE_STATUS_AMOUNT_CONFLICT"
        and error["field_names"] == [amount_field]
        for error in saved["review_errors"]
    )
    unchanged = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert unchanged["draft_revision"] == current["draft_revision"] + 1
    assert _field(unchanged, amount_field)["normalized_value"] == "1.00"
    with pytest.raises(BackendError):
        service.submit_quote_draft(
            task["task_id"], draft["quote_draft_id"],
            expected_task_revision=task["task_revision"],
            expected_draft_revision=saved["draft_revision"],
            idempotency_key=f"submit-invalid-{key}",
        )


@pytest.mark.parametrize(("field_name", "value", "code"), [
    ("unit_price", "not-a-number", "MONEY_VALUE_INVALID"),
    ("quote_date", "2026-02-30", "ISO_DATE_REQUIRED"),
    ("condition", "BAD_ENUM", "NORMALIZED_ENUM_INVALID"),
    ("price_basis_quantity", "many", "NORMALIZED_TYPE_INVALID"),
])
def test_invalid_input_is_saved_then_repaired_before_submission(service, tmp_path, field_name, value, code):
    task, draft, current = _processed_canonical_draft(service, tmp_path, key=f"invalid-{field_name}")
    original = _field(current, field_name)
    saved = _review_all_fields(service, task, draft, current, key=f"save-{field_name}", overrides={field_name: {
        "action": "SET_VALUE", "raw_value": value, "normalized_value": value, "unit": original["unit"],
    }})
    assert _field(saved, field_name)["normalized_value"] == value
    assert not saved["submission_ready"]
    issue = next(issue for issue in saved["review_errors"] if issue["code"] == code)
    assert issue["category"] == "INVALID_INPUT"
    assert issue["next_action"] and "EDIT_VALUE" in issue["actions"]
    assert not any("passed deterministic" in issue["message"] for issue in saved["review_errors"])
    restored = _review_all_fields(service, task, draft, saved, key=f"repair-{field_name}", overrides={field_name: {
        "action": "SET_VALUE", "raw_value": str(original["normalized_value"]),
        "normalized_value": original["normalized_value"], "unit": original["unit"],
    }})
    assert restored["submission_ready"]
    service.submit_quote_draft(task["task_id"], draft["quote_draft_id"], expected_task_revision=task["task_revision"],
        expected_draft_revision=restored["draft_revision"], idempotency_key=f"submit-repaired-{field_name}")


def test_invalid_full_review_is_atomic_even_after_a_valid_correction_action(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key="atomic-review"
    )
    original_price = _field(current, "unit_price")
    actions = _all_field_actions(current)
    price_action = next(item for item in actions if item["field_name"] == "unit_price")
    price_action.update(
        action="SET_VALUE",
        raw_value="SGD 641.00",
        normalized_value="641.00",
        unit="SGD",
        reason="Buyer corrected the extracted unit price.",
    )
    invalid = next(item for item in actions if item["field_name"] == "supplier_name")
    invalid["action"] = "CONFIRM_MISSING"
    actions.remove(price_action)
    actions.insert(0, price_action)

    with pytest.raises(BackendError) as rejected:
        service.review_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_draft_revision=current["draft_revision"],
            schema_version=service.quote_field_schema()["schema_version"],
            actions=actions,
            idempotency_key="atomic-review-invalid",
        )

    assert rejected.value.code == "quote_draft_review_invalid"
    assert any(
        error["code"] == "FIELD_REVIEW_ACTION_INVALID"
        and error["field_names"] == ["supplier_name"]
        for error in rejected.value.details["errors"]
    )
    unchanged = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert unchanged["draft_revision"] == current["draft_revision"]
    assert _field(unchanged, "unit_price")["field_id"] == original_price["field_id"]
    assert _field(unchanged, "unit_price")["normalized_value"] == "640.00"
    assert unchanged["unconfirmed_fields"] == current["unconfirmed_fields"]


def test_full_review_rejects_stale_draft_and_field_revisions(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key="review-revisions"
    )
    original_actions = _all_field_actions(current)
    reviewed = _review_all_fields(
        service,
        task,
        draft,
        current,
        key="review-current-revision",
    )

    with pytest.raises(ConflictError) as stale_draft:
        service.review_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_draft_revision=current["draft_revision"],
            schema_version=service.quote_field_schema()["schema_version"],
            actions=original_actions,
            idempotency_key="review-stale-draft-revision",
        )
    assert stale_draft.value.code == "quote_draft_revision_conflict"
    assert stale_draft.value.details == {
        "expected": current["draft_revision"],
        "actual": reviewed["draft_revision"],
    }

    stale_field_actions = _all_field_actions(reviewed)
    stale_field_actions[0]["expected_field_version"] += 1
    with pytest.raises(ConflictError) as stale_field:
        service.review_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_draft_revision=reviewed["draft_revision"],
            schema_version=service.quote_field_schema()["schema_version"],
            actions=stale_field_actions,
            idempotency_key="review-stale-field-revision",
        )
    assert stale_field.value.code == "quote_draft_field_conflict"
    assert stale_field.value.details["errors"][0]["code"] == "FIELD_VERSION_CONFLICT"


def test_formal_submit_recomputes_gate_instead_of_trusting_saved_ready_flags(
    service: BackendService,
    tmp_path: Path,
) -> None:
    task, draft, current = _processed_canonical_draft(
        service, tmp_path, key="submit-recheck"
    )
    reviewed = _review_all_fields(
        service,
        task,
        draft,
        current,
        key="review-before-recheck",
    )
    assert reviewed["submission_ready"] is True

    # Leave the persisted ready flags untouched but remove the underlying
    # version-bound human events. Formal submission must rebuild the gate from
    # the batch and events, not trust these cached booleans.
    with service.session_factory.begin() as session:
        stored = session.get(QuoteDraft, draft["quote_draft_id"])
        envelope = session.get(WorkflowArtifact, stored.review_artifact_id)
        stale_payload = dict(envelope.payload)
        stale_payload["review_events"] = []
        envelope.payload = stale_payload
        envelope.content_sha256 = content_hash(stale_payload)

    with pytest.raises(ConflictError) as blocked:
        service.submit_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_task_revision=1,
            expected_draft_revision=reviewed["draft_revision"],
            idempotency_key="submit-with-stale-gate",
        )

    assert blocked.value.code == "quote_draft_revalidation_required"
    assert set(blocked.value.details["unconfirmed_fields"]) == POLICY_FIELDS
    assert service.list_quotes(task["task_id"])["items"] == []
    assert service.get_quote_draft(
        task["task_id"], draft["quote_draft_id"]
    )["status"] == "REVIEW_REQUIRED"
