"""Contracts for immutable supplier-performance datasets and snapshots."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HISTORY_SCHEMA_VERSION = "supplier-history/1.0.0"
RATING_METHOD_VERSION = "mcu9-demo-rating/1.0.0"
IDENTITY_MATCHER_VERSION = "exact-supplier-directory/1.0.0"
GENERATOR_VERSION = "supplier-history-generator/1.0.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HistoryAvailabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NO_DATA = "NO_DATA"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class RateMetric(FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: Decimal | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def rate_matches_counts(self) -> "RateMetric":
        expected = (
            Decimal(self.numerator) / Decimal(self.denominator)
            if self.denominator
            else None
        )
        if self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        if self.rate != expected:
            raise ValueError("rate must exactly equal numerator / denominator")
        return self


class SupplierPerformance(FrozenModel):
    supplier_id: str = Field(min_length=1)
    supplier_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    order_line_count: int = Field(ge=1)
    on_time: RateMetric
    rejected_lines: RateMetric
    overall_grade: str | None = None
    history_availability_status: HistoryAvailabilityStatus

    @field_validator("overall_grade")
    @classmethod
    def grade_is_supported(cls, value: str | None) -> str | None:
        if value is not None and value not in {"A", "B", "C", "D", "N"}:
            raise ValueError("unsupported supplier history grade")
        return value


class SupplierHistoryScope(FrozenModel):
    category: str = Field(min_length=1)
    item: str = Field(min_length=1)
    task_product_mappings: tuple[str, ...] = ()


class SupplierHistoryThresholds(FrozenModel):
    min_sample_size: int = Field(ge=1)
    grade_a_on_time_min: Decimal = Field(ge=0, le=1)
    grade_a_rejected_max: Decimal = Field(ge=0, le=1)
    grade_b_on_time_min: Decimal = Field(ge=0, le=1)
    grade_b_rejected_max: Decimal = Field(ge=0, le=1)
    grade_c_on_time_min: Decimal = Field(ge=0, le=1)
    grade_c_rejected_max: Decimal = Field(ge=0, le=1)


class SupplierHistoryDatasetContext(FrozenModel):
    schema_version: str = HISTORY_SCHEMA_VERSION
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    rating_method_version: str = RATING_METHOD_VERSION
    identity_matcher_version: str = IDENTITY_MATCHER_VERSION
    scope: SupplierHistoryScope
    as_of_date: date
    period_start: date
    period_end: date
    lookback: str = "ALL_COMPLETED_HISTORY"
    thresholds: SupplierHistoryThresholds
    source_project: str = Field(min_length=1)
    source_license: str = Field(min_length=1)
    is_synthetic: bool

    @model_validator(mode="after")
    def period_is_valid(self) -> "SupplierHistoryDatasetContext":
        if self.period_end < self.period_start:
            raise ValueError("period_end cannot precede period_start")
        if self.period_end > self.as_of_date:
            raise ValueError("period_end cannot exceed as_of_date")
        if not self.is_synthetic:
            raise ValueError("the bundled history dataset must be marked synthetic")
        return self


class SupplierHistoryDataset(FrozenModel):
    context: SupplierHistoryDatasetContext
    suppliers: tuple[SupplierPerformance, ...]

    @model_validator(mode="after")
    def suppliers_are_unique(self) -> "SupplierHistoryDataset":
        ids = [item.supplier_id for item in self.suppliers]
        if len(ids) != len(set(ids)):
            raise ValueError("supplier IDs must be unique")
        return self


class SupplierHistoryManifest(FrozenModel):
    schema_version: str = HISTORY_SCHEMA_VERSION
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_record_count: int = Field(ge=1)
    filtered_record_count: int = Field(ge=1)
    generated_at: datetime
    generator_version: str = GENERATOR_VERSION
    dataset_context: SupplierHistoryDatasetContext
    alias_allowlist_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("generated_at")
    @classmethod
    def generated_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


def grade_for(
    on_time: RateMetric,
    rejected: RateMetric,
    thresholds: SupplierHistoryThresholds,
) -> tuple[str | None, HistoryAvailabilityStatus]:
    """Recompute the versioned demo grade from exact ratios."""

    if on_time.denominator == 0 and rejected.denominator == 0:
        return None, HistoryAvailabilityStatus.NO_DATA
    if (
        on_time.denominator < thresholds.min_sample_size
        or rejected.denominator < thresholds.min_sample_size
    ):
        return "N", HistoryAvailabilityStatus.INSUFFICIENT_SAMPLE
    assert on_time.rate is not None and rejected.rate is not None
    for grade, on_time_min, rejected_max in (
        ("A", thresholds.grade_a_on_time_min, thresholds.grade_a_rejected_max),
        ("B", thresholds.grade_b_on_time_min, thresholds.grade_b_rejected_max),
        ("C", thresholds.grade_c_on_time_min, thresholds.grade_c_rejected_max),
    ):
        if on_time.rate >= on_time_min and rejected.rate <= rejected_max:
            return grade, HistoryAvailabilityStatus.AVAILABLE
    return "D", HistoryAvailabilityStatus.AVAILABLE


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    import json

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
