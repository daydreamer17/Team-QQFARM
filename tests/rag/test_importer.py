from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.rag.clients import (
    EmbeddingBatch,
    FixedEmbeddingClient,
    ModelClientError,
)
from supplier_comparison.rag.importer import PolicyImportError, PolicyImporter
from supplier_comparison.rag.manifest import load_policy_manifest
from supplier_comparison.rag.models import (
    PolicyClause,
    PolicyClauseEmbedding,
    PolicyImportRun,
    PolicyIndex,
    PolicySet,
)


def _manifest(root: Path, *, body: str = "Quotes must state shipping.") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "policy.md").write_text(
        f"# Policy\n\n## [QUOTE-001] Shipping\n{body}\n\n"
        "## [QUOTE-002] Currency\nQuotes must state currency.\n",
        encoding="utf-8",
    )
    payload = {
        "schema_version": "1.0.0",
        "policy_set_id": "electronics-procurement",
        "policy_set_version": "2026.09.1",
        "documents": [
            {
                "policy_id": "POL-QUOTE",
                "document_id": "DOC-QUOTE",
                "document_version": "1.0.0",
                "title": "Quote Policy",
                "path": "policy.md",
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": None,
                "categories": ["Electronics"],
                "regions": ["SG"],
                "clauses": {
                    "QUOTE-001": {
                        "control_code": "TOTAL_COST",
                        "rule_parameters": {"field": "shipping"},
                    },
                    "QUOTE-002": {
                        "control_code": "QUOTE_COMPLETENESS",
                        "rule_parameters": {"field": "currency"},
                    },
                },
            }
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _client() -> FixedEmbeddingClient:
    return FixedEmbeddingClient(
        {
            "Quotes must state shipping.": [0.1] * 1024,
            "Quotes must state currency.": [0.2] * 1024,
        },
        model_id="BAAI/bge-m3",
        dimension=1024,
    )


def test_import_publishes_all_embeddings_and_replay_is_idempotent(
    tmp_path: Path, sessions
) -> None:
    root = tmp_path / "policies"
    manifest = _manifest(root)
    client = _client()
    importer = PolicyImporter(sessions, client, allowed_root=root, provider="fixed")

    first = importer.import_manifest(manifest, publish=True)
    second = importer.import_manifest(manifest, publish=True)

    assert first.status == "PUBLISHED"
    assert first.replayed is False
    assert second.index_version == first.index_version
    assert second.replayed is True
    assert len(client.calls) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PolicyClause)) == 2
        assert session.scalar(select(func.count()).select_from(PolicyClauseEmbedding)) == 2
        assert session.scalar(select(func.count()).select_from(PolicySet)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyImportRun)) == 2


def test_same_policy_version_with_different_hash_is_rejected(tmp_path: Path, sessions) -> None:
    root = tmp_path / "policies"
    manifest = _manifest(root)
    importer = PolicyImporter(sessions, _client(), allowed_root=root, provider="fixed")
    importer.import_manifest(manifest, publish=True)
    _manifest(root, body="Changed text under the same immutable version.")

    with pytest.raises(PolicyImportError, match="same version has different content"):
        importer.import_manifest(manifest, publish=True)
    with sessions() as session:
        runs = list(
            session.scalars(
                select(PolicyImportRun).order_by(PolicyImportRun.started_at)
            )
        )
        assert len(runs) == 2
        assert runs[-1].status == "FAILED"
        assert runs[-1].error_code == "policy_version_content_mismatch"


def test_embedding_failure_marks_index_unavailable_without_partial_vectors(
    tmp_path: Path, sessions
) -> None:
    root = tmp_path / "policies"
    manifest = _manifest(root)

    class FailingClient:
        model_id = "BAAI/bge-m3"
        dimension = 1024

        def embed(self, texts: list[str]) -> EmbeddingBatch:
            del texts
            raise ModelClientError("provider unavailable", attempts=2)

    importer = PolicyImporter(sessions, FailingClient(), allowed_root=root, provider="fixed")
    with pytest.raises(PolicyImportError, match="embedding_failed"):
        importer.import_manifest(manifest, publish=True)

    with sessions() as session:
        index = session.scalar(select(PolicyIndex))
        run = session.scalar(select(PolicyImportRun).order_by(PolicyImportRun.started_at.desc()))
        assert index is not None and index.status == "FAILED"
        assert run is not None and run.status == "FAILED"
        assert run.attempts == 2
        assert session.scalar(select(func.count()).select_from(PolicyClauseEmbedding)) == 0


