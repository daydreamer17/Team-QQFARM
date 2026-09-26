"""Run one synthetic, read-only organiser gateway call for quote diagnostics.

Run inside the configured worker container with this repository mounted at
``/repo``. The script never prints environment variables or request headers.
"""

from __future__ import annotations

import json
from pathlib import Path

from supplier_comparison.extraction.adapters import (
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
    _build_organizer_prompt,
    _decode_openai_response,
    _organizer_field_groups,
    _source_handle_map,
)
from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    quote_path = root / "data/generated/demos/full_flow_demo4/quotes/pdf/great_wall_quote.pdf"
    dictionary = QuoteDictionary.load(root / "data/contracts/quote_data_field.csv")
    parsed = PdfQuoteParser().parse(
        quote_path,
        DocumentContext(
            task_id="DIAGNOSTIC-SYNTHETIC",
            task_revision=1,
            quote_id="DIAGNOSTIC-SUP-029",
            quote_version=1,
            document_id="DIAGNOSTIC-GREAT-WALL-PDF",
            document_version=1,
            supplier_id="SUP-029",
        ),
    )
    group = _organizer_field_groups(dictionary)[0]
    handles = _source_handle_map(parsed)
    prompt = _build_organizer_prompt(parsed, group, handles)
    adapter = OpenAICompatibleAdapter(OpenAICompatibleConfig.from_env())
    decoded = _decode_openai_response(adapter._request(prompt, tuple(handles)).body)
    content = decoded.content
    print(json.dumps({
        "prompt_bytes": len(prompt.encode("utf-8")),
        "content_bytes": len(content.encode("utf-8")),
        "finish_reason": decoded.finish_reason,
        "completion_tokens": decoded.completion_tokens,
        "reasoning_tokens": decoded.reasoning_tokens,
        "first_1200_characters": content[:1200],
        "last_1200_characters": content[-1200:],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
