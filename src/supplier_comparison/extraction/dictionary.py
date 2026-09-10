"""Loader for A's frozen supplier-quote data dictionary."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .errors import ContractError


REQUIRED_COLUMNS = {
    "field_name",
    "type",
    "meaning",
    "example",
    "必填级别",
    "统一表达规则",
    "校验与歧义边界",
    "证据要求",
    "责任方",
    "字典版本",
}

FIELD_ALLOWED_NORMALIZED_VALUES = {
    "price_basis_unit": ("piece",),
    "shipping_fee_status": (
        "KNOWN_AMOUNT",
        "FREE",
        "INCLUDED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ),
    "other_fees_status": (
        "KNOWN_AMOUNT",
        "FREE",
        "INCLUDED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    ),
}


@dataclass(frozen=True, slots=True)
class QuoteFieldDefinition:
    field_name: str
    value_type: str
    meaning: str
    example: str
    required_level: str
    normalization_rule: str
    validation_boundary: str
    evidence_requirement: str
    owner: str
    dictionary_version: str

    @property
    def is_extractable_by_b(self) -> bool:
        return "B提取" in self.owner and self.required_level != "系统"

    @property
    def allowed_normalized_values(self) -> tuple[str, ...] | None:
        return FIELD_ALLOWED_NORMALIZED_VALUES.get(self.field_name)


@dataclass(frozen=True, slots=True)
class QuoteDictionary:
    version: str
    fields: dict[str, QuoteFieldDefinition]

    @classmethod
    def load(cls, path: str | Path) -> "QuoteDictionary":
        dictionary_path = Path(path)
        try:
            with dictionary_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_columns = set(reader.fieldnames or ())
                missing_columns = REQUIRED_COLUMNS - actual_columns
                if missing_columns:
                    raise ContractError(
                        "dictionary_columns_missing",
                        "quote dictionary is missing required columns",
                        missing_columns=sorted(missing_columns),
                    )
                definitions: dict[str, QuoteFieldDefinition] = {}
                versions: set[str] = set()
                for row in reader:
                    field_name = (row["field_name"] or "").strip()
                    if not field_name:
                        raise ContractError("dictionary_field_blank", "dictionary field_name cannot be blank")
                    if field_name in definitions:
                        raise ContractError(
                            "dictionary_field_duplicate",
                            f"duplicate dictionary field: {field_name}",
                            field_name=field_name,
                        )
                    definition = QuoteFieldDefinition(
                        field_name=field_name,
                        value_type=(row["type"] or "").strip(),
                        meaning=(row["meaning"] or "").strip(),
                        example=(row["example"] or "").strip(),
                        required_level=(row["必填级别"] or "").strip(),
                        normalization_rule=(row["统一表达规则"] or "").strip(),
                        validation_boundary=(row["校验与歧义边界"] or "").strip(),
                        evidence_requirement=(row["证据要求"] or "").strip(),
                        owner=(row["责任方"] or "").strip(),
                        dictionary_version=(row["字典版本"] or "").strip(),
                    )
                    definitions[field_name] = definition
                    versions.add(definition.dictionary_version)
        except OSError as exc:
            raise ContractError(
                "dictionary_unreadable", f"cannot read quote dictionary: {dictionary_path}", path=str(dictionary_path)
            ) from exc

        if len(versions) != 1 or "" in versions:
            raise ContractError(
                "dictionary_version_inconsistent",
                "quote dictionary must contain one non-empty version",
                versions=sorted(versions),
            )
        return cls(version=versions.pop(), fields=definitions)

    @property
    def extractable_fields(self) -> tuple[QuoteFieldDefinition, ...]:
        return tuple(field for field in self.fields.values() if field.is_extractable_by_b)
