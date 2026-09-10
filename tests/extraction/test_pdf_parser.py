from __future__ import annotations

import pytest

from supplier_comparison.extraction.contracts import SourceKind
from supplier_comparison.extraction.errors import InputLimitError, UnreadableInputError, UnsupportedInputError
from supplier_comparison.extraction.files import FileLimits
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser

from .conftest import context_for, quote_path


def test_three_development_pdfs_produce_stable_positioned_sources() -> None:
    parser = PdfQuoteParser()
    for alias in ("a", "b", "c"):
        path = quote_path(alias)
        first = parser.parse(path, context_for(alias))
        second = parser.parse(path, context_for(alias))
        assert first.document_sha256 == second.document_sha256
        assert [source.source_id for source in first.sources] == [source.source_id for source in second.sources]
        assert first.sources
        assert all(source.kind == SourceKind.PDF_TEXT_BLOCK for source in first.sources)
        assert all(source.page_number == 1 for source in first.sources)
        assert all(source.block_id and source.bbox for source in first.sources)


@pytest.mark.parametrize("alias", ("a", "b", "c"))
def test_v2_development_pdfs_produce_stable_positioned_sources(alias: str) -> None:
    path = quote_path(alias, version=2)
    parser = PdfQuoteParser()
    first = parser.parse(path, context_for(alias, version=2))
    second = parser.parse(path, context_for(alias, version=2))

    assert first.document_sha256 == second.document_sha256
    assert [source.source_id for source in first.sources] == [source.source_id for source in second.sources]
    assert len(first.sources) >= 10
    assert all(source.kind == SourceKind.PDF_TEXT_BLOCK for source in first.sources)
    assert all(source.page_number == 1 for source in first.sources)
    assert all(source.block_id and source.bbox for source in first.sources)


def test_supplier_b_pdf_does_not_contain_shipping_amount() -> None:
    path = quote_path("b")
    parsed = PdfQuoteParser().parse(path, context_for("b"))
    full_text = " ".join(source.raw_text for source in parsed.sources).lower()
    assert "shipping" not in full_text
    assert "freight" not in full_text
    assert "200" not in full_text


def test_supplier_b_v2_pdf_preserves_shipping_omission() -> None:
    path = quote_path("b", version=2)
    parsed = PdfQuoteParser().parse(path, context_for("b", version=2))
    full_text = " ".join(source.raw_text for source in parsed.sources).lower()

    assert "shipping" not in full_text
    assert "freight" not in full_text
    assert "logistics charge" not in full_text


def test_non_pdf_content_is_explicitly_rejected(tmp_path) -> None:
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("this is not a PDF", encoding="utf-8")
    with pytest.raises(UnsupportedInputError) as raised:
        PdfQuoteParser().parse(fake_pdf, context_for("a"))
    assert raised.value.code == "unsupported_pdf"


def test_pdf_page_limit_is_enforced() -> None:
    path = quote_path("a")
    with pytest.raises(InputLimitError) as raised:
        PdfQuoteParser(FileLimits(max_pdf_pages=0)).parse(path, context_for("a"))
    assert raised.value.code == "pdf_page_limit_exceeded"


def test_pdf_without_extractable_text_requires_manual_fallback(monkeypatch) -> None:
    path = quote_path("a")
    monkeypatch.setattr("supplier_comparison.extraction.pdf_parser._extract_lines", lambda page: ())
    with pytest.raises(UnreadableInputError) as raised:
        PdfQuoteParser().parse(path, context_for("a"))
    assert raised.value.code == "pdf_text_unavailable"
