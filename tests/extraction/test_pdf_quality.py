from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.contracts import (
    CoordinateSpace,
    ExtractionBatch,
    PageRoute,
    ParsedInput,
)
from supplier_comparison.extraction.errors import (
    ContractError,
    UnreadableInputError,
    UnsupportedInputError,
)
from supplier_comparison.extraction.pdf_parser import PARSER_VERSION, PdfQuoteParser
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig
from supplier_comparison.extraction.service import extract_quote_candidates

from .conftest import REPO_ROOT, context_for, quote_path


_RELIABLE_TEXT = (
    "Supplier Quote Unit Price SGD 6.42 per piece Part QW-MCU9-DEMO "
    "Quantity 1000 Delivery 10 business days Payment net 30 "
)


def _pdf_text(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 12 Tf 36 740 Td ({escaped}) Tj ET\n".encode("ascii")


def _write_structural_pdf(path: Path, page_kinds: tuple[str, ...]) -> None:
    """Write a tiny deterministic PDF without adding a test-only PDF dependency."""

    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    image_stream = b"80>"
    objects.append(
        b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /ASCIIHexDecode "
        + f"/Length {len(image_stream)} >>\nstream\n".encode("ascii")
        + image_stream
        + b"\nendstream"
    )

    page_object_numbers: list[int] = []
    for page_number, kind in enumerate(page_kinds, start=1):
        if kind == "native":
            content = _pdf_text(_RELIABLE_TEXT)
        elif kind == "image":
            content = b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
        elif kind == "hybrid":
            content = _pdf_text(_RELIABLE_TEXT) + b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
        elif kind == "watermark":
            content = _pdf_text(f"Page {page_number}") + b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
        elif kind == "gibberish":
            content = _pdf_text("#" * 100)
        elif kind == "blank":
            content = b""
        else:  # pragma: no cover - test helper misuse
            raise ValueError(f"unsupported test page kind: {kind}")

        page_object_number = len(objects) + 1
        content_object_number = page_object_number + 1
        page_object_numbers.append(page_object_number)
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> "
                "/XObject << /Im0 4 0 R >> >> "
                f"/Contents {content_object_number} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii")
            + content
            + b"endstream"
        )

    kids = " ".join(f"{number} 0 R" for number in page_object_numbers)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Count {len(page_kinds)} /Kids [{kids}] >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_number} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(output)


def test_page_quality_defaults_are_frozen_and_ocr_is_off() -> None:
    config = PdfQualityConfig()

    assert config.min_native_chars == 80
    assert config.min_printable_ratio == 0.90
    assert config.min_alnum_ratio == 0.50
    assert config.primary_image_area_ratio == 0.50
    assert config.ocr_enabled is False


@pytest.mark.parametrize(
    ("name", "value", "code"),
    (
        ("SUPPLIER_PDF_OCR_ENABLED", "perhaps", "pdf_config_invalid_boolean"),
        ("SUPPLIER_PDF_MIN_NATIVE_CHARS", "many", "pdf_config_invalid_integer"),
        ("SUPPLIER_PDF_MIN_ALNUM_RATIO", "high", "pdf_config_invalid_number"),
    ),
)
def test_invalid_quality_environment_is_explicit(monkeypatch, name, value, code) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ContractError) as raised:
        PdfQualityConfig.from_env()

    assert raised.value.code == code


def test_native_pdf_has_one_stable_analysis_per_actual_page() -> None:
    parser = PdfQuoteParser()
    first = parser.parse(quote_path("a"), context_for("a"))
    second = parser.parse(quote_path("a"), context_for("a"))

    assert first.parser_version == PARSER_VERSION == "pdfplumber-ocr/3.0.0"
    assert first.parser_fingerprint == second.parser_fingerprint
    assert first.parser_fingerprint is not None and len(first.parser_fingerprint) == 64
    assert [item.page_number for item in first.page_analyses] == [1]
    assert [item.route for item in first.page_analyses] == [PageRoute.NATIVE_TEXT]
    assert all(source.coordinate_space == CoordinateSpace.PDF_POINTS for source in first.sources)


