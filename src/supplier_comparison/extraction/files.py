"""File validation and stable hashing helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InputLimitError, UnreadableInputError, UnsupportedInputError


@dataclass(frozen=True, slots=True)
class FileLimits:
    max_file_size_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 5


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UnreadableInputError("file_unreadable", f"cannot read input file: {path}", path=str(path)) from exc
    return digest.hexdigest()


def validate_regular_file(path: str | Path, limits: FileLimits) -> tuple[Path, int, str]:
    resolved = Path(path)
    if not resolved.is_file():
        raise UnreadableInputError("file_not_found", f"input is not a regular file: {resolved}", path=str(resolved))
    size = resolved.stat().st_size
    if size <= 0:
        raise UnreadableInputError("file_empty", "input file is empty", path=str(resolved))
    if size > limits.max_file_size_bytes:
        raise InputLimitError(
            "file_too_large",
            "input exceeds configured size limit",
            actual_bytes=size,
            max_bytes=limits.max_file_size_bytes,
        )
    return resolved, size, sha256_file(resolved)


def require_pdf_magic(path: Path) -> None:
    with path.open("rb") as handle:
        signature = handle.read(5)
    if path.suffix.lower() != ".pdf" or signature != b"%PDF-":
        raise UnsupportedInputError("unsupported_pdf", "input is not a PDF file", path=str(path))


def require_csv_shape(path: Path) -> None:
    if path.suffix.lower() != ".csv":
        raise UnsupportedInputError("unsupported_csv", "input is not a CSV file", path=str(path))
    with path.open("rb") as handle:
        prefix = handle.read(4096)
    if b"\x00" in prefix:
        raise UnsupportedInputError("binary_csv", "CSV input contains binary NUL bytes", path=str(path))
    try:
        prefix.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnsupportedInputError("csv_not_utf8", "CSV input must be UTF-8", path=str(path)) from exc


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"
