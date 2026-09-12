from __future__ import annotations

import json

import pytest

from scripts.run_real_extraction import (
    _context,
    _exit_code,
    _pdf_path,
    _profiled_csv_path,
    _run_supplier,
)
from supplier_comparison.extraction.adapters import ModelCallBudget, OpenAICompatibleConfig
from supplier_comparison.extraction.contracts import AdapterEnvironment
from supplier_comparison.extraction.errors import AdapterError


@pytest.mark.parametrize("alias", ("A", "B", "C"))
def test_v2_real_runner_builds_versioned_jobs(alias: str) -> None:
    path = _pdf_path("V2", alias)
    context = _context("V2", alias)

    assert path.name == f"supplier_{alias.lower()}_quote_v2.pdf"
    assert path.parent.name == "quote_V2"
    assert path.is_file()
    assert context.task_revision == 2
    assert context.quote_version == 2
    assert context.document_version == 2
    assert context.document_id.endswith("-V2-PDF")


def test_real_runner_preserves_v1_paths_and_exit_precedence() -> None:
    assert _pdf_path("V1", "A").name == "supplier_a_quote_v1.pdf"
    assert _pdf_path("V1", "A").parent.name == "quote_V1"
    assert _exit_code([0, 0, 0]) == 0
    assert _exit_code([0, 2, 0]) == 2
    assert _exit_code([2, 1, 0]) == 1


@pytest.mark.parametrize("alias", ("A", "B", "C"))
def test_v2_real_runner_builds_profiled_csv_paths(alias: str) -> None:
    path = _profiled_csv_path("V2", alias)
    context = _context("V2", alias, input_format="csv")

    assert path.name == f"supplier_{alias.lower()}_quote_v2.csv"
    assert path.parent.name == "quote_V2"
    assert path.is_file()
    assert context.document_id.endswith("-V2-CSV")


@pytest.mark.parametrize("alias", ("A", "B", "C", "D", "E"))
def test_v3_real_runner_builds_pdf_and_csv_jobs(alias: str) -> None:
    pdf_path = _pdf_path("V3", alias)
    csv_path = _profiled_csv_path("V3", alias)
    pdf_context = _context("V3", alias)
    csv_context = _context("V3", alias, input_format="csv")

    assert pdf_path.name == f"supplier_{alias.lower()}_quote_v3.pdf"
    assert csv_path.name == f"supplier_{alias.lower()}_quote_v3.csv"
    assert pdf_path.is_file() and csv_path.is_file()
    assert pdf_context.document_id.endswith("-V3-PDF")
    assert csv_context.document_id.endswith("-V3-CSV")
    assert pdf_context.supplier_id == csv_context.supplier_id


@pytest.mark.parametrize("alias", ("A", "B"))
def test_v4_real_runner_builds_valid_source_pdf_jobs(alias: str) -> None:
    path = _pdf_path("V4", alias)
    context = _context("V4", alias)

    assert path.name == f"source_supplier_{alias.lower()}_quote_v4.pdf"
    assert path.is_file()
    assert context.task_revision == 4
    assert context.document_id.endswith("-V4-PDF")


def test_real_runner_failure_is_explicitly_unscored(monkeypatch, quote_dictionary, tmp_path) -> None:
    def fail_extract(*args, **kwargs):
        del args, kwargs
        raise AdapterError("model_test_failure", "synthetic failure")

    monkeypatch.setattr("scripts.run_real_extraction.extract_quote_candidates", fail_extract)
    output = tmp_path / "failed.json"
    config = OpenAICompatibleConfig(
        provider="local-test",
        model_id="test-model",
        base_url="http://127.0.0.1:9999/v1",
        environment=AdapterEnvironment.LOCAL,
    )

    code, summary = _run_supplier(
        "B",
        "V2",
        "csv",
        output,
        config,
        quote_dictionary,
        ModelCallBudget(graph_run_id="GRAPH-UNSCORED"),
    )

    assert code == 1
    assert summary["status"] == "FAILED"
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["scoring_status"] == "UNSCORED"
    assert "A-approved reference" in record["scoring_note"]
