from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendError, ConflictError
from supplier_comparison.rag.clients import FixedEmbeddingClient
from supplier_comparison.rag.importer import PolicyImporter
from supplier_comparison.rag.models import (
    PolicyClause,
    PolicyClauseEmbedding,
    PolicyFileImport,
)
from supplier_comparison.rag.uploads import (
    PolicyDraftClauseInput,
    PolicyFileImportMetadata,
    PolicyFileImportService,
)


def _single_page_pdf(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content = f"BT /F1 12 Tf 36 740 Td ({escaped}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [4 0 R] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
        + content
        + b"endstream",
    ]
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)


@pytest.fixture
def sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


def _metadata() -> PolicyFileImportMetadata:
    return PolicyFileImportMetadata(
        policy_set_id="uploaded-electronics-policy",
        policy_set_version="2026.09.1",
        policy_id="POL-UPLOAD-001",
        document_id="DOC-UPLOAD-001",
        document_version="1.0.0",
        title="Uploaded Electronics Policy",
        effective_from="2026-09-17T00:00:00Z",
        categories=["Electronics"],
        regions=["SG"],
    )


def _service(
    sessions,
    tmp_path: Path,
    vectors: dict[str, list[float]] | None = None,
    **service_options,
):
    embedding = FixedEmbeddingClient(
        vectors or {}, model_id="BAAI/bge-m3", dimension=1024
    )
    importer = PolicyImporter(
        sessions,
        embedding,
        allowed_root=tmp_path / "manifests",
        provider="fixed",
    )
    return (
        PolicyFileImportService(
            sessions,
            tmp_path / "policy-files",
            importer,
            actor_id="local-test-user",
            **service_options,
        ),
        embedding,
    )


def test_txt_upload_is_immutable_idempotent_and_does_not_expose_storage_path(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    content = b"All electronics suppliers must provide current RoHS evidence."

    first = service.upload_stream(
        metadata=_metadata(),
        original_filename="../policy.txt",
        media_type="text/plain",
        stream=BytesIO(content),
        idempotency_key="upload-policy-1",
    )
    second = service.upload_stream(
        metadata=_metadata(),
        original_filename="../policy.txt",
        media_type="text/plain",
        stream=BytesIO(content),
        idempotency_key="upload-policy-1",
    )

    assert first == second
    assert first["status"] == "REVIEW_REQUIRED"
    assert first["revision"] == 1
    assert first["original_filename"] == "policy.txt"
    assert first["extracted_text"] == content.decode()
    assert first["clauses"] == [
        {
            "clause_id": "DRAFT-001",
            "title": "Uploaded Electronics Policy",
            "text": content.decode(),
            "control_code": None,
            "rule_parameters": {},
            "position": 1,
        }
    ]
    assert "storage_path" not in first
    stored_files = [path for path in (tmp_path / "policy-files").rglob("*") if path.is_file()]
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == content
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PolicyFileImport)) == 1


