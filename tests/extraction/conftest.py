from __future__ import annotations

from pathlib import Path

import pytest

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.development_data import (
    canonical_quotes_csv_path,
    development_dataset_dir,
    supplier_quote_path,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
FIXTURE_ROOT = DATA_ROOT / "generated" / "fixtures" / "extraction"
DEVELOPMENT_ROOT = FIXTURE_ROOT


def development_dir(version: int = 1) -> Path:
    if version == 1:
        return FIXTURE_ROOT / "canonical-quotes"
    if version == 2:
        return FIXTURE_ROOT / "requirements"
    return development_dataset_dir(DEVELOPMENT_ROOT, f"V{version}")


def quote_path(alias: str, version: int = 1, extension: str = "pdf") -> Path:
    if version in {1, 2}:
        return development_dir(version) / f"supplier_{alias.lower()}_quote_v{version}.{extension}"
    return supplier_quote_path(DEVELOPMENT_ROOT, f"V{version}", alias, extension)


def quotes_csv_path(version: int = 1) -> Path:
    if version == 1:
        return FIXTURE_ROOT / "canonical-quotes" / "quotes.csv"
    return canonical_quotes_csv_path(DEVELOPMENT_ROOT, f"V{version}")


@pytest.fixture(scope="session")
def quote_dictionary() -> QuoteDictionary:
    return QuoteDictionary.load(DATA_ROOT / "contracts" / "quote_data_field.csv")


def context_for(alias: str, version: int = 1) -> DocumentContext:
    alias = alias.upper()
    suppliers = {
        "A": "SUP-022",
        "B": "SUP-023",
        "C": "SUP-024",
        "D": "SUP-025",
        "E": "SUP-026",
    }
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
