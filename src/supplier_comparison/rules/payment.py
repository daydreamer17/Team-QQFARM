"""Deterministic normalization of the deliberately narrow Net-N MVP."""

from __future__ import annotations

import re

from supplier_comparison.extraction.contracts import ValidationStatus

from .contracts import PaymentParseStatus, PaymentTermEvaluation, QuoteInput
from .field_access import FieldAccess


NET_WORDS = {
    "zero": 0,
    "fifteen": 15,
    "thirty": 30,
    "forty-five": 45,
    "sixty": 60,
    "ninety": 90,
}
NET_PATTERN = re.compile(
    r"^\s*(?:N|NET\s*)(?P<days>\d{1,3}|zero|fifteen|thirty|forty[- ]five|sixty|ninety)"
    r"(?:\s+(?:AFTER|FROM)\s+(?P<event>INVOICE(?:\s+DATE)?|RECEIPT\s+OF\s+INVOICE))?\s*$",
    re.IGNORECASE,
)
COMPLEX_PATTERN = re.compile(
    r"\b(?:PREPAID|PREPAYMENT|ADVANCE|DEPOSIT|LETTER\s+OF\s+CREDIT|L/C|INSTALLMENT)\b|\d+\s*/\s*\d+",
    re.IGNORECASE,
)


def evaluate_payment_term(quote: QuoteInput) -> PaymentTermEvaluation:
    fields = FieldAccess.from_quote(quote)
    candidate = fields.candidate("payment_terms")
    start_candidate = fields.candidate("payment_start_event")
    if candidate is None or candidate.validation_status == ValidationStatus.MISSING:
        return PaymentTermEvaluation(
            parse_status=PaymentParseStatus.MISSING,
            reason_codes=("PAYMENT_TERMS_MISSING",),
        )
    refs = tuple(ref.source_id for ref in candidate.source_refs)
    raw = candidate.raw_value
    value = candidate.normalized_value
    if candidate.validation_status != ValidationStatus.VERIFIED or not isinstance(value, str):
        return PaymentTermEvaluation(
            raw_text=raw,
            normalized_text=value if isinstance(value, str) else None,
            parse_status=PaymentParseStatus.INCOMPARABLE,
            reason_codes=("PAYMENT_TERMS_NOT_VERIFIED",),
            evidence_refs=refs,
            validation_status=candidate.validation_status.value,
        )
    if COMPLEX_PATTERN.search(value):
        return PaymentTermEvaluation(
            raw_text=raw,
            normalized_text=value,
            parse_status=PaymentParseStatus.INCOMPARABLE,
            reason_codes=("PAYMENT_TERMS_COMPLEX",),
            evidence_refs=refs,
            validation_status=candidate.validation_status.value,
        )
    match = NET_PATTERN.fullmatch(value)
    if match is None:
        return PaymentTermEvaluation(
            raw_text=raw,
            normalized_text=value,
            parse_status=PaymentParseStatus.INCOMPARABLE,
            reason_codes=("PAYMENT_TERMS_UNSUPPORTED",),
            evidence_refs=refs,
            validation_status=candidate.validation_status.value,
        )
    raw_days = match.group("days").lower().replace(" ", "-")
    days = int(raw_days) if raw_days.isdigit() else NET_WORDS[raw_days]
    explicit_event = quote.payment_start_event_override
    if quote.payment_start_event_evidence_ref:
        refs = tuple(dict.fromkeys((*refs, quote.payment_start_event_evidence_ref)))
    if (
        start_candidate is not None
        and start_candidate.validation_status == ValidationStatus.VERIFIED
        and isinstance(start_candidate.normalized_value, str)
    ):
        explicit_event = start_candidate.normalized_value
        refs = tuple(dict.fromkeys((*refs, *(ref.source_id for ref in start_candidate.source_refs))))
    phrase_event = "INVOICE_DATE" if match.group("event") else None
    event = explicit_event or phrase_event
    if explicit_event and phrase_event and explicit_event != phrase_event:
        return PaymentTermEvaluation(
            raw_text=raw,
            normalized_text=f"Net {days}",
            payment_type="NET_DAYS",
            net_days=days,
            payment_start_event=explicit_event,
            parse_status=PaymentParseStatus.INCOMPARABLE,
            reason_codes=("PAYMENT_START_EVENT_CONFLICT",),
            evidence_refs=refs,
            validation_status=candidate.validation_status.value,
        )
    if event is None:
        return PaymentTermEvaluation(
            raw_text=raw,
            normalized_text=f"Net {days}",
            payment_type="NET_DAYS",
            net_days=days,
            parse_status=PaymentParseStatus.INCOMPARABLE,
            reason_codes=("PAYMENT_START_EVENT_MISSING",),
            evidence_refs=refs,
            validation_status=candidate.validation_status.value,
        )
    return PaymentTermEvaluation(
        raw_text=raw,
        normalized_text=f"Net {days}",
        payment_type="NET_DAYS",
        net_days=days,
        payment_start_event=event,
        parse_status=PaymentParseStatus.COMPARABLE,
        evidence_refs=refs,
        validation_status=candidate.validation_status.value,
    )
