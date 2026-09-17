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
    assert draft["status"] == "REVIEW_REQUIRED"
    assert "storage_path" not in draft
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
