"""Strict canonical CSV parsing and explicit heterogeneous CSV source profiles."""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import (
    CandidateProducer,
    DocumentContext,
    EvidenceSource,
    ExtractionBatch,
    Origin,
    ParsedInput,
    QuoteFieldCandidate,
    SourceCitation,
    SourceKind,
    ValidationStatus,
)
from .dictionary import QuoteDictionary, QuoteFieldDefinition
from .errors import ContractError, UnreadableInputError
from .files import FileLimits, require_csv_shape, stable_id, validate_regular_file


PARSER_VERSION = "fixed-csv/1.0.0"
PROFILED_PARSER_VERSION = "profiled-csv/1.0.0"

FROZEN_CSV_COLUMNS = (
    "scenario_id", "quote_id", "quote_version", "document_id", "supplier_alias", "supplier_id",
    "supplier_name", "supplier_country", "category", "item", "manufacturer", "manufacturer_part_number",
    "package", "revision", "condition", "currency", "unit_price", "price_basis_quantity",
    "price_basis_unit", "packaging_type", "units_per_pack", "order_multiple_units", "moq_quantity",
    "moq_unit", "shipping_fee_status", "shipping_fee_amount", "other_fees_status", "other_fees_amount",
    "fees_complete", "tax_mode", "lead_time_days", "day_basis", "delivery_semantics", "start_event",
    "start_date", "delivery_location", "payment_terms", "quote_date", "valid_until", "source_po_id",
    "is_synthetic",
)


@dataclass(frozen=True, slots=True)
class CsvSourceProfile:
    """An explicitly selected CSV header contract that produces evidence only."""

    profile_id: str
    version: str
    columns: tuple[str, ...]

    @property
    def parser_version(self) -> str:
        return f"{PROFILED_PARSER_VERSION}:{self.profile_id}/{self.version}"


V2_CSV_PROFILES = {
    "v2_supplier_a": CsvSourceProfile(
        profile_id="v2_supplier_a",
        version="1.0.0",
        columns=(
            "Quotation No.", "Vendor", "Country", "Product Category", "Item Description",
            "Maker", "Mfr P/N", "Device Package", "Device Rev", "Condition", "Price Offer",
            "Pack Configuration", "Minimum Order", "Freight", "Other Charges", "Tax Treatment",
            "Delivery Promise", "Payment", "Quote Date", "Valid To",
        ),
    ),
    "v2_supplier_b": CsvSourceProfile(
        profile_id="v2_supplier_b",
        version="1.0.0",
        columns=(
            "Response Ref", "Supplier", "Product Description", "Brand", "Part No.", "Case",
            "Version", "Item Status", "Currency", "Each Price", "Minimum Qty", "Supply Form",
            "Order Increment", "Additional Fees", "Tax", "Lead Time", "Validity",
        ),
    ),
    "v2_supplier_c": CsvSourceProfile(
        profile_id="v2_supplier_c",
        version="1.0.0",
        columns=(
            "Offer ID", "Seller", "Product", "MPN", "Package/Revision", "Condition", "Rate",
            "Packing", "MOQ", "Logistics Charge", "Fees Note", "Tax Basis", "Delivery",
            "Pay Terms", "Issued", "Offer Expiry",
        ),
    ),
}

