from __future__ import annotations

import csv
import io
import re
from typing import Literal


SupplierIdSource = Literal["CSV_FIELD", "PDF_TEXT"]

_CSV_ID_HEADERS = {
    "supplier_id",
    "supplier_code",
    "vendor_id",
    "vendor_code",
    "供应商编号",
    "供应商代码",
}
_SUPPLIER_ID = re.compile(r"(?<![A-Z0-9])SUP[-_][A-Z0-9][A-Z0-9._/-]{0,63}", re.IGNORECASE)
_VALID_EXPLICIT_ID = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,127}$", re.IGNORECASE)


def _unique(values: list[str]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            unique.setdefault(cleaned.casefold(), cleaned)
    return list(unique.values())


def _identify_csv(content: bytes) -> list[str]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    matching = [header for header in headers if header.strip().casefold() in _CSV_ID_HEADERS]
    if not matching:
        return []
    values: list[str] = []
    for index, row in enumerate(reader):
        if index >= 100:
            break
        for header in matching:
            value = (row.get(header) or "").strip()
            if value and _VALID_EXPLICIT_ID.fullmatch(value):
                values.append(value)
    return _unique(values)


def _identify_pdf(content: bytes) -> list[str]:
    if not content.startswith(b"%PDF-"):
        raise ValueError("invalid_pdf")
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            if len(pdf.pages) > 50:
                raise ValueError("pdf_page_limit_exceeded")
            text_parts: list[str] = []
            used = 0
            for page in pdf.pages:
                text = page.extract_text() or ""
                remaining = 100_000 - used
                if remaining <= 0:
                    break
                text_parts.append(text[:remaining])
                used += min(len(text), remaining)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalid_pdf") from exc
    return _unique(_SUPPLIER_ID.findall("\n".join(text_parts)))


def identify_supplier_id(
    content: bytes,
    *,
    filename: str,
    media_type: str,
) -> dict[str, object]:
    """Identify only an explicit supplier ID; never infer one from a company name."""
    lower_name = filename.casefold()
    if lower_name.endswith(".csv") or media_type.casefold() in {"text/csv", "application/csv"}:
        try:
            candidates = _identify_csv(content)
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_csv_encoding") from exc
        source: SupplierIdSource = "CSV_FIELD"
    elif lower_name.endswith(".pdf") or media_type.casefold() == "application/pdf":
        candidates = _identify_pdf(content)
        source = "PDF_TEXT"
    else:
        raise ValueError("unsupported_media_type")

    status = "FOUND" if len(candidates) == 1 else "AMBIGUOUS" if candidates else "NOT_FOUND"
    return {
        "status": status,
        "supplier_id": candidates[0] if status == "FOUND" else None,
        "candidates": candidates,
        "source": source,
    }
