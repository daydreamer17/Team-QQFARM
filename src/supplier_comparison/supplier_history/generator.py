"""Deterministically aggregate the bundled synthetic procurement history."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .contracts import (
    GENERATOR_VERSION,
    HISTORY_SCHEMA_VERSION,
    IDENTITY_MATCHER_VERSION,
    RATING_METHOD_VERSION,
    RateMetric,
    SupplierHistoryDataset,
    SupplierHistoryDatasetContext,
    SupplierHistoryManifest,
    SupplierHistoryScope,
    SupplierHistoryThresholds,
    SupplierPerformance,
    canonical_json_bytes,
    grade_for,
)


DATASET_ID = "synthetic-mcu9-supplier-performance"
CATEGORY = "Electronics"
ITEM = "Microcontroller MCU-9"
EXPECTED_SOURCE_SHA256 = "fba22a467e5dd800a0af960930d2ca5e2ecbd9d2bc747e37304867d8d031ae21"
EXPECTED_SCOPE_ROWS = 1185
SOURCE_PROJECT = "dytcoke23/procurement-spend-analysis-dashboard"
SOURCE_LICENSE = "MIT"
TASK_PRODUCT_MAPPINGS = ("QW-MCU9-DEMO",)


DEFAULT_THRESHOLDS = SupplierHistoryThresholds(
    min_sample_size=10,
    grade_a_on_time_min="0.90",
    grade_a_rejected_max="0.02",
    grade_b_on_time_min="0.80",
    grade_b_rejected_max="0.05",
    grade_c_on_time_min="0.70",
    grade_c_rejected_max="0.10",
)


class SupplierHistoryGenerationError(ValueError):
    pass


def _parse_date(value: str, *, row_number: int, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError) as exc:
        raise SupplierHistoryGenerationError(
            f"row {row_number}: {field} must be a valid ISO date"
        ) from exc


def _parse_bool(value: str, *, row_number: int, field: str) -> bool:
    normalized = value.strip()
    if normalized == "True":
        return True
    if normalized == "False":
        return False
    raise SupplierHistoryGenerationError(
        f"row {row_number}: {field} must be True or False"
    )


def _read_rows(source_path: Path) -> list[dict[str, str]]:
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "po_id",
            "order_date",
            "promised_delivery_date",
            "actual_delivery_date",
            "supplier_id",
            "supplier_name",
            "category",
            "item",
            "quality_rejected",
        }
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise SupplierHistoryGenerationError(
                f"source CSV is missing columns: {', '.join(sorted(missing))}"
            )
        return [dict(row) for row in reader]


def aggregate_rows(
    rows: Iterable[dict[str, str]],
    *,
    as_of_date: date,
    thresholds: SupplierHistoryThresholds = DEFAULT_THRESHOLDS,
) -> tuple[tuple[SupplierPerformance, ...], date, date, int, int]:
    seen_po_ids: set[str] = set()
    source_count = 0
    scoped_count = 0
    included_dates: list[date] = []
    by_supplier: dict[tuple[str, str], list[tuple[date, date, bool]]] = defaultdict(list)
    names_by_id: dict[str, str] = {}

    for row_number, row in enumerate(rows, start=2):
        source_count += 1
        po_id = row["po_id"].strip()
        if not po_id or po_id in seen_po_ids:
            raise SupplierHistoryGenerationError(
                f"row {row_number}: po_id is blank or duplicated: {po_id!r}"
            )
        seen_po_ids.add(po_id)
        if row["category"].strip() != CATEGORY or row["item"].strip() != ITEM:
            continue
        scoped_count += 1
        order_date = _parse_date(row["order_date"], row_number=row_number, field="order_date")
        promised = _parse_date(
            row["promised_delivery_date"], row_number=row_number, field="promised_delivery_date"
        )
        actual = _parse_date(
            row["actual_delivery_date"], row_number=row_number, field="actual_delivery_date"
        )
        rejected = _parse_bool(
            row["quality_rejected"], row_number=row_number, field="quality_rejected"
        )
        supplier_id = row["supplier_id"].strip()
        supplier_name = row["supplier_name"].strip()
        if not supplier_id or not supplier_name:
            raise SupplierHistoryGenerationError(
                f"row {row_number}: supplier identity cannot be blank"
            )
        prior_name = names_by_id.setdefault(supplier_id, supplier_name)
        if prior_name != supplier_name:
            raise SupplierHistoryGenerationError(
                f"row {row_number}: supplier ID {supplier_id} has conflicting names"
            )
        if order_date <= as_of_date and actual <= as_of_date:
            by_supplier[(supplier_id, supplier_name)].append((promised, actual, rejected))
            included_dates.append(order_date)

    if source_count == 0 or scoped_count == 0 or not included_dates:
        raise SupplierHistoryGenerationError("source does not contain completed MCU-9 rows")

    suppliers: list[SupplierPerformance] = []
    for (supplier_id, supplier_name), facts in sorted(by_supplier.items()):
        on_time_count = sum(actual <= promised for promised, actual, _ in facts)
        rejected_count = sum(rejected for _, _, rejected in facts)
        on_time = RateMetric(
            numerator=on_time_count,
            denominator=len(facts),
            rate=Decimal(on_time_count) / Decimal(len(facts)),
        )
        rejected = RateMetric(
            numerator=rejected_count,
            denominator=len(facts),
            rate=Decimal(rejected_count) / Decimal(len(facts)),
        )
        grade, availability = grade_for(on_time, rejected, thresholds)
        suppliers.append(
            SupplierPerformance(
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                order_line_count=len(facts),
                on_time=on_time,
                rejected_lines=rejected,
                overall_grade=grade,
                history_availability_status=availability,
            )
        )
    return (
        tuple(suppliers),
        min(included_dates),
        max(included_dates),
        source_count,
        scoped_count,
    )


def generate_supplier_history(
    source_path: Path,
    output_dir: Path,
    *,
    dataset_version: str,
    generated_at: datetime,
    as_of_date: date,
    require_bundled_source: bool = True,
) -> tuple[Path, Path, str, str]:
    """Generate immutable content and manifest files; return paths and hashes."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise SupplierHistoryGenerationError("generated_at must be timezone-aware")
    source_bytes = source_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if require_bundled_source and source_sha256 != EXPECTED_SOURCE_SHA256:
        raise SupplierHistoryGenerationError("source SHA-256 does not match bundled authority")
    suppliers, period_start, period_end, source_count, scoped_count = aggregate_rows(
        _read_rows(source_path), as_of_date=as_of_date
    )
    if require_bundled_source and scoped_count != EXPECTED_SCOPE_ROWS:
        raise SupplierHistoryGenerationError(
            f"expected {EXPECTED_SCOPE_ROWS} scoped rows, found {scoped_count}"
        )
    context = SupplierHistoryDatasetContext(
        schema_version=HISTORY_SCHEMA_VERSION,
        dataset_id=DATASET_ID,
        dataset_version=dataset_version,
        rating_method_version=RATING_METHOD_VERSION,
        identity_matcher_version=IDENTITY_MATCHER_VERSION,
        scope={
            "category": CATEGORY,
            "item": ITEM,
            "task_product_mappings": TASK_PRODUCT_MAPPINGS,
        },
        as_of_date=as_of_date,
        period_start=period_start,
        period_end=period_end,
        thresholds=DEFAULT_THRESHOLDS,
        source_project=SOURCE_PROJECT,
        source_license=SOURCE_LICENSE,
        is_synthetic=True,
    )
    dataset = SupplierHistoryDataset(context=context, suppliers=suppliers)
    content_bytes = canonical_json_bytes(dataset)
    content_sha256 = hashlib.sha256(content_bytes).hexdigest()
    alias_payload = {
        item.supplier_id: [item.supplier_name, *item.aliases] for item in suppliers
    }
    alias_sha256 = hashlib.sha256(
        (json.dumps(alias_payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()
    manifest = SupplierHistoryManifest(
        dataset_id=DATASET_ID,
        dataset_version=dataset_version,
        content_sha256=content_sha256,
        source_path="data/source/purchase_orders.csv",
        source_sha256=source_sha256,
        source_record_count=source_count,
        filtered_record_count=scoped_count,
        generated_at=generated_at,
        generator_version=GENERATOR_VERSION,
        dataset_context=context,
        alias_allowlist_sha256=alias_sha256,
    )
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    version_dir = output_dir / dataset_version
    version_dir.mkdir(parents=True, exist_ok=True)
    content_path = version_dir / "supplier_performance.json"
    manifest_path = version_dir / "manifest.json"
    for target in (content_path, manifest_path):
        if target.exists():
            raise SupplierHistoryGenerationError(
                f"refusing to overwrite immutable dataset file: {target}"
            )
    content_path.write_bytes(content_bytes)
    manifest_path.write_bytes(manifest_bytes)
    return content_path, manifest_path, content_sha256, manifest_sha256