V3_CSV_PROFILES = {
    "v3_supplier_a": CsvSourceProfile(
        profile_id="v3_supplier_a",
        version="1.0.0",
        columns=(
            "Vendor reference", "Seller legal name", "Registered vendor country",
            "Commodity family", "Catalogue item", "Maker shown for the device",
            "Manufacturer ordering code", "IC body / case", "Device revision",
            "Stock condition", "Money denomination", "Quoted rate",
            "Quantity covered by that rate", "Rate unit", "Physical supply form",
            "Contents of one sales pack", "Permitted quantity step",
            "Minimum commitment - number", "Minimum commitment - unit",
            "Freight treatment", "Freight amount", "Ancillary charge rule",
            "Ancillary charge amount", "Tax treatment", "Promised duration",
            "Days mean", "Promise ends when", "Duration starts on", "Settlement",
            "Offer issued", "Offer remains good through",
        ),
    ),
    "v3_supplier_b": CsvSourceProfile(
        profile_id="v3_supplier_b",
        version="1.0.0",
        columns=(
            "Expiry of offer", "Dated", "Quoted by", "Supplier code",
            "Vendor country of registration", "Sourcing class", "Article",
            "Mfr. part reference", "Device brand / maker", "Silicon rev.",
            "Case style", "Material status", "Line rate", "Currency code",
            "Pricing UOM", "The line rate covers", "Packing method", "Tray contents",
            "Ordering restriction", "Minimum order UOM", "Minimum order count",
            "Logistics charge treatment", "Separate freight amount", "Extras policy",
            "Extras total", "Indirect tax", "Lead-time milestone",
            "Time to that milestone", "Counting convention", "Count begins", "Terms",
        ),
    ),
    "v3_supplier_c": CsvSourceProfile(
        profile_id="v3_supplier_c",
        version="1.0.0",
        columns=(
            "Company issuing this offer", "Company country", "Account vendor ID",
            "Commodity", "Manufactured by", "Product description", "Original maker P/N",
            "Device package", "Supply condition", "Part revision",
            "All quoted money is in", "Price applies to", "Price for that basis",
            "Basis unit", "Sales presentation", "Units in each sales package",
            "Order step", "Smallest accepted quantity",
            "Smallest quantity measured in", "One-time delivery charge",
            "Delivery charge status", "One-time handling amount", "Handling status",
            "Tax basis", "Service clock starts", "Committed transit duration",
            "Days are counted as", "Completion event", "Payment arrangement",
            "Offer expiration date", "Quotation date",
        ),
    ),
    "v3_supplier_d": CsvSourceProfile(
        profile_id="v3_supplier_d",
        version="1.0.0",
        columns=(
            "Seller number", "Offeror", "Offeror registered in", "Quoted article",
            "Product family", "Maker's part number", "Maker", "Hardware revision",
            "Component case", "Goods offered", "Each quoted price covers",
            "Price basis UOM", "Price for the stated basis", "Price currency",
            "Physical pack", "Exact fill of each tray", "Accepted ordering step",
            "MOQ measure", "MOQ count", "Delivery fee", "Delivery fee rule",
            "Handling and admin", "Separate other-fee amount",
            "Tax treatment for this offer", "Arrival commitment",
            "The five-day point means", "Day definition", "Lead-time origin", "Payment",
            "Prepared on", "Valid up to and including",
        ),
    ),
    "v3_supplier_e": CsvSourceProfile(
        profile_id="v3_supplier_e",
        version="1.0.0",
        columns=(
            "Buying category", "Quoted goods", "Vendor", "Vendor master code",
            "Vendor registration country", "Brand owner / manufacturer",
            "Manufacturer P/N", "Body package", "Device version", "Item state",
            "Each-price amount", "Each price covers", "Each-price unit",
            "Quote denominated in", "Supply packaging", "Count per package",
            "Order granularity", "Minimum unit", "Minimum quantity", "Freight status",
            "Freight number", "Other charges", "Other-charge amount", "Tax handling",
            "Delivery duration", "Duration basis", "Delivery means",
            "Duration begins on", "Price held through", "Credit term", "Issued date",
        ),
    ),
}

REGISTERED_CSV_PROFILES = {**V2_CSV_PROFILES, **V3_CSV_PROFILES}


def _normalize(raw_value: str, definition: QuoteFieldDefinition) -> str | int:
    value = raw_value.strip()
    if definition.value_type.startswith("integer"):
        try:
            return int(value)
        except ValueError as exc:
            raise ContractError(
                "csv_integer_invalid",
                f"{definition.field_name} must be an integer",
                field_name=definition.field_name,
                raw_value=raw_value,
            ) from exc
    return value


def _candidate_unit(field_name: str, row: dict[str, str]) -> str | None:
    if field_name in {"unit_price", "shipping_fee_amount", "other_fees_amount"}:
        return (row.get("currency") or "").strip() or None
    if field_name in {"price_basis_quantity", "order_multiple_units", "units_per_pack"}:
        return "piece"
    if field_name == "moq_quantity":
        return (row.get("moq_unit") or "").strip() or None
    if field_name == "lead_time_days":
        return (row.get("day_basis") or "").strip() or None
    return None


