from __future__ import annotations

from datetime import datetime, timezone

import pytest

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    ExtractionBatch,
    Origin,
    QuoteFieldCandidate,
    SourceCitation,
    ValidationStatus,
)
from supplier_comparison.extraction.criticality import (
    ALWAYS_CRITICAL_FIELDS,
    NON_CRITICAL_FIELDS,
    CriticalityContext,
)
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.corrections import apply_candidate_correction
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.review import (
    model_failed_envelope,
    review_extraction_batch,
)
from supplier_comparison.extraction.human_review import create_review_event
from supplier_comparison.extraction.review_contracts import (
    CorrectionAction,
    HumanReviewAction,
    ReviewReason,
    ReviewStatus,
)
from supplier_comparison.extraction.contracts import AdapterEnvironment

from .conftest import FIXTURE_ROOT, context_for, quotes_csv_path


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
CRITICALITY_CONTEXT = CriticalityContext(required_revision="R1", base_unit="piece")


def _batch(quote_dictionary, alias: str = "a", row_number: int = 2) -> ExtractionBatch:
    return FixedCsvQuoteParser(quote_dictionary).parse_row(
        quotes_csv_path(), context_for(alias), row_number
    )


def _replace_candidate(
    batch: ExtractionBatch,
    field_name: str,
    **updates: object,
) -> ExtractionBatch:
    candidates = []
    for candidate in batch.candidates:
        if candidate.field_name != field_name:
            candidates.append(candidate)
            continue
        values = candidate.model_dump(mode="python")
        values.update(updates)
        candidates.append(QuoteFieldCandidate.model_validate(values))
    values = batch.model_dump(mode="python")
    values["candidates"] = tuple(candidates)
    return ExtractionBatch.model_validate(values)


def _review(batch: ExtractionBatch, quote_dictionary):
    return review_extraction_batch(
        batch,
        quote_dictionary,
        CRITICALITY_CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
    )


def test_all_applicable_critical_fields_pass_for_supplier_a(quote_dictionary) -> None:
    envelope = _review(_batch(quote_dictionary), quote_dictionary)

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True
    assert envelope.calculation_inputs_complete is True
    assert envelope.checks is not None and envelope.checks.critical_values_complete is True
    payload = envelope.model_dump(mode="json")
    assert ExtractionBatch.model_validate(payload["batch"]).dictionary_version == "1.2.0"


@pytest.mark.parametrize("field_name", sorted(ALWAYS_CRITICAL_FIELDS))
def test_each_always_critical_missing_field_requires_review(
    quote_dictionary, field_name: str
) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        field_name,
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == field_name and "CRITICAL_FIELD_MISSING" in finding.codes
        for finding in envelope.review.findings
    )


def test_supplier_b_missing_shipping_requires_review(quote_dictionary) -> None:
    envelope = _review(_batch(quote_dictionary, "b", 3), quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.downstream_ready is False
    assert envelope.calculation_inputs_complete is False
    assert envelope.review is not None
    shipping_findings = [
        finding
        for finding in envelope.review.findings
        if finding.field_name == "shipping_fee_status"
    ]
    assert any(
        finding.review_reason == ReviewReason.MISSING_REQUIRED_INFO
        and "CRITICAL_FIELD_MISSING" in finding.codes
        for finding in shipping_findings
    )


@pytest.mark.parametrize("field_name", sorted(NON_CRITICAL_FIELDS))
def test_noncritical_missing_does_not_block_supplier_a(
    quote_dictionary, field_name: str
) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        field_name,
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True
    assert envelope.calculation_inputs_complete is True


def test_known_shipping_amount_becomes_conditionally_required(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary, "c", 4),
        "shipping_fee_amount",
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "shipping_fee_amount"
        and "CRITICAL_FIELD_MISSING" in finding.codes
        for finding in envelope.review.findings
    )


def test_relative_delivery_group_is_conditionally_required(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "day_basis",
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "day_basis" and "CRITICAL_FIELD_MISSING" in finding.codes
        for finding in envelope.review.findings
    )