def test_new_pdf_batch_is_emitted_as_schema_1_1(quote_dictionary) -> None:
    parsed = PdfQuoteParser().parse(quote_path("a"), context_for("a"))
    payload = {
        "candidates": [
            {
                "field_name": definition.field_name,
                "raw_value": None,
                "normalized_value": None,
                "unit": None,
                "validation_status": "MISSING",
                "source_refs": [],
            }
            for definition in quote_dictionary.extractable_fields
        ]
    }

    batch = extract_quote_candidates(
        parsed,
        quote_dictionary,
        FixedOutputAdapter({parsed.context.document_id: payload}),
        ModelCallBudget(graph_run_id="GRAPH-STAGE1-SCHEMA"),
        "EXTRACT-STAGE1-SCHEMA",
    )

    assert batch.schema_version == "1.1"
    assert batch.parsed_input.page_analyses[0].route == PageRoute.NATIVE_TEXT


def test_image_only_pdf_is_never_silently_treated_as_missing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scan.pdf"
    _write_structural_pdf(path, ("image",))

    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser(quality_config=PdfQualityConfig(ocr_enabled=False)).parse(
            path,
            context_for("a"),
        )

    assert raised.value.code == "pdf_page_requires_ocr"
    assert raised.value.details["page_numbers"] == [1]
    assert raised.value.details["ocr_enabled"] is False
    assert raised.value.details["ocr_implementation_available"] is True
    assert [item["route"] for item in raised.value.details["page_analyses"]] == ["OCR"]


def test_mixed_pdf_reports_every_page_and_only_flags_the_scan_page(tmp_path: Path) -> None:
    path = tmp_path / "mixed.pdf"
    _write_structural_pdf(path, ("native", "image"))

    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))

    analyses = raised.value.details["page_analyses"]
    assert raised.value.code == "pdf_page_requires_ocr"
    assert raised.value.details["page_numbers"] == [2]
    assert [item["page_number"] for item in analyses] == [1, 2]
    assert [item["route"] for item in analyses] == ["NATIVE_TEXT", "OCR"]


def test_reliable_text_over_a_primary_image_routes_to_hybrid(tmp_path: Path) -> None:
    path = tmp_path / "hybrid.pdf"
    _write_structural_pdf(path, ("hybrid",))

    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))

    analysis = raised.value.details["page_analyses"][0]
    assert raised.value.code == "pdf_page_requires_ocr"
    assert analysis["route"] == "HYBRID"
    assert float(analysis["image_area_ratio"]) == 1.0


def test_page_number_watermark_does_not_disguise_scan_page(tmp_path: Path) -> None:
    path = tmp_path / "watermark.pdf"
    _write_structural_pdf(path, ("watermark",))

    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))

    analysis = raised.value.details["page_analyses"][0]
    assert analysis["route"] == "OCR"
    assert 0 < analysis["native_char_count"] < 80
    assert "PRIMARY_PAGE_IMAGE_PRESENT" in analysis["quality_reasons"]


def test_gibberish_text_layer_requires_manual_review(tmp_path: Path) -> None:
    path = tmp_path / "gibberish.pdf"
    _write_structural_pdf(path, ("gibberish",))

    with pytest.raises(UnreadableInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))

    analysis = raised.value.details["page_analyses"][0]
    assert raised.value.code == "pdf_text_quality_insufficient"
    assert analysis["route"] == "MANUAL_REQUIRED"
    assert float(analysis["alnum_ratio"]) == 0.0


def test_blank_page_has_an_explicit_manual_route(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    _write_structural_pdf(path, ("blank",))

    with pytest.raises(UnreadableInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))

    assert raised.value.code == "blank_pdf"
    assert raised.value.details["page_numbers"] == [1]
    assert raised.value.details["page_analyses"][0]["route"] == "MANUAL_REQUIRED"


def test_page_analysis_contract_rejects_duplicate_or_missing_page_numbers() -> None:
    parsed = PdfQuoteParser().parse(quote_path("a"), context_for("a"))
    payload = parsed.model_dump(mode="json")
    payload["page_analyses"] = [payload["page_analyses"][0], payload["page_analyses"][0]]

    with pytest.raises(ValidationError, match="unique, ordered, and contiguous"):
        ParsedInput.model_validate(payload)


@pytest.mark.parametrize(
    "example_name",
    ("extraction_batch_v1_0_example.json", "extraction_batch_v1_1_proposed.json"),
)
def test_schema_migration_examples_are_readable(example_name: str) -> None:
    path = REPO_ROOT / "docs" / "v7" / "stage0" / example_name
    payload = json.loads(path.read_text(encoding="utf-8"))

    batch = ExtractionBatch.model_validate(payload)

    assert batch.schema_version in {"1.0", "1.1"}