def test_upload_generates_missing_technical_identifiers(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    metadata = PolicyFileImportMetadata(
        title="Supplier Qualification Policy",
        effective_from="2026-09-21T00:00:00Z",
        categories=["Electronics"],
        regions=["SG"],
    )

    uploaded = service.upload_stream(
        metadata=metadata,
        original_filename="qualification.txt",
        media_type="text/plain",
        stream=BytesIO(b"Suppliers must provide valid qualification evidence."),
        idempotency_key="upload-policy-generated-identifiers",
    )
    repeated = service.upload_stream(
        metadata=metadata,
        original_filename="qualification.txt",
        media_type="text/plain",
        stream=BytesIO(b"Suppliers must provide valid qualification evidence."),
        idempotency_key="upload-policy-generated-identifiers",
    )

    assert repeated == uploaded
    assert uploaded["policy_set_id"].startswith("policy-set-")
    assert uploaded["policy_set_version"] == "1.0.0"
    assert uploaded["policy_id"].startswith("POL-")
    assert uploaded["document_id"].startswith("DOC-")
    assert uploaded["document_version"] == "1.0.0"


def test_pdf_upload_extracts_native_text_and_page_count(sessions, tmp_path: Path) -> None:
    service, _embedding = _service(sessions, tmp_path)

    uploaded = service.upload_stream(
        metadata=_metadata(),
        original_filename="policy.pdf",
        media_type="application/pdf",
        stream=BytesIO(_single_page_pdf("Electronics quotes must state currency.")),
        idempotency_key="upload-pdf-1",
    )

    assert uploaded["media_type"] == "application/pdf"
    assert uploaded["extracted_text"] == "Electronics quotes must state currency."
    assert uploaded["extraction_metadata"] == {
        "parser": "pdfplumber/1.0",
        "page_count": 1,
    }


@pytest.mark.parametrize(
    ("filename", "media_type", "content", "code"),
    [
        ("policy.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", b"x", "unsupported_policy_media_type"),
        ("policy.txt", "text/plain", b"\xff\xfe", "policy_txt_not_utf8"),
        ("policy.pdf", "application/pdf", b"not-a-pdf", "invalid_policy_pdf"),
    ],
)
def test_upload_rejects_unsupported_or_unreadable_documents_without_persisting_file(
    sessions,
    tmp_path: Path,
    filename: str,
    media_type: str,
    content: bytes,
    code: str,
) -> None:
    service, _embedding = _service(sessions, tmp_path)

    with pytest.raises(BackendError) as error:
        service.upload_stream(
            metadata=_metadata(),
            original_filename=filename,
            media_type=media_type,
            stream=BytesIO(content),
            idempotency_key=f"invalid-{code}",
        )

    assert error.value.code == code
    assert not [path for path in (tmp_path / "policy-files").rglob("*") if path.is_file()]


def test_upload_rejects_extracted_text_over_configured_limit(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path, max_extracted_characters=10)

    with pytest.raises(BackendError) as error:
        service.upload_stream(
            metadata=_metadata(),
            original_filename="policy.txt",
            media_type="text/plain",
            stream=BytesIO(b"This policy is longer than ten characters."),
            idempotency_key="too-much-text",
        )

    assert error.value.code == "policy_text_limit_exceeded"
    assert not [path for path in (tmp_path / "policy-files").rglob("*") if path.is_file()]


def test_reviewed_clauses_require_current_revision_and_cannot_change_after_publish(
    sessions, tmp_path: Path
) -> None:
    clause_text = "Suppliers must provide current RoHS evidence."
    service, _embedding = _service(sessions, tmp_path, {clause_text: [0.2] * 1024})
    uploaded = service.upload_stream(
        metadata=_metadata(),
        original_filename="policy.txt",
        media_type="text/plain",
        stream=BytesIO(clause_text.encode()),
        idempotency_key="upload-policy-1",
    )
    reviewed = service.replace_clauses(
        uploaded["policy_import_id"],
        expected_revision=1,
        clauses=[
            PolicyDraftClauseInput(
                clause_id="ROHS-001",
                title="Evidence requirement",
                text=clause_text,
                control_code="rohs_compliance",
                rule_parameters={"current": True},
            )
        ],
        idempotency_key="review-policy-1",
    )

    assert reviewed["status"] == "READY_TO_PUBLISH"
    assert reviewed["revision"] == 2
    assert reviewed["clauses"][0]["control_code"] == "ROHS_COMPLIANCE"
    with pytest.raises(ConflictError) as stale:
        service.replace_clauses(
            uploaded["policy_import_id"],
            expected_revision=1,
            clauses=[
                PolicyDraftClauseInput(
                    clause_id="ROHS-002",
                    title="Stale",
                    text="Stale replacement.",
                    control_code="ROHS_COMPLIANCE",
                )
            ],
            idempotency_key="review-policy-stale",
        )
    assert stale.value.code == "policy_import_revision_conflict"

    published = service.publish(
        uploaded["policy_import_id"],
        expected_revision=2,
        idempotency_key="publish-policy-1",
    )
    repeated = service.publish(
        uploaded["policy_import_id"],
        expected_revision=2,
        idempotency_key="publish-policy-1",
    )
    assert published == repeated
    assert published["status"] == "PUBLISHED"
    assert published["revision"] == 3
    assert published["policy_index_version"].startswith("pidx-")
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PolicyClause)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyClauseEmbedding)) == 1

    with pytest.raises(ConflictError) as immutable:
        service.replace_clauses(
            uploaded["policy_import_id"],
            expected_revision=3,
            clauses=[
                PolicyDraftClauseInput(
                    clause_id="ROHS-001",
                    title="Changed",
                    text="Changed after publication.",
                    control_code="ROHS_COMPLIANCE",
                )
            ],
            idempotency_key="review-after-publish",
        )
    assert immutable.value.code == "published_policy_immutable"


