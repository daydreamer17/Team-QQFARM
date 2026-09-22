"""Deterministic fee-pair and versioned unit-price rules.

The helpers in this module are deliberately independent from the review
envelope.  They provide a single truth table that can be reused by draft
review, formal submission, and tests without making an LLM authoritative for
money or version selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from enum import StrEnum
from typing import Iterable

from .contracts import EvidenceContextPurpose, ParsedInput


class FeeStatus(StrEnum):
    KNOWN_AMOUNT = "KNOWN_AMOUNT"
    FREE = "FREE"
    INCLUDED = "INCLUDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class FeeAmountRule(StrEnum):
    REQUIRED_NON_NEGATIVE = "REQUIRED_NON_NEGATIVE"
    NULL_OR_ZERO = "NULL_OR_ZERO"
    MUST_BE_NULL = "MUST_BE_NULL"


FEE_AMOUNT_RULE_BY_STATUS = {
    FeeStatus.KNOWN_AMOUNT: FeeAmountRule.REQUIRED_NON_NEGATIVE,
    FeeStatus.FREE: FeeAmountRule.NULL_OR_ZERO,
    FeeStatus.NOT_APPLICABLE: FeeAmountRule.NULL_OR_ZERO,
    FeeStatus.INCLUDED: FeeAmountRule.MUST_BE_NULL,
    FeeStatus.UNKNOWN: FeeAmountRule.MUST_BE_NULL,
}


@dataclass(frozen=True, slots=True)
class FeePairValidation:
    valid: bool
    code: str | None
    message: str | None
    status: FeeStatus | None
    amount: Decimal | None


def validate_fee_status_amount(
    status: str | FeeStatus | None,
    amount: object | None,
) -> FeePairValidation:
    """Validate one fee status/amount pair using the frozen truth table.

    Amounts intentionally accept only decimal strings.  This preserves the
    repository invariant that money does not cross JSON/API boundaries as a
    binary float.  ``None`` means no separately stated amount; an empty string
    is an invalid value rather than an alias for null.
    """

    try:
        normalized_status = FeeStatus(status) if status is not None else None
    except ValueError:
        normalized_status = None
    if normalized_status is None:
        return FeePairValidation(
            valid=False,
            code="FEE_STATUS_INVALID",
            message="Fee status must use a supported status value.",
            status=None,
            amount=None,
        )

    rule = FEE_AMOUNT_RULE_BY_STATUS[normalized_status]
    if rule == FeeAmountRule.MUST_BE_NULL:
        if amount is None:
            return _valid_fee_pair(normalized_status, None)
        return FeePairValidation(
            valid=False,
            code="FEE_AMOUNT_MUST_BE_NULL",
            message=f"{normalized_status.value} cannot have a separate fee amount.",
            status=normalized_status,
            amount=None,
        )

    if amount is None:
        if rule == FeeAmountRule.NULL_OR_ZERO:
            return _valid_fee_pair(normalized_status, None)
        return FeePairValidation(
            valid=False,
            code="FEE_AMOUNT_REQUIRED",
            message="KNOWN_AMOUNT requires a non-negative fee amount.",
            status=normalized_status,
            amount=None,
        )

    parsed_amount = _parse_money_string(amount)
    if parsed_amount is None:
        return FeePairValidation(
            valid=False,
            code="FEE_AMOUNT_INVALID",
            message="Fee amount must be a finite non-negative decimal string.",
            status=normalized_status,
            amount=None,
        )

    if rule == FeeAmountRule.NULL_OR_ZERO and parsed_amount != 0:
        return FeePairValidation(
            valid=False,
            code="FEE_AMOUNT_MUST_BE_ZERO_OR_NULL",
            message=f"{normalized_status.value} requires a zero or empty fee amount.",
            status=normalized_status,
            amount=parsed_amount,
        )
    return _valid_fee_pair(normalized_status, parsed_amount)


def _valid_fee_pair(status: FeeStatus, amount: Decimal | None) -> FeePairValidation:
    return FeePairValidation(
        valid=True,
        code=None,
        message=None,
        status=status,
        amount=amount,
    )


def _parse_money_string(value: object) -> Decimal | None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is None
    ):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


class UnitPriceVersionStatus(StrEnum):
    CURRENT = "CURRENT"
    SUPERSEDED = "SUPERSEDED"
    UNVERSIONED = "UNVERSIONED"


@dataclass(frozen=True, slots=True)
class UnitPriceObservation:
    amount: Decimal
    version_status: UnitPriceVersionStatus
    source_ids: tuple[str, ...]
    currency: str | None = None
    basis_quantity: Decimal | None = None
    basis_unit: str | None = None

    def __post_init__(self) -> None:
        if not self.amount.is_finite() or self.amount < 0:
            raise ValueError("unit-price observation must be finite and non-negative")
        if not self.source_ids:
            raise ValueError("unit-price observation must retain at least one source ID")
        if self.basis_quantity is not None and (not self.basis_quantity.is_finite() or self.basis_quantity <= 0):
            raise ValueError("price basis must be positive and finite")


@dataclass(frozen=True, slots=True)
class UnitPriceSelection:
    selected_value: Decimal | None
    selected_source_ids: tuple[str, ...]
    observations: tuple[UnitPriceObservation, ...]
    audit_observations: tuple[UnitPriceObservation, ...]
    conflict_code: str | None = None

    @property
    def has_conflict(self) -> bool:
        return self.conflict_code is not None

    def matches(self, amount: Decimal | None, currency: str | None, quantity: Decimal | None, unit: str | None) -> bool:
        """Match an extracted price without changing its stated monetary basis."""
        if amount is None:
            return False
        status = (UnitPriceVersionStatus.CURRENT if any(item.version_status == UnitPriceVersionStatus.CURRENT for item in self.observations)
                  else UnitPriceVersionStatus.UNVERSIONED)
        chosen = [item for item in self.observations if item.version_status == status
                  and any(s in self.selected_source_ids for s in item.source_ids)]
        for item in chosen:
            if item.currency and currency and item.currency != currency.upper():
                continue
            if item.basis_quantity is not None and item.basis_unit and quantity is not None and unit:
                if quantity > 0 and _price_key(item) == (item.currency, _price_unit(unit), Fraction(amount) / Fraction(quantity)):
                    return True
            elif item.amount == amount:
                return True
        return False


def select_current_unit_price(
    observations: Iterable[UnitPriceObservation],
) -> UnitPriceSelection:
    """Select the authoritative price while retaining superseded audit facts.

    An explicit unique ``CURRENT`` value wins.  Repeated evidence for the same
    value is not a conflict.  Without an explicit current value, one unique
    unversioned value may be used; multiple distinct unversioned values are
    ambiguous.  A document containing only superseded prices has no current
    price and therefore conflicts.
    """

    items = tuple(observations)
    audit = tuple(
        item
        for item in items
        if item.version_status == UnitPriceVersionStatus.SUPERSEDED
    )
    current = tuple(
        item for item in items if item.version_status == UnitPriceVersionStatus.CURRENT
    )
    current_values = _distinct_amounts(current)
    if len(current_values) > 1:
        return UnitPriceSelection(
            selected_value=None,
            selected_source_ids=(),
            observations=items,
            audit_observations=audit,
            conflict_code="MULTIPLE_CURRENT_UNIT_PRICES",
        )
    if len(current_values) == 1:
        selected = current[0].amount
        return UnitPriceSelection(
            selected_value=selected,
            selected_source_ids=tuple(dict.fromkeys(s for item in current for s in item.source_ids)),
            observations=items,
            audit_observations=audit,
        )

    unversioned = tuple(
        item
        for item in items
        if item.version_status == UnitPriceVersionStatus.UNVERSIONED
    )
    unversioned_values = _distinct_amounts(unversioned)
    if len(unversioned_values) > 1:
        return UnitPriceSelection(
            selected_value=None,
            selected_source_ids=(),
            observations=items,
            audit_observations=audit,
            conflict_code="MULTIPLE_UNVERSIONED_UNIT_PRICES",
        )
    if len(unversioned_values) == 1:
        selected = unversioned[0].amount
        return UnitPriceSelection(
            selected_value=selected,
            selected_source_ids=tuple(dict.fromkeys(s for item in unversioned for s in item.source_ids)),
            observations=items,
            audit_observations=audit,
        )
    if audit:
        return UnitPriceSelection(
            selected_value=None,
            selected_source_ids=(),
            observations=items,
            audit_observations=audit,
            conflict_code="CURRENT_UNIT_PRICE_NOT_IDENTIFIED",
        )
    return UnitPriceSelection(
        selected_value=None,
        selected_source_ids=(),
        observations=items,
        audit_observations=(),
    )


def _price_unit(unit: str) -> str:
    unit = unit.lower().replace("(s)", "s")
    return "piece" if unit in {"piece", "pieces", "pc", "pcs", "ea", "each"} else unit


def _price_key(item: UnitPriceObservation) -> tuple:
    if item.basis_quantity is not None and item.basis_unit:
        return (item.currency, _price_unit(item.basis_unit), Fraction(item.amount) / Fraction(item.basis_quantity))
    return (item.currency, item.basis_unit, item.basis_quantity, item.amount)


def _distinct_amounts(observations: Iterable[UnitPriceObservation]) -> tuple:
    return tuple(dict.fromkeys(_price_key(item) for item in observations))


def _price_metadata(value: str) -> dict:
    currency = re.search(r"(?:S\$|SGD|USD|EUR|GBP|MYR)\s*[0-9]", value, re.IGNORECASE)
    code = re.match(r"S\$|[A-Z]+", currency.group(), re.IGNORECASE).group().upper() if currency else None
    basis = re.search(r"(?:\bper\b|/)\s*(?:(\d+(?:\.\d+)?)\s*)?([A-Za-z]+(?:\(s\))?)", value, re.IGNORECASE)
    quantity = Decimal(basis.group(1) or "1") if basis else None
    return {"currency": "SGD" if code == "S$" else code,
            "basis_quantity": quantity if quantity is not None and quantity > 0 else None,
            "basis_unit": _price_unit(basis.group(2)) if basis else None}


UNIT_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:current\s+)?unit\s+(?:price|rate)\b|"
    r"\bquoted\s+price\b|\bprice\s+for\s+stated\s+basis\b",
    re.IGNORECASE,
)
NON_PRODUCT_PRICE_LABEL_PATTERN = re.compile(
    r"\b(?:freight|shipping|delivery|handling|tax|gst|vat|goods\s+total|"
    r"landed\s+total|native\s+total|confirmed\s+total)\b",
    re.IGNORECASE,
)
STATUS_LABEL_PATTERN = re.compile(r"^status$", re.IGNORECASE)
CURRENT_PATTERN = re.compile(r"\bCURRENT\b", re.IGNORECASE)
SUPERSEDED_PATTERN = re.compile(r"\bSUPERSEDED\b", re.IGNORECASE)
CURRENCY_AMOUNT_PATTERN = re.compile(
    r"(?:S\$|SGD|USD|EUR|GBP|MYR)\s*([0-9][0-9,]*(?:\.[0-9]{1,4})?)",
    re.IGNORECASE,
)


def extract_document_unit_price_observations(
    parsed_input: ParsedInput,
) -> tuple[UnitPriceObservation, ...]:
    """Extract auditable unit-price observations from PDF layout groups.

    The parser's ``TABLE_ROW`` groups preserve header/data column alignment, so
    version-table statuses can be tied to the price in the same row.  Remaining
    ``FIELD_AND_VALUE`` groups provide ordinary unversioned price evidence.
    """

    source_by_id = {source.source_id: source for source in parsed_input.sources}
    observations: list[UnitPriceObservation] = []
    consumed_price_source_ids: set[str] = set()

    for group in parsed_input.context_groups:
        if group.purpose != EvidenceContextPurpose.TABLE_ROW:
            continue
        sources = [source_by_id[source_id] for source_id in group.source_ids]
        headers = {
            source.column_index: source
            for source in sources
            if source.row_index == 0 and source.column_index is not None
        }
        data = {
            source.column_index: source
            for source in sources
            if source.row_index not in {None, 0} and source.column_index is not None
        }
        price_columns = [
            column
            for column, source in headers.items()
            if _is_unit_price_label(source.raw_text)
        ]
        if len(price_columns) != 1:
            continue
        price_source = data.get(price_columns[0])
        if price_source is None:
            continue
        amount = _single_currency_amount(price_source.raw_text)
        if amount is None:
            continue

        status = _status_from_sources(headers, data, price_columns[0])
        status_sources = [
            source
            for source in data.values()
            if CURRENT_PATTERN.search(source.raw_text)
            or SUPERSEDED_PATTERN.search(source.raw_text)
        ]
        observations.append(
            UnitPriceObservation(
                amount=amount,
                version_status=status,
                **_price_metadata(price_source.raw_text + " " + headers[price_columns[0]].raw_text),
                source_ids=tuple(
                    dict.fromkeys(
                        [headers[price_columns[0]].source_id, price_source.source_id]
                        + [source.source_id for source in status_sources]
                    )
                ),
            )
        )
        consumed_price_source_ids.add(price_source.source_id)

    for group in parsed_input.context_groups:
        if group.purpose != EvidenceContextPurpose.FIELD_AND_VALUE:
            continue
        sources = [source_by_id[source_id] for source_id in group.source_ids]
        labels = [source for source in sources if _is_unit_price_label(source.raw_text)]
        if not labels:
            continue
        values = [
            (source, _single_currency_amount(source.raw_text))
            for source in sources
            if source.source_id not in consumed_price_source_ids
        ]
        values = [(source, amount) for source, amount in values if amount is not None]
        if len(values) != 1:
            continue
        value_source, amount = values[0]
        combined_text = " ".join(source.raw_text for source in sources)
        observations.append(
            UnitPriceObservation(
                amount=amount,
                version_status=_status_from_text(combined_text),
                **_price_metadata(value_source.raw_text + " " + " ".join(s.raw_text for s in labels)),
                source_ids=tuple(
                    dict.fromkeys(
                        [source.source_id for source in labels] + [value_source.source_id]
                    )
                ),
            )
        )
        consumed_price_source_ids.add(value_source.source_id)

    # Inline prose is also evidence: a second current price need not be in a
    # table or a key/value pair. Keep its source for the ordinary conflict gate.
    for source in parsed_input.sources:
        if source.source_id in consumed_price_source_ids or not _is_unit_price_label(source.raw_text):
            continue
        amount = _single_currency_amount(source.raw_text)
        if amount is not None:
            observations.append(UnitPriceObservation(
                amount=amount,
                version_status=_status_from_text(source.raw_text),
                source_ids=(source.source_id,),
                **_price_metadata(source.raw_text),
            ))
    return tuple(observations)


def select_document_unit_price(parsed_input: ParsedInput) -> UnitPriceSelection:
    return select_current_unit_price(
        extract_document_unit_price_observations(parsed_input)
    )


def _is_unit_price_label(value: str) -> bool:
    return bool(
        UNIT_PRICE_LABEL_PATTERN.search(value)
        and not NON_PRODUCT_PRICE_LABEL_PATTERN.search(value)
    )


def _single_currency_amount(value: str) -> Decimal | None:
    matches = CURRENCY_AMOUNT_PATTERN.findall(value)
    if len(matches) != 1:
        return None
    try:
        amount = Decimal(matches[0].replace(",", ""))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _status_from_sources(
    headers: dict[int, object],
    data: dict[int, object],
    price_column: int,
) -> UnitPriceVersionStatus:
    for column, header in headers.items():
        raw_text = getattr(header, "raw_text", "")
        if not STATUS_LABEL_PATTERN.fullmatch(raw_text.strip()):
            continue
        status_source = data.get(column)
        if status_source is not None:
            return _status_from_text(getattr(status_source, "raw_text", ""))
    price_header = headers[price_column]
    return _status_from_text(getattr(price_header, "raw_text", ""))


def _status_from_text(value: str) -> UnitPriceVersionStatus:
    if SUPERSEDED_PATTERN.search(value):
        return UnitPriceVersionStatus.SUPERSEDED
    if CURRENT_PATTERN.search(value):
        return UnitPriceVersionStatus.CURRENT
    return UnitPriceVersionStatus.UNVERSIONED
