from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendError, ConflictError
from supplier_comparison.rag.clients import FixedEmbeddingClient
from supplier_comparison.rag.importer import PolicyImporter
from supplier_comparison.rag.manifest import load_policy_manifest
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
from supplier_comparison.rules.compliance import ExecutableRuleParameters


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
    assert first["status"] == "READY_TO_PUBLISH"
    assert first["revision"] == 1
    assert first["original_filename"] == "policy.txt"
    assert first["extracted_text"] == content.decode()
    assert len(first["clauses"]) == 1
    assert first["clauses"][0] == {
        "clause_id": first["clauses"][0]["clause_id"],
        "title": "Uploaded Electronics Policy",
        "text": content.decode(),
        "control_code": "ROHS_COMPLIANCE",
        "rule_parameters": {},
        "classification": {
            "status": "AUTO_ACCEPTED",
            "base_status": "AUTO_ACCEPTED",
            "method": "CONTENT_RULE",
            "reason_codes": [],
            "conflicts_with": [],
        },
        "position": 1,
    }
    assert first["clauses"][0]["clause_id"].startswith("DRAFT-")
    assert "storage_path" not in first
    stored_files = [path for path in (tmp_path / "policy-files").rglob("*") if path.is_file()]
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == content
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PolicyFileImport)) == 1