class FixedCsvQuoteParser:
    def __init__(self, dictionary: QuoteDictionary, limits: FileLimits | None = None) -> None:
        self.dictionary = dictionary
        self.limits = limits or FileLimits()

    def parse_row(self, path: str | Path, context: DocumentContext, row_number: int) -> ExtractionBatch:
        if row_number < 2:
            raise ContractError("csv_row_invalid", "CSV data row numbers start at 2", row_number=row_number)
        csv_path, size, file_hash = validate_regular_file(path, self.limits)
        require_csv_shape(csv_path)
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_columns = tuple(reader.fieldnames or ())
                if actual_columns != FROZEN_CSV_COLUMNS:
                    raise ContractError(
                        "csv_header_mismatch",
                        "CSV header does not match the frozen Week 1 template",
                        expected=list(FROZEN_CSV_COLUMNS),
                        actual=list(actual_columns),
                    )
                selected: dict[str, str] | None = None
                for current_row_number, row in enumerate(reader, start=2):
                    if current_row_number == row_number:
                        selected = {key: value or "" for key, value in row.items() if key is not None}
                        break
        except UnicodeDecodeError as exc:
            raise UnreadableInputError("csv_not_utf8", "CSV input must be UTF-8", path=str(csv_path)) from exc
        except csv.Error as exc:
            raise UnreadableInputError("csv_parse_failed", "CSV input is malformed", path=str(csv_path)) from exc

        if selected is None:
            raise ContractError("csv_row_missing", "requested CSV row does not exist", row_number=row_number)
        self._validate_authority_columns(selected, context, row_number)

        sources: list[EvidenceSource] = []
        candidates: list[QuoteFieldCandidate] = []
        for definition in self.dictionary.extractable_fields:
            raw_value = selected.get(definition.field_name, "").strip()
            field_id = stable_id(
                "fld",
                {
                    "quote_id": context.quote_id,
                    "quote_version": context.quote_version,
                    "field_name": definition.field_name,
                    "field_version": 1,
                },
            )
            if not raw_value:
                candidates.append(
                    QuoteFieldCandidate(
                        field_id=field_id,
                        quote_id=context.quote_id,
                        quote_version=context.quote_version,
                        field_name=definition.field_name,
                        validation_status=ValidationStatus.MISSING,
                        origin=None,
                        producer=CandidateProducer.DETERMINISTIC_PARSER,
                    )
                )
                continue

            source_id = stable_id(
                "src",
                {
                    "kind": SourceKind.CSV_CELL,
                    "document_id": context.document_id,
                    "document_version": context.document_version,
                    "sha256": file_hash,
                    "row": row_number,
                    "column": definition.field_name,
                    "raw_text": raw_value,
                },
            )
            source = EvidenceSource(
                source_id=source_id,
                kind=SourceKind.CSV_CELL,
                document_id=context.document_id,
                document_version=context.document_version,
                document_sha256=file_hash,
                parser_version=PARSER_VERSION,
                raw_text=raw_value,
                row_number=row_number,
                column_name=definition.field_name,
            )
            sources.append(source)
            candidates.append(
                QuoteFieldCandidate(
                    field_id=field_id,
                    quote_id=context.quote_id,
                    quote_version=context.quote_version,
                    field_name=definition.field_name,
                    raw_value=raw_value,
                    normalized_value=_normalize(raw_value, definition),
                    unit=_candidate_unit(definition.field_name, selected),
                    validation_status=ValidationStatus.EXTRACTED,
                    origin=Origin.DOCUMENT,
                    source_refs=(SourceCitation(source_id=source_id, quoted_text=raw_value),),
                    producer=CandidateProducer.DETERMINISTIC_PARSER,
                )
            )

        parsed = ParsedInput(
            context=context,
            original_filename=csv_path.name,
            media_type="text/csv",
            file_size_bytes=size,
            document_sha256=file_hash,
            parser_version=PARSER_VERSION,
            sources=tuple(sources),
        )
        return ExtractionBatch(
            dictionary_version=self.dictionary.version,
            parsed_input=parsed,
            candidates=tuple(candidates),
        )

    @staticmethod
    def _validate_authority_columns(row: dict[str, str], context: DocumentContext, row_number: int) -> None:
        expected = {
            "quote_id": context.quote_id,
            "quote_version": str(context.quote_version),
            "document_id": context.document_id,
        }
        if context.scenario_id is not None:
            expected["scenario_id"] = context.scenario_id
        if context.supplier_id is not None:
            expected["supplier_id"] = context.supplier_id
        mismatches = {
            key: {"expected": value, "actual": (row.get(key) or "").strip()}
            for key, value in expected.items()
            if (row.get(key) or "").strip() != value
        }
        if mismatches:
            raise ContractError(
                "csv_authority_mismatch",
                "CSV identity/version does not match authority-owned context",
                row_number=row_number,
                mismatches=mismatches,
            )


