from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, Quote, WorkflowArtifact
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.rules import ProcurementRequirement


def _requirement(*, quantity: int = 1000) -> ProcurementRequirement:
    return ProcurementRequirement.model_validate(
        {
            "manufacturer": "QQ Demo Components",
            "manufacturer_part_number": "QW-MCU9-DEMO",
            "package": "QFN-32",
            "revision": "R1",
            "condition": "NEW",
            "allow_substitutes": False,
            "base_unit": "piece",
            "required_quantity": quantity,
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
            "secondary_preference": None,
        }
    )


@pytest.fixture
def service(tmp_path: Path) -> BackendService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return BackendService(sessions, tmp_path / "quotes", actor_id="test-user")


def test_create_task_is_idempotent_and_preserves_decimal_payload(
    service: BackendService,
) -> None:
    first = service.create_task(_requirement(), idempotency_key="create-1")
    repeated = service.create_task(_requirement(), idempotency_key="create-1")

    assert repeated == first
    assert first["task_revision"] == 1
    assert first["status"] == "DRAFT"
    task = service.get_task(first["task_id"])
    assert task["requirement"]["budget_amount"] == "8000.00"


def test_reusing_idempotency_key_with_different_request_is_rejected(
    service: BackendService,
) -> None:
    service.create_task(_requirement(), idempotency_key="create-1")

    with pytest.raises(ConflictError) as raised:
        service.create_task(_requirement(quantity=1200), idempotency_key="create-1")

    assert raised.value.code == "idempotency_key_reused"


def test_quote_upload_advances_revision_and_rejects_stale_revision(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create-1")
    uploaded = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.csv",
        media_type="text/csv",
        content=b"quote data",
        idempotency_key="upload-1",
    )

    assert uploaded["task_revision"] == 2
    assert uploaded["document_sha256"] == (
        "c3d42f55c0402860fb2d6ca78685ecb583188582e0053f60db198384437be806"
    )
    assert Path(uploaded["storage_path"]).read_bytes() == b"quote data"
    repeated = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.csv",
        media_type="text/csv",
        content=b"quote data",
        idempotency_key="upload-1",
    )
    assert repeated == uploaded
    assert not list((service.storage_root / ".staging").glob("*.tmp"))
    with service.session_factory() as session:
        assert len(session.scalars(select(Quote)).all()) == 1

    with pytest.raises(ConflictError) as idempotency_collision:
        service.upload_quote(
            task["task_id"],
            expected_task_revision=1,
            supplier_id="SUP-022",
            original_filename="supplier-a.csv",
            media_type="text/csv",
            content=b"different quote data",
            idempotency_key="upload-1",
        )
    assert idempotency_collision.value.code == "idempotency_key_reused"

    with pytest.raises(ConflictError) as raised:
        service.upload_quote(
            task["task_id"],
            expected_task_revision=1,
            supplier_id="SUP-023",
            original_filename="supplier-b.csv",
            media_type="text/csv",
            content=b"other",
            idempotency_key="upload-2",
        )

    assert raised.value.code == "task_revision_conflict"


def test_artifact_keeps_unknown_schema_fields_and_is_content_addressed(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create-1")
    payload = {"schema_version": "1.1", "known": 1, "future_extension": {"x": 2}}

    artifact = service.append_artifact(
        task_id=task["task_id"],
        task_revision=1,
        artifact_type="EXTRACTION_BATCH",
        schema_version="1.1",
        payload=payload,
    )

    with service.session_factory() as session:
        stored = session.scalar(
            select(WorkflowArtifact).where(
                WorkflowArtifact.artifact_id == artifact["artifact_id"]
            )
        )
    assert stored is not None
    assert stored.payload["future_extension"] == {"x": 2}
    assert stored.content_sha256 == (
        "a9bd17d0de20167bc433524178dfad8db0d4440801325cfcb29fa232172c3d9e"
    )


def test_quote_upload_streams_in_bounded_chunks(service: BackendService) -> None:
    class TrackingStream(BytesIO):
        def __init__(self, value: bytes) -> None:
            super().__init__(value)
            self.requested_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.requested_sizes.append(size)
            return super().read(size)

    task = service.create_task(_requirement(), idempotency_key="create-stream")
    content = b"x" * (2 * 64 * 1024 + 17)
    stream = TrackingStream(content)

    uploaded = service.upload_quote_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.pdf",
        media_type="application/pdf",
        stream=stream,
        idempotency_key="upload-stream",
    )

    assert stream.requested_sizes
    assert set(stream.requested_sizes) == {64 * 1024}
    assert Path(uploaded["storage_path"]).read_bytes() == content
