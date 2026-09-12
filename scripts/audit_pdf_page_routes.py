#!/usr/bin/env python3
"""Audit deterministic PDF page routes without invoking a model or OCR engine."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.errors import ExtractionError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser


def _pdf_paths(inputs: list[Path]) -> list[Path]:
    paths: set[Path] = set()
    for item in inputs:
        if item.is_dir():
            paths.update(path for path in item.rglob("*.pdf") if path.is_file())
        elif item.is_file() and item.suffix.lower() == ".pdf":
            paths.add(item)
        else:
            raise SystemExit(f"not a PDF file or directory: {item}")
    return sorted(paths)


def _context(index: int) -> DocumentContext:
    return DocumentContext(
        task_id="TASK-V7-PAGE-ROUTE-AUDIT",
        task_revision=1,
        quote_id=f"QUOTE-V7-PAGE-ROUTE-AUDIT-{index:03d}",
        quote_version=1,
        document_id=f"DOC-V7-PAGE-ROUTE-AUDIT-{index:03d}",
        document_version=1,
        supplier_id="SUP-V7-PAGE-ROUTE-AUDIT",
    )


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("inputs", nargs="+", type=Path)
    argument_parser.add_argument("--pretty", action="store_true")
    args = argument_parser.parse_args()

    paths = _pdf_paths(args.inputs)
    if not paths:
        argument_parser.error("no PDF files found")

    parser = PdfQuoteParser()
    route_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    records: list[dict[str, object]] = []
    parsed_documents = 0
    parsed_pages = 0

    for index, path in enumerate(paths, start=1):
        try:
            parsed = parser.parse(path, _context(index))
            page_analyses = [
                analysis.model_dump(mode="json") for analysis in parsed.page_analyses
            ]
            parsed_documents += 1
            parsed_pages += len(page_analyses)
            route_counts.update(item["route"] for item in page_analyses)
            records.append(
                {
                    "path": str(path),
                    "status": "PARSED",
                    "parser_version": parsed.parser_version,
                    "parser_fingerprint": parsed.parser_fingerprint,
                    "source_count": len(parsed.sources),
                    "page_analyses": page_analyses,
                }
            )
        except ExtractionError as exc:
            error_counts[exc.code] += 1
            page_analyses = exc.details.get("page_analyses", [])
            route_counts.update(item["route"] for item in page_analyses)
            records.append(
                {
                    "path": str(path),
                    "status": "REJECTED",
                    "error_code": exc.code,
                    "page_analyses": page_analyses,
                }
            )

    result = {
        "result_kind": "DETERMINISTIC_PAGE_ROUTE_AUDIT",
        "model_called": False,
        "ocr_called": False,
        "documents": len(paths),
        "parsed_documents": parsed_documents,
        "rejected_documents": len(paths) - parsed_documents,
        "parsed_pages": parsed_pages,
        "route_counts_including_analyzed_rejections": dict(sorted(route_counts.items())),
        "error_counts": dict(sorted(error_counts.items())),
        "records": records,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
