"""Language selection for user-facing decision-conversation output."""

from __future__ import annotations

import re
from typing import Any, Literal


ResponseLanguage = Literal["zh", "en"]

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def response_language(text: str | None) -> ResponseLanguage:
    """Treat mixed Chinese/English procurement questions as Chinese."""

    return "zh" if text and _CJK.search(text) else "en"


def conversation_response_language(context: dict[str, Any]) -> ResponseLanguage:
    for row in reversed(context.get("recent_messages", [])):
        if isinstance(row, dict) and row.get("role") == "USER":
            return response_language(str(row.get("content") or ""))
    return "en"


def response_matches_language(text: str, language: ResponseLanguage) -> bool:
    if not text.strip():
        return True
    cjk_count = len(_CJK.findall(text))
    latin_count = len(_LATIN.findall(text))
    if language == "zh":
        return cjk_count > 0
    # Permit an English answer to retain a short Chinese proper noun, while
    # rejecting a Chinese narration that merely contains an English ID/name.
    return latin_count > 0 and latin_count >= cjk_count


def language_name(language: ResponseLanguage) -> str:
    return "Simplified Chinese" if language == "zh" else "English"
