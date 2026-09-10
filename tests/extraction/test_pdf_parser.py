from __future__ import annotations

import pytest

from supplier_comparison.extraction.contracts import SourceKind
from supplier_comparison.extraction.errors import InputLimitError, UnreadableInputError, UnsupportedInputError
from supplier_comparison.extraction.files import FileLimits
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser

from .conftest import DATA_ROOT, context_for


def test_three_development_pdfs_produce_stable_positioned_sources() -> None:
    parser = PdfQuoteParser()
    for alias in ("a", "b", "c"):
        path = DATA_ROOT / "generated" / "inputs" / "development" / f"supplier_{alias}_quote_v1.pdf"
        first = parser.parse(path, context_for(alias))
        second = parser.parse(path, context_for(alias))
        assert first.document_sha256 == second.document_sha256
        assert [source.source_id for source in first.sources] == [source.source_id for source in second.sources]
        assert first.sources
        assert all(source.kind == SourceKind.PDF_TEXT_BLOCK for source in first.sources)
        assert all(source.page_number == 1 for source in first.sources)
        assert all(source.block_id and source.bbox for source in first.sources)


def test_supplier_b_pdf_does_not_contain_shipping_amount() -> None:
    path = DATA_ROOT / "generated" / "inputs" / "development" / "supplier_b_quote_v1.pdf"
    parsed = PdfQuoteParser().parse(path, context_for("b"))
    full_text = " ".join(source.raw_text for source in parsed.sources).lower()
    assert "shipping" not in full_text
    assert "freight" not in full_text
    assert "200" not in full_text


def test_non_pdf_content_is_explicitly_rejected(tmp_path) -> None:
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("this is not a PDF", encoding="utf-8")
    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser().parse(fake_pdf, context_for("a"))
    assert raised.value.code == "unsupported_pdf"


def test_pdf_page_limit_is_enforced() -> None:
    path = DATA_ROOT / "generated" / "inputs" / "development" / "supplier_a_quote_v1.pdf"
    with pytest.raises(InputLimitError) as raised:
        PdfQuoteParser(FileLimits(max_pdf_pages=0)).parse(path, context_for("a"))
    assert raised.value.code == "pdf_page_limit_exceeded"


def test_pdf_without_extractable_text_requires_manual_fallback(monkeypatch) -> None:
    path = DATA_ROOT / "generated" / "inputs" / "development" / "supplier_a_quote_v1.pdf"
    monkeypatch.setattr("supplier_comparison.extraction.pdf_parser._extract_lines", lambda page: ())
    with pytest.raises(UnreadableInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))
    assert raised.value.code == "pdf_text_unavailable"
