#!/usr/bin/env python3
"""Convert A's legacy V2 system-evidence subset into a non-authoritative draft reference."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from scripts.validate_extraction_reference import (
        DocumentReference,
        ExtractionReferenceSet,
        ReferenceField,
    )
except ModuleNotFoundError:  # Direct `python scripts/...` execution.
    from validate_extraction_reference import (
        DocumentReference,
        ExtractionReferenceSet,
        ReferenceField,
    )
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ContractError


REPO_ROOT = Path(__file__).resolve().parents[1]
STATUS_MAP = {
    "VERIFIED": "EXTRACTED",
    "EXTRACTED": "EXTRACTED",
    "MISSING": "MISSING",
    "CONFLICT": "CONFLICT",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique_evidence(record: dict) -> tuple[str, ...]:
    values: list[str] = []
    for source in record.get("source_refs", []):
        text = str(source.get("evidence_text") or "").strip()
        if text and text not in values:
            values.append(text)
    return tuple(values)


def _migrate_legacy_field(record: dict, *, critical: bool) -> ReferenceField:
    field_name = str(record["field_name"])
    legacy_status = str(record["validation_status"])
    try:
        expected_status = STATUS_MAP[legacy_status]
    except KeyError as exc:
        raise ContractError(
            "legacy_reference_status_unknown",
            "legacy field uses an unsupported validation status",
            field_name=field_name,
            validation_status=legacy_status,
        ) from exc

    raw_value = record.get("raw_value")
    normalized_value = record.get("normalized_value")
    unit = record.get("unit")
    evidence = _unique_evidence(record)
    note = "Migrated from A's legacy V2 evidence subset; A review is still required."

    if expected_status == "MISSING":
        return ReferenceField(
            field_name=field_name,
            expected_status="MISSING",
            critical=critical,
            notes=note,
        )
    if field_name == "other_fees_status":
        normalized_value = "NOT_APPLICABLE"
        note = (
            "Migrated from the legacy KNOWN_ZERO value and changed to the team-confirmed "
            "NOT_APPLICABLE contract; A review is still required."
        )
    return ReferenceField(
        field_name=field_name,
        expected_status=expected_status,
        expected_raw_values=(str(raw_value),) if raw_value is not None else (),
        expected_normalized_value=normalized_value,
        expected_unit=str(unit) if unit is not None else None,
        semantic_evidence=evidence,
        critical=critical,
        notes=note,
    )


def migrate_reference(
    legacy_path: Path,
    input_path: Path,
    dictionary_path: Path,
) -> ExtractionReferenceSet:
    try:
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "legacy_reference_invalid",
            "legacy reference is unreadable or invalid JSON",
            path=str(legacy_path),
        ) from exc
    if not input_path.is_file():
        raise ContractError(
            "reference_input_missing",
            "V2 CSV input does not exist",
            input_path=str(input_path),
        )

    dictionary = QuoteDictionary.load(dictionary_path)
    legacy_records = {
        str(record["field_name"]): record for record in legacy.get("field_records", [])
    }
    definitions = {item.field_name: item for item in dictionary.extractable_fields}
    unknown_fields = sorted(set(legacy_records) - set(definitions))
    if unknown_fields:
        raise ContractError(
            "legacy_reference_field_unknown",
            "legacy reference contains fields outside the current B extraction dictionary",
            fields=unknown_fields,
        )

    fields: list[ReferenceField] = []
    for definition in dictionary.extractable_fields:
        critical = "关键" in definition.required_level
        record = legacy_records.get(definition.field_name)
        if record is not None:
            fields.append(_migrate_legacy_field(record, critical=critical))
        elif definition.field_name == "shipping_fee_amount":
            fields.append(
                ReferenceField(
                    field_name=definition.field_name,
                    expected_status="MISSING",
                    critical=critical,
                    notes=(
                        "Added from the confirmed B shipping omission rule; A review is still required."
                    ),
                )
            )
        elif definition.field_name == "other_fees_amount":
            fields.append(
                ReferenceField(
                    field_name=definition.field_name,
                    expected_status="EXTRACTED",
                    expected_raw_values=("None",),
                    expected_normalized_value="0.00",
                    expected_unit="SGD",
                    semantic_evidence=("None",),
                    critical=critical,
                    notes=(
                        "Added from the team-confirmed None-to-0.00 normalization; "
                        "A review is still required."
                    ),
                )
            )
        else:
            fields.append(
                ReferenceField(
                    field_name=definition.field_name,
                    expected_status=None,
                    critical=critical,
                    notes="Not present in the legacy subset; A must review this field.",
                )
            )

    try:
        relative_input = input_path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ContractError(
            "reference_input_path_invalid",
            "V2 CSV input must be located inside the repository",
            input_path=str(input_path),
        ) from exc

    supplier_alias = str(legacy.get("supplier_alias") or "B").upper()
    scenario_id = str(legacy.get("scenario_id") or "MCU-DEMO-001")
    quote_id = str(legacy.get("quote_id") or f"QUOTE-{scenario_id}-{supplier_alias}")
    document = DocumentReference(
        dataset_id="quote_V2_csv",
        split="development",
        template_id=f"v2_supplier_{supplier_alias.lower()}",
        dictionary_version=dictionary.version,
        input_path=relative_input,
        input_sha256=_sha256(input_path),
        scenario_id=scenario_id,
        quote_id=quote_id,
        quote_version=2,
        document_id=f"DOC-{scenario_id}-{supplier_alias}-V2-CSV",
        document_version=2,
        supplier_alias=supplier_alias,
        is_synthetic=True,
        review_status="DRAFT",
        reviewed_by=None,
        fields=tuple(fields),
    )
    return ExtractionReferenceSet(
        reference_set_id=f"{scenario_id}-V2-{supplier_alias}-CSV-DRAFT",
        documents=(document,),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=REPO_ROOT / "data/contracts/quote_data_field.csv",
    )
    args = parser.parse_args()

    if args.output.exists():
        print(json.dumps({"status": "FAILED", "error_code": "reference_output_exists"}))
        return 1
    try:
        reference = migrate_reference(args.legacy, args.input, args.dictionary)
    except ContractError as exc:
        print(json.dumps({"status": "FAILED", "error_code": exc.code, "details": exc.details}))
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(reference.model_dump_json(indent=2) + "\n", encoding="utf-8")
    fields = reference.documents[0].fields
    print(
        json.dumps(
            {
                "status": "PASSED",
                "output": str(args.output),
                "review_status": "DRAFT",
                "field_count": len(fields),
                "reviewed_field_count": sum(item.expected_status is not None for item in fields),
                "unreviewed_field_count": sum(item.expected_status is None for item in fields),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
