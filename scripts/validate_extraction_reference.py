#!/usr/bin/env python3
"""Validate an isolated extraction reference set against the current dictionary and inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
ExpectedScalar = Annotated[StrictStr | StrictInt | StrictBool, Field(union_mode="left_to_right")]
NonEmptyString = Annotated[StrictStr, Field(min_length=1)]


class FrozenReferenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceField(FrozenReferenceModel):
    field_name: str = Field(min_length=1)
    expected_status: Literal["EXTRACTED", "MISSING", "CONFLICT"] | None = None
    expected_raw_values: tuple[NonEmptyString, ...] = ()
    expected_normalized_value: ExpectedScalar | None = None
    expected_unit: NonEmptyString | None = None
    semantic_evidence: tuple[NonEmptyString, ...] = ()
    critical: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def expected_shape_matches_status(self) -> "ReferenceField":
        if self.expected_status is None:
            if (
                self.expected_raw_values
                or self.expected_normalized_value is not None
                or self.expected_unit is not None
                or self.semantic_evidence
            ):
                raise ValueError("unreviewed reference fields must not contain expected values or evidence")
            return self
        if self.expected_status == "MISSING":
            if (
                self.expected_raw_values
                or self.expected_normalized_value is not None
                or self.expected_unit is not None
                or self.semantic_evidence
            ):
                raise ValueError("MISSING reference fields must not contain values, units, or evidence")
            return self
        if not self.expected_raw_values or not self.semantic_evidence:
            raise ValueError("EXTRACTED and CONFLICT reference fields require raw values and semantic evidence")
        if self.expected_status == "EXTRACTED" and self.expected_normalized_value is None:
            raise ValueError("EXTRACTED reference fields require an expected normalized value")
        return self


class DocumentReference(FrozenReferenceModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(min_length=1)
    split: Literal["development", "holdout"]
    template_id: str = Field(min_length=1)
    dictionary_version: str = Field(min_length=1)
    input_path: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_id: str = Field(min_length=1)
    quote_id: str = Field(min_length=1)
    quote_version: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    document_version: int = Field(ge=1)
    supplier_alias: str = Field(min_length=1)
    is_synthetic: Literal[True]
    review_status: Literal["DRAFT", "A_APPROVED"] = "DRAFT"
    reviewed_by: str | None = None
    fields: tuple[ReferenceField, ...]

    @model_validator(mode="after")
    def document_reference_is_consistent(self) -> "DocumentReference":
        field_names = [field.field_name for field in self.fields]
        if len(field_names) != len(set(field_names)):
            raise ValueError("reference field names must be unique within a document")
        if self.review_status == "A_APPROVED" and not (self.reviewed_by or "").strip():
            raise ValueError("A_APPROVED references require reviewed_by")
        if self.review_status == "A_APPROVED" and any(
            field.expected_status is None for field in self.fields
        ):
            raise ValueError("A_APPROVED references cannot contain unreviewed fields")
        return self


class ExtractionReferenceSet(FrozenReferenceModel):
    schema_version: Literal["1.0"] = "1.0"
    reference_set_id: str = Field(min_length=1)
    documents: tuple[DocumentReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def document_versions_are_unique(self) -> "ExtractionReferenceSet":
        identities = [(item.document_id, item.document_version) for item in self.documents]
        if len(identities) != len(set(identities)):
            raise ValueError("document_id/document_version pairs must be unique")
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_path(repo_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ContractError(
            "reference_input_path_invalid",
            "reference input_path must be relative to the repository",
            input_path=relative_path,
        )
    resolved_root = repo_root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ContractError(
            "reference_input_path_invalid",
            "reference input_path cannot escape the repository",
            input_path=relative_path,
        ) from exc
    return resolved


def validate_reference_set(
    reference_path: Path,
    dictionary_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    require_approved: bool = False,
) -> ExtractionReferenceSet:
    try:
        reference = ExtractionReferenceSet.model_validate_json(reference_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, json.JSONDecodeError) as exc:
        raise ContractError(
            "reference_invalid",
            "reference file is unreadable or does not match the reference schema",
            path=str(reference_path),
        ) from exc

    dictionary = QuoteDictionary.load(dictionary_path)
    expected_fields = {definition.field_name for definition in dictionary.extractable_fields}
    for document in reference.documents:
        if document.dictionary_version != dictionary.version:
            raise ContractError(
                "reference_dictionary_version_mismatch",
                "reference dictionary version does not match the loaded dictionary",
                document_id=document.document_id,
                expected=dictionary.version,
                actual=document.dictionary_version,
            )
        actual_fields = {field.field_name for field in document.fields}
        if actual_fields != expected_fields:
            raise ContractError(
                "reference_field_set_mismatch",
                "reference fields must exactly match B-extractable dictionary fields",
                document_id=document.document_id,
                missing=sorted(expected_fields - actual_fields),
                extra=sorted(actual_fields - expected_fields),
            )
        if require_approved and document.review_status != "A_APPROVED":
            raise ContractError(
                "reference_not_approved",
                "field scoring requires an A-approved reference",
                document_id=document.document_id,
                review_status=document.review_status,
            )
        input_path = _input_path(repo_root, document.input_path)
        if not input_path.is_file():
            raise ContractError(
                "reference_input_missing",
                "reference input file does not exist",
                document_id=document.document_id,
                input_path=document.input_path,
            )
        actual_hash = _sha256(input_path)
        if actual_hash != document.input_sha256:
            raise ContractError(
                "reference_input_hash_mismatch",
                "reference input hash does not match the bound file",
                document_id=document.document_id,
                expected=document.input_sha256,
                actual=actual_hash,
            )
    return reference


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=REPO_ROOT / "data/contracts/quote_data_field.csv",
    )
    parser.add_argument("--require-approved", action="store_true")
    args = parser.parse_args()

    try:
        reference = validate_reference_set(
            args.reference,
            args.dictionary,
            require_approved=args.require_approved,
        )
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1
    print(
        json.dumps(
            {
                "status": "PASSED",
                "reference_set_id": reference.reference_set_id,
                "document_count": len(reference.documents),
                "all_a_approved": all(item.review_status == "A_APPROVED" for item in reference.documents),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
