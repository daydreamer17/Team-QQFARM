from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from supplier_comparison.backend.api import create_app
from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService
from supplier_comparison.rag.clients import FixedEmbeddingClient
from supplier_comparison.rag.importer import PolicyImporter
from supplier_comparison.rag.uploads import PolicyFileImportService


TASK_REQUIREMENT = {
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


@pytest.fixture
def client(tmp_path: Path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    backend = BackendService(sessions, tmp_path / "quotes", actor_id="local-test-user")
    clause_text = "Suppliers must provide current RoHS evidence."
    embedding = FixedEmbeddingClient(
        {clause_text: [0.2] * 1024}, model_id="BAAI/bge-m3", dimension=1024
    )
    importer = PolicyImporter(
        sessions,
        embedding,
        allowed_root=tmp_path / "manifests",
        provider="fixed",
    )
    policy_files = PolicyFileImportService(
        sessions,
        tmp_path / "policy-files",
        importer,
        actor_id="local-test-user",
    )
    with TestClient(
        create_app(
            backend,
            readiness_check=lambda: True,
            policy_file_import_service=policy_files,
        )
    ) as http:
        yield http
    engine.dispose()


def test_policy_markdown_upload_api(client: TestClient) -> None:
    metadata = {
        "title": "Supplier Qualification Policy",
        "effective_from": "2026-09-17T00:00:00Z",
        "effective_to": None,
        "categories": ["Electronics"],
        "regions": ["SG"],
    }
    content = "## [QUAL-001] Qualification\nSuppliers must provide current evidence."

    uploaded = client.post(
        "/api/v1/policy-imports",
        headers={"Idempotency-Key": "upload-policy-markdown-api-1"},
        data={"metadata": json.dumps(metadata)},
        files={
            "file": (
                "supplier_qualification.md",
                content.encode("utf-8"),
                "text/markdown",
            )
        },
    )

    assert uploaded.status_code == 201
    payload = uploaded.json()
    assert payload["original_filename"] == "supplier_qualification.md"
    assert payload["media_type"] == "text/markdown"
    assert payload["extracted_text"] == content
    assert payload["clauses"][0]["clause_id"] == "QUAL-001"


def test_policy_markdown_upload_accepts_generic_browser_media_type(
    client: TestClient,
) -> None:
    metadata = {
        "title": "Supplier Qualification Policy",
        "effective_from": "2026-09-17T00:00:00Z",
        "effective_to": None,
        "categories": ["Electronics"],
        "regions": ["SG"],
    }
    content = "## [QUAL-001] Qualification\nSuppliers must provide current evidence."

    uploaded = client.post(
        "/api/v1/policy-imports",
        headers={"Idempotency-Key": "upload-policy-markdown-generic-media-1"},
        data={"metadata": json.dumps(metadata)},
        files={
            "file": (
                "supplier_qualification.md",
                content.encode("utf-8"),
                "application/octet-stream",
            )
        },
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["media_type"] == "text/markdown"


def test_policy_txt_upload_review_and_publish_api(client: TestClient) -> None:
    http = client
    metadata = {
        "policy_set_id": "uploaded-electronics-policy",
        "policy_set_version": "2026.09.1",
        "policy_id": "POL-UPLOAD-001",
        "document_id": "DOC-UPLOAD-001",
        "document_version": "1.0.0",
        "title": "Uploaded Electronics Policy",
        "effective_from": "2026-09-17T00:00:00Z",
        "effective_to": None,
        "categories": ["Electronics"],
        "regions": ["SG"],
    }
    uploaded = http.post(
        "/api/v1/policy-imports",
        headers={"Idempotency-Key": "upload-policy-1"},
        data={"metadata": json.dumps(metadata)},
        files={
            "file": (
                "policy.txt",
                b"Suppliers must provide current RoHS evidence.",
                "text/plain",
            )
        },
    )

    assert uploaded.status_code == 201
    draft = uploaded.json()
    assert draft["status"] == "READY_TO_PUBLISH"
    assert "storage_path" not in draft

    import_list = http.get(
        "/api/v1/policy-imports",
        params={
            "status": "READY_TO_PUBLISH",
            "category": "Electronics",
            "region": "SG",
            "limit": 1,
            "offset": 0,
        },
    )
    assert import_list.status_code == 200
    assert import_list.json()["total"] == 1
    assert import_list.json()["limit"] == 1
    assert import_list.json()["offset"] == 0
    assert import_list.json()["items"] == [
        {
            "policy_import_id": draft["policy_import_id"],
            "status": "READY_TO_PUBLISH",
            "revision": 1,
            "original_filename": "policy.txt",
            "media_type": "text/plain",
            "size_bytes": len(b"Suppliers must provide current RoHS evidence."),
            "policy_set_id": "uploaded-electronics-policy",
            "policy_set_version": "2026.09.1",
            "policy_id": "POL-UPLOAD-001",
            "document_id": "DOC-UPLOAD-001",
            "document_version": "1.0.0",
            "title": "Uploaded Electronics Policy",
            "categories": ["Electronics"],
            "regions": ["SG"],
            "policy_index_version": None,
            "clause_count": 1,
            "created_at": import_list.json()["items"][0]["created_at"],
            "updated_at": import_list.json()["items"][0]["updated_at"],
        }
    ]
    assert "extracted_text" not in import_list.json()["items"][0]
    assert "clauses" not in import_list.json()["items"][0]
    assert "storage_path" not in import_list.json()["items"][0]
    next_page = http.get(
        "/api/v1/policy-imports", params={"limit": 1, "offset": 1}
    )
    assert next_page.json() == {"items": [], "total": 1, "limit": 1, "offset": 1}

    assert http.get(
        "/api/v1/policy-imports", params={"category": "Packaging"}
    ).json()["total"] == 0

    loaded = http.get(f"/api/v1/policy-imports/{draft['policy_import_id']}")
    assert loaded.status_code == 200
    assert loaded.json() == draft

    reviewed = http.put(
        f"/api/v1/policy-imports/{draft['policy_import_id']}/clauses",
        headers={"Idempotency-Key": "review-policy-1"},
        json={
            "expected_revision": 1,
            "clauses": [
                {
                    "clause_id": "ROHS-001",
                    "title": "Evidence requirement",
                    "text": "Suppliers must provide current RoHS evidence.",
                    "control_code": "ROHS_COMPLIANCE",
                    "rule_parameters": {},
                }
            ],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "READY_TO_PUBLISH"

    published = http.post(
        f"/api/v1/policy-imports/{draft['policy_import_id']}/publish",
        headers={"Idempotency-Key": "publish-policy-1"},
        json={"expected_revision": 2},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    assert published.json()["policy_index_version"].startswith("pidx-")

    published_imports = http.get(
        "/api/v1/policy-imports",
        params={"status": "PUBLISHED", "policy_set_version": "2026.09.1"},
    )
    assert published_imports.status_code == 200
    assert published_imports.json()["total"] == 1
    assert published_imports.json()["items"][0]["status"] == "PUBLISHED"

    policy_sets = http.get(
        "/api/v1/policy-sets",
        params={"category": "Electronics", "region": "SG", "limit": 10},
    )
    assert policy_sets.status_code == 200
    payload = policy_sets.json()
    assert payload["total"] == 1
    assert payload["limit"] == 10
    assert payload["offset"] == 0
    assert payload["items"] == [
        {
            "policy_set_id": "uploaded-electronics-policy",
            "policy_set_version": "2026.09.1",
            "policy_index_version": published.json()["policy_index_version"],
            "status": "PUBLISHED",
            "categories": ["Electronics"],
            "regions": ["SG"],
            "document_count": 1,
            "clause_count": 1,
            "provider": "fixed",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dimension": 1024,
            "preprocessing_version": "policy-text/nfkc-en-hyphen-v1",
            "published_at": payload["items"][0]["published_at"],
        }
    ]
    assert payload["items"][0]["published_at"].endswith("Z")
    assert http.get(
        "/api/v1/policy-sets", params={"region": "US"}
    ).json()["total"] == 0

    active_binding = {
        "policy_set_version": "2026.09.1",
        "policy_index_version": published.json()["policy_index_version"],
        "category": "Electronics",
        "region": "SG",
    }
    bound_task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-with-active-policy"},
        json={"requirement": TASK_REQUIREMENT, "policy_binding": active_binding},
    )
    assert bound_task.status_code == 201

    deactivated = http.post(
        "/api/v1/policy-sets/uploaded-electronics-policy/versions/"
        "2026.09.1/deactivate",
        headers={"Idempotency-Key": "deactivate-policy-set-1"},
    )
    assert deactivated.status_code == 200
    assert deactivated.json() == {
        "policy_set_id": "uploaded-electronics-policy",
        "policy_set_version": "2026.09.1",
        "status": "INACTIVE",
    }
    assert http.get("/api/v1/policy-sets").json()["total"] == 0
    inactive_sets = http.get(
        "/api/v1/policy-sets", params={"include_inactive": True}
    )
    assert inactive_sets.status_code == 200
    assert inactive_sets.json()["total"] == 1
    assert inactive_sets.json()["items"][0]["status"] == "INACTIVE"

    rejected_binding = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-with-inactive-policy"},
        json={
            "requirement": TASK_REQUIREMENT,
            "policy_binding": active_binding,
        },
    )
    assert rejected_binding.status_code == 409
    assert rejected_binding.json()["error"]["code"] == "policy_binding_unavailable"

    updated_requirement = {**TASK_REQUIREMENT, "budget_amount": "8100.00"}
    existing_binding_update = http.put(
        f"/api/v1/tasks/{bound_task.json()['task_id']}/requirement",
        headers={"Idempotency-Key": "update-with-frozen-policy"},
        json={
            "expected_task_revision": 1,
            "requirement": updated_requirement,
            "policy_binding": active_binding,
        },
    )
    assert existing_binding_update.status_code == 202
    loaded_bound_task = http.get(
        f"/api/v1/tasks/{bound_task.json()['task_id']}"
    )
    assert loaded_bound_task.status_code == 200
    assert loaded_bound_task.json()["policy_binding"] == active_binding


def test_policy_upload_rejects_invalid_metadata_with_standard_error(
    client: TestClient,
) -> None:
    http = client
    response = http.post(
        "/api/v1/policy-imports",
        headers={"Idempotency-Key": "bad-policy"},
        data={"metadata": "{}"},
        files={"file": ("policy.txt", b"Policy text", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "policy_metadata_invalid"


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/policy-imports", {"status": "FAILED"}),
        ("/api/v1/policy-imports", {"limit": 101}),
        ("/api/v1/policy-sets", {"status": "DRAFT"}),
        ("/api/v1/policy-sets", {"offset": -1}),
    ],
)
def test_policy_list_rejects_unsupported_filters(
    client: TestClient, path: str, params: dict[str, object]
) -> None:
    response = client.get(path, params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_failed"
