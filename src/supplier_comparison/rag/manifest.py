from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


CLAUSE_HEADING = re.compile(r"^## \[([^\]]+)\]\s+(.+?)\s*$", re.MULTILINE)


class ManifestError(ValueError):
    pass


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClauseMetadata(ManifestModel):
    control_code: str = Field(min_length=1, max_length=128)
    rule_parameters: dict[str, Any] = Field(default_factory=dict)


class DocumentManifest(ManifestModel):
    policy_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    document_version: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    path: str = Field(min_length=1, max_length=512)
    effective_from: datetime
    effective_to: datetime | None = None
    categories: list[str] = Field(min_length=1)
    regions: list[str] = Field(min_length=1)
    clauses: dict[str, ClauseMetadata] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates_and_path(self) -> "DocumentManifest":
        if self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None:
            raise ValueError("effective_from must include a timezone")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None:
                raise ValueError("effective_to must include a timezone")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        candidate = Path(self.path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("document path must be relative and cannot traverse directories")
        return self


class PolicyManifest(ManifestModel):
    schema_version: str = "1.0.0"
    policy_set_id: str = Field(min_length=1, max_length=128)
    policy_set_version: str = Field(min_length=1, max_length=128)
    documents: list[DocumentManifest] = Field(min_length=1)


class LoadedClause(ManifestModel):
    clause_id: str
    title: str
    text: str
    content_sha256: str
    control_code: str
    rule_parameters: dict[str, Any]


class LoadedDocument(ManifestModel):
    policy_id: str
    document_id: str
    document_version: str
    title: str
    relative_path: str
    effective_from: datetime
    effective_to: datetime | None
    categories: list[str]
    regions: list[str]
    content_sha256: str
    clauses: list[LoadedClause]


class LoadedPolicyManifest(ManifestModel):
    schema_version: str
    policy_set_id: str
    policy_set_version: str
    content_sha256: str
    source_path: str
    documents: list[LoadedDocument]


def load_policy_manifest(path: Path | str, *, allowed_root: Path | str) -> LoadedPolicyManifest:
    root = Path(allowed_root).resolve()
    manifest_path = Path(path).resolve()
    _require_within(manifest_path, root, "manifest must be inside the allowed policy directory")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = PolicyManifest.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ManifestError(f"invalid policy manifest: {exc}") from exc

    loaded_documents: list[LoadedDocument] = []
    seen_clause_ids: set[str] = set()
    seen_document_ids: set[str] = set()
    for document in manifest.documents:
        if document.document_id in seen_document_ids:
            raise ManifestError(f"duplicate document ID: {document.document_id}")
        seen_document_ids.add(document.document_id)
        document_path = (manifest_path.parent / document.path).resolve()
        _require_within(document_path, root, "document must be inside the allowed policy directory")
        try:
            markdown = document_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"cannot read policy document {document.path}: {exc}") from exc
        parsed = _parse_clauses(markdown)
        registered_ids = set(document.clauses)
        parsed_ids = {item[0] for item in parsed}
        orphaned = sorted(parsed_ids - registered_ids)
        missing = sorted(registered_ids - parsed_ids)
        if orphaned:
            raise ManifestError(f"orphan clause headings: {orphaned}")
        if missing:
            raise ManifestError(f"manifest clauses missing from Markdown: {missing}")
        clauses: list[LoadedClause] = []
        for clause_id, title, text in parsed:
            if clause_id in seen_clause_ids:
                raise ManifestError(f"duplicate clause ID: {clause_id}")
            seen_clause_ids.add(clause_id)
            metadata = document.clauses[clause_id]
            clauses.append(
                LoadedClause(
                    clause_id=clause_id,
                    title=title,
                    text=text,
                    content_sha256=_sha256_text(text),
                    control_code=metadata.control_code.upper(),
                    rule_parameters=metadata.rule_parameters,
                )
            )
        loaded_documents.append(
            LoadedDocument(
                policy_id=document.policy_id,
                document_id=document.document_id,
                document_version=document.document_version,
                title=document.title,
                relative_path=document.path,
                effective_from=document.effective_from,
                effective_to=document.effective_to,
                categories=document.categories,
                regions=document.regions,
                content_sha256=_sha256_text(markdown),
                clauses=clauses,
            )
        )
    canonical = {
        "schema_version": manifest.schema_version,
        "policy_set_id": manifest.policy_set_id,
        "policy_set_version": manifest.policy_set_version,
        "documents": [document.model_dump(mode="json") for document in loaded_documents],
    }
    return LoadedPolicyManifest(
        schema_version=manifest.schema_version,
        policy_set_id=manifest.policy_set_id,
        policy_set_version=manifest.policy_set_version,
        content_sha256=_sha256_text(json.dumps(canonical, sort_keys=True, separators=(",", ":"))),
        source_path=manifest_path.relative_to(root).as_posix(),
        documents=loaded_documents,
    )


def _parse_clauses(markdown: str) -> list[tuple[str, str, str]]:
    invalid_sections = [
        line.strip()
        for line in markdown.splitlines()
        if line.startswith("## ")
        and re.fullmatch(r"## \[[^\]]+\]\s+.+", line.strip()) is None
    ]
    if invalid_sections:
        raise ManifestError(f"orphan section without stable clause ID: {invalid_sections}")
    matches = list(CLAUSE_HEADING.finditer(markdown))
    if not matches:
        raise ManifestError("policy document contains no clause headings")
    clauses = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        text = markdown[body_start:body_end].strip()
        if not text:
            raise ManifestError(f"empty clause: {match.group(1)}")
        clauses.append((match.group(1).strip(), match.group(2).strip(), text))
    return clauses


def _require_within(path: Path, root: Path, message: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ManifestError(message) from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
