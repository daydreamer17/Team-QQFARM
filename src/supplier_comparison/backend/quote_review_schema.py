"""Versioned UI contract for human review of extracted quote fields.

The CSV data dictionary remains the source of truth for field ownership and
required levels.  This module only adds presentation metadata and the
cross-field groups that are enforced by deterministic backend review.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.review_contracts import REVIEW_POLICY_VERSION


QUOTE_REVIEW_SCHEMA_VERSION = "quote-review-schema/1.0.0"

GROUP_ORDER = (
    ("身份", "identity"),
    ("规格", "specification"),
    ("价格", "price"),
    ("包装", "packaging"),
    ("MOQ", "moq"),
    ("费用", "fees"),
    ("交期", "delivery"),
    ("商务", "commercial"),
)

GROUP_IDS = dict(GROUP_ORDER)

INTEGER_FIELDS = {
    "price_basis_quantity": 1,
    "units_per_pack": 1,
    "order_multiple_units": 1,
    "moq_quantity": 1,
    "lead_time_days": 0,
}
MONEY_FIELDS = {"unit_price", "shipping_fee_amount", "other_fees_amount"}
DATE_FIELDS = {"quote_date", "valid_until"}

RELATION_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "group_id": "price_basis",
        "kind": "ALL_OR_NONE",
        "field_names": ["unit_price", "price_basis_quantity", "price_basis_unit"],
        "message": "单价、计价数量和计价单位必须同时有效。",
    },
    {
        "group_id": "money_currency",
        "kind": "MONEY_CURRENCY",
        "field_names": [
            "currency",
            "unit_price",
            "shipping_fee_amount",
            "other_fees_amount",
        ],
        "message": "所有金额字段的币种必须与报价币种一致。",
    },
    {
        "group_id": "shipping_fee",
        "kind": "FEE_STATUS_AMOUNT",
        "field_names": ["shipping_fee_status", "shipping_fee_amount"],
        "message": "运费状态与运费金额不一致。",
    },
    {
        "group_id": "other_fees",
        "kind": "FEE_STATUS_AMOUNT",
        "field_names": ["other_fees_status", "other_fees_amount"],
        "message": "其他费用状态与金额不一致。",
    },
    {
        "group_id": "moq_packaging",
        "kind": "MOQ_PACKAGING",
        "field_names": [
            "packaging_type",
            "units_per_pack",
            "order_multiple_units",
            "moq_quantity",
            "moq_unit",
        ],
        "message": "MOQ 单位与包装方式或每包数量不一致。",
    },
    {
        "group_id": "relative_delivery",
        "kind": "ALL_OR_NONE",
        "field_names": [
            "lead_time_days",
            "day_basis",
            "delivery_semantics",
            "start_event",
        ],
        "message": "相对交期的天数、日历口径、交付语义和起算事件必须完整。",
    },
    {
        "group_id": "quote_validity",
        "kind": "DATE_ORDER",
        "field_names": ["quote_date", "valid_until"],
        "message": "报价日期不能晚于有效截止日。",
    },
)


def _editor(field_name: str, value_type: str, allowed: tuple[str, ...] | None) -> str:
    if allowed:
        return "select"
    if field_name in MONEY_FIELDS:
        return "decimal"
    if field_name in INTEGER_FIELDS:
        return "integer"
    if field_name in DATE_FIELDS or value_type.startswith("date"):
        return "date"
    return "text"


def build_quote_field_schema(
    dictionary: QuoteDictionary,
    dictionary_path: str | Path,
) -> dict[str, Any]:
    """Return the cacheable public schema for the 30 human-reviewable fields."""

    path = Path(dictionary_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    fields: list[dict[str, Any]] = []
    for definition in dictionary.extractable_fields:
        allowed = definition.allowed_normalized_values
        fields.append(
            {
                "field_name": definition.field_name,
                "label": definition.meaning,
                "group_id": GROUP_IDS.get(definition.field_group, definition.field_group),
                "group_label": definition.field_group,
                "value_type": definition.value_type,
                "editor": _editor(definition.field_name, definition.value_type, allowed),
                "required_level": definition.required_level,
                "nullable": "/blank" in definition.value_type,
                "allowed_values": list(allowed) if allowed is not None else None,
                "minimum": INTEGER_FIELDS.get(definition.field_name, 0 if definition.field_name in MONEY_FIELDS else None),
                "unit_kind": (
                    "currency"
                    if definition.field_name in MONEY_FIELDS
                    else "none"
                ),
                "missing_handling": definition.missing_handling,
                "normalization_rule": definition.normalization_rule,
                "validation_boundary": definition.validation_boundary,
                "evidence_requirement": definition.evidence_requirement,
            }
        )
    return {
        "schema_version": QUOTE_REVIEW_SCHEMA_VERSION,
        "dictionary_version": dictionary.version,
        "dictionary_sha256": digest,
        "review_policy_version": REVIEW_POLICY_VERSION,
        "groups": [
            {
                "group_id": group_id,
                "label": label,
                "field_names": [
                    field["field_name"]
                    for field in fields
                    if field["group_id"] == group_id
                ],
            }
            for label, group_id in GROUP_ORDER
        ],
        "fields": fields,
        "relation_groups": list(RELATION_GROUPS),
    }