def test_publish_false_builds_authoritative_clauses_without_calling_model(
    tmp_path: Path, sessions
) -> None:
    root = tmp_path / "policies"
    client = _client()
    outcome = PolicyImporter(
        sessions, client, allowed_root=root, provider="fixed"
    ).import_manifest(_manifest(root), publish=False)
    assert outcome.status == "DRAFT"
    assert client.calls == []


def test_publish_rejects_tampered_authoritative_clause_hash(tmp_path: Path, sessions) -> None:
    root = tmp_path / "policies"
    manifest = _manifest(root)
    client = _client()
    importer = PolicyImporter(sessions, client, allowed_root=root, provider="fixed")
    importer.import_manifest(manifest, publish=False)
    with sessions.begin() as session:
        clause = session.scalar(select(PolicyClause).where(PolicyClause.clause_id == "QUOTE-001"))
        assert clause is not None
        clause.text = "Tampered authoritative text."
    with pytest.raises(PolicyImportError, match="content hash mismatch"):
        importer.import_manifest(manifest, publish=True)
    with sessions() as session:
        index = session.scalar(select(PolicyIndex))
        assert index is not None and index.status == "FAILED"
        assert session.scalar(select(func.count()).select_from(PolicyClauseEmbedding)) == 0
    assert client.calls == []


def test_model_or_preprocessing_change_creates_a_new_complete_index(
    tmp_path: Path, sessions
) -> None:
    root = tmp_path / "policies"
    manifest = _manifest(root)
    first = PolicyImporter(
        sessions, _client(), allowed_root=root, provider="fixed"
    ).import_manifest(manifest, publish=True)
    second_client = FixedEmbeddingClient(
        {
            "Quotes must state shipping.": [0.1] * 1024,
            "Quotes must state currency.": [0.2] * 1024,
        },
        model_id="BAAI/bge-m3-revision-2",
        dimension=1024,
    )
    second = PolicyImporter(
        sessions,
        second_client,
        allowed_root=root,
        provider="fixed",
        preprocessing_version="policy-text/revision-2",
    ).import_manifest(manifest, publish=True)
    assert first.index_version != second.index_version
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PolicySet)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyIndex)) == 2
        assert session.scalar(select(func.count()).select_from(PolicyClauseEmbedding)) == 4


def test_electronics_policy_dataset_has_two_immutable_executable_versions() -> None:
    policy_root = Path(__file__).resolve().parents[2] / "data" / "policies"
    historical = load_policy_manifest(
        policy_root / "electronics-components" / "v1" / "manifest.json",
        allowed_root=policy_root,
    )
    current = load_policy_manifest(
        policy_root / "electronics-components" / "v2" / "manifest.json",
        allowed_root=policy_root,
    )

    assert historical.policy_set_id == current.policy_set_id == "electronics-components-procurement"
    assert historical.policy_set_version == "2026.01.1"
    assert current.policy_set_version == "2026.07.1"
    assert historical.content_sha256 != current.content_sha256
    assert len(historical.documents) == len(current.documents) == 3
    assert sum(len(document.clauses) for document in historical.documents) == 15
    assert sum(len(document.clauses) for document in current.documents) == 18
    assert {
        clause.control_code
        for document in current.documents
        for clause in document.clauses
    } == {"AMOUNT_APPROVAL", "APPROVED_SUPPLIER", "ROHS_COMPLIANCE"}

    historical_threshold = next(
        clause.rule_parameters["threshold"]
        for document in historical.documents
        for clause in document.clauses
        if clause.clause_id == "ECP-APR-002"
    )
    current_threshold = next(
        clause.rule_parameters["threshold"]
        for document in current.documents
        for clause in document.clauses
        if clause.clause_id == "ECP-APR-002"
    )
    assert historical_threshold == "8000.00"
    assert current_threshold == "7000.00"
