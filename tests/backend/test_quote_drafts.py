from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.backend.workflow import DraftReviewRunner
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.contracts import DocumentContext, ExtractionBatch
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.rules import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DICTIONARY_PATH = ROOT / "data/contracts/quote_data_field.csv"
CANONICAL_QUOTES = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"


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
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
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
            row = next(item for item in reader if item["supplier_id"] == "SUP-022")
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


@pytest.fixture
def service(tmp_path: Path) -> BackendService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
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
