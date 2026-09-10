"""Shared paths for versioned synthetic development datasets."""

from __future__ import annotations

from pathlib import Path


DATASET_VERSIONS = {"V1": 1, "V2": 2}
SUPPLIER_ALIASES = ("A", "B", "C")


def dataset_version_number(dataset_version: str) -> int:
    try:
        return DATASET_VERSIONS[dataset_version.upper()]
    except KeyError as exc:
        raise ValueError(f"unsupported development dataset version: {dataset_version}") from exc


def development_dataset_dir(development_root: str | Path, dataset_version: str) -> Path:
    version = dataset_version_number(dataset_version)
    return Path(development_root) / f"quote_V{version}"


def supplier_quote_path(
    development_root: str | Path,
    dataset_version: str,
    supplier_alias: str,
    extension: str,
) -> Path:
    alias = supplier_alias.upper()
    if alias not in SUPPLIER_ALIASES:
        raise ValueError(f"unsupported supplier alias: {supplier_alias}")
    normalized_extension = extension.lower().lstrip(".")
    if normalized_extension not in {"pdf", "csv"}:
        raise ValueError(f"unsupported quote extension: {extension}")
    version = dataset_version_number(dataset_version)
    return (
        development_dataset_dir(development_root, dataset_version)
        / f"supplier_{alias.lower()}_quote_v{version}.{normalized_extension}"
    )


def canonical_quotes_csv_path(development_root: str | Path, dataset_version: str = "V1") -> Path:
    return development_dataset_dir(development_root, dataset_version) / "quotes.csv"
