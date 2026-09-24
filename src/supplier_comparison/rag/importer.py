from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from supplier_comparison.backend.models import utc_now

from .clients import EmbeddingClient
from .manifest import LoadedPolicyManifest, load_policy_manifest
from .models import (
    PolicyClause,
    PolicyClauseEmbedding,
    PolicyDocument,
    PolicyImportRun,
    PolicyIndex,
    PolicySet,
)
from .preprocessing import PREPROCESSING_VERSION, normalize_policy_text


class PolicyImportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PolicyImportOutcome:
    import_run_id: str
    policy_set_id: str
    policy_set_version: str
    index_version: str
    status: str
    replayed: bool
    clause_count: int


class PolicyImporter:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        embedding_client: EmbeddingClient,
        *,
        allowed_root: Path,
        provider: str,
        preprocessing_version: str = PREPROCESSING_VERSION,
    ) -> None:
        self._sessions = sessions
        self._embedding_client = embedding_client
        self._allowed_root = allowed_root
        self._provider = provider
        self._preprocessing_version = preprocessing_version

    def import_manifest(self, manifest_path: Path | str, *, publish: bool) -> PolicyImportOutcome:
        manifest = load_policy_manifest(manifest_path, allowed_root=self._allowed_root)
        return self.import_loaded(manifest, publish=publish)

    def import_loaded(
        self, manifest: LoadedPolicyManifest, *, publish: bool
    ) -> PolicyImportOutcome:
        index_version = _index_version(
            manifest,
            provider=self._provider,
            model_id=self._embedding_client.model_id,
            dimension=self._embedding_client.dimension,
            preprocessing_version=self._preprocessing_version,
        )
        run_id = _id("PIR")
        clause_count = sum(len(document.clauses) for document in manifest.documents)

        with self._sessions() as session:
            existing_set = session.scalar(
                select(PolicySet).where(
                    PolicySet.policy_set_id == manifest.policy_set_id,
                    PolicySet.policy_set_version == manifest.policy_set_version,
                )
            )
            content_mismatch = bool(
                existing_set is not None
                and existing_set.manifest_sha256 != manifest.content_sha256
            )
        if content_mismatch:
            with self._sessions.begin() as session:
                session.add(
                    PolicyImportRun(
                        import_run_id=run_id,
                        policy_set_id=manifest.policy_set_id,
                        policy_set_version=manifest.policy_set_version,
                        manifest_sha256=manifest.content_sha256,
                        policy_index_version=index_version,
                        status="FAILED",
                        replayed=False,
                        attempts=0,
                        error_code="policy_version_content_mismatch",
                        error_message="same version has different content",
                        finished_at=utc_now(),
                    )
                )
            raise PolicyImportError("same version has different content")

        with self._sessions.begin() as session:
            policy_set = session.scalar(
                select(PolicySet).where(
                    PolicySet.policy_set_id == manifest.policy_set_id,
                    PolicySet.policy_set_version == manifest.policy_set_version,
                )
            )
            if policy_set is None:
                policy_set = self._insert_authoritative_content(session, manifest)

            index = session.get(PolicyIndex, index_version)
            replayed = bool(index is not None and index.status == "PUBLISHED")
            if index is None:
                index = PolicyIndex(
                    policy_index_version=index_version,
                    policy_set_record_id=policy_set.policy_set_record_id,
                    collection_sha256=manifest.content_sha256,
                    provider=self._provider,
                    embedding_model=self._embedding_client.model_id,
                    embedding_dimension=self._embedding_client.dimension,
                    preprocessing_version=self._preprocessing_version,
                    status="BUILDING" if publish else "DRAFT",
                )
                session.add(index)
            elif publish and not replayed:
                index.status = "BUILDING"
                index.error_code = None
            run = PolicyImportRun(
                import_run_id=run_id,
                policy_set_id=manifest.policy_set_id,
                policy_set_version=manifest.policy_set_version,
                manifest_sha256=manifest.content_sha256,
                policy_index_version=index_version,
                status="SUCCEEDED" if replayed or not publish else "RUNNING",
                replayed=replayed,
                attempts=0,
                finished_at=utc_now() if replayed or not publish else None,
            )
            session.add(run)

        if replayed or not publish:
            return PolicyImportOutcome(
                run_id,
                manifest.policy_set_id,
                manifest.policy_set_version,
                index_version,
                "PUBLISHED" if replayed else "DRAFT",
                replayed,
                clause_count,
            )

        with self._sessions() as session:
            clauses = list(
                session.scalars(
                    select(PolicyClause)
                    .join(PolicySet, PolicyClause.policy_set_record_id == PolicySet.policy_set_record_id)
                    .where(
                        PolicySet.policy_set_id == manifest.policy_set_id,
                        PolicySet.policy_set_version == manifest.policy_set_version,
                    )
                    .order_by(PolicyClause.clause_id)
                )
            )
        try:
            for clause in clauses:
                actual_hash = hashlib.sha256(clause.text.encode("utf-8")).hexdigest()
                if actual_hash != clause.content_sha256:
                    raise ValueError(f"clause content hash mismatch: {clause.clause_id}")
            batch = self._embedding_client.embed([clause.text for clause in clauses])
            if len(batch.vectors) != len(clauses):
                raise ValueError("embedding count does not match clause count")
            if any(len(vector) != self._embedding_client.dimension for vector in batch.vectors):
                raise ValueError("embedding dimension does not match configured dimension")
        except Exception as exc:
            with self._sessions.begin() as session:
                index = session.get(PolicyIndex, index_version)
                run = session.get(PolicyImportRun, run_id)
                if index is not None:
                    index.status = "FAILED"
                    index.error_code = "embedding_failed"
                if run is not None:
                    run.status = "FAILED"
                    run.attempts = int(getattr(exc, "attempts", 1))
                    run.error_code = "embedding_failed"
                    run.error_message = str(exc)[:1000]
                    run.finished_at = utc_now()
            raise PolicyImportError(f"embedding_failed: {exc}") from exc

        with self._sessions.begin() as session:
            index = session.get(PolicyIndex, index_version)
            run = session.get(PolicyImportRun, run_id)
            policy_set = session.scalar(
                select(PolicySet).where(
                    PolicySet.policy_set_id == manifest.policy_set_id,
                    PolicySet.policy_set_version == manifest.policy_set_version,
                )
            )
            if index is None or run is None or policy_set is None:
                raise PolicyImportError("import state disappeared before publication")
            session.execute(
                delete(PolicyClauseEmbedding).where(
                    PolicyClauseEmbedding.policy_index_version == index_version
                )
            )
            for clause, vector in zip(clauses, batch.vectors):
                session.add(
                    PolicyClauseEmbedding(
                        policy_clause_embedding_id=_id("PCE"),
                        policy_index_version=index_version,
                        policy_clause_record_id=clause.policy_clause_record_id,
                        content_sha256=clause.content_sha256,
                        embedding=vector,
                    )
                )
            now = utc_now()
            index.status = "PUBLISHED"
            index.published_at = now
            index.error_code = None
            previous_versions = list(
                session.scalars(
                    select(PolicySet)
                    .where(
                        PolicySet.policy_set_id == manifest.policy_set_id,
                        PolicySet.policy_set_record_id
                        != policy_set.policy_set_record_id,
                        PolicySet.status == "PUBLISHED",
                    )
                    .with_for_update()
                )
            )
            for previous_version in previous_versions:
                previous_version.status = "INACTIVE"
            policy_set.status = "PUBLISHED"
            policy_set.published_at = policy_set.published_at or now
            run.status = "SUCCEEDED"
            run.attempts = batch.attempts
            run.finished_at = now

        return PolicyImportOutcome(
            run_id,
            manifest.policy_set_id,
            manifest.policy_set_version,
            index_version,
            "PUBLISHED",
            False,
            clause_count,
        )

    def _insert_authoritative_content(
        self, session: Session, manifest: LoadedPolicyManifest
    ) -> PolicySet:
        set_record = PolicySet(
            policy_set_record_id=_id("PSET"),
            policy_set_id=manifest.policy_set_id,
            policy_set_version=manifest.policy_set_version,
            schema_version=manifest.schema_version,
            manifest_sha256=manifest.content_sha256,
            manifest_path=manifest.source_path,
            status="DRAFT",
        )
        session.add(set_record)
        session.flush()
        for document in manifest.documents:
            document_record_id = _id("PDOC")
            session.add(
                PolicyDocument(
                    policy_document_record_id=document_record_id,
                    policy_set_record_id=set_record.policy_set_record_id,
                    policy_id=document.policy_id,
                    document_id=document.document_id,
                    document_version=document.document_version,
                    title=document.title,
                    source_path=document.relative_path,
                    content_sha256=document.content_sha256,
                    effective_from=document.effective_from,
                    effective_to=document.effective_to,
                    categories=document.categories,
                    regions=document.regions,
                )
            )
            session.flush()
            for clause in document.clauses:
                session.add(
                    PolicyClause(
                        policy_clause_record_id=_id("PCL"),
                        policy_set_record_id=set_record.policy_set_record_id,
                        policy_document_record_id=document_record_id,
                        clause_id=clause.clause_id,
                        section=clause.title,
                        text=clause.text,
                        normalized_text=normalize_policy_text(clause.text),
                        content_sha256=clause.content_sha256,
                        control_code=clause.control_code,
                        rule_parameters=clause.rule_parameters,
                    )
                )
        return set_record


def _index_version(
    manifest: LoadedPolicyManifest,
    *,
    provider: str,
    model_id: str,
    dimension: int,
    preprocessing_version: str,
) -> str:
    identity = {
        "collection_sha256": manifest.content_sha256,
        "provider": provider,
        "model_id": model_id,
        "dimension": dimension,
        "preprocessing_version": preprocessing_version,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"pidx-{digest[:24]}"


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"