def test_publish_requires_reviewed_clauses(sessions, tmp_path: Path) -> None:
    service, _embedding = _service(sessions, tmp_path)
    uploaded = service.upload_stream(
        metadata=_metadata(),
        original_filename="policy.txt",
        media_type="text/plain",
        stream=BytesIO(b"Policy text requiring review."),
        idempotency_key="upload-policy-1",
    )

    with pytest.raises(ConflictError) as error:
        service.publish(
            uploaded["policy_import_id"],
            expected_revision=1,
            idempotency_key="publish-policy-1",
        )

    assert error.value.code == "policy_import_not_ready"


def test_same_reviewed_file_version_reuploaded_as_new_draft_reuses_published_index(
    sessions, tmp_path: Path
) -> None:
    clause_text = "Suppliers must provide current RoHS evidence."
    service, embedding = _service(sessions, tmp_path, {clause_text: [0.2] * 1024})

    published_indexes = []
    for number in (1, 2):
        uploaded = service.upload_stream(
            metadata=_metadata(),
            original_filename="policy.txt",
            media_type="text/plain",
            stream=BytesIO(clause_text.encode()),
            idempotency_key=f"upload-policy-{number}",
        )
        reviewed = service.replace_clauses(
            uploaded["policy_import_id"],
            expected_revision=1,
            clauses=[
                PolicyDraftClauseInput(
                    clause_id="ROHS-001",
                    title="Evidence requirement",
                    text=clause_text,
                    control_code="ROHS_COMPLIANCE",
                )
            ],
            idempotency_key=f"review-policy-{number}",
        )
        published = service.publish(
            uploaded["policy_import_id"],
            expected_revision=reviewed["revision"],
            idempotency_key=f"publish-policy-{number}",
        )
        published_indexes.append(published["policy_index_version"])

    assert published_indexes[0] == published_indexes[1]
    assert len(embedding.calls) == 1



def test_interrupted_publishing_can_resume_and_active_publisher_cannot_be_duplicated(sessions, tmp_path, monkeypatch):
    text = "Suppliers must provide current RoHS evidence."
    service, embedding = _service(sessions, tmp_path, {text: [0.2] * 1024})
    uploaded = service.upload_stream(metadata=_metadata(), original_filename="policy.txt",
        media_type="text/plain", stream=BytesIO(text.encode()), idempotency_key="recovery-upload")
    ident = uploaded["policy_import_id"]
    service.replace_clauses(ident, expected_revision=1, clauses=[PolicyDraftClauseInput(
        clause_id="R1", title="RoHS", text=text, control_code="ROHS_COMPLIANCE")], idempotency_key="review")
    original = service._importer.import_loaded
    def interrupt(*args, **kwargs):
        with pytest.raises(ConflictError) as active:
            service.publish(ident, expected_revision=2, idempotency_key="duplicate")
        assert active.value.code == "policy_publish_in_progress"
        raise KeyboardInterrupt("simulate terminated publication process")
    monkeypatch.setattr(service._importer, "import_loaded", interrupt)
    with pytest.raises(KeyboardInterrupt):
        service.publish(ident, expected_revision=2, idempotency_key="first-attempt")
    with sessions() as session:
        assert session.get(PolicyFileImport, ident).status == "PUBLISHING"
    monkeypatch.setattr(service._importer, "import_loaded", original)
    published = service.publish(ident, expected_revision=2, idempotency_key="resume")
    assert published["status"] == "PUBLISHED"
    assert published["revision"] == 3
    assert service.publish(ident, expected_revision=2, idempotency_key="resume") == published
