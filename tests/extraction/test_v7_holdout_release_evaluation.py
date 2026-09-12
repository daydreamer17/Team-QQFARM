from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.evaluate_v7_holdout_release import evaluate_holdout
from scripts.evaluate_v7_model_results import _sha256
from supplier_comparison.extraction.errors import ContractError


ROOT = Path(__file__).resolve().parents[2]
OPEN_REFERENCE = ROOT / "evaluation/reference/quote_V7/development/reference_answers.json"


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _released_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    reference = json.loads(OPEN_REFERENCE.read_text(encoding="utf-8"))
    reference["dataset_version"] = "V7.2"
    reference["split"] = "holdout"
    reference_path = tmp_path / "holdout_reference_answers.json"
    _write_json(reference_path, reference)

    scorer_path = ROOT / "scripts/evaluate_v7_model_results.py"
    freeze_path = tmp_path / "freeze.json"
    _write_json(
        freeze_path,
        {
            "status": "FROZEN",
            "candidate_version": "V7.2",
            "dataset": {
                "dataset_version": "V7.2",
                "holdout_reference_commitment_sha256": _digest(reference_path),
            },
            "components": {
                "scripts/evaluate_v7_model_results.py": _digest(scorer_path)
            },
        },
    )
    freeze_sha256 = _digest(freeze_path)
    _write_json(
        tmp_path / "HOLDOUT_RUN_STARTED.json",
        {
            "result_kind": "V7_HOLDOUT_ONE_TIME_MARKER",
            "freeze_sha256": freeze_sha256,
            "reference_answers_loaded": False,
        },
    )
    _write_json(
        tmp_path / "holdout_run_summary.json",
        {
            "result_kind": "V7_HOLDOUT_ONE_TIME_RUN",
            "status": "PASSED",
            "candidate_version": "V7.2",
            "freeze_sha256": freeze_sha256,
            "reference_answers_loaded": False,
        },
    )
    return reference_path, freeze_path, freeze_sha256


def test_holdout_release_wrapper_rejects_unapproved_freeze_hash(
    tmp_path: Path,
) -> None:
    reference_path, freeze_path, _ = _released_fixture(tmp_path)

    with pytest.raises(ContractError) as caught:
        evaluate_holdout(
            reference_path,
            tmp_path,
            freeze_path,
            "0" * 64,
            repo_root=ROOT,
        )

    assert caught.value.code == "v7_holdout_freeze_hash_mismatch"


def test_holdout_release_wrapper_uses_matching_frozen_scorer(
    tmp_path: Path,
) -> None:
    reference_path, freeze_path, freeze_sha256 = _released_fixture(tmp_path)

    score = evaluate_holdout(
        reference_path,
        tmp_path,
        freeze_path,
        freeze_sha256,
        repo_root=ROOT,
    )

    assert score["dataset_version"] == "V7.2"
    assert score["split"] == "holdout"
    assert score["summary"]["expected_documents"] == 8
    assert score["summary"]["scored_documents"] == 0
    assert score["holdout_release"]["frozen_scorer_sha256"] == _sha256(
        ROOT / "scripts/evaluate_v7_model_results.py"
    )
