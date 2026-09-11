"""Deterministic product specification checks."""

from __future__ import annotations

from .contracts import (
    ProcurementRequirement,
    QuoteInput,
    RuleIssue,
    SpecificationCheck,
)
from .field_access import FieldAccess


EXACT_SPEC_FIELDS = (
    "manufacturer",
    "manufacturer_part_number",
    "package",
    "revision",
    "condition",
)
SUBSTITUTABLE_IDENTITY_FIELDS = frozenset(
    {"manufacturer", "manufacturer_part_number"}
)


def check_specification(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
) -> SpecificationCheck:
    """Compare normalized quote specifications with buyer-owned requirements."""

    fields = FieldAccess.from_quote(quote)
    pending: list[RuleIssue] = []
    failed: list[RuleIssue] = []
    mismatches: list[str] = []

    for field_name in EXACT_SPEC_FIELDS:
        quoted_value, issue = fields.require(field_name)
        if issue is not None:
            pending.append(issue)
            continue
        if not isinstance(quoted_value, str):
            pending.append(
                RuleIssue(
                    code="FIELD_STRING_REQUIRED",
                    fields=(field_name,),
                    message=f"Field {field_name} must be a normalized string.",
                )
            )
            continue

        required_value = getattr(requirement, field_name)
        if quoted_value != required_value:
            mismatches.append(field_name)

    identity_mismatches = [
        name for name in mismatches if name in SUBSTITUTABLE_IDENTITY_FIELDS
    ]
    if identity_mismatches:
        if requirement.allow_substitutes:
            pending.append(
                RuleIssue(
                    code="SUBSTITUTE_COMPATIBILITY_REVIEW_REQUIRED",
                    fields=tuple(identity_mismatches),
                    message=(
                        "Alternative manufacturer or part number requires explicit "
                        "compatibility review outside the current MVP."
                    ),
                )
            )
        else:
            failed.extend(_mismatch_issue(name) for name in identity_mismatches)

    failed.extend(
        _mismatch_issue(name)
        for name in mismatches
        if name not in SUBSTITUTABLE_IDENTITY_FIELDS
    )
    return SpecificationCheck(
        failed_reasons=tuple(failed),
        pending_reasons=tuple(pending),
    )


def _mismatch_issue(field_name: str) -> RuleIssue:
    return RuleIssue(
        code=f"{field_name.upper()}_MISMATCH",
        fields=(field_name,),
        message=f"Quoted {field_name} does not match the procurement requirement.",
    )
