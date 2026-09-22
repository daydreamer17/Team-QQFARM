from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.settings import settings
from supplier_comparison.rag.clients import FixedEmbeddingClient, FixedRerankClient
from supplier_comparison.rag.contracts import RetrievalRequest, RetrievalStatus
from supplier_comparison.rag.importer import PolicyImporter
from supplier_comparison.rag.models import PolicyImportRun, PolicySet, RetrievalTrace
from supplier_comparison.rag.repository import SQLPolicyRepository
from supplier_comparison.rag.retriever import HybridPolicyRetriever


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 to run PostgreSQL policy retrieval tests",
)


def _write_policy(root: Path, version: str) -> tuple[Path, list[str]]:
    root.mkdir(parents=True)
    texts = [
        "Every quote must state shipping charges.",
        "Every quote must state currency and unit price.",
        "Missing material charges require procurement review.",
    ]
    (root / "policy.md").write_text(
        "# Test Policy\n\n"
        + "\n\n".join(
            f"## [PG-{index:03d}] Clause {index}\n{body}"
            for index, body in enumerate(texts, start=1)
        ),
        encoding="utf-8",
    )
    payload = {
        "schema_version": "1.0.0",
        "policy_set_id": f"postgres-test-{version}",
        "policy_set_version": version,
        "documents": [
            {
                "policy_id": "POL-PG",
                "document_id": "DOC-PG",
                "document_version": "1.0.0",
                "title": "PostgreSQL Test Policy",
                "path": "policy.md",
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": "2027-01-01T00:00:00Z",
                "categories": ["Electronics"],
                "regions": ["SG"],
                "clauses": {
                    f"PG-{index:03d}": {
                        "control_code": "QUOTE_COMPLETENESS",
                        "rule_parameters": {},
                    }
                    for index in range(1, 4)
                },
            }
        ],
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest, texts


def test_postgres_exact_vector_retrieval_trace_and_new_session_recovery(tmp_path: Path) -> None:
    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    version = f"pg-{uuid4().hex}"
    manifest, texts = _write_policy(tmp_path / "policies", version)
    query = "Which shipping and currency fields are required?"
    base = [0.01] * 1024
    vectors = {
        texts[0]: [1.0] + base[1:],
        texts[1]: [0.9, 0.1] + base[2:],
        texts[2]: [0.1, 0.9] + base[2:],
        query: [1.0] + base[1:],
    }
    embedding = FixedEmbeddingClient(vectors, model_id="fixed-pgvector", dimension=1024)
    importer = PolicyImporter(
        sessions,
        embedding,
        allowed_root=tmp_path / "policies",
        provider="fixed-postgres",
    )
    outcome = importer.import_manifest(manifest, publish=True)
    request = RetrievalRequest(
        task_id=f"TASK-{version}",
        task_revision=1,
        snapshot_id=f"SNAP-{version}",
        policy_set_version=version,
        policy_index_version=outcome.index_version,
        query=query,
        required_control_codes=["QUOTE_COMPLETENESS"],
        category="Electronics",
        region="SG",
        evaluated_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )
    try:
        engine.dispose()
        recovered_engine = create_engine(database_url, pool_pre_ping=True)
        recovered_sessions = sessionmaker(recovered_engine, expire_on_commit=False)
        repository = SQLPolicyRepository(recovered_sessions)
        assert len(repository.load_published_clauses(request).clauses) == 3
        result = HybridPolicyRetriever(
            repository,
            embedding,
            FixedRerankClient(
                [0, 1, 2], scores=[0.99, 0.9, 0.8], model_id="fixed-rerank"
            ),
        ).retrieve(request)
        assert result.status == RetrievalStatus.OK, result.error_code
        assert result.citations[0].clause_id == "PG-001"
        with recovered_sessions() as session:
            trace = session.get(RetrievalTrace, result.retrieval_id)
            assert trace is not None and trace.status == "OK"
            assert trace.embedding_model == "fixed-pgvector"
            assert trace.rerank_model == "fixed-rerank"
            index_definitions = session.scalars(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'policy_clause_embeddings'"
                )
            ).all()
            assert all("hnsw" not in value.lower() and "ivfflat" not in value.lower() for value in index_definitions)
        recovered_engine.dispose()
    finally:
        cleanup_engine = create_engine(database_url)
        cleanup_sessions = sessionmaker(cleanup_engine)
        with cleanup_sessions.begin() as session:
            session.execute(delete(RetrievalTrace).where(RetrievalTrace.task_id == request.task_id))
            policy_set = session.scalar(select(PolicySet).where(PolicySet.policy_set_version == version))
            if policy_set is not None:
                session.delete(policy_set)
            session.execute(
                delete(PolicyImportRun).where(PolicyImportRun.policy_set_version == version)
            )
        cleanup_engine.dispose()



