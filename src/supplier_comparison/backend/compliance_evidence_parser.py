"""Deterministic extraction for human-reviewed compliance evidence uploads."""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Literal


MAX_EVIDENCE_PARSE_BYTES = 10 * 1024 * 1024
MAX_EVIDENCE_PARSE_CHARACTERS = 100_000
ControlCode = Literal["APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL"]


def _extract_text(content: bytes, *, filename: str, media_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    if not content:
        raise ValueError("empty_file")
    if len(content) > MAX_EVIDENCE_PARSE_BYTES:
        raise ValueError("file_too_large")
    if suffix == ".pdf" or media_type.casefold() == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("invalid_pdf")
        try:
            import pdfplumber

            with pdfplumber.open(io.BytesIO(content)) as pdf:
                if len(pdf.pages) > 50:
                    raise ValueError("pdf_page_limit_exceeded")
                text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("invalid_pdf") from exc
    elif suffix in {".txt", ".md"} or media_type.casefold() in {
        "text/plain", "text/markdown", "text/x-markdown"
    }:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid_text_encoding") from exc
    else:
        raise ValueError("unsupported_media_type")
    text = text[:MAX_EVIDENCE_PARSE_CHARACTERS].strip()
    if len(text) < 10:
        raise ValueError("text_unavailable")
    return text


def _label(text: str, *labels: str) -> str | None:
    alternatives = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?im)^\s*(?:{alternatives})\s*[:：]\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日)?\b", value)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _outcome(text: str, control_code: ControlCode) -> str | None:
    if control_code == "APPROVED_SUPPLIER":
        value = _label(text, "Status", "Decision", "状态", "决定", "结论")
        failures = r"REVOKED|SUSPENDED|REFUSED|REJECTED|NOT\s+APPROVED|已撤销|已暂停|拒绝|未准入|不通过"
        passes = r"APPROVED|REINSTATED|ADMITTED|准入|批准|通过"
    elif control_code == "ROHS_COMPLIANCE":
        value = _label(text, "Outcome", "Result", "Finding", "结论", "结果")
        failures = r"NON[-\s]?CONFORMING|FAILED|FAIL|不符合|不合格|未通过"
        passes = r"CONFORMITY\s+DECLARED|CONFORMING|COMPLIANT|PASSED|PASS|符合|合格|通过"
    else:
        value = _label(text, "Decision", "Status", "Approval result", "审批结论", "状态", "结论")
        failures = r"REJECTED|REFUSED|DENIED|NOT\s+APPROVED|拒绝|驳回|未批准|不通过"
        passes = r"APPROVED|批准|通过"
    if value and re.search(failures, value, re.IGNORECASE):
        return "FAIL"
    if value and re.search(passes, value, re.IGNORECASE):
        return "PASS"
    return None


def _detected_control_code(text: str) -> ControlCode | None:
    """Classify only from explicit document labels; do not infer from a supplier name."""
    if re.search(r"(?im)^\s*(?:Approval ID|Approved amount|审批编号|批准金额)\s*[:：]", text):
        return "AMOUNT_APPROVAL"
    if re.search(r"(?im)^\s*(?:Certificate ID|Manufacturer part number|证书编号|制造商料号)\s*[:：]", text):
        return "ROHS_COMPLIANCE"
    if re.search(r"(?im)^\s*(?:Record ID|Admission ID|准入编号|记录编号)\s*[:：]", text):
        return "APPROVED_SUPPLIER"
    return None


def parse_compliance_evidence(
    content: bytes,
    *,
    filename: str,
    media_type: str,
    control_code: ControlCode,
) -> dict[str, object]:
    """Extract explicit facts only; a human still confirms scope and authenticity."""
    text = _extract_text(content, filename=filename, media_type=media_type)
    detected_control_code = _detected_control_code(text)
    if detected_control_code and detected_control_code != control_code:
        return {
            "status": "TYPE_MISMATCH",
            "facts": {},
            "parsed_fields": [],
            "missing_fields": [],
            "source_filename": Path(filename).name,
            "detected_control_code": detected_control_code,
        }
    facts: dict[str, object] = {}
    material_labels = {
        "APPROVED_SUPPLIER": ("Record ID", "Admission ID", "记录编号", "准入编号"),
        "ROHS_COMPLIANCE": ("Certificate ID", "Certificate number", "证书编号"),
        "AMOUNT_APPROVAL": ("Approval ID", "Approval number", "审批编号", "批准编号"),
    }[control_code]
    material_number = _label(text, *material_labels)
    supplier_id = _label(
        text,
        "Supplier ID",
        "Supplier ID stated in record",
        "Vendor ID",
        "供应商编号",
        "供应商代码",
    )
    if material_number:
        facts["material_number"] = material_number
    if supplier_id:
        facts["supplier_id"] = supplier_id

    outcome = _outcome(text, control_code)
    if outcome:
        facts["outcome"] = outcome

    effective_from = _date(_label(text, "Effective from", "Valid from", "生效日期", "有效期起"))
    expires_on = _date(_label(text, "Expires on", "Valid until", "Expiry date", "失效日期", "有效期至"))
    permanent = bool(re.search(r"(?i)\bpermanent(?:ly)?\s+valid\b|永久有效", text))
    if effective_from:
        facts["effective_from"] = effective_from
    if expires_on:
        facts["expires_on"] = expires_on
    if permanent:
        facts["permanent"] = True
        facts["expires_on"] = None

    if control_code == "ROHS_COMPLIANCE":
        manufacturer = _label(text, "Manufacturer", "制造商")
        part_number = _label(text, "Manufacturer part number", "Part number", "制造商料号", "物料号")
        if manufacturer:
            facts["manufacturer"] = manufacturer
        if part_number:
            facts["manufacturer_part_number"] = part_number
    elif control_code == "AMOUNT_APPROVAL":
        approved = _label(text, "Approved amount", "Approval amount", "批准金额", "审批金额")
        if approved:
            amount = re.search(r"\b([A-Z]{3})\s*([0-9]+(?:\.[0-9]{1,2})?)\b", approved)
            if amount:
                facts["currency"] = amount.group(1).upper()
                facts["approval_amount"] = amount.group(2)

    source = _label(text, "Source", "来源")
    facts["source_refs"] = [source] if source else [f"Uploaded file: {Path(filename).name}"]
    expected = {
        "APPROVED_SUPPLIER": {"material_number", "supplier_id", "outcome"},
        "ROHS_COMPLIANCE": {"material_number", "supplier_id", "manufacturer", "manufacturer_part_number", "outcome"},
        "AMOUNT_APPROVAL": {"material_number", "supplier_id", "approval_amount", "currency", "outcome"},
    }[control_code]
    missing = sorted(expected - facts.keys())
    return {
        "status": "FOUND" if not missing else "PARTIAL" if facts else "NOT_FOUND",
        "facts": facts,
        "parsed_fields": sorted(key for key in facts if key != "source_refs"),
        "missing_fields": missing,
        "source_filename": Path(filename).name,
        "detected_control_code": detected_control_code,
    }