def test_quote_date_is_required_for_relative_validity(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "quote_date",
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CriticalityContext(
            required_revision="R1",
            base_unit="piece",
            relative_validity_requires_quote_date=True,
        ),
        input_is_synthetic=True,
        reviewed_at=NOW,
    )

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert "quote_date" in envelope.review.blocking_fields


def test_invalid_money_is_intercepted_for_human_review(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "unit_price",
        raw_value="not-money",
        normalized_value="not-money",
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "unit_price" and "MONEY_VALUE_INVALID" in finding.codes
        for finding in envelope.review.findings
    )


def test_ascii_integer_string_is_accepted_for_existing_c_contract(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "order_multiple_units",
        raw_value="100",
        normalized_value="100",
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM


def test_money_unit_may_be_omitted_when_currency_is_separate(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "unit_price",
        unit=None,
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM


def test_cross_file_source_reference_rejects_the_batch(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "supplier_name",
        source_refs=(
            SourceCitation(source_id="src_other_document", quoted_text="Redwood Components"),
        ),
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.downstream_ready is False
    assert envelope.review is not None
    assert any("SOURCE_REF_UNKNOWN" in finding.codes for finding in envelope.review.findings)


def test_candidate_from_another_quote_version_is_rejected(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "supplier_name",
        quote_id="QUOTE-OTHER",
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.review is not None
    assert any("CANDIDATE_AUTHORITY_MISMATCH" in finding.codes for finding in envelope.review.findings)


def test_candidate_set_mismatch_is_rejected(quote_dictionary) -> None:
    batch = _batch(quote_dictionary)
    values = batch.model_dump(mode="python")
    values["candidates"] = tuple(batch.candidates[:-1])
    incomplete = ExtractionBatch.model_validate(values)
    envelope = _review(incomplete, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.checks is not None
    assert envelope.checks.candidate_set_complete is False


def test_dictionary_version_mismatch_is_rejected(quote_dictionary) -> None:
    batch = _batch(quote_dictionary).model_copy(update={"dictionary_version": "0.0.0"})
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.review is not None
    assert any("DICTIONARY_VERSION_MISMATCH" in finding.codes for finding in envelope.review.findings)


def test_model_cannot_self_verify_at_review_boundary(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "supplier_name",
        producer=CandidateProducer.MODEL_ADAPTER,
        adapter_version="test-adapter/1.0",
        prompt_version="test-prompt/1.0",
        validation_status=ValidationStatus.VERIFIED,
        origin=Origin.DOCUMENT,
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.review is not None
    assert any("MODEL_SELF_VERIFIED" in finding.codes for finding in envelope.review.findings)


def test_human_correction_without_audit_event_is_rejected(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "shipping_fee_status",
        producer=CandidateProducer.MODEL_ADAPTER,
        adapter_version="test-adapter/1.0",
        prompt_version="test-prompt/1.0",
        validation_status=ValidationStatus.VERIFIED,
        origin=Origin.USER_CORRECTION,
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REJECTED
    assert envelope.review is not None
    codes = {code for finding in envelope.review.findings for code in finding.codes}
    assert "MODEL_SELF_VERIFIED" not in codes
    assert "CORRECTION_AUDIT_MISSING" in codes


def test_not_applicable_fee_with_nonzero_amount_is_blocked(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "other_fees_amount",
        raw_value="10.00",
        normalized_value="10.00",
    )
    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        "FEE_STATUS_AMOUNT_CONFLICT" in finding.codes
        for finding in envelope.review.findings
    )


def test_unknown_critical_fee_status_requires_human_review(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "shipping_fee_status",
        raw_value="Unknown",
        normalized_value="UNKNOWN",
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        "FEE_STATUS_UNKNOWN" in finding.codes
        for finding in envelope.review.findings
    )


def test_confirmed_missing_amount_resolves_explicit_unknown_fee_status(
    quote_dictionary,
) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "shipping_fee_status",
        raw_value="Unknown - confirm after order review",
        normalized_value="UNKNOWN",
    )
    batch = _replace_candidate(
        batch,
        "shipping_fee_amount",
        raw_value=None,
        normalized_value=None,
        unit=None,
        validation_status=ValidationStatus.MISSING,
        origin=None,
        source_refs=(),
    )
    event = create_review_event(
        batch,
        field_name="shipping_fee_amount",
        action=HumanReviewAction.CONFIRM_MISSING,
        reviewer_id="reviewer-1",
        reviewed_at=NOW,
    )

    envelope = review_extraction_batch(
        batch,
        quote_dictionary,
        CRITICALITY_CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        review_events=(event,),
    )

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True
    assert envelope.calculation_inputs_complete is False
    assert envelope.review is not None
    assert any(
        finding.field_name == "shipping_fee_status"
        and "HUMAN_CONFIRMED_FEE_UNKNOWN" in finding.codes
        and finding.resolved
        for finding in envelope.review.findings
    )


def test_derived_each_price_is_blocked_when_quote_states_a_basis_price(
    quote_dictionary,
) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "unit_price",
        normalized_value="6.37",
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        "NORMALIZED_PRICE_NOT_IN_EVIDENCE" in finding.codes
        for finding in envelope.review.findings
    )


def test_equivalent_price_format_is_accepted(quote_dictionary) -> None:
    batch = _batch(quote_dictionary)
    unit_price = next(
        candidate for candidate in batch.candidates if candidate.field_name == 'unit_price'
    )
    cited_ids = set(unit_price.source_refs)
    parsed = batch.parsed_input.model_copy(
        update={
            'sources': tuple(
                source.model_copy(update={'raw_text': 'S$640 per tray'})
                if source.source_id in cited_ids
                else source
                for source in batch.parsed_input.sources
            )
        }
    )
    envelope = _review(batch.model_copy(update={'parsed_input': parsed}), quote_dictionary)

    assert envelope.review is not None
    assert not any(
        finding.field_name == 'unit_price'
        and 'NORMALIZED_PRICE_NOT_IN_EVIDENCE' in finding.codes
        for finding in envelope.review.findings
    )


def test_competing_start_events_in_document_require_review(quote_dictionary) -> None:
    batch = _batch(quote_dictionary)
    source = batch.parsed_input.sources[0].model_copy(
        update={
            "source_id": "src_competing_start_event",
            "raw_text": "A separate note says timing begins after cleared funds.",
        }
    )
    parsed = batch.parsed_input.model_copy(
        update={"sources": (*batch.parsed_input.sources, source)}
    )
    batch = batch.model_copy(update={"parsed_input": parsed})

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        "START_EVENT_DOCUMENT_CONFLICT" in finding.codes
        for finding in envelope.review.findings
    )


def test_non_order_date_start_event_requires_review(quote_dictionary) -> None:
    batch = _replace_candidate(
        _batch(quote_dictionary),
        "start_event",
        raw_value="When cleared payment is received",
        normalized_value="PAYMENT_RECEIPT",
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.downstream_ready is False
    assert envelope.review is not None
    assert any(
        "START_EVENT_UNSUPPORTED" in finding.codes
        for finding in envelope.review.findings
        if finding.field_name == "start_event"
    )


def test_supplier_name_containing_context_supplier_id_requires_review(
    quote_dictionary,
) -> None:
    batch = _batch(quote_dictionary)
    batch = _replace_candidate(
        batch,
        "supplier_name",
        raw_value=(
            f"Example Components ({batch.parsed_input.context.supplier_id}), Singapore"
        ),
        normalized_value=(
            f"Example Components ({batch.parsed_input.context.supplier_id})"
        ),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.downstream_ready is False
    assert envelope.review is not None
    assert any(
        "SUPPLIER_NAME_CONTAINS_SUPPLIER_ID" in finding.codes
        for finding in envelope.review.findings
        if finding.field_name == "supplier_name"
    )


def test_single_character_supplier_slot_does_not_trigger_name_review(
    quote_dictionary,
) -> None:
    batch = _batch(quote_dictionary)
    context = batch.parsed_input.context.model_copy(update={"supplier_id": "A"})
    parsed_input = batch.parsed_input.model_copy(update={"context": context})
    batch = batch.model_copy(update={"parsed_input": parsed_input})
    batch = _replace_candidate(
        batch,
        "supplier_name",
        raw_value="Synthetic Meridian Supplier Ltd.",
        normalized_value="Synthetic Meridian Supplier Ltd.",
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review is not None
    assert not any(
        "SUPPLIER_NAME_CONTAINS_SUPPLIER_ID" in finding.codes
        for finding in envelope.review.findings
        if finding.field_name == "supplier_name"
    )


def test_document_absence_statement_cannot_prove_fee_value(quote_dictionary) -> None:
    batch = _batch(quote_dictionary)
    shipping = next(
        candidate
        for candidate in batch.candidates
        if candidate.field_name == "shipping_fee_status"
    )
    source_id = shipping.source_refs[0].source_id
    sources = tuple(
        source.model_copy(
            update={
                "raw_text": (
                    source.raw_text
                    + " No freight or shipping statement appears in this quotation."
                )
            }
        )
        if source.source_id == source_id
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={
            "parsed_input": batch.parsed_input.model_copy(update={"sources": sources})
        }
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        "DOCUMENT_ABSENCE_MISREAD_AS_FEE_VALUE" in finding.codes
        for finding in envelope.review.findings
    )


def test_tax_evidence_cannot_prove_other_fee_status(quote_dictionary) -> None:
    batch = _batch(quote_dictionary)
    candidate = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    source_id = candidate.source_refs[0].source_id
    tax_text = "Tax is not applicable to this synthetic test quote"
    sources = tuple(
        source.model_copy(update={"raw_text": tax_text, "column_name": "tax_mode"})
        if source.source_id == source_id
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={
            "parsed_input": batch.parsed_input.model_copy(update={"sources": sources})
        }
    )
    batch = _replace_candidate(
        batch,
        "other_fees_status",
        raw_value=tax_text,
        normalized_value="NOT_APPLICABLE",
        source_refs=(SourceCitation(source_id=source_id, quoted_text=tax_text),),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review is not None
    assert any(
        finding.field_name == "other_fees_status"
        and "SOURCE_SEMANTIC_MISMATCH" in finding.codes
        for finding in envelope.review.findings
    )


@pytest.mark.parametrize(
    "evidence",
    [
        "Handling and admin Included in the quoted line rate",
        "Handling SGD 75.00",
        "SGD 75.00 Handling",
        "Ancillary charges NOT APPLICABLE",
        "Extras policy AMOUNT",
        "Administration fee status AMOUNT",
        "Freight is SGD 40.00; no other mandatory fees apply.",
        "Other mandatory fees None",
    ],
)
def test_supported_other_fee_alias_can_prove_other_fee_status(
    quote_dictionary,
    evidence: str,
) -> None:
    batch = _batch(quote_dictionary)
    candidate = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    source_id = candidate.source_refs[0].source_id
    sources = tuple(
        source.model_copy(update={"raw_text": evidence})
        if source.source_id == source_id
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={
            "parsed_input": batch.parsed_input.model_copy(update={"sources": sources})
        }
    )
    batch = _replace_candidate(
        batch,
        "other_fees_status",
        raw_value=evidence,
        normalized_value="INCLUDED",
        source_refs=(SourceCitation(source_id=source_id, quoted_text=evidence),),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review is not None
    assert not any(
        finding.field_name == "other_fees_status"
        and "SOURCE_SEMANTIC_MISMATCH" in finding.codes
        for finding in envelope.review.findings
    )


@pytest.mark.parametrize(
    "evidence",
    [
        "Freight is SGD 40.00; no other mandatory fees apply.",
        "Other mandatory fees None",
    ],
)
def test_explicit_no_other_fees_requires_not_applicable_status(
    quote_dictionary,
    evidence: str,
) -> None:
    batch = _batch(quote_dictionary)
    status = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    source_id = status.source_refs[0].source_id
    sources = tuple(
        source.model_copy(update={"raw_text": evidence})
        if source.source_id == source_id
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={"parsed_input": batch.parsed_input.model_copy(update={"sources": sources})}
    )
    batch = _replace_candidate(
        batch,
        "other_fees_status",
        raw_value=evidence,
        normalized_value="KNOWN_AMOUNT",
        source_refs=(SourceCitation(source_id=source_id, quoted_text=evidence),),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "other_fees_status"
        and "OTHER_FEES_ABSENCE_STATUS_CONFLICT" in finding.codes
        for finding in envelope.review.findings
    )


def test_explicit_no_other_fees_accepts_not_applicable_and_zero(
    quote_dictionary,
) -> None:
    evidence = "Freight is SGD 40.00; no other mandatory fees apply."
    batch = _batch(quote_dictionary)
    status = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    amount = next(
        item for item in batch.candidates if item.field_name == "other_fees_amount"
    )
    source_ids = {status.source_refs[0].source_id, amount.source_refs[0].source_id}
    sources = tuple(
        source.model_copy(update={"raw_text": evidence})
        if source.source_id in source_ids
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={"parsed_input": batch.parsed_input.model_copy(update={"sources": sources})}
    )
    batch = _replace_candidate(
        batch,
        "other_fees_status",
        raw_value=evidence,
        normalized_value="NOT_APPLICABLE",
        source_refs=(SourceCitation(source_id=status.source_refs[0].source_id, quoted_text=evidence),),
    )
    batch = _replace_candidate(
        batch,
        "other_fees_amount",
        raw_value="0.00",
        normalized_value="0.00",
        source_refs=(SourceCitation(source_id=amount.source_refs[0].source_id, quoted_text=evidence),),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review is not None
    blocked_codes = {
        code
        for finding in envelope.review.findings
        if finding.field_name in {"other_fees_status", "other_fees_amount"}
        and finding.severity.value == "BLOCKING"
        for code in finding.codes
    }
    assert not blocked_codes


def test_human_correction_resolves_mandatory_other_fee_evidence_blocker(
    quote_dictionary,
) -> None:
    evidence = "Freight is SGD 40.00; no other mandatory fees apply."
    batch = _batch(quote_dictionary)
    original_status = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    original_amount = next(
        item for item in batch.candidates if item.field_name == "other_fees_amount"
    )
    source_ids = {
        original_status.source_refs[0].source_id,
        original_amount.source_refs[0].source_id,
    }
    sources = tuple(
        source.model_copy(update={"raw_text": evidence})
        if source.source_id in source_ids
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={"parsed_input": batch.parsed_input.model_copy(update={"sources": sources})}
    )
    batch = _replace_candidate(
        batch,
        "other_fees_status",
        raw_value="KNOWN_AMOUNT",
        normalized_value="KNOWN_AMOUNT",
        source_refs=(
            SourceCitation(
                source_id=original_status.source_refs[0].source_id,
                quoted_text=evidence,
            ),
        ),
    )
    batch = _replace_candidate(
        batch,
        "other_fees_amount",
        raw_value="0.00",
        normalized_value="0.00",
        source_refs=(
            SourceCitation(
                source_id=original_amount.source_refs[0].source_id,
                quoted_text=evidence,
            ),
        ),
    )

    with_status, status_event = apply_candidate_correction(
        batch,
        field_name="other_fees_status",
        action=CorrectionAction.USER_CORRECTION,
        raw_value="No other mandatory fees apply",
        normalized_value="NOT_APPLICABLE",
        unit=None,
        reason_code="QUOTE_DRAFT_FIELD_CORRECTION",
        reason="Buyer confirmed the explicit no-other-fees statement.",
        reviewer_id="test-user",
        reviewed_at=NOW,
    )
    corrected, amount_event = apply_candidate_correction(
        with_status,
        field_name="other_fees_amount",
        action=CorrectionAction.USER_CORRECTION,
        raw_value="0.00",
        normalized_value="0.00",
        unit="SGD",
        reason_code="QUOTE_DRAFT_FIELD_CORRECTION",
        reason="No other mandatory fees means a zero other-fee amount.",
        reviewer_id="test-user",
        reviewed_at=NOW,
    )

    envelope = review_extraction_batch(
        corrected,
        quote_dictionary,
        CRITICALITY_CONTEXT,
        input_is_synthetic=True,
        reviewed_at=NOW,
        corrections=(status_event, amount_event),
    )

    assert envelope.review_status == ReviewStatus.READY_FOR_DOWNSTREAM
    assert envelope.downstream_ready is True


def test_other_fee_evidence_for_shipping_is_preserved_but_requires_review(
    quote_dictionary,
) -> None:
    batch = _batch(quote_dictionary)
    other_fee = next(
        item for item in batch.candidates if item.field_name == "other_fees_status"
    )
    citation = other_fee.source_refs[0]
    batch = _replace_candidate(
        batch,
        "shipping_fee_status",
        raw_value=citation.quoted_text,
        normalized_value="NOT_APPLICABLE",
        unit=None,
        validation_status=ValidationStatus.EXTRACTED,
        origin=Origin.DOCUMENT,
        source_refs=(citation,),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "shipping_fee_status"
        and "SOURCE_SEMANTIC_MISMATCH" in finding.codes
        for finding in envelope.review.findings
    )


def test_order_increment_evidence_for_price_basis_requires_review(
    quote_dictionary,
) -> None:
    batch = _batch(quote_dictionary)
    order_multiple = next(
        item for item in batch.candidates if item.field_name == "order_multiple_units"
    )
    source_id = order_multiple.source_refs[0].source_id
    sources = tuple(
        source.model_copy(
            update={"raw_text": source.raw_text + " ORDER INCREMENT 100 pieces"}
        )
        if source.source_id == source_id
        else source
        for source in batch.parsed_input.sources
    )
    batch = batch.model_copy(
        update={
            "parsed_input": batch.parsed_input.model_copy(update={"sources": sources})
        }
    )
    citation = SourceCitation(
        source_id=source_id,
        quoted_text="ORDER INCREMENT 100 pieces",
    )
    batch = _replace_candidate(
        batch,
        "price_basis_quantity",
        raw_value=citation.quoted_text,
        normalized_value="100",
        unit="piece",
        validation_status=ValidationStatus.EXTRACTED,
        origin=Origin.DOCUMENT,
        source_refs=(citation,),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review_status == ReviewStatus.REVIEW_REQUIRED
    assert envelope.review is not None
    assert any(
        finding.field_name == "price_basis_quantity"
        and "SOURCE_SEMANTIC_MISMATCH" in finding.codes
        for finding in envelope.review.findings
    )


def test_distinct_document_unit_prices_block_single_selected_value(
    quote_dictionary,
) -> None:
    parsed = PdfQuoteParser().parse(
        FIXTURE_ROOT / "pdf-layout/review_gate_quote.pdf",
        context_for("a", version=7),
    )
    price_source = next(
        source for source in parsed.sources if "S$ 5.81" in source.raw_text
    )
    candidates = []
    for definition in quote_dictionary.extractable_fields:
        if definition.field_name == "unit_price":
            candidates.append(
                QuoteFieldCandidate(
                    field_id="fld-unit-price-conflict-test",
                    quote_id=parsed.context.quote_id,
                    quote_version=parsed.context.quote_version,
                    field_name="unit_price",
                    raw_value=price_source.raw_text,
                    normalized_value="5.81",
                    unit="SGD",
                    validation_status=ValidationStatus.EXTRACTED,
                    origin=Origin.DOCUMENT,
                    source_refs=(
                        SourceCitation(
                            source_id=price_source.source_id,
                            quoted_text=price_source.raw_text,
                        ),
                    ),
                    producer=CandidateProducer.MODEL_ADAPTER,
                    adapter_version="test-adapter/1.0",
                    prompt_version="test-prompt/1.0",
                )
            )
        else:
            candidates.append(
                QuoteFieldCandidate(
                    field_id=f"fld-{definition.field_name}",
                    quote_id=parsed.context.quote_id,
                    quote_version=parsed.context.quote_version,
                    field_name=definition.field_name,
                    validation_status=ValidationStatus.MISSING,
                    producer=CandidateProducer.MODEL_ADAPTER,
                    adapter_version="test-adapter/1.0",
                    prompt_version="test-prompt/1.0",
                )
            )
    batch = ExtractionBatch(
        schema_version="1.1",
        dictionary_version=quote_dictionary.version,
        parsed_input=parsed,
        candidates=tuple(candidates),
    )

    envelope = _review(batch, quote_dictionary)

    assert envelope.review is not None
    assert any(
        finding.field_name == "unit_price"
        and "CRITICAL_FIELD_CONFLICT" in finding.codes
        for finding in envelope.review.findings
    )


@pytest.mark.parametrize("use_correction", [False, True])
def test_human_can_adopt_same_price_despite_automatic_evidence_doubt(quote_dictionary, use_correction):
    batch = _replace_candidate(_batch(quote_dictionary), "unit_price", normalized_value="6.88")
    before = _review(batch, quote_dictionary)
    assert any("NORMALIZED_PRICE_NOT_IN_EVIDENCE" in finding.codes for finding in before.review.findings)
    events, corrections = (), ()
    if use_correction:
        batch, correction = apply_candidate_correction(
            batch, field_name="unit_price", action=CorrectionAction.USER_CORRECTION,
            raw_value="6.88", normalized_value="6.88", unit="SGD",
            reason_code="HUMAN_SELECTED_VALUE", reason="Buyer checked the effective quote.",
            reviewer_id="test-user", reviewed_at=NOW,
        )
        corrections = (correction,)
    else:
        events = (create_review_event(batch, field_name="unit_price", action=HumanReviewAction.CONFIRM_VALUE,
                                      reviewer_id="test-user", reviewed_at=NOW),)
    after = review_extraction_batch(batch, quote_dictionary, CRITICALITY_CONTEXT,
        input_is_synthetic=True, reviewed_at=NOW, review_events=events, corrections=corrections)
    price_findings = [finding for finding in after.review.findings if finding.field_name == "unit_price"]
    assert price_findings
    assert all(finding.resolved for finding in price_findings)
    assert all(finding.resolution_event_id for finding in price_findings)
    assert "unit_price" not in after.submission_blocking_fields


def test_human_confirmation_cannot_accept_fabricated_source_quote(quote_dictionary):
    batch = _batch(quote_dictionary)
    price = next(item for item in batch.candidates if item.field_name == "unit_price")
    batch = _replace_candidate(batch, "unit_price", source_refs=(SourceCitation(
        source_id=price.source_refs[0].source_id, quoted_text="invented text outside the file"),))
    event = create_review_event(batch, field_name="unit_price", action=HumanReviewAction.CONFIRM_VALUE,
                                reviewer_id="test-user", reviewed_at=NOW)
    result = review_extraction_batch(batch, quote_dictionary, CRITICALITY_CONTEXT,
        input_is_synthetic=True, reviewed_at=NOW, review_events=(event,))
    assert result.review_status == ReviewStatus.REJECTED
    assert any("SOURCE_QUOTE_MISMATCH" in finding.codes and not finding.resolved for finding in result.review.findings)


def test_model_failure_is_not_disguised_as_missing_fields() -> None:
    envelope = model_failed_envelope(
        environment=AdapterEnvironment.LOCAL,
        input_is_synthetic=True,
        errors=("provider timeout after three attempts",),
    )

    assert envelope.review_status == ReviewStatus.MODEL_FAILED
    assert envelope.batch is None
    assert envelope.downstream_ready is False
    assert envelope.errors == ("provider timeout after three attempts",)
