from __future__ import annotations

import json

import pytest

from supplier_comparison.model_json import load_model_json


def test_load_model_json_accepts_plain_json() -> None:
    assert load_model_json(' {"ok": true} ') == {"ok": True}


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
