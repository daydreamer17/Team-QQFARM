from __future__ import annotations

import json
from pathlib import Path

import pytest

from supplier_comparison.rag.manifest import ManifestError, load_policy_manifest


def _write_manifest(root: Path, *, clause_id: str = "QUOTE-001") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "quote.md").write_text(
        f"# Quote Policy\n\n## [{clause_id}] Required fields\nEvery quote must state currency and shipping.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "1.0.0",
        "policy_set_id": "electronics-procurement",
        "policy_set_version": "2026.09.1",
        "documents": [
            {
                "policy_id": "POL-QUOTE",
                "document_id": "DOC-QUOTE",
                "document_version": "1.0.0",
                "title": "Quote Policy",
                "path": "quote.md",
                "effective_from": "2026-01-01T00:00:00Z",
                "effective_to": None,
                "categories": ["Electronics"],
                "regions": ["SG"],
                "clauses": {
                    clause_id: {
                        "control_code": "QUOTE_COMPLETENESS",
                        "rule_parameters": {"required_fields": ["currency", "shipping"]},
                    }
                },
            }
        ],
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_manifest_parses_stable_clause_boundaries_and_hashes(tmp_path: Path) -> None:
    manifest_path = _write_manifest(tmp_path / "policies")
    loaded = load_policy_manifest(manifest_path, allowed_root=tmp_path / "policies")
    assert loaded.policy_set_version == "2026.09.1"
    assert loaded.documents[0].clauses[0].clause_id == "QUOTE-001"
    assert loaded.documents[0].clauses[0].text == "Every quote must state currency and shipping."
    assert len(loaded.documents[0].clauses[0].content_sha256) == 64
    assert len(loaded.content_sha256) == 64


def test_manifest_rejects_paths_outside_whitelist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = _write_manifest(tmp_path / "outside")
    allowed.mkdir()
    with pytest.raises(ManifestError, match="allowed policy directory"):
        load_policy_manifest(outside, allowed_root=allowed)


def test_manifest_rejects_duplicate_clause_ids(tmp_path: Path) -> None:
    root = tmp_path / "policies"
    path = _write_manifest(root)
    payload = json.loads(path.read_text(encoding="utf-8"))
    duplicate = dict(payload["documents"][0])
    duplicate["document_id"] = "DOC-DUPLICATE"
    duplicate["policy_id"] = "POL-DUPLICATE"
    payload["documents"].append(duplicate)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match="duplicate clause ID"):
        load_policy_manifest(path, allowed_root=root)


def test_manifest_rejects_orphaned_or_empty_clauses(tmp_path: Path) -> None:
    root = tmp_path / "policies"
    path = _write_manifest(root)
    (root / "quote.md").write_text(
        "# Quote Policy\n\n## [QUOTE-001] Required fields\n\n## [ORPHAN] Unregistered\nText.\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="empty clause|orphan"):
        load_policy_manifest(path, allowed_root=root)


def test_manifest_rejects_level_two_sections_without_stable_clause_id(
    tmp_path: Path,
) -> None:
    root = tmp_path / "policies"
    path = _write_manifest(root)
    (root / "quote.md").write_text(
        "# Quote Policy\n\n## Notes\nUnregistered section.\n\n"
        "## [QUOTE-001] Required fields\nEvery quote must state currency and shipping.\n",
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="orphan section"):
        load_policy_manifest(path, allowed_root=root)


def test_repository_demo_manifest_contains_five_documents_and_24_clauses() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    policy_root = repo_root / "data" / "policies"
    loaded = load_policy_manifest(
        policy_root / "electronics-v1" / "manifest.json", allowed_root=policy_root
    )
    assert len(loaded.documents) == 5
    assert sum(len(document.clauses) for document in loaded.documents) == 24
