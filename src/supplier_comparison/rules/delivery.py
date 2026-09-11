"""Deterministic arrival-deadline and quote-validity checks."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .contracts import DeliveryCheck, ProcurementRequirement, QuoteInput, RuleIssue
from .field_access import FieldAccess


SINGAPORE_TIMEZONE = ZoneInfo("Asia/Singapore")


def check_delivery(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
    *,
    evaluated_at: datetime,
) -> DeliveryCheck:
    """Check the supported calendar-day arrival path and quote validity."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    fields = FieldAccess.from_quote(quote)
    pending: list[RuleIssue] = []
    failed: list[RuleIssue] = []

    lead_raw = _required(fields, "lead_time_days", pending)
    day_basis_raw = _required(fields, "day_basis", pending)
    semantics_raw = _required(fields, "delivery_semantics", pending)
    start_event_raw = _required(fields, "start_event", pending)

    lead_days = _non_negative_integer(lead_raw, "lead_time_days", pending)
    day_basis = _string(day_basis_raw, "day_basis", pending)
    semantics = _string(semantics_raw, "delivery_semantics", pending)
    start_event = _string(start_event_raw, "start_event", pending)

    supported_relative_delivery = all(
        value is not None
        for value in (lead_days, day_basis, semantics, start_event)
    )
    if requirement.planned_order_date is None:
        pending.append(
            RuleIssue(
                code="PLANNED_ORDER_DATE_REQUIRED",
                fields=("planned_order_date",),
                message="Planned order date is required for relative lead time.",
            )
        )
        supported_relative_delivery = False
    if day_basis is not None and day_basis != "CALENDAR_DAYS":
        pending.append(
            RuleIssue(
                code="DAY_BASIS_UNSUPPORTED",
                fields=("day_basis",),
                message="MVP cannot infer arrival from non-calendar-day lead time.",
            )
        )
        supported_relative_delivery = False
    if semantics is not None and semantics != "ARRIVAL":
        pending.append(
            RuleIssue(
                code="SHIPMENT_IS_NOT_ARRIVAL",
                fields=("delivery_semantics",),
                message="Shipment timing cannot be compared directly with an arrival deadline.",
            )
        )
        supported_relative_delivery = False
    if start_event is not None and start_event != "ORDER_DATE":
        pending.append(
            RuleIssue(
                code="START_EVENT_UNSUPPORTED",
                fields=("start_event",),
                message="Lead time must explicitly start from the order date.",
            )
        )
        supported_relative_delivery = False
    estimated_arrival: date | None = None
    if supported_relative_delivery and requirement.planned_order_date is not None:
        estimated_arrival = requirement.planned_order_date + timedelta(days=lead_days)
        if estimated_arrival > requirement.delivery_deadline:
            failed.append(
                RuleIssue(
                    code="DELIVERY_DEADLINE_EXCEEDED",
                    fields=("lead_time_days", "delivery_deadline"),
                    message="Estimated arrival date is later than the required deadline.",
                )
            )

    valid_until_raw = _required(fields, "valid_until", pending)
    valid_until = _iso_date(valid_until_raw, "valid_until", pending)
    if valid_until is not None:
        evaluation_date = evaluated_at.astimezone(SINGAPORE_TIMEZONE).date()
        if evaluation_date > valid_until:
            failed.append(
                RuleIssue(
                    code="QUOTE_EXPIRED",
                    fields=("valid_until",),
                    message="Quote is expired at the evaluation time.",
                )
            )
        if (
            requirement.planned_order_date is not None
            and requirement.planned_order_date > valid_until
        ):
            failed.append(
                RuleIssue(
                    code="QUOTE_EXPIRES_BEFORE_ORDER",
                    fields=("valid_until", "planned_order_date"),
                    message="Quote expires before the planned order date.",
                )
            )

    return DeliveryCheck(
        estimated_arrival_date=estimated_arrival,
        failed_reasons=tuple(failed),
        pending_reasons=tuple(pending),
    )


def _required(
    fields: FieldAccess,
    field_name: str,
    issues: list[RuleIssue],
) -> object | None:
    value, issue = fields.require(field_name)
    if issue is not None:
        issues.append(issue)
    return value


def _non_negative_integer(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.isascii() and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        issues.append(
            RuleIssue(
                code="FIELD_NON_NEGATIVE_INTEGER_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must be a non-negative normalized integer.",
            )
        )
        return None
    return value


def _string(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        issues.append(
            RuleIssue(
                code="FIELD_STRING_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must be a non-empty normalized string.",
            )
        )
        return None
    return value


def _iso_date(
    value: object | None,
    field_name: str,
    issues: list[RuleIssue],
) -> date | None:
    text = _string(value, field_name, issues)
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        issues.append(
            RuleIssue(
                code="ISO_DATE_REQUIRED",
                fields=(field_name,),
                message=f"Field {field_name} must use YYYY-MM-DD format.",
            )
        )
        return None
