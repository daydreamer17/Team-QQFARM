from __future__ import annotations

from pathlib import Path

import pytest

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.dictionary import QuoteDictionary


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"


@pytest.fixture(scope="session")
def quote_dictionary() -> QuoteDictionary:
    return QuoteDictionary.load(DATA_ROOT / "contracts" / "quote_data_field.csv")


def context_for(alias: str, version: int = 1) -> DocumentContext:
    alias = alias.upper()
    suppliers = {"A": "SUP-022", "B": "SUP-023", "C": "SUP-024"}
    return DocumentContext(
        task_id="TASK-MCU-DEMO-001",
        task_revision=version,
        scenario_id="MCU-DEMO-001",
        quote_id=f"QUOTE-MCU-DEMO-001-{alias}",
        quote_version=version,
        document_id=f"DOC-MCU-DEMO-001-{alias}-V{version}",
        document_version=version,
        supplier_id=suppliers[alias],
    )