def test_markdown_upload_preserves_utf8_text_and_media_type(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    content = "# 供应商资质制度\n\n## [QUAL-001] 资质证明\n供应商必须提供有效证明。"

    uploaded = service.upload_stream(
        metadata=_metadata(),
        original_filename="supplier_qualification.md",
        media_type="text/markdown",
        stream=BytesIO(content.encode("utf-8")),
        idempotency_key="upload-policy-markdown-1",
    )

    assert uploaded["media_type"] == "text/markdown"
    assert uploaded["original_filename"] == "supplier_qualification.md"
    assert uploaded["extracted_text"] == content
    assert uploaded["extraction_metadata"] == {
        "parser": "utf-8-markdown/1.0",
        "page_count": None,
    }
    assert uploaded["clauses"][0]["clause_id"] == "QUAL-001"


def test_plain_policy_files_are_recognized_without_uploading_a_manifest(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    root = (
        Path(__file__).resolve().parents[2]
        / "data/policies/compliance-closure-demo/v1"
    )
    controls = {}

    for number, filename in enumerate(("admission.md", "rohs.md", "amount.md"), start=1):
        uploaded = service.upload_stream(
            metadata=_metadata().model_copy(
                update={
                    "policy_id": f"POL-NO-MANIFEST-{number}",
                    "document_id": f"DOC-NO-MANIFEST-{number}",
                    "title": Path(filename).stem,
                }
            ),
            original_filename=filename,
            media_type="text/markdown",
            stream=BytesIO((root / filename).read_bytes()),
            idempotency_key=f"no-manifest-{filename}",
        )

        assert uploaded["status"] == "READY_TO_PUBLISH"
        assert len(uploaded["clauses"]) == 1
        controls[filename] = uploaded["clauses"][0]

    assert controls["admission.md"]["control_code"] == "APPROVED_SUPPLIER"
    assert controls["rohs.md"]["control_code"] == "ROHS_COMPLIANCE"
    assert controls["amount.md"]["control_code"] == "AMOUNT_APPROVAL"
    assert controls["amount.md"]["rule_parameters"] == {
        "rule_key": "approval_threshold:post-selection_amount_action",
        "currency": "SGD",
        "threshold": "10000.00",
        "operator": ">=",
        "execution_stage": "AFTER_SELECTION",
    }


def test_compliance_closure_v2_manifest_has_three_reviewed_executable_controls():
    root = Path(__file__).resolve().parents[2]
    manifest = load_policy_manifest(root / 'data/policies/compliance-closure-demo/v2/manifest.json',
                                    allowed_root=root / 'data/policies')
    rules = [ExecutableRuleParameters.model_validate(clause.rule_parameters)
             for document in manifest.documents for clause in document.clauses]
    assert manifest.policy_set_version == 'compliance-closure-demo-2026.09.2'
    assert {rule.control_code for rule in rules} == {
        'APPROVED_SUPPLIER', 'ROHS_COMPLIANCE', 'AMOUNT_APPROVAL'}
    amount = next(rule for rule in rules if rule.control_code == 'AMOUNT_APPROVAL')
    assert str(amount.threshold) == '6500.00'
    assert amount.execution_stage == 'BEFORE_PUBLICATION'


@pytest.mark.parametrize(
    "version_directory",
    [
        "electronics-components/v1",
        "electronics-components/v2",
        "industrial-automation/v1",
        "industrial-automation/v2",
        "data-center-hardware/v1",
        "data-center-hardware/v2",
    ],
)
def test_upload_classification_matches_supported_demo_manifests(
    sessions, tmp_path: Path, version_directory: str
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    policy_root = Path(__file__).parents[2] / "data" / "policies" / version_directory
    manifest = json.loads((policy_root / "manifest.json").read_text(encoding="utf-8"))

    for document in manifest["documents"]:
        path = policy_root / document["path"]
        uploaded = service.upload_stream(
            metadata=_metadata().model_copy(
                update={
                    "policy_set_id": manifest["policy_set_id"],
                    "policy_set_version": manifest["policy_set_version"],
                    "policy_id": document["policy_id"],
                    "document_id": document["document_id"],
                    "document_version": document["document_version"],
                    "title": path.stem,
                }
            ),
            original_filename=path.name,
            media_type="text/markdown",
            stream=BytesIO(path.read_bytes()),
            idempotency_key=f"upload-{version_directory}-{document['document_id']}",
        )

        assert uploaded["status"] == "READY_TO_PUBLISH"
        actual_clauses = {
            clause["clause_id"]: {
                "control_code": clause["control_code"],
                "rule_parameters": clause["rule_parameters"],
            }
            for clause in uploaded["clauses"]
        }
        expected_clauses = {
            clause_id: (
                {"control_code": "INFORMATIONAL", "rule_parameters": {}}
                if definition["control_code"] == "AMOUNT_APPROVAL"
                and not definition["rule_parameters"]
                else definition
            )
            for clause_id, definition in document["clauses"].items()
        }
        assert actual_clauses == expected_clauses
        assert all(
            clause["classification"]["status"] == "AUTO_ACCEPTED"
            for clause in uploaded["clauses"]
        )


def test_unsupported_clause_is_routed_to_admin_review(sessions, tmp_path: Path) -> None:
    service, _embedding = _service(sessions, tmp_path)
    content = (
        "# 供应商网络安全制度\n\n"
        "## [SEC-001] 网络安全评估\n"
        "供应商必须提交渗透测试和漏洞扫描报告。"
    )

    uploaded = service.upload_stream(
        metadata=_metadata(),
        # A familiar filename must not override a capability the system cannot run.
        original_filename="spend_approval.md",
        media_type="text/markdown",
        stream=BytesIO(content.encode("utf-8")),
        idempotency_key="unsupported-policy",
    )

    assert uploaded["status"] == "REVIEW_REQUIRED"
    assert uploaded["clauses"][0]["control_code"] is None
    assert uploaded["clauses"][0]["classification"] == {
        "status": "UNSUPPORTED",
        "base_status": "UNSUPPORTED",
        "method": "CAPABILITY_REGISTRY",
        "reason_codes": ["UNSUPPORTED_CAPABILITY"],
        "unsupported_capability": "CYBERSECURITY_ASSESSMENT",
        "conflicts_with": [],
    }


def test_missing_executable_parameter_is_routed_to_admin_review(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    content = (
        "# 采购审批制度\n\n"
        "## [APR-001] 金额审批门槛\n"
        "大额采购必须取得采购经理批准。"
    )

    uploaded = service.upload_stream(
        metadata=_metadata(),
        original_filename="spend_approval.md",
        media_type="text/markdown",
        stream=BytesIO(content.encode("utf-8")),
        idempotency_key="missing-threshold",
    )

    assert uploaded["status"] == "REVIEW_REQUIRED"
    clause = uploaded["clauses"][0]
    assert clause["control_code"] == "AMOUNT_APPROVAL"
    assert clause["classification"]["status"] == "ADMIN_REVIEW"
    assert clause["classification"]["reason_codes"] == [
        "MISSING_REQUIRED_PARAMETER"
    ]


def test_conflicting_structured_rules_mark_both_files_for_review(
    sessions, tmp_path: Path
) -> None:
    service, _embedding = _service(sessions, tmp_path)
    uploaded_ids = []
    for number, threshold in enumerate(("7,000.00", "8,000.00"), start=1):
        content = (
            f"# 采购审批制度 {number}\n\n"
            f"## [APR-00{number}] 金额审批门槛\n"
            f"采购总成本达到 SGD {threshold} 时必须取得经理审批。"
        )
        uploaded = service.upload_stream(
            metadata=_metadata().model_copy(
                update={
                    "policy_id": f"POL-CONFLICT-{number}",
                    "document_id": f"DOC-CONFLICT-{number}",
                    "title": f"采购审批制度 {number}",
                }
            ),
            original_filename=f"spend_approval_{number}.md",
            media_type="text/markdown",
            stream=BytesIO(content.encode("utf-8")),
            idempotency_key=f"conflicting-threshold-{number}",
        )
        uploaded_ids.append(uploaded["policy_import_id"])

    records = [service.get(policy_import_id) for policy_import_id in uploaded_ids]
    assert {record["status"] for record in records} == {"REVIEW_REQUIRED"}
    for record in records:
        analysis = record["clauses"][0]["classification"]
        assert analysis["status"] == "ADMIN_REVIEW"
        assert "CONFLICTING_RULE" in analysis["reason_codes"]
        assert len(analysis["conflicts_with"]) == 1


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
        ("README.md", "text/markdown", b"# Documentation", "policy_document_not_policy"),
        ("policy.txt", "text/plain", b"\xff\xfe", "policy_txt_not_utf8"),
        ("policy.md", "text/markdown", b"\xff\xfe", "policy_markdown_not_utf8"),
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
        stored_clause = session.scalar(select(PolicyClause))
        assert stored_clause is not None
        rule = ExecutableRuleParameters.model_validate(stored_clause.rule_parameters)
        assert rule.control_code == "ROHS_COMPLIANCE"
        assert rule.matching_fields == (
            "supplier_id", "manufacturer", "manufacturer_part_number"
        )

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


def test_policy_set_files_are_reviewed_and_published_as_one_version(
    sessions, tmp_path: Path
) -> None:
    first_text = "Approved suppliers must be used."
    second_text = "Policy evidence must be reviewed before use."
    service, _embedding = _service(
        sessions,
        tmp_path,
        {first_text: [0.1] * 1024, second_text: [0.2] * 1024},
    )

    uploads = []
    for number, text in enumerate((first_text, second_text), start=1):
        metadata = _metadata().model_copy(
            update={
                "policy_id": f"POL-UPLOAD-00{number}",
                "document_id": f"DOC-UPLOAD-00{number}",
                "title": f"Policy document {number}",
            }
        )
        uploads.append(
            service.upload_stream(
                metadata=metadata,
                original_filename=f"policy-{number}.txt",
                media_type="text/plain",
                stream=BytesIO(text.encode()),
                idempotency_key=f"upload-set-file-{number}",
            )
        )

    first_review = service.replace_clauses(
        uploads[0]["policy_import_id"],
        expected_revision=1,
        clauses=[
            PolicyDraftClauseInput(
                clause_id="APPROVED-001",
                title="Approved supplier",
                text=first_text,
                control_code="APPROVED_SUPPLIER",
            )
        ],
        idempotency_key="review-set-file-1",
    )
    with pytest.raises(ConflictError) as incomplete:
        service.publish(
            uploads[0]["policy_import_id"],
            expected_revision=first_review["revision"],
            idempotency_key="publish-incomplete-set",
        )
    assert incomplete.value.code == "policy_set_review_incomplete"

    second_review = service.replace_clauses(
        uploads[1]["policy_import_id"],
        expected_revision=1,
        clauses=[
            PolicyDraftClauseInput(
                clause_id="ROHS-001",
                title="RoHS evidence",
                text=second_text,
                control_code="ROHS_COMPLIANCE",
            )
        ],
        idempotency_key="review-set-file-2",
    )
    published = service.publish(
        uploads[1]["policy_import_id"],
        expected_revision=second_review["revision"],
        idempotency_key="publish-complete-set",
    )

    assert published["status"] == "PUBLISHED"
    grouped = service.list_imports(
        policy_set_id="uploaded-electronics-policy",
        policy_set_version="2026.09.1",
    )
    assert grouped["total"] == 2
    assert {item["status"] for item in grouped["items"]} == {"PUBLISHED"}
    assert len({item["policy_index_version"] for item in grouped["items"]}) == 1
    policy_sets = service.list_policy_sets()
    assert policy_sets["items"][0]["document_count"] == 2
    assert policy_sets["items"][0]["clause_count"] == 2
    binding = policy_sets["items"][0]
    service.require_active_binding(
        policy_set_version=binding["policy_set_version"],
        policy_index_version=binding["policy_index_version"],
        category="Electronics",
        region="SG",
    )

    deactivated = service.deactivate_policy_set(
        binding["policy_set_id"],
        binding["policy_set_version"],
        idempotency_key="deactivate-policy-set-1",
    )
    repeated = service.deactivate_policy_set(
        binding["policy_set_id"],
        binding["policy_set_version"],
        idempotency_key="deactivate-policy-set-1",
    )

    assert repeated == deactivated
    assert deactivated["status"] == "INACTIVE"
    assert service.list_policy_sets()["total"] == 0
    inactive_sets = service.list_policy_sets(include_inactive=True)
    assert inactive_sets["total"] == 1
    assert inactive_sets["items"][0]["status"] == "INACTIVE"
    with pytest.raises(ConflictError) as unavailable:
        service.require_active_binding(
            policy_set_version=binding["policy_set_version"],
            policy_index_version=binding["policy_index_version"],
            category="Electronics",
            region="SG",
        )
    assert unavailable.value.code == "policy_binding_unavailable"


def test_publishing_new_version_supersedes_previous_active_version(
    sessions, tmp_path: Path
) -> None:
    texts = {
        "First version policy text.": [0.1] * 1024,
        "Second version policy text.": [0.2] * 1024,
    }
    service, _embedding = _service(sessions, tmp_path, texts)

    for number, (text, version) in enumerate(
        (("First version policy text.", "2026.09.1"),
         ("Second version policy text.", "2026.09.2")),
        start=1,
    ):
        metadata = _metadata().model_copy(
            update={
                "policy_set_version": version,
                "policy_id": f"POL-UPLOAD-V{number}",
                "document_id": f"DOC-UPLOAD-V{number}",
                "title": f"Policy version {number}",
            }
        )
        uploaded = service.upload_stream(
            metadata=metadata,
            original_filename=f"policy-v{number}.txt",
            media_type="text/plain",
            stream=BytesIO(text.encode()),
            idempotency_key=f"upload-version-{number}",
        )
        reviewed = service.replace_clauses(
            uploaded["policy_import_id"],
            expected_revision=uploaded["revision"],
            clauses=[
                PolicyDraftClauseInput(
                    clause_id=f"INFO-{number:03d}",
                    title=f"Policy version {number}",
                    text=text,
                    control_code="INFORMATIONAL",
                )
            ],
            idempotency_key=f"review-version-{number}",
        )
        service.publish(
            uploaded["policy_import_id"],
            expected_revision=reviewed["revision"],
            idempotency_key=f"publish-version-{number}",
        )

    active = service.list_policy_sets()
    assert active["total"] == 1
    assert active["items"][0]["policy_set_version"] == "2026.09.2"
    assert active["items"][0]["status"] == "PUBLISHED"

    all_versions = service.list_policy_sets(include_inactive=True)
    assert all_versions["total"] == 2
    statuses = {
        item["policy_set_version"]: item["status"]
        for item in all_versions["items"]
    }
    assert statuses == {
        "2026.09.1": "INACTIVE",
        "2026.09.2": "PUBLISHED",
    }


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
