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
