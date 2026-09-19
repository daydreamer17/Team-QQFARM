from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pdfplumber

from supplier_comparison.extraction.csv_parser import identify_csv_contract
from supplier_comparison.rag.uploads import (
    PolicyDraftClauseInput,
    PolicyFileImportMetadata,
    _draft_clauses,
)
from supplier_comparison.rules.contracts import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/generated/inputs/development/full_flow_demo"
REFERENCE = ROOT / "evaluation/reference/full_flow_demo/reference_answers.json"


def test_full_flow_demo_manifest_and_uploads_are_valid() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "full_flow_demo"
    assert manifest["is_synthetic"] is True
    assert manifest["runtime_safe"] is True
    assert len(manifest["quotes"]) == 3
    assert manifest["scenario_id"] == "MCU-DEMO-001"
    assert "推荐上传顺序" in (DATASET / "README.md").read_text(encoding="utf-8")

    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file()
        assert path.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]

    requirement = json.loads(
        (DATASET / "requirement/confirmed_requirement.json").read_text(encoding="utf-8")
    )
    validated = ProcurementRequirement.model_validate(requirement)
    assert validated.required_quantity == 1000
    assert str(validated.budget_amount) == "8000.00"

    with pdfplumber.open(DATASET / "requirement/procurement_requirement.pdf") as pdf:
        assert "QW-MCU9-DEMO" in "\n".join(page.extract_text() or "" for page in pdf.pages)

    for quote in manifest["quotes"]:
        pdf_path = ROOT / quote["recommended_upload"]
        csv_path = ROOT / quote["csv_alternative"]
        with pdfplumber.open(pdf_path) as pdf:
            assert any((page.extract_text() or "").strip() for page in pdf.pages)
        assert identify_csv_contract(csv_path) is not None

    validation = json.loads((DATASET / "validation_inputs.json").read_text(encoding="utf-8"))
    assert validation["suite_id"] == "full-flow-feasibility"
    assert validation["reference_answers_must_remain_runtime_inaccessible"] is True
    referenced_paths: list[str] = []
    for case in validation["supplemental_inputs"]:
        referenced_paths.extend(case.get("paths", []))
        referenced_paths.extend(case.get("requirements", []))
        if case.get("manifest"):
            referenced_paths.append(case["manifest"])
        directory = case.get("directory")
        for group in case.get("quote_groups", []):
            referenced_paths.extend(f"{directory}/{name}" for name in group)
    assert referenced_paths
    assert not any("evaluation/reference" in path for path in referenced_paths)
    assert all((ROOT / path).is_file() for path in referenced_paths)


def test_full_flow_demo_policy_review_matches_uploaded_text() -> None:
    metadata = json.loads((DATASET / "policy/upload_metadata.json").read_text(encoding="utf-8"))
    PolicyFileImportMetadata.model_validate(metadata)
    reviewed = json.loads(
        (DATASET / "policy/reviewed_clauses.json").read_text(encoding="utf-8")
    )["clauses"]
    validated = [PolicyDraftClauseInput.model_validate(item) for item in reviewed]
    extracted = _draft_clauses(
        (DATASET / "policy/electronics_procurement_full_flow.txt").read_text(encoding="utf-8"),
        fallback_title=metadata["title"],
    )
    assert [(item["clause_id"], item["title"], item["text"]) for item in extracted] == [
        (item.clause_id, item.title, item.text) for item in validated
    ]
    assert {item.control_code for item in validated} == {
        "APPROVED_SUPPLIER",
        "ROHS_COMPLIANCE",
        "AMOUNT_APPROVAL",
    }


def test_full_flow_demo_reference_is_separate_and_complete() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert reference["runtime_access"] == "FORBIDDEN"
    assert reference["expected_final"]["recommended_supplier_ids"] == ["SUP-023"]
    assert [item["issue_type"] for item in reference["operator_answers"]] == [
        "CONFIRM_MISSING",
        "SHIPPING_AMOUNT",
    ]
    assert not any("evaluation/reference" in item["path"] for item in manifest["files"])
