from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from supplier_comparison.extraction.contracts import (
    CandidateProducer,
    ParsedInput,
    Origin,
    QuoteFieldCandidate,
    SourceCitation,
    ValidationStatus,
)
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.model_payload import ModelFieldCandidate
from supplier_comparison.extraction.model_payload import ModelExtractionPayload

from .conftest import context_for, quote_path


MODEL_CANDIDATE_ADAPTER = TypeAdapter(ModelFieldCandidate)


def test_a_dictionary_is_loaded_as_the_only_quote_field_list(quote_dictionary) -> None:
    assert quote_dictionary.version == "1.2.0"
    assert len(quote_dictionary.fields) == 31
    assert len(quote_dictionary.extractable_fields) == 30
    assert "supplier_id" not in {field.field_name for field in quote_dictionary.extractable_fields}
    assert "fees_complete" not in quote_dictionary.fields
    assert "start_date" not in quote_dictionary.fields
    assert quote_dictionary.fields["shipping_fee_status"].allowed_normalized_values == (
        "KNOWN_AMOUNT",
        "FREE",
        "INCLUDED",
        "NOT_APPLICABLE",
        "UNKNOWN",
    )
    assert quote_dictionary.fields["price_basis_unit"].allowed_normalized_values == ("piece",)


def test_missing_candidate_cannot_carry_a_value_or_fake_source() -> None:
    with pytest.raises(ValidationError, match="MISSING candidate"):
        QuoteFieldCandidate(
            field_id="FIELD-1",
            quote_id="QUOTE-1",
            quote_version=1,
            field_name="shipping_fee_status",
            raw_value="FREE",
            normalized_value="FREE",
            validation_status=ValidationStatus.MISSING,
            origin=None,
            source_refs=(SourceCitation(source_id="SRC-1", quoted_text="FREE"),),
            producer=CandidateProducer.DETERMINISTIC_PARSER,
        )


def test_missing_candidate_requires_null_origin() -> None:
    with pytest.raises(ValidationError, match="null origin"):
        QuoteFieldCandidate(
            field_id="FIELD-1",
            quote_id="QUOTE-1",
            quote_version=1,
            field_name="shipping_fee_amount",
            validation_status=ValidationStatus.MISSING,
            origin=Origin.DOCUMENT,
            producer=CandidateProducer.DETERMINISTIC_PARSER,
        )


def test_non_missing_candidate_requires_origin() -> None:
    with pytest.raises(ValidationError, match="requires origin"):
        QuoteFieldCandidate(
            field_id="FIELD-1",
            quote_id="QUOTE-1",
            quote_version=1,
            field_name="unit_price",
            raw_value="6.80",
            normalized_value="6.80",
            validation_status=ValidationStatus.EXTRACTED,
            origin=None,
            producer=CandidateProducer.DETERMINISTIC_PARSER,
        )


def test_model_cannot_self_verify_a_candidate() -> None:
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        MODEL_CANDIDATE_ADAPTER.validate_python(
            {
                "field_name": "currency",
                "raw_value": "SGD",
                "normalized_value": "SGD",
                "unit": None,
                "validation_status": ValidationStatus.VERIFIED,
                "source_refs": (SourceCitation(source_id="SRC-1", quoted_text="SGD"),),
            }
        )


def test_extracted_model_candidate_requires_normalized_value() -> None:
    with pytest.raises(ValidationError):
        MODEL_CANDIDATE_ADAPTER.validate_python(
            {
                "field_name": "currency",
                "raw_value": "S$",
                "normalized_value": None,
                "unit": None,
                "validation_status": ValidationStatus.EXTRACTED,
                "source_refs": (SourceCitation(source_id="SRC-1", quoted_text="S$"),),
            }
        )


def test_structured_output_schema_enforces_status_dependent_shapes() -> None:
    schema = ModelExtractionPayload.model_json_schema()
    item_schema = schema["properties"]["candidates"]["items"]
    assert item_schema["discriminator"]["propertyName"] == "validation_status"
    assert set(item_schema["discriminator"]["mapping"]) == {"EXTRACTED", "MISSING", "CONFLICT"}

    extracted = schema["$defs"]["ExtractedModelFieldCandidate"]
    assert set(extracted["required"]) == set(extracted["properties"])
    assert extracted["properties"]["validation_status"]["const"] == "EXTRACTED"
    assert {choice["type"] for choice in extracted["properties"]["normalized_value"]["anyOf"]} == {
        "string",
        "integer",
        "boolean",
    }
    assert extracted["properties"]["source_refs"]["minItems"] == 1

    missing = schema["$defs"]["MissingModelFieldCandidate"]
    assert missing["properties"]["unit"]["type"] == "null"
    assert missing["properties"]["normalized_value"]["type"] == "null"
    assert missing["properties"]["source_refs"]["maxItems"] == 0


def test_money_candidate_rejects_binary_float() -> None:
    with pytest.raises(ValidationError):
        MODEL_CANDIDATE_ADAPTER.validate_python(
            {
                "field_name": "unit_price",
                "raw_value": "6.80",
                "normalized_value": 6.8,
                "unit": None,
                "validation_status": ValidationStatus.EXTRACTED,
                "source_refs": (SourceCitation(source_id="SRC-1", quoted_text="6.80"),),
            }
        )


def test_parsed_input_rejects_source_from_wrong_document_version() -> None:
    parsed = PdfQuoteParser().parse(
        quote_path("a"),
        context_for("a"),
    )
    wrong_version_source = parsed.sources[0].model_copy(update={"document_version": 2})
    payload = parsed.model_dump()
    payload["sources"] = [wrong_version_source.model_dump(), *[source.model_dump() for source in parsed.sources[1:]]]
    with pytest.raises(ValidationError, match="another document/version"):
        ParsedInput.model_validate(payload)
