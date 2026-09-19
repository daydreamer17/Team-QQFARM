from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.backend.workflow import DefaultQuoteProcessor, DraftReviewRunner
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    DocumentContext,
    ExtractionBatch,
    QuoteFieldCandidate,
    ValidationStatus,
)
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.rules import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DICTIONARY_PATH = ROOT / "data/contracts/quote_data_field.csv"
CANONICAL_QUOTES = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"
V9_ROOT = ROOT / "data/generated/inputs/development/quote_V9"
V9_REQUIREMENT = V9_ROOT / "procurement_requirement_v9_cost.csv"
V9_QUOTES = tuple(sorted(V9_ROOT.glob("v9_supplier_?.csv")))


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
            quote_version="1",
            document_id=context.document_id,
            supplier_id=context.supplier_id or "",
        )
        generated = self.work_dir / f"{context.document_id}.csv"
        with generated.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)
        return FixedCsvQuoteParser(self.dictionary).parse_row(generated, context, 2)


class TwoBlockingFieldsProcessor(CanonicalProcessor):
    """Create two independent blockers to exercise incremental draft corrections."""

    def process(self, **kwargs) -> ExtractionBatch:
        batch = super().process(**kwargs)
        candidates = []
        for candidate in batch.candidates:
            if candidate.field_name != "tax_mode":
                candidates.append(candidate)
                continue
            values = candidate.model_dump(mode="python")
            values.update(
                raw_value=None,
                normalized_value=None,
                unit=None,
                validation_status=ValidationStatus.MISSING,
                origin=None,
                source_refs=(),
                producer=CandidateProducer.DETERMINISTIC_PARSER,
            )
            candidates.append(QuoteFieldCandidate.model_validate(values))
        return batch.model_copy(update={"candidates": tuple(candidates)})


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


def test_draft_review_and_submit_is_a_single_authoritative_revision(
    service: BackendService, tmp_path: Path
) -> None:
    task = service.create_task(requirement(), idempotency_key="create", scenario_id="MCU-DEMO-001")
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.csv",
        media_type="text/csv",
        stream=CANONICAL_QUOTES.open("rb"),
        idempotency_key="draft-upload",
        is_synthetic=True,
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    assert draft["status"] == "PROCESSING"
    assert service.get_task(task["task_id"])["task_revision"] == 1
    assert service.list_quotes(task["task_id"])["items"] == []

    with pytest.raises(ConflictError) as duplicate:
        service.upload_quote_draft_stream(
            task["task_id"],
            expected_task_revision=1,
            supplier_id="SUP-023",
            original_filename="supplier-b.csv",
            media_type="text/csv",
            stream=CANONICAL_QUOTES.open("rb"),
            idempotency_key="draft-upload-2",
        )
    assert duplicate.value.code == "active_quote_draft_exists"

    reviewed = DraftReviewRunner(
        service,
        processor=CanonicalProcessor(tmp_path),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    assert reviewed["status"] == "READY_TO_SUBMIT"
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    assert current["fields"]

    submitted = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=current["draft_revision"],
        idempotency_key="draft-submit",
    )
    assert submitted["task_revision"] == 2
    assert submitted["status"] == "SUBMITTED"
    assert len(service.list_quotes(task["task_id"])["items"]) == 1


def test_confirmed_unknown_shipping_can_enter_formal_workflow(
    service: BackendService, tmp_path: Path
) -> None:
    task = service.create_task(
        requirement(), idempotency_key="create-missing-shipping", scenario_id="MCU-DEMO-001"
    )
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-023",
        original_filename="supplier-b.csv",
        media_type="text/csv",
        stream=CANONICAL_QUOTES.open("rb"),
        idempotency_key="draft-upload-missing-shipping",
        is_synthetic=True,
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    reviewed = DraftReviewRunner(
        service,
        processor=CanonicalProcessor(tmp_path, supplier_id="SUP-023"),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    assert reviewed["status"] == "REVIEW_REQUIRED"

    corrected = service.correct_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_draft_revision=1,
        corrections=[
            {
                "field_name": "shipping_fee_status",
                "raw_value": "Not stated in the quotation",
                "normalized_value": "UNKNOWN",
                "unit": None,
                "reason": "The quotation does not state a shipping charge.",
            }
        ],
        idempotency_key="confirm-missing-shipping",
    )
    assert corrected["status"] == "READY_TO_SUBMIT"
    assert next(
        field for field in corrected["fields"] if field["field_name"] == "shipping_fee_amount"
    )["normalized_value"] is None

    submitted = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=corrected["draft_revision"],
        idempotency_key="submit-missing-shipping",
    )
    assert submitted["status"] == "SUBMITTED"


def test_incremental_draft_corrections_keep_prior_audit_events(
    service: BackendService, tmp_path: Path
) -> None:
    task = service.create_task(
        requirement(), idempotency_key="create-two-blockers", scenario_id="MCU-DEMO-001"
    )
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-023",
        original_filename="supplier-b.csv",
        media_type="text/csv",
        stream=CANONICAL_QUOTES.open("rb"),
        idempotency_key="draft-upload-two-blockers",
        is_synthetic=True,
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    reviewed = DraftReviewRunner(
        service,
        processor=TwoBlockingFieldsProcessor(tmp_path, supplier_id="SUP-023"),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    assert reviewed["status"] == "REVIEW_REQUIRED"

    first = service.correct_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_draft_revision=1,
        corrections=[{
            "field_name": "shipping_fee_status",
            "raw_value": "Not stated in the quotation",
            "normalized_value": "UNKNOWN",
            "unit": None,
            "reason": "Supplier document does not state shipping.",
        }],
        idempotency_key="correct-shipping-first",
    )
    assert first["status"] == "REVIEW_REQUIRED"

    second = service.correct_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_draft_revision=first["draft_revision"],
        corrections=[{
            "field_name": "tax_mode",
            "raw_value": "Tax excluded",
            "normalized_value": "EXCLUDED",
            "unit": None,
            "reason": "Supplier confirmed that tax is excluded.",
        }],
        idempotency_key="correct-tax-second",
    )

    assert second["status"] == "READY_TO_SUBMIT"
    assert not any(
        "CORRECTION_AUDIT_MISSING" in finding["codes"]
        for finding in second["review_findings"]
    )


@pytest.mark.parametrize("quote_path", V9_QUOTES, ids=lambda path: path.stem)
def test_v9_csv_drafts_respect_foreign_keys_and_reach_submission(
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
    reviewed = DraftReviewRunner(
        service,
        processor=DefaultQuoteProcessor(QuoteDictionary.load(DICTIONARY_PATH)),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    current = service.get_quote_draft(task["task_id"], draft["quote_draft_id"])
    unresolved = [
        finding
        for finding in current["review_findings"]
        if finding["decision"] != "PASS"
    ]
    assert reviewed["status"] == "READY_TO_SUBMIT", json.dumps(
        unresolved, ensure_ascii=False, indent=2
    )

    submitted = service.submit_quote_draft(
        task["task_id"],
        draft["quote_draft_id"],
        expected_task_revision=1,
        expected_draft_revision=current["draft_revision"],
        idempotency_key=f"draft-submit-{quote_path.stem}",
    )
    assert submitted["status"] == "SUBMITTED"
    assert submitted["task_revision"] == 2
    assert service.list_quotes(task["task_id"])["items"][0]["supplier_id"] == supplier_id