class ProfiledCsvQuoteParser:
    """Parse a known heterogeneous CSV row into sources for the model adapter."""

    def __init__(
        self,
        profiles: Mapping[str, CsvSourceProfile] | None = None,
        limits: FileLimits | None = None,
    ) -> None:
        self.profiles = dict(REGISTERED_CSV_PROFILES if profiles is None else profiles)
        self.limits = limits or FileLimits()

    def parse_row(
        self,
        path: str | Path,
        context: DocumentContext,
        row_number: int,
        *,
        profile_id: str,
    ) -> ParsedInput:
        if row_number < 2:
            raise ContractError("csv_row_invalid", "CSV data row numbers start at 2", row_number=row_number)
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ContractError(
                "csv_profile_unknown",
                "CSV profile must be explicitly selected from the registered profiles",
                profile_id=profile_id,
                available_profiles=sorted(self.profiles),
            )

        csv_path, size, file_hash = validate_regular_file(path, self.limits)
        require_csv_shape(csv_path)
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                actual_columns = tuple(reader.fieldnames or ())
                if actual_columns != profile.columns:
                    counts = Counter(actual_columns)
                    raise ContractError(
                        "csv_profile_header_mismatch",
                        "CSV header does not match the selected versioned profile",
                        profile_id=profile.profile_id,
                        profile_version=profile.version,
                        expected=list(profile.columns),
                        actual=list(actual_columns),
                        duplicate_headers=sorted(
                            name for name, count in counts.items() if count > 1
                        ),
                        missing_headers=sorted(set(profile.columns) - set(actual_columns)),
                        unexpected_headers=sorted(set(actual_columns) - set(profile.columns)),
                    )
                selected: dict[str, str] | None = None
                for current_row_number, row in enumerate(reader, start=2):
                    if current_row_number != row_number:
                        continue
                    if row.get(None):
                        raise ContractError(
                            "csv_row_shape_invalid",
                            "CSV row contains more cells than the selected profile",
                            row_number=row_number,
                            profile_id=profile.profile_id,
                        )
                    selected = {column: row.get(column) or "" for column in profile.columns}
                    break
        except UnicodeDecodeError as exc:
            raise UnreadableInputError("csv_not_utf8", "CSV input must be UTF-8", path=str(csv_path)) from exc
        except csv.Error as exc:
            raise UnreadableInputError("csv_parse_failed", "CSV input is malformed", path=str(csv_path)) from exc

        if selected is None:
            raise ContractError("csv_row_missing", "requested CSV row does not exist", row_number=row_number)

        sources: list[EvidenceSource] = []
        for column_name in profile.columns:
            raw_value = selected[column_name].strip()
            if not raw_value:
                continue
            source_id = stable_id(
                "src",
                {
                    "kind": SourceKind.CSV_CELL,
                    "document_id": context.document_id,
                    "document_version": context.document_version,
                    "sha256": file_hash,
                    "parser_version": profile.parser_version,
                    "row": row_number,
                    "column": column_name,
                    "raw_text": raw_value,
                },
            )
            sources.append(
                EvidenceSource(
                    source_id=source_id,
                    kind=SourceKind.CSV_CELL,
                    document_id=context.document_id,
                    document_version=context.document_version,
                    document_sha256=file_hash,
                    parser_version=profile.parser_version,
                    raw_text=raw_value,
                    row_number=row_number,
                    column_name=column_name,
                )
            )

        if not sources:
            raise ContractError(
                "csv_row_empty",
                "selected CSV row does not contain any non-empty cells",
                row_number=row_number,
                profile_id=profile.profile_id,
            )

        return ParsedInput(
            context=context,
            original_filename=csv_path.name,
            media_type="text/csv",
            file_size_bytes=size,
            document_sha256=file_hash,
            parser_version=profile.parser_version,
            sources=tuple(sources),
        )
