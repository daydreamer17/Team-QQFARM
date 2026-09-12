from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_stage3_dataset_is_frozen_synthetic_development_data() -> None:
    definition = json.loads(
        (REPO_ROOT / "evaluation/ocr/v7_stage3_development_set.json").read_text(
            encoding="utf-8"
        )
    )

    assert definition["dataset_kind"] == "DEVELOPMENT"
    assert definition["synthetic"] is True
    assert definition["holdout_eligible"] is False
    assert definition["render_dpi"] == 300
    assert len(definition["cases"]) == 8
    assert len({case["case_id"] for case in definition["cases"]}) == 8

    for case in definition["cases"]:
        pdf_path = REPO_ROOT / case["pdf_path"]
        actual_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        assert actual_hash == case["pdf_sha256"]
        assert len(case["critical_tokens"]) == 8
        assert len(case["table_associations"]) == 4
