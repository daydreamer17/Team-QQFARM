"""Text-only PDF parser producing stable, position-aware evidence sources."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .contracts import BoundingBox, DocumentContext, EvidenceSource, ParsedInput, SourceKind
from .errors import InputLimitError, UnreadableInputError
from .files import FileLimits, require_pdf_magic, stable_id, validate_regular_file


PARSER_VERSION = "pdfplumber-text/1.0.0"


def _extract_lines(page: Any) -> Iterable[dict[str, Any]]:
    if hasattr(page, "extract_text_lines"):
        lines = page.extract_text_lines(strip=True, return_chars=False) or []
        for line in lines:
            text = str(line.get("text", "")).strip()
            if text:
                yield {
                    "text": text,
                    "x0": float(line["x0"]),
                    "top": float(line["top"]),
                    "x1": float(line["x1"]),
                    "bottom": float(line["bottom"]),
                }
        return

    words = page.extract_words() or []
    for word in words:
        text = str(word.get("text", "")).strip()
        if text:
            yield {
                "text": text,
                "x0": float(word["x0"]),
                "top": float(word["top"]),
                "x1": float(word["x1"]),
                "bottom": float(word["bottom"]),
            }


class PdfQuoteParser:
    def __init__(self, limits: FileLimits | None = None) -> None:
        self.limits = limits or FileLimits()

    def parse(self, path: str | Path, context: DocumentContext) -> ParsedInput:
        pdf_path, size, file_hash = validate_regular_file(path, self.limits)
        require_pdf_magic(pdf_path)
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - exercised only in a broken environment
            raise UnreadableInputError(
                "pdfplumber_unavailable", "pdfplumber is required to parse PDF input"
            ) from exc

        sources: list[EvidenceSource] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) > self.limits.max_pdf_pages:
                    raise InputLimitError(
                        "pdf_page_limit_exceeded",
                        "PDF exceeds configured page limit",
                        actual_pages=len(pdf.pages),
                        max_pages=self.limits.max_pdf_pages,
                    )
                for page_number, page in enumerate(pdf.pages, start=1):
                    for block_index, line in enumerate(_extract_lines(page), start=1):
                        bbox = BoundingBox(
                            x0=round(line["x0"], 3),
                            top=round(line["top"], 3),
                            x1=round(line["x1"], 3),
                            bottom=round(line["bottom"], 3),
                        )
                        block_id = stable_id(
                            "blk",
                            {
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "page": page_number,
                                "index": block_index,
                                "bbox": bbox.model_dump(),
                                "text": line["text"],
                            },
                        )
                        source_id = stable_id(
                            "src",
                            {
                                "kind": SourceKind.PDF_TEXT_BLOCK,
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "block_id": block_id,
                            },
                        )
                        sources.append(
                            EvidenceSource(
                                source_id=source_id,
                                kind=SourceKind.PDF_TEXT_BLOCK,
                                document_id=context.document_id,
                                document_version=context.document_version,
                                document_sha256=file_hash,
                                parser_version=PARSER_VERSION,
                                raw_text=line["text"],
                                page_number=page_number,
                                block_id=block_id,
                                bbox=bbox,
                            )
                        )
        except InputLimitError:
            raise
        except Exception as exc:
            raise UnreadableInputError("pdf_parse_failed", "PDF could not be parsed", path=str(pdf_path)) from exc

        if not sources:
            raise UnreadableInputError(
                "pdf_text_unavailable",
                "PDF has no extractable text; scanned PDFs and OCR are not supported",
                path=str(pdf_path),
            )
        return ParsedInput(
            context=context,
            original_filename=pdf_path.name,
            media_type="application/pdf",
            file_size_bytes=size,
            document_sha256=file_hash,
            parser_version=PARSER_VERSION,
            sources=tuple(sources),
        )
