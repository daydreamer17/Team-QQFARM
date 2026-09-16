from __future__ import annotations

import re
import unicodedata


TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PREPROCESSING_VERSION = "policy-text/nfkc-en-hyphen-v1"


def normalize_policy_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).lower()


def tokenize_policy_text(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(normalize_policy_text(value))
