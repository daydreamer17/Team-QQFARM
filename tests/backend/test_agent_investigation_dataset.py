from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data/generated/inputs/development/agent_investigation_demo/manifest.json"
EXPECTATIONS = ROOT / "evaluation/reference/agent_investigation_demo/cases.json"


def test_agent_investigation_pack_has_eight_isolated_scenarios_and_valid_hashes():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expectations = json.loads(EXPECTATIONS.read_text(encoding="utf-8"))
    assert manifest["case_count"] == 8
    assert len(manifest["cases"]) == 8
    assert {row["case_id"] for row in manifest["cases"]} == {
        row["case_id"] for row in expectations["cases"]
    }
    assert {row["kind"] for row in manifest["cases"]} == {
        "DOCUMENT", "WORKFLOW_STATE", "USER_REQUEST", "CONTROLLED_FAULT"
    }
    for case in manifest["cases"]:
        for item in case["files"]:
            path = ROOT / item["path"]
            assert path.is_file(), item["path"]
            assert path.stat().st_size == item["size_bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_runtime_manifest_does_not_leak_expected_tool_routes():
    runtime = MANIFEST.read_text(encoding="utf-8")
    assert "required_tools" not in runtime
    assert "forbidden_tools" not in runtime
    assert "allowed_stop_reasons" not in runtime
