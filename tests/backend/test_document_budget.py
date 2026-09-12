from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendError, BackendService
from supplier_comparison.rules import ProcurementRequirement


def _requirement() -> ProcurementRequirement:
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


def _service(tmp_path: Path) -> BackendService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return BackendService(sessions, tmp_path / "quotes", actor_id="test-user")


def test_document_call_budget_survives_resume_and_cannot_be_reset(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = service.create_task(_requirement(), idempotency_key="create")
    quote = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="quote.csv",
        media_type="text/csv",
        content=b"quote",
        idempotency_key="upload",
    )
    run = service.start_run(
        task["task_id"], expected_task_revision=2, idempotency_key="run"
    )

    first = service.ensure_document_execution(
        graph_run_id=run["graph_run_id"],
        document_id=quote["document_id"],
        max_calls=8,
    )
    service.record_document_calls(
        first["document_execution_id"], expected_calls_used=0, calls_after=3
    )
    resumed = service.ensure_document_execution(
        graph_run_id=run["graph_run_id"],
        document_id=quote["document_id"],
        max_calls=8,
    )

    assert resumed["document_execution_id"] == first["document_execution_id"]
    assert resumed["calls_used"] == 3
    assert resumed["max_calls"] == 8

    with pytest.raises(BackendError) as raised:
        service.record_document_calls(
            first["document_execution_id"], expected_calls_used=3, calls_after=9
        )
    assert raised.value.code == "model_call_budget_exceeded"
