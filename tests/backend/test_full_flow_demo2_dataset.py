from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pdfplumber

from supplier_comparison.rag.uploads import PolicyDraftClauseInput, PolicyFileImportMetadata, _draft_clauses
from supplier_comparison.rules.contracts import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/generated/inputs/development/full_flow_demo2"
REFERENCE = ROOT / "evaluation/reference/full_flow_demo2/reference_answers.json"


def test_full_flow_demo2_manifest_hashes_and_pdf_text() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "full_flow_demo2"
    assert manifest["is_synthetic"] is True
    assert len(manifest["quotes"]) == 5
    assert len(manifest["buyer_intents"]["files"]) == 4
    for item in manifest["files"]:
        path = ROOT / item["path"]
        assert path.is_file()
        assert path.stat().st_size == item["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    pdfs = [ROOT / manifest["requirement"]["upload_file"]] + [ROOT / item["upload_file"] for item in manifest["quotes"]]
    for path in pdfs:
        with pdfplumber.open(path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            assert len(text) > 300
            assert "Synthetic" in text or "synthetic" in text


def test_full_flow_demo2_contracts_and_policy_review() -> None:
    requirement = json.loads((DATASET / "requirement/confirmed_requirement.json").read_text(encoding="utf-8"))
    assert ProcurementRequirement.model_validate(requirement).required_quantity == 1250
    metadata = json.loads((DATASET / "policy/upload_metadata.json").read_text(encoding="utf-8"))
    PolicyFileImportMetadata.model_validate(metadata)
    reviewed = [PolicyDraftClauseInput.model_validate(item) for item in json.loads((DATASET / "policy/reviewed_clauses.json").read_text(encoding="utf-8"))["clauses"]]
    extracted = _draft_clauses((DATASET / "policy/electronics_tradeoff_policy.txt").read_text(encoding="utf-8"), fallback_title=metadata["title"])
    assert [(item["clause_id"], item["title"], item["text"]) for item in extracted] == [(item.clause_id, item.title, item.text) for item in reviewed]
    assert {item.control_code for item in reviewed} >= {"APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL", "FX_CONVERSION", "DECISION_PREFERENCE", "UNKNOWN_COST_HANDLING"}


def test_full_flow_demo2_reference_math_and_separation() -> None:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert reference["runtime_access"] == "FORBIDDEN"
    assert not any("evaluation/reference" in item["path"] for item in manifest["files"])
    quotes = reference["quote_facts_after_answers"]
    converted = (Decimal(quotes["D"]["native_total"]) * Decimal(reference["fx_snapshot"]["rate"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert converted == Decimal("9212.42")
    expected = {item["intent_id"]: item["recommended_supplier_ids"] for item in reference["intent_expectations"]}
    assert expected == {
        "cost_first": ["V9-SUP-C", "V9-SUP-E"],
        "cost_then_fastest": ["V9-SUP-E"],
        "delivery_urgent": ["V9-SUP-G"],
        "balanced_tolerance": ["V9-SUP-D"],
    }
