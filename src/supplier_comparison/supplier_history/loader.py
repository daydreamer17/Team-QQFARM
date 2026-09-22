"""Strict loader for immutable supplier-history releases."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from .contracts import (
    SupplierHistoryDataset,
    SupplierHistoryManifest,
    grade_for,
)


class SupplierHistoryLoadError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SupplierHistoryLoader:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def load(
        self,
        *,
        dataset_version: str,
        expected_content_sha256: str | None = None,
        expected_manifest_sha256: str | None = None,
    ) -> tuple[SupplierHistoryDataset, SupplierHistoryManifest, str]:
        if not dataset_version or any(part in {"", ".", ".."} for part in Path(dataset_version).parts):
            raise SupplierHistoryLoadError("invalid_dataset_version", "invalid dataset version")
        version_dir = (self.root / dataset_version).resolve()
        if self.root not in version_dir.parents:
            raise SupplierHistoryLoadError("invalid_dataset_version", "dataset path escapes root")
        content_path = version_dir / "supplier_performance.json"
        manifest_path = version_dir / "manifest.json"
        try:
            content_bytes = content_path.read_bytes()
            manifest_bytes = manifest_path.read_bytes()
        except OSError as exc:
            raise SupplierHistoryLoadError(
                "dataset_unavailable", "supplier history dataset is unavailable"
            ) from exc
        content_sha256 = hashlib.sha256(content_bytes).hexdigest()
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        if expected_content_sha256 and content_sha256 != expected_content_sha256:
            raise SupplierHistoryLoadError("content_hash_mismatch", "dataset content hash mismatch")
        if expected_manifest_sha256 and manifest_sha256 != expected_manifest_sha256:
            raise SupplierHistoryLoadError("manifest_hash_mismatch", "dataset manifest hash mismatch")
        try:
            manifest = SupplierHistoryManifest.model_validate_json(manifest_bytes)
            dataset = SupplierHistoryDataset.model_validate_json(content_bytes)
        except ValidationError as exc:
            raise SupplierHistoryLoadError("dataset_schema_invalid", "dataset schema is invalid") from exc
        if manifest.dataset_version != dataset_version:
            raise SupplierHistoryLoadError("dataset_version_mismatch", "manifest version mismatch")
        if manifest.content_sha256 != content_sha256:
            raise SupplierHistoryLoadError("content_hash_mismatch", "manifest content hash mismatch")
        if manifest.dataset_context != dataset.context:
            raise SupplierHistoryLoadError("dataset_context_mismatch", "manifest context mismatch")
        for supplier in dataset.suppliers:
            grade, availability = grade_for(
                supplier.on_time,
                supplier.rejected_lines,
                dataset.context.thresholds,
            )
            if grade != supplier.overall_grade or availability != supplier.history_availability_status:
                raise SupplierHistoryLoadError("rating_mismatch", "supplier rating failed validation")
            if supplier.order_line_count != supplier.on_time.denominator:
                raise SupplierHistoryLoadError("count_mismatch", "delivery count failed validation")
            if supplier.order_line_count != supplier.rejected_lines.denominator:
                raise SupplierHistoryLoadError("count_mismatch", "quality count failed validation")
        return dataset, manifest, manifest_sha256
