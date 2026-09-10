from __future__ import annotations

import pytest

from scripts.run_real_extraction import _context, _exit_code, _pdf_path, _profiled_csv_path


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
    assert context.document_id.endswith("-V2")


def test_real_runner_preserves_v1_paths_and_exit_precedence() -> None:
    assert _pdf_path("V1", "A").name == "supplier_a_quote_v1.pdf"
    assert _pdf_path("V1", "A").parent.name == "quote_V1"
    assert _exit_code([0, 0, 0]) == 0
    assert _exit_code([0, 2, 0]) == 2
    assert _exit_code([2, 1, 0]) == 1


@pytest.mark.parametrize("alias", ("A", "B", "C"))
def test_v2_real_runner_builds_profiled_csv_paths(alias: str) -> None:
    path = _profiled_csv_path("V2", alias)

    assert path.name == f"supplier_{alias.lower()}_quote_v2.csv"
    assert path.parent.name == "quote_V2"
    assert path.is_file()
