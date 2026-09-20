from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, GraphRun, Job, QuoteDraft, WorkflowArtifact
from supplier_comparison.backend.service import (
    BackendError,
    BackendService,
    ConflictError,
    content_hash,
)
from supplier_comparison.backend.workflow import DefaultQuoteProcessor, DraftReviewRunner
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.contracts import (
    DocumentContext,
    ExtractionBatch,
)
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.criticality import POLICY_FIELDS
from supplier_comparison.rules import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DICTIONARY_PATH = ROOT / "data/contracts/quote_data_field.csv"
CANONICAL_QUOTES = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"
V9_ROOT = ROOT / "data/generated/inputs/development/quote_V9"
V9_REQUIREMENT = V9_ROOT / "procurement_requirement_v9_cost.csv"
V9_QUOTES = tuple(sorted(V9_ROOT.glob("v9_supplier_?.csv")))
V9_FIVE_QUOTE_FLOW = tuple(
    V9_ROOT / f"v9_supplier_{alias}.csv" for alias in ("b", "c", "d", "e", "g")
)


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


def v9_requirement() -> ProcurementRequirement:
    with V9_REQUIREMENT.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    row["secondary_preference"] = row["secondary_preference"] or None
    return ProcurementRequirement.model_validate(row)


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
    )
    first = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=reviewed["draft_revision"],
        idempotency_key="submit-version-one",
    )

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
    DraftReviewRunner(
        service,
        processor=CanonicalProcessor(tmp_path),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(replacement["job"]["job_id"])
    replacement_current = service.get_quote_draft(
        task["task_id"], replacement["quote_draft_id"]
    )
    replacement_reviewed = _review_all_fields(
        service,
        task,
        replacement,
        replacement_current,
        key="review-version-two",
    )
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


def test_unknown_required_fee_blocks_review_and_rolls_back_all_actions(
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

    with pytest.raises(BackendError) as blocked:
        _review_all_fields(
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

    assert blocked.value.code == "quote_draft_review_failed"
    assert "shipping_fee_status" in blocked.value.details[
        "submission_blocking_fields"
    ]
    assert blocked.value.details["unconfirmed_fields"] == []
    assert any(
        error["code"] == "FEE_STATUS_UNKNOWN"
        and error["field_names"] == ["shipping_fee_status"]
        for error in blocked.value.details["errors"]
    )

    unchanged = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert unchanged["draft_revision"] == initial_revision
    assert unchanged["status"] == "REVIEW_REQUIRED"
    assert _field(unchanged, "shipping_fee_status")["field_id"] == initial_status[
        "field_id"
    ]
    assert _field(unchanged, "shipping_fee_status")["validation_status"] == "MISSING"
    assert unchanged["human_review_complete"] is False
    with service.session_factory() as session:
        artifact_ids_after = tuple(
            session.scalars(
                select(WorkflowArtifact.artifact_id).where(
                    WorkflowArtifact.task_id == task["task_id"]
                )
            )
        )
    assert artifact_ids_after == artifact_ids_before


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

    with pytest.raises(BackendError) as blocked:
        _review_all_fields(
            service,
            task,
            draft,
            current,
            key=f"review-{key}",
            overrides=overrides,
        )

    assert blocked.value.code == "quote_draft_review_failed"
    assert blocked.value.details["unconfirmed_fields"] == []
    assert any(
        error["code"] == "FEE_STATUS_AMOUNT_CONFLICT"
        and error["field_names"] == [amount_field]
        for error in blocked.value.details["errors"]
    )
    unchanged = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert unchanged["draft_revision"] == current["draft_revision"]
    assert _field(unchanged, amount_field)["normalized_value"] == "0.00"


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


@pytest.mark.parametrize("quote_path", V9_QUOTES, ids=lambda path: path.stem)
def test_v9_csv_drafts_require_full_review_then_reach_submission(
    service: BackendService,
    quote_path: Path,
) -> None:
    with quote_path.open("r", encoding="utf-8-sig", newline="") as handle:
        quote_row = next(csv.DictReader(handle))
    supplier_id = quote_row["supplier_id"]
    task = service.create_task(
        v9_requirement(),
        idempotency_key=f"create-{quote_path.stem}",
        scenario_id="MCU-V9-TRADEOFF",
    )
    with quote_path.open("rb") as stream:
        draft = service.upload_quote_draft_stream(
            task["task_id"],
            expected_task_revision=1,
            supplier_id=supplier_id,
            original_filename=quote_path.name,
            media_type="text/csv",
            stream=stream,
            idempotency_key=f"draft-upload-{quote_path.stem}",
            is_synthetic=True,
            provider="fixed",
            model_id="fixed-output",
            environment="FIXED_TEST",
            prompt_version="quote-extraction/1.0.0",
        )

    assert draft["status"] == "PROCESSING"
    processed = DraftReviewRunner(
        service,
        processor=DefaultQuoteProcessor(QuoteDictionary.load(DICTIONARY_PATH)),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])

    assert processed["status"] == "REVIEW_REQUIRED"
    assert current["human_review_complete"] is False
    assert len(current["unconfirmed_fields"]) == 30

    reviewed = _review_all_fields(
        service,
        task,
        draft,
        current,
        key=f"review-all-{quote_path.stem}",
        overrides={
            # V9 deliberately keeps the source spelling (PIECE/TRAY).  The
            # quote dictionary accepts canonical lower-case enum values only,
            # so a human correction is required; blindly confirming the
            # parser value must remain a hard submission blocker.
            "packaging_type": {
                "action": "SET_VALUE",
                "raw_value": quote_row["packaging_type"],
                "normalized_value": quote_row["packaging_type"].lower(),
                "unit": None,
                "reason": "Buyer normalized the packaging enum after review.",
            }
        },
    )
    assert reviewed["status"] == "READY_TO_SUBMIT"
    assert reviewed["human_review_complete"] is True
    assert reviewed["submission_ready"] is True

    submitted = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=reviewed["draft_revision"],
        idempotency_key=f"draft-submit-{quote_path.stem}",
    )
    assert submitted["status"] == "SUBMITTED"
    assert submitted["task_revision"] == 2
    assert service.list_quotes(task["task_id"])["items"][0]["supplier_id"] == supplier_id


def test_five_v9_quotes_only_start_comparison_after_explicit_run(
    service: BackendService,
) -> None:
    """Submitting reviewed quotes must not implicitly start comparison work."""

    task = service.create_task(
        v9_requirement(),
        idempotency_key="create-five-quote-manual-run",
        scenario_id="MCU-V9-FIVE-QUOTE-MANUAL-RUN",
    )
    task_revision = task["task_revision"]
    processor = DefaultQuoteProcessor(QuoteDictionary.load(DICTIONARY_PATH))

    for position, quote_path in enumerate(V9_FIVE_QUOTE_FLOW, start=1):
        with quote_path.open("r", encoding="utf-8-sig", newline="") as handle:
            quote_row = next(csv.DictReader(handle))
        supplier_id = quote_row["supplier_id"]
        with quote_path.open("rb") as stream:
            draft = service.upload_quote_draft_stream(
                task["task_id"],
                expected_task_revision=task_revision,
                supplier_id=supplier_id,
                original_filename=quote_path.name,
                media_type="text/csv",
                stream=stream,
                idempotency_key=f"upload-five-quote-{quote_path.stem}",
                is_synthetic=True,
                provider="fixed",
                model_id="fixed-output",
                environment="FIXED_TEST",
                prompt_version="quote-extraction/1.0.0",
            )

        processed = DraftReviewRunner(
            service,
            processor=processor,
            dictionary_path=DICTIONARY_PATH,
        ).run_job(draft["job"]["job_id"])
        assert processed["status"] == "REVIEW_REQUIRED"
        current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
        reviewed = _review_all_fields(
            service,
            task,
            draft,
            current,
            key=f"review-five-quote-{quote_path.stem}",
            overrides={
                "packaging_type": {
                    "action": "SET_VALUE",
                    "raw_value": quote_row["packaging_type"],
                    "normalized_value": quote_row["packaging_type"].lower(),
                    "unit": None,
                    "reason": "Buyer normalized the packaging enum after review.",
                }
            },
        )
        submitted = service.submit_quote_draft(
            task["task_id"],
            draft["quote_draft_id"],
            expected_task_revision=task_revision,
            expected_draft_revision=reviewed["draft_revision"],
            idempotency_key=f"submit-five-quote-{quote_path.stem}",
        )
        task_revision = submitted["task_revision"]

        assert len(service.list_quotes(task["task_id"])["items"]) == position
        with service.session_factory() as session:
            assert session.scalars(
                select(GraphRun).where(GraphRun.task_id == task["task_id"])
            ).all() == []
            assert session.scalars(
                select(Job).where(
                    Job.task_id == task["task_id"],
                    Job.job_type == "START",
                )
            ).all() == []

    started = service.start_run(
        task["task_id"],
        expected_task_revision=task_revision,
        idempotency_key="explicit-start-after-five-quotes",
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )

    assert started["job_type"] == "START"
    assert started["job_status"] == "PENDING"
    with service.session_factory() as session:
        graphs = session.scalars(
            select(GraphRun).where(GraphRun.task_id == task["task_id"])
        ).all()
        start_jobs = session.scalars(
            select(Job).where(
                Job.task_id == task["task_id"],
                Job.job_type == "START",
            )
        ).all()
    assert [graph.graph_run_id for graph in graphs] == [started["graph_run_id"]]
    assert [job.job_id for job in start_jobs] == [started["job_id"]]
