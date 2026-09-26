"""Run one synthetic, read-only organiser gateway call for quote diagnostics.

Run inside the configured worker container with this repository mounted at
``/repo``. The script never prints environment variables or request headers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from supplier_comparison.extraction.adapters import (
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
    _build_organizer_prompt,
    _decode_openai_response,
    _organizer_field_groups,
    _source_handle_map,
    _normalize_sparse_model_payload,
    _complete_sparse_model_payload,
    QUOTE_OUTPUT_TOOL,
)
from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.model_payload import SparseModelExtractionPayload
from supplier_comparison.model_json import load_single_model_json_object, model_response_is_complete


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    from deployment_runtime_report import report
    print(json.dumps(report(), indent=2, default=str), flush=True)
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
    response = adapter._request(prompt, tuple(handles))
    envelope = json.loads(response.body)
    expected_tool = QUOTE_OUTPUT_TOOL if adapter.config.environment.value == "ORGANIZER" else None
    decoded = _decode_openai_response(response.body, expected_tool_name=expected_tool)
    content = decoded.content
    try:
        tool_finished = expected_tool is not None and decoded.finish_reason in {"tool_calls", "tool_use"}
        if not tool_finished and not model_response_is_complete(decoded.finish_reason):
            raise ValueError("incomplete response")
        payload = _normalize_sparse_model_payload(load_single_model_json_object(content, required_key="candidates"))
        _complete_sparse_model_payload(SparseModelExtractionPayload.model_validate(payload), group)
        validation = "JSON_AND_FIELD_SCHEMA_OK (grounding not tested)"
    except Exception as exc:
        validation = type(exc).__name__
    # Only a synthetic fixture is sent. Still redact configured credentials if echoed.
    for key_name in {adapter.config.api_key_env, "LLM_GATEWAY_API_KEY", "QQFARM_SILICONFLOW_API_KEY"}:
        secret = os.getenv(key_name or "")
        if secret:
            content = content.replace(secret, "[REDACTED]")
    print(json.dumps({
        "requested_model": adapter.config.model_id,
        "returned_model": envelope.get("model"),
        "validation": validation,
        "output_channel": "tool_arguments" if expected_tool else "message_content",
        "message_keys": sorted(envelope["choices"][0]["message"]),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "content_bytes": len(content.encode("utf-8")),
        "finish_reason": decoded.finish_reason,
        "completion_tokens": decoded.completion_tokens,
        "reasoning_tokens": decoded.reasoning_tokens,
        "synthetic_response_content": content[:60000],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
