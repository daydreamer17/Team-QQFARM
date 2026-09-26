"""Strict parsing for JSON-only model responses.

Some OpenAI-compatible gateways accept ``response_format`` but still wrap the
JSON document in a Markdown fence. Accept that one compatibility variation
without accepting prose, multiple blocks, or partially recoverable JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any


_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>.*)\r?\n```[ \t]*\Z",
    re.IGNORECASE | re.DOTALL,
)


def load_model_json(content: str) -> Any:
    """Decode a plain JSON document or one complete ``json`` code fence."""

    candidate = content.strip()
    match = _JSON_FENCE.fullmatch(candidate)
    if match is not None:
        candidate = match.group("body")
    return json.loads(candidate)


def load_single_model_json_object(content: str, *, required_key: str) -> Any:
    """Decode strict JSON or one required object inside gateway prose.

    Some OpenAI-compatible gateways preserve a reasoning preamble despite a
    JSON response format. The fallback accepts exactly one object containing
    ``required_key``. Callers must still validate that object against their
    strict schema and grounding rules.
    """

    try:
        return load_model_json(content)
    except json.JSONDecodeError as strict_error:
        decoder = json.JSONDecoder()
        matches: list[Any] = []
        for index, character in enumerate(content):
            if character != "{":
                continue
            try:
                candidate, _end = decoder.raw_decode(content, index)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and required_key in candidate:
                matches.append(candidate)
        if len(matches) == 1:
            return matches[0]
        raise strict_error


def model_response_is_complete(finish_reason: object) -> bool:
    """Accept complete OpenAI and Anthropic-compatible gateway responses.

    OpenAI-compatible gateways do not use one portable success value: observed
    responses include OpenAI's ``stop``, Anthropic's ``end_turn`` and
    provider-specific completion labels. Schema and JSON validation still
    reject partial content, so only explicit truncation, safety or tool-call
    termination reasons need to be rejected here.
    """

    if finish_reason is None:
        return True
    if not isinstance(finish_reason, str):
        return False
    return finish_reason.lower() not in {
        "length",
        "max_tokens",
        "content_filter",
        "tool_calls",
        "function_call",
        "tool_use",
        "pause_turn",
        "refusal",
    }
