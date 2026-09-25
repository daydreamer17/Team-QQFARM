"""Deterministic Markdown and Word exports for a frozen procurement summary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def build_summary_export(
    *,
    facts: dict[str, Any],
    narrative: dict[str, Any],
    summary_id: str,
    status: str,
    updated_at: datetime,
    export_format: str,
) -> dict[str, Any]:
    report = _report_model(
        facts=facts,
        narrative=narrative,
        summary_id=summary_id,
        status=status,
        updated_at=updated_at,
    )
    stem = _safe_filename(
        f"{report['scenario']}_Procurement_Summary_Revision_{report['task_revision']}"
    )
    if export_format == "md":
        return {
            "filename": f"{stem}.md",
            "media_type": "text/markdown; charset=utf-8",
            "content": _render_markdown(report).encode("utf-8"),
        }
    if export_format == "docx":
        return {
            "filename": f"{stem}.docx",
            "media_type": DOCX_MEDIA_TYPE,
            "content": _render_docx(report),
        }
    raise ValueError("unsupported summary export format")


def _report_model(
    *,
    facts: dict[str, Any],
    narrative: dict[str, Any],
    summary_id: str,
    status: str,
    updated_at: datetime,
) -> dict[str, Any]:
    requirement = dict(facts.get("requirement") or {})
    quote_rows = [
        dict(value)
        for value in (facts.get("references") or {}).values()
        if isinstance(value, dict) and value.get("type") == "QUOTE_RESULT"
    ]
    recommended_ids = set(facts.get("recommended_quote_ids") or [])
    quote_rows.sort(key=lambda row: (row.get("quote_id") not in recommended_ids, row.get("supplier_name") or ""))
    recommended = next(
        (row for row in quote_rows if row.get("quote_id") in recommended_ids),
        None,
    )
    compliance = facts.get("policy_compliance") or {}
    assessments = {
        str(item.get("quote_id")): item
        for item in compliance.get("assessments", [])
        if isinstance(item, dict) and item.get("quote_id")
    } if isinstance(compliance, dict) else {}
    currency = str(requirement.get("currency") or "")
    for row in quote_rows:
        assessment = assessments.get(str(row.get("quote_id")))
        row["recommended"] = row.get("quote_id") in recommended_ids
        row["status_label"] = _status_label(row.get("status"))
        row["payment_text"] = _payment_text(row)
        row["cost_text"] = _money(currency, row.get("total_cost"))
        row["quantity_text"] = _quantity(
            row.get("actual_quantity"), requirement.get("quantity_unit")
        )
        row["cost_delta"] = _cost_delta(row, recommended, currency)
        row["policy_assessment"] = assessment
        row["policy_status_text"] = _policy_status_text(assessment)
        row["selection_text"] = _selection_text(row, recommended, currency, assessment)
        row["communication_goal"], row["communication_text"] = _communication(
            row, recommended, currency, assessment
        )
    scenario = str(
        requirement.get("manufacturer_part_number")
        or facts.get("task_id")
        or "Procurement task"
    )
    return {
        "title": f"{scenario} Procurement Summary",
        "scenario": scenario,
        "summary_id": summary_id,
        "task_id": facts.get("task_id"),
        "task_revision": facts.get("task_revision"),
        "result_id": facts.get("result_id"),
        "status": status,
        "generated_at": updated_at.isoformat(),
        "requirement": requirement,
        "currency": currency,
        "quotes": quote_rows,
        "recommended": recommended,
        "overview": str(narrative.get("overview") or ""),
        "narrative_sections": [
            dict(section) for section in narrative.get("sections") or []
            if isinstance(section, dict)
        ],
        "disclaimer": str(narrative.get("disclaimer") or ""),
        "policy_binding": facts.get("policy_binding"),
        "policy_compliance": facts.get("policy_compliance"),
        "scope": facts.get("scope"),
        "comparison_reasons": [
            dict(reason) for reason in facts.get("comparison_reasons") or []
            if isinstance(reason, dict)
        ],
    }


def _render_markdown(report: dict[str, Any]) -> str:
    requirement = report["requirement"]
    recommended = report["recommended"]
    lines = [
        f"# {report['title']}",
        "",
        f"> Task revision: {report['task_revision']}  ",
        f"> Result ID: `{report['result_id']}`  ",
        f"> Summary ID: `{report['summary_id']}`  ",
        f"> Generated at: {report['generated_at']}",
        "",
        "## 1. Executive Summary",
        "",
        report["overview"],
        "",
    ]
    if recommended:
        lines.extend([
            f"**Recommended for progression: {recommended.get('supplier_name', '—')}**  ",
            f"Confirmed total cost: {recommended['cost_text']}; estimated arrival: {recommended.get('estimated_arrival_date') or 'Pending confirmation'}.",
            "",
        ])
    lines.extend([
        "## 2. Procurement Requirements",
        "",
        _requirement_paragraph(requirement),
        "",
        "| Item | Details |",
        "| --- | --- |",
        f"| Manufacturer | {_md(requirement.get('manufacturer'))} |",
        f"| Part number | {_md(requirement.get('manufacturer_part_number'))} |",
        f"| Quantity | {_md(requirement.get('required_quantity'))} {_md(requirement.get('quantity_unit'))} |",
        f"| Package / revision | {_md(requirement.get('package'))} / {_md(requirement.get('revision'))} |",
        f"| Budget | {_money(report['currency'], requirement.get('budget_amount'))} |",
        f"| Delivery deadline | {_md(requirement.get('delivery_deadline'))} |",
        f"| Delivery location | {_md(requirement.get('delivery_location'))} |",
        "",
        "## 3. Quotations, Cost, and Key Trade-offs",
        "",
        "| Metric | " + " | ".join(_md(row.get("supplier_name")) for row in report["quotes"]) + " |",
        "| --- | " + " | ".join("---" for _ in report["quotes"]) + " |",
    ])
    matrix_rows = [
        ("Policy eligibility", lambda row: row["policy_status_text"]),
        ("Confirmed total cost", lambda row: row["cost_text"] + (" (recommended)" if row["recommended"] else "")),
        ("Difference from preferred option", lambda row: row["cost_delta"]),
        ("Actual purchase quantity", lambda row: row["quantity_text"]),
        ("Estimated arrival", lambda row: row.get("estimated_arrival_date") or "Pending confirmation"),
        ("Payment terms", lambda row: row["payment_text"]),
        ("Feasibility", lambda row: row["status_label"]),
        ("Trade-off", lambda row: row["selection_text"]),
    ]
    for label, getter in matrix_rows:
        lines.append(f"| {label} | " + " | ".join(_md(getter(row)) for row in report["quotes"]) + " |")
    lines.extend(["", "### Cost Position", ""])
    finite_costs = [_decimal(row.get("total_cost")) for row in report["quotes"]]
    budget = _decimal(requirement.get("budget_amount"))
    maximum = max([value for value in [*finite_costs, budget] if value is not None] or [Decimal("1")])
    for row, cost in zip(report["quotes"], finite_costs, strict=True):
        if cost is not None:
            bars = max(1, min(20, int((cost / maximum) * 20)))
            lines.append(f"- {row.get('supplier_name')}: {'█' * bars}{'░' * (20 - bars)} {row['cost_text']}")
    lines.extend(["", "### Additional AI Analysis", ""])
    for section in report["narrative_sections"]:
        lines.extend([f"**{section.get('heading', 'Analysis')}**", "", str(section.get("text") or ""), ""])
    lines.extend(["## 4. Selection Rationale and Suggested Communication", ""])
    for row in report["quotes"]:
        lines.extend([
            f"### {row.get('supplier_name') or 'Supplier'}",
            "",
            f"**Trade-off:** {row['selection_text']}  ",
            f"**Communication objective:** {row['communication_goal']}  ",
            f"**Suggested message:** {row['communication_text']}",
            "",
        ])
    lines.extend([
        "## 5. Risks, Policy, and Approval Gates",
        "",
        f"- Quotation completeness: {sum(row.get('status') == 'PENDING' for row in report['quotes'])} pending confirmation.",
        f"- Policy binding: {_policy_text(report.get('policy_binding'))}.",
        "- Approval status: not approved; this report has no authority to place orders, make payments, or approve procurement.",
        "",
        *_compliance_lines(report.get('policy_compliance')),
        "",
        "## 6. Recommended Actions and Records",
        "",
        "1. Complete supplier communication and add responses as a new revision input.",
        "2. Close policy review items while retaining clauses, exceptions, and manual confirmation records.",
        "3. Attach source quotations, the frozen comparison result, and this report for approval by an authorised person.",
        "",
        "---",
        "",
        report["disclaimer"],
        "",
    ])
    return "\n".join(lines)


def _render_docx(report: dict[str, Any]) -> bytes:
    requirement = report["requirement"]
    recommended = report["recommended"]
    body: list[str] = []
    body.append(_w_paragraph(report["title"], style="Title"))
    body.append(_w_paragraph(
        f"Task revision: {report['task_revision']}    Result: {report['result_id']}    Generated: {report['generated_at']}",
        color="6B778C",
    ))
    body.append(_w_heading("1. Executive Summary"))
    body.append(_w_paragraph(report["overview"]))
    if recommended:
        body.append(_w_paragraph(
            f"Recommended for progression: {recommended.get('supplier_name', '—')}. Confirmed total cost: {recommended['cost_text']}; estimated arrival: {recommended.get('estimated_arrival_date') or 'pending confirmation'}.",
            bold=True,
            color="137A54",
        ))
    body.append(_w_heading("2. Procurement Requirements"))
    body.append(_w_paragraph(_requirement_paragraph(requirement)))
    body.append(_w_table([
        ["Item", "Details"],
        ["Manufacturer", _text(requirement.get("manufacturer"))],
        ["Part number", _text(requirement.get("manufacturer_part_number"))],
        ["Quantity", f"{_text(requirement.get('required_quantity'))} {_text(requirement.get('quantity_unit'))}"],
        ["Package / revision", f"{_text(requirement.get('package'))} / {_text(requirement.get('revision'))}"],
        ["Budget", _money(report["currency"], requirement.get("budget_amount"))],
        ["Delivery deadline", _text(requirement.get("delivery_deadline"))],
        ["Delivery location", _text(requirement.get("delivery_location"))],
    ]))
    body.append(_w_heading("3. Quotations, Cost, and Key Trade-offs"))
    matrix = [["Metric", *[_text(row.get("supplier_name")) for row in report["quotes"]]]]
    for label, key in [
        ("Policy eligibility", "policy_status_text"),
        ("Confirmed total cost", "cost_text"),
        ("Difference from preferred option", "cost_delta"),
        ("Actual purchase quantity", "quantity_text"),
        ("Payment terms", "payment_text"),
        ("Feasibility", "status_label"),
        ("Trade-off", "selection_text"),
    ]:
        matrix.append([label, *[_text(row.get(key)) for row in report["quotes"]]])
    matrix.insert(4, ["Estimated arrival", *[_text(row.get("estimated_arrival_date")) for row in report["quotes"]]])
    body.append(_w_table(matrix))
    body.append(_w_heading("Cost Position", level=2))
    finite_costs = [_decimal(row.get("total_cost")) for row in report["quotes"]]
    budget = _decimal(requirement.get("budget_amount"))
    maximum = max([value for value in [*finite_costs, budget] if value is not None] or [Decimal("1")])
    for row, cost in zip(report["quotes"], finite_costs, strict=True):
        if cost is not None:
            bars = max(1, min(20, int((cost / maximum) * 20)))
            body.append(_w_paragraph(
                f"{row.get('supplier_name')}: {'■' * bars}{'□' * (20 - bars)} {row['cost_text']}"
            ))
    body.append(_w_heading("Additional AI Analysis", level=2))
    for section in report["narrative_sections"]:
        body.append(_w_paragraph(_text(section.get("heading")), bold=True))
        body.append(_w_paragraph(_text(section.get("text"))))
    body.append(_w_heading("4. Selection Rationale and Suggested Communication"))
    for row in report["quotes"]:
        body.append(_w_heading(_text(row.get("supplier_name")), level=2))
        body.append(_w_paragraph(f"Trade-off: {row['selection_text']}"))
        body.append(_w_paragraph(f"Communication objective: {row['communication_goal']}", bold=True))
        body.append(_w_paragraph(f"Suggested message: {row['communication_text']}"))
    body.append(_w_heading("5. Risks, Policy, and Approval Gates"))
    body.append(_w_table([
        ["Item", "Status", "Notes"],
        ["Quotation completeness", f"{sum(row.get('status') == 'PENDING' for row in report['quotes'])} pending confirmation", "Unknown values are never treated as zero or automatically compliant."],
        ["Policy binding", _policy_text(report.get("policy_binding")), "Policy citations do not replace manual applicability judgement."],
        ["Approval status", "Not approved", "This report has no authority to place orders, make payments, or approve procurement."],
    ]))
    for line in _compliance_lines(report.get('policy_compliance')):
        body.append(_w_paragraph(line))
    body.append(_w_heading("6. Recommended Actions and Records"))
    for text in [
        "1. Complete supplier communication and add responses as a new revision input.",
        "2. Close policy review items while retaining clauses, exceptions, and manual confirmation records.",
        "3. Attach source quotations, the frozen comparison result, and this report for approval by an authorised person.",
    ]:
        body.append(_w_paragraph(text))
    body.append(_w_paragraph(report["disclaimer"], color="6B778C"))
    body.append(_w_paragraph(
        f"Summary {report['summary_id']} · Result {report['result_id']} · Task Revision {report['task_revision']}",
        color="7A8699",
    ))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>' + "".join(body) +
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
        '</w:body></w:document>'
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", _DOCX_RELS)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", _DOCX_STYLES)
        archive.writestr("word/_rels/document.xml.rels", _DOCX_DOCUMENT_RELS)
    return buffer.getvalue()


def _requirement_paragraph(requirement: dict[str, Any]) -> str:
    return (
        f"This procurement covers {_text(requirement.get('manufacturer'))} "
        f"{_text(requirement.get('manufacturer_part_number'))}, with a required quantity of "
        f"{_text(requirement.get('required_quantity'))} {_text(requirement.get('quantity_unit'))}, "
        f"The budget ceiling is {_money(str(requirement.get('currency') or ''), requirement.get('budget_amount'))}, "
        f"and delivery is required by {_text(requirement.get('delivery_deadline'))}."
    )


def _selection_text(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str,
    assessment: dict[str, Any] | None,
) -> str:
    commercial = _commercial_selection_text(row, recommended, currency)
    status, issues = _policy_assessment_summary(assessment)
    issue_text = ", ".join(issues) or "policy requirements"
    if status == "EXCLUDED":
        return f"Compliance check failed: {issue_text}; excluded from recommendation candidates. Commercial comparison: {commercial}"
    if status == "UNVERIFIED":
        return f"{issue_text} verification is incomplete; this option is not prioritised when verified eligible candidates exist. Commercial comparison: {commercial}"
    if status == "VERIFIED":
        return f"Compliance checks passed; {commercial}"
    return commercial


def _commercial_selection_text(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str
) -> str:
    if row.get("recommended"):
        return "Recommendation basis: ranks first under the current settings."
    failed = row.get("failed_reasons") or []
    pending = row.get("pending_reasons") or []
    reason = (failed or pending or [None])[0]
    if isinstance(reason, dict) and reason.get("message"):
        return str(reason["message"])
    delta = _cost_delta(row, recommended, currency)
    return f"Not selected because the cost difference from the preferred option is {delta}." if delta != "—" else "Not prioritised under the current ranking settings."


def _communication(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str,
    assessment: dict[str, Any] | None,
) -> tuple[str, str]:
    supplier = _text(row.get("supplier_name"))
    status, issues = _policy_assessment_summary(assessment)
    issue_text = ", ".join(issues) or "policy requirements"
    if status == "EXCLUDED":
        return (
            "Correct policy evidence and reassess",
            f"This supplier failed the {issue_text} check and cannot be recommended. Ask {supplier} to submit or correct the relevant evidence, complete the review, and then rerun the analysis.",
        )
    if status == "UNVERIFIED":
        return (
            "Complete policy evidence and review",
            f"{issue_text} verification is incomplete. Ask {supplier} to add valid evidence or confirmation records and complete the review before comparing price and delivery.",
        )
    if row.get("recommended"):
        return (
            "Secure price, delivery, and quotation validity",
            f"Please confirm in writing that the total cost of {row['cost_text']}, estimated arrival date of {_text(row.get('estimated_arrival_date'))}, and current payment terms remain unchanged throughout the quotation validity period, and state whether any new fees apply after order confirmation.",
        )
    reasons = (row.get("failed_reasons") or []) + (row.get("pending_reasons") or [])
    if reasons and isinstance(reasons[0], dict):
        message = _text(reasons[0].get("message"))
        return (
            "Complete or correct information affecting feasibility",
            f"Please clarify “{message}” in writing and ask {supplier} to provide a revised quotation, delivery date, and validity period that meet procurement requirements.",
        )
    delta = _cost_delta(row, recommended, currency)
    return (
        "Narrow the commercial gap with the preferred option",
        f"The cost difference from the preferred option is {delta}. Please confirm whether price, shipping, or payment terms can be improved and submit a complete updated quotation with its validity period.",
    )


_CONTROL_LABELS = {
    "APPROVED_SUPPLIER": "Supplier eligibility",
    "ROHS_COMPLIANCE": "RoHS compliance",
    "AMOUNT_APPROVAL": "Amount approval",
}


def _policy_assessment_summary(
    assessment: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    if not isinstance(assessment, dict):
        return "UNKNOWN", []
    status = str(assessment.get("status") or "")
    eligibility = str(assessment.get("eligibility") or "")
    issues: list[str] = []
    for check in assessment.get("checks") or []:
        if not isinstance(check, dict) or check.get("status") not in {
            "FAIL", "REVIEW_REQUIRED", "NOT_EVALUATED",
        }:
            continue
        label = _CONTROL_LABELS.get(str(check.get("control_code")), "Other policy requirements")
        if label not in issues:
            issues.append(label)
    if eligibility == "EXCLUDED" or status == "NON_COMPLIANT":
        return "EXCLUDED", issues
    if eligibility == "UNVERIFIED" or status in {"REVIEW_REQUIRED", "NOT_EVALUATED"}:
        return "UNVERIFIED", issues
    if eligibility == "VERIFIED" or status == "COMPLIANT":
        return "VERIFIED", issues
    return "UNKNOWN", issues


def _policy_status_text(assessment: dict[str, Any] | None) -> str:
    status, _issues = _policy_assessment_summary(assessment)
    return {
        "EXCLUDED": "Excluded by policy",
        "UNVERIFIED": "Evidence or review pending",
        "VERIFIED": "Verified candidate",
    }.get(status, "Not recorded")


def _payment_text(row: dict[str, Any]) -> str:
    payment = row.get("payment_term") or {}
    if not isinstance(payment, dict):
        return "—"
    return _text(
        payment.get("normalized_text")
        or payment.get("raw_text")
        or (f"{payment['net_days']} days" if payment.get("net_days") is not None else None)
    )


def _cost_delta(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str
) -> str:
    if recommended is None:
        return "—"
    current = _decimal(row.get("total_cost"))
    baseline = _decimal(recommended.get("total_cost"))
    if current is None or baseline is None:
        return "—"
    delta = current - baseline
    if delta == 0:
        return "Baseline"
    sign = "+" if delta > 0 else "−"
    return f"{sign}{_money(currency, abs(delta))}"


def _status_label(value: Any) -> str:
    return {
        "FEASIBLE": "Meets procurement requirements",
        "PENDING": "Pending confirmation",
        "INFEASIBLE": "Does not meet procurement requirements",
    }.get(str(value), _text(value))


def _policy_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "No policy bound"
    return f"{_text(value.get('policy_set_version'))} / {_text(value.get('policy_index_version'))}"


def _compliance_lines(assessment: Any) -> list[str]:
    if not isinstance(assessment, dict):
        return ['Legacy workflow result: the new evidence verification was not run; historical facts are unchanged.']
    if not assessment.get('policy_enabled'):
        return ['Compliance review was not enabled. This is for procurement comparison only and does not indicate compliance or procurement approval.']
    lines = [f"Assessment: {assessment.get('assessment_id')}; task revision: {assessment.get('task_revision')}.",
             'Recommendation strategy: prioritise verified candidates. Verification uses manually confirmed evidence and does not authenticate the evidence itself.']
    status_labels = {'COMPLIANT': 'Applicable checks verified', 'NON_COMPLIANT': 'A recommendation prerequisite is not met',
                     'REVIEW_REQUIRED': 'Insufficient evidence; verification pending', 'NOT_EVALUATED': 'Not evaluated'}
    for row in assessment.get('assessments', []):
        lines.append(f"{row.get('supplier_name') or row.get('quote_id')}: {status_labels.get(row.get('status'), row.get('status'))}.")
        for check in row.get('checks', []):
            lines.append(f"Clause {check.get('clause_id')}: {check.get('status')}; reasons {', '.join(check.get('reason_codes', []))}; "
                         f"evidence {', '.join(check.get('evidence_ids', [])) or 'not provided'}; citations {', '.join(check.get('citation_ids', [])) or 'none'}.")
    for amount in assessment.get('amount_requirements', []):
        approval = ('Approval threshold not reached' if amount.get('triggered') is False else
                    'Approval record verified' if amount.get('approval_confirmed') else 'Approval record missing or insufficient')
        lines.append(f"Amount requirement {amount.get('quote_id')}: {amount.get('currency')} {amount.get('amount')}; "
                     f"threshold {amount.get('threshold')}; status {approval}; "
                     f"next action {amount.get('action') or 'no confirmed triggered action'}. The system records approval facts and does not grant procurement approval.")
    lines.append(f"Missing items: {len(assessment.get('missing_item_ids', []))}.")
    return lines


def _money(currency: str, value: Any) -> str:
    amount = _decimal(value)
    if amount is None:
        return "—"
    return f"{currency} {amount:,.2f}".strip()


def _quantity(value: Any, unit: Any) -> str:
    if value is None:
        return "—"
    labels = {"piece": "pieces", "pieces": "pieces", "unit": "units", "units": "units"}
    return f"{value:,} {labels.get(str(unit), _text(unit))}" if isinstance(value, int) else f"{value} {_text(unit)}"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text(value: Any) -> str:
    return "—" if value is None or value == "" else str(value)


def _md(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _safe_filename(value: str) -> str:
    return "".join(character if character not in '\\/:*?\"<>|' else "_" for character in value)


def _w_paragraph(
    text: str, *, style: str | None = None, bold: bool = False, color: str | None = None
) -> str:
    properties = f'<w:pStyle w:val="{escape(style)}"/>' if style else ""
    run_properties = ""
    if bold or color:
        run_properties = "<w:rPr>" + ("<w:b/>" if bold else "") + (
            f'<w:color w:val="{escape(color)}"/>' if color else ""
        ) + "</w:rPr>"
    return (
        f"<w:p><w:pPr>{properties}</w:pPr><w:r>{run_properties}"
        f'<w:t xml:space="preserve">{escape(_text(text))}</w:t></w:r></w:p>'
    )


def _w_heading(text: str, *, level: int = 1) -> str:
    return _w_paragraph(text, style=f"Heading{level}")


def _w_table(rows: list[list[str]]) -> str:
    table_rows: list[str] = []
    for row_index, row in enumerate(rows):
        cells = []
        for value in row:
            run = f"<w:r>{'<w:rPr><w:b/></w:rPr>' if row_index == 0 else ''}<w:t>{escape(_text(value))}</w:t></w:r>"
            cells.append(f"<w:tc><w:tcPr/><w:p>{run}</w:p></w:tc>")
        table_rows.append("<w:tr>" + "".join(cells) + "</w:tr>")
    borders = "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="D9E0EA"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    return (
        '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
        f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr>"
        + "".join(table_rows) + "</w:tbl>"
    )


_DOCX_CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

_DOCX_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

_DOCX_DOCUMENT_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

_DOCX_STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/><w:sz w:val="21"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="260" w:after="100"/></w:pPr><w:rPr><w:b/><w:color w:val="245EEA"/><w:sz w:val="28"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="180" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="23"/></w:rPr></w:style>
</w:styles>'''
