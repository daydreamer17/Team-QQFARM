from __future__ import annotations

import json

import pytest

from supplier_comparison.rag.cli import _policy_root, main, smoke_models
from supplier_comparison.rag.clients import FixedEmbeddingClient, FixedRerankClient
from supplier_comparison.rag.importer import PolicyImportOutcome


class FakeImporter:
    def __init__(self) -> None:
        self.calls = []

    def import_manifest(self, manifest, *, publish):
        self.calls.append((manifest, publish))
        return PolicyImportOutcome(
            import_run_id="PIR-1",
            policy_set_id="electronics-procurement",
            policy_set_version="2026.09.1",
            index_version="pidx-1",
            status="PUBLISHED",
            replayed=False,
            clause_count=24,
        )


def test_import_cli_emits_only_safe_structured_outcome(capsys) -> None:
    importer = FakeImporter()
    exit_code = main(
        ["import-policies", "--manifest", "data/policies/electronics-v1/manifest.json", "--publish"],
        importer_factory=lambda: importer,
    )
    assert exit_code == 0
    assert importer.calls == [("data/policies/electronics-v1/manifest.json", True)]
    output = json.loads(capsys.readouterr().out)
    assert output == {
        "clause_count": 24,
        "import_run_id": "PIR-1",
        "index_version": "pidx-1",
        "policy_set_id": "electronics-procurement",
        "policy_set_version": "2026.09.1",
        "replayed": False,
        "status": "PUBLISHED",
    }


def test_model_smoke_checks_dimension_and_rerank_mapping() -> None:
    embedding = FixedEmbeddingClient(
        {"procurement policy retrieval smoke test": [0.1, 0.2]},
        model_id="fixed-embedding",
        dimension=2,
    )
    rerank = FixedRerankClient([1, 0], scores=[0.9, 0.8], model_id="fixed-rerank")
    result = smoke_models(embedding, rerank)
    assert result["embedding_dimension"] == 2
    assert result["rerank_indexes"] == [1, 0]
    assert result["status"] == "OK"


def test_policy_root_defaults_to_current_project_and_supports_explicit_env(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPPLIER_POLICY_ROOT", raising=False)
    assert _policy_root() == (tmp_path / "data" / "policies").resolve()
    monkeypatch.setenv("SUPPLIER_POLICY_ROOT", "mounted/policies")
    assert _policy_root() == (tmp_path / "mounted" / "policies").resolve()


def test_import_cli_rejects_absolute_and_traversal_manifest_paths(tmp_path) -> None:
    importer = FakeImporter()
    with pytest.raises(ValueError, match="relative"):
        main(
            ["import-policies", "--manifest", str(tmp_path / "manifest.json")],
            importer_factory=lambda: importer,
        )
    with pytest.raises(ValueError, match="traversal"):
        main(
            ["import-policies", "--manifest", "../policies/manifest.json"],
            importer_factory=lambda: importer,
        )
    assert importer.calls == []