def test_publication_lock_releases_after_process_exit_and_publishing_resumes(tmp_path):
    import hashlib
    import subprocess
    import sys
    from io import BytesIO
    from supplier_comparison.backend.models import IdempotencyRecord
    from supplier_comparison.backend.service import ConflictError
    from supplier_comparison.rag.models import PolicyFileImport
    from supplier_comparison.rag.uploads import PolicyFileImportService, PolicyFileImportMetadata, PolicyDraftClauseInput
    database_url = os.getenv("TEST_DATABASE_URL", settings.database_url)
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    version = "recovery-" + uuid4().hex
    body = "Suppliers must provide current RoHS evidence."
    embedding = FixedEmbeddingClient({body: [0.2] * 1024}, model_id="fixed", dimension=1024)
    importer = PolicyImporter(sessions, embedding, allowed_root=tmp_path, provider="fixed")
    service = PolicyFileImportService(sessions, tmp_path / "files", importer, actor_id=version)
    child = None
    try:
        draft = service.upload_stream(metadata=PolicyFileImportMetadata(title="Recovery test",
            policy_set_id=version, policy_set_version=version, effective_from="2026-01-01T00:00:00Z",
            categories=["Electronics"], regions=["SG"]), original_filename="policy.txt",
            media_type="text/plain", stream=BytesIO(body.encode()), idempotency_key="upload")
        ident = draft["policy_import_id"]
        service.replace_clauses(ident, expected_revision=1, idempotency_key="review", clauses=[
            PolicyDraftClauseInput(clause_id="R1", title="RoHS", text=body, control_code="ROHS_COMPLIANCE")])
        with sessions.begin() as session:
            session.get(PolicyFileImport, ident).status = "PUBLISHING"
        key = int.from_bytes(hashlib.sha256(("policy-publish:" + ident).encode()).digest()[:8], "big", signed=True)
        code = ("import os,sys; from sqlalchemy import create_engine,text; "
                "engine=create_engine(os.environ['TEST_PUBLICATION_DATABASE_URL']); "
                "conn=engine.connect().execution_options(isolation_level='AUTOCOMMIT'); "
                "conn.execute(text('SELECT pg_advisory_lock(:key)'),{'key':int(sys.argv[1])}); "
                "print('LOCKED',flush=True); sys.stdin.readline(); os._exit(0)")
        child = subprocess.Popen([sys.executable, "-c", code, str(key)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env={**os.environ, "TEST_PUBLICATION_DATABASE_URL": database_url})
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(ConflictError) as busy:
            service.publish(ident, expected_revision=2, idempotency_key="busy")
        assert busy.value.code == "policy_publish_in_progress"
        child.communicate("exit\n", timeout=15)
        restored = service.publish(ident, expected_revision=2, idempotency_key="restore")
        assert restored["status"] == "PUBLISHED"
        assert service.publish(ident, expected_revision=2, idempotency_key="restore") == restored
    finally:
        if child is not None and child.poll() is None:
            child.kill(); child.wait(timeout=10)
        with sessions.begin() as session:
            session.execute(delete(PolicyFileImport).where(PolicyFileImport.actor_id == version))
            session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.actor_id == version))
            policy_set = session.scalar(select(PolicySet).where(PolicySet.policy_set_version == version))
            if policy_set is not None:
                session.delete(policy_set)
            session.execute(delete(PolicyImportRun).where(PolicyImportRun.policy_set_version == version))
        engine.dispose()
