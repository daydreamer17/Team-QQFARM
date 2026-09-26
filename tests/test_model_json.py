from __future__ import annotations

import json

import pytest

from supplier_comparison.model_json import load_model_json, model_response_is_complete


def test_load_model_json_accepts_plain_json() -> None:
    assert load_model_json(' {"ok": true} ') == {"ok": True}


@pytest.mark.parametrize(
    "finish_reason", [None, "stop", "STOP", "end_turn", "stop_sequence", "completed"]
)
def test_model_response_completion_accepts_gateway_variants(finish_reason) -> None:
    assert model_response_is_complete(finish_reason)


@pytest.mark.parametrize(
    "finish_reason",
    [
        "length",
        "max_tokens",
        "content_filter",
        "tool_calls",
        "function_call",
        "tool_use",
        "pause_turn",
        "refusal",
        {"unexpected": "shape"},
    ],
)
def test_model_response_completion_rejects_incomplete_outputs(finish_reason) -> None:
    assert not model_response_is_complete(finish_reason)


@pytest.mark.parametrize("language", ["json", "JSON", ""])
def test_load_model_json_accepts_one_complete_json_fence(language: str) -> None:
    assert load_model_json(f"```{language}\n{{\"ok\": true}}\n```") == {"ok": True}


@pytest.mark.parametrize(
    "content",
    [
        "Here is the result:\n```json\n{\"ok\": true}\n```",
        "```json\n{\"ok\": true}\n```\nExtra text",
        "```python\n{\"ok\": true}\n```",
        "```json\n{\"ok\": true}\n```\n```json\n{}\n```",
        "```json\n{\"ok\": true}",
    ],
)
def test_load_model_json_rejects_prose_or_non_json_fences(content: str) -> None:
    with pytest.raises(json.JSONDecodeError):
        load_model_json(content)
