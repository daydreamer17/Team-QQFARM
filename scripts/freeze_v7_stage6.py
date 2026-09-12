#!/usr/bin/env python3
"""Freeze the validated V7 Stage 6 parser/OCR/model/review configuration."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.adapters import OpenAICompatibleConfig
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError
from supplier_comparison.extraction.pdf_layout import PdfLayoutConfig
from supplier_comparison.extraction.pdf_ocr import (
    SELECTED_OCR_ENGINE,
    SELECTED_OCR_ENGINE_VERSION,
    PdfOcrConfig,
)
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CANDIDATE_VERSION = "V7.2"
DEFAULT_MANIFEST = REPO_ROOT / "data/generated/manifests/quote_V7_2_manifest.json"
DEFAULT_DICTIONARY = REPO_ROOT / "data/contracts/quote_data_field.csv"
DEFAULT_HOLDOUT_COMMITMENT = (
    REPO_ROOT / "evaluation/reference/quote_V7_2/holdout_commitment.json"
)
DEFAULT_DEVELOPMENT_SCORE = REPO_ROOT / "evaluation/results/local/2026-09-11/v7_2_stage6/development_model_real/development_model_score.json"
DEFAULT_CALIBRATION_SCORE = REPO_ROOT / "evaluation/results/local/2026-09-11/v7_2_stage6/calibration_model_real/calibration_model_score.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs/v7/stage6/V7_2_STAGE6_FREEZE.json"

COMPONENT_PATHS = (
    "src/supplier_comparison/extraction/adapters.py",
    "src/supplier_comparison/extraction/evidence.py",
    "src/supplier_comparison/extraction/normalization.py",
    "src/supplier_comparison/extraction/pdf_layout.py",
    "src/supplier_comparison/extraction/pdf_ocr.py",
    "src/supplier_comparison/extraction/pdf_parser.py",
    "src/supplier_comparison/extraction/pdf_quality.py",
    "src/supplier_comparison/extraction/review.py",
    "src/supplier_comparison/extraction/review_contracts.py",
    "src/supplier_comparison/extraction/service.py",
    "scripts/run_v7_model_evaluation.py",
    "scripts/run_v7_holdout_once.py",
    "scripts/evaluate_v7_model_results.py",
    "scripts/freeze_v7_stage6.py",
    "scripts/refresh_saved_reviews.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("v7_freeze_input_invalid", "freeze input is unreadable JSON", path=str(path)) from exc
    if not isinstance(value, dict):
        raise ContractError("v7_freeze_input_invalid", "freeze input must be a JSON object", path=str(path))
    return value


def canonical_json_value(value: Any) -> Any:
    """Normalize tuples and other JSON-compatible containers before freezing."""

    return json.loads(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def safe_runtime_config() -> dict[str, Any]:
    model = OpenAICompatibleConfig.from_env()
    model_values = model.model_dump(mode="json")
    model_values.pop("diagnostic_artifact_dir", None)
    model_values["diagnostic_artifact_enabled"] = model.diagnostic_artifact_dir is not None
    return canonical_json_value({
        "model": model_values,
        "pdf_quality": asdict(PdfQualityConfig(ocr_enabled=True)),
        "pdf_layout": PdfLayoutConfig.from_env().fingerprint_payload(),
        "pdf_ocr": PdfOcrConfig.from_env().fingerprint_payload(),
        "ocr_engine": SELECTED_OCR_ENGINE,
        "ocr_engine_version": SELECTED_OCR_ENGINE_VERSION,
    })


def _validate_score(score: dict[str, Any], split: str, expected_documents: int) -> None:
    summary = score.get("summary", {})
    checks = {
        "candidate_version": score.get("candidate_version") == EXPECTED_CANDIDATE_VERSION,
        "split": score.get("split") == split,
        "documents": summary.get("expected_documents") == expected_documents,
        "all_scored": summary.get("scored_documents") == expected_documents,
        "document_success": summary.get("document_success_rate") == "1.0000",
        "critical_silent_errors": summary.get("critical_silent_error_count") == 0,
        "source_identity": summary.get("source_identity_rate") == "1.0000",
        "source_location": summary.get("source_location_rate") == "1.0000",
        "field_accuracy": Decimal(str(summary.get("conservative_field_accuracy", "0"))) >= Decimal("0.95"),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise ContractError(
            "v7_freeze_gate_failed",
            "Development/Calibration results do not satisfy the V7 freeze gate",
            split=split,
            failed_checks=failed,
        )


def _validate_v7_2_manifest(
    manifest: dict[str, Any],
    commitment: dict[str, Any],
    dictionary: QuoteDictionary,
    dictionary_path: Path,
) -> None:
    cases = manifest.get("cases")
    case_ids = [item.get("case_id") for item in cases] if isinstance(cases, list) else []
    dictionary_manifest = manifest.get("dictionary")
    checks = {
        "dataset_version": manifest.get("dataset_version") == EXPECTED_CANDIDATE_VERSION,
        "split": manifest.get("split") == "holdout",
        "synthetic_only": manifest.get("synthetic_only") is True,
        "commercial_data": manifest.get("commercial_data") is False,
        "reference_visibility": manifest.get("reference_visibility") == "HASH_ONLY_BEFORE_RUN",
        "answer_fields_hidden": manifest.get("answer_fields_in_manifest") is False,
        "source_mapping_hidden": manifest.get("source_mapping_in_manifest") is False,
        "case_count": len(case_ids) == 6,
        "case_ids_unique": len(case_ids) == len(set(case_ids)),
        "case_contracts": bool(cases)
        and all(
            isinstance(item, dict)
            and item.get("split") == "holdout"
            and item.get("dataset_version") == EXPECTED_CANDIDATE_VERSION
            and item.get("reference_visibility") == "HASH_ONLY_BEFORE_RUN"
            for item in cases
        ),
        "dictionary": isinstance(dictionary_manifest, dict)
        and dictionary_manifest.get("version") == dictionary.version
        and dictionary_manifest.get("sha256") == _sha256(dictionary_path),
        "commitment_dataset": commitment.get("dataset_version") == EXPECTED_CANDIDATE_VERSION,
        "commitment_split": commitment.get("split") == "holdout",
        "commitment_hidden": commitment.get("answer_content_in_repository") is False,
        "commitment_case_ids": commitment.get("case_ids") == case_ids,
        "commitment_hash": (
            commitment.get("holdout_reference_commitment_sha256")
            == manifest.get("holdout_reference_commitment_sha256")
        ),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise ContractError(
            "v7_2_freeze_manifest_invalid",
            "V7.2 holdout manifest or public commitment is invalid",
            failed_checks=failed,
        )


def build_freeze(
    manifest_path: Path,
    dictionary_path: Path,
    holdout_commitment_path: Path,
    development_score_path: Path,
    calibration_score_path: Path,
    *,
    a_attestation_confirmed: bool,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    commitment = _load_json(holdout_commitment_path)
    development = _load_json(development_score_path)
    calibration = _load_json(calibration_score_path)
    dictionary = QuoteDictionary.load(dictionary_path)
    if not a_attestation_confirmed:
        raise ContractError(
            "v7_2_a_attestation_required",
            "V7.2 freeze requires A's explicit private-reference attestation",
        )
    _validate_v7_2_manifest(
        manifest,
        commitment,
        dictionary,
        dictionary_path,
    )
    _validate_score(development, "development", 8)
    _validate_score(calibration, "calibration", 6)
    components = {
        relative: _sha256(REPO_ROOT / relative) for relative in COMPONENT_PATHS
    }
    return {
        "result_kind": "V7_STAGE6_CONFIGURATION_FREEZE",
        "status": "FROZEN",
        "candidate_version": EXPECTED_CANDIDATE_VERSION,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "manifest_path": str(manifest_path.relative_to(REPO_ROOT)),
            "manifest_sha256": _sha256(manifest_path),
            "dataset_version": manifest.get("dataset_version"),
            "open_split_dataset_version": development.get("dataset_version"),
            "open_split_dataset_revision": development.get("dataset_revision"),
            "development_patch_revision": development.get("development_patch_revision"),
            "holdout_reference_commitment_sha256": manifest.get("holdout_reference_commitment_sha256"),
            "holdout_commitment_path": str(
                holdout_commitment_path.relative_to(REPO_ROOT)
            ),
            "holdout_commitment_file_sha256": _sha256(holdout_commitment_path),
            "a_private_reference_attestation": {
                "confirmed": True,
                "confirmed_on": "2026-09-12",
                "case_count": 6,
                "scored_fields_per_case": 30,
                "not_derived_from_b_output": True,
                "not_exposed_to_b_or_model": True,
            },
        },
        "dictionary": {
            "path": str(dictionary_path.relative_to(REPO_ROOT)),
            "version": dictionary.version,
            "sha256": _sha256(dictionary_path),
        },
        "runtime_config": safe_runtime_config(),
        "components": components,
        "validated_results": {
            "development": {
                "path": str(development_score_path.relative_to(REPO_ROOT)),
                "sha256": _sha256(development_score_path),
                "summary": development["summary"],
            },
            "calibration": {
                "path": str(calibration_score_path.relative_to(REPO_ROOT)),
                "sha256": _sha256(calibration_score_path),
                "summary": calibration["summary"],
            },
        },
        "environment": {
            "python": platform.python_version(),
            "pydantic": importlib.metadata.version("pydantic"),
            "pdfplumber": importlib.metadata.version("pdfplumber"),
            "pytest": importlib.metadata.version("pytest"),
        },
        "holdout_run_state": "NOT_STARTED",
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    cli.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    cli.add_argument(
        "--holdout-commitment",
        type=Path,
        default=DEFAULT_HOLDOUT_COMMITMENT,
    )
    cli.add_argument("--development-score", type=Path, default=DEFAULT_DEVELOPMENT_SCORE)
    cli.add_argument("--calibration-score", type=Path, default=DEFAULT_CALIBRATION_SCORE)
    cli.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    cli.add_argument("--confirm-a-attestation", action="store_true", required=True)
    args = cli.parse_args()
    try:
        value = build_freeze(
            args.manifest.resolve(),
            args.dictionary.resolve(),
            args.holdout_commitment.resolve(),
            args.development_score.resolve(),
            args.calibration_score.resolve(),
            a_attestation_confirmed=args.confirm_a_attestation,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "FROZEN", "output": str(args.output), "sha256": _sha256(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
