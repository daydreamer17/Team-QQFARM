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
        f"{report['scenario']}_采购总结_第{report['task_revision']}版"
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
        or "采购任务"
    )
    return {
        "title": f"{scenario} 采购总结",
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
        f"> 任务版本：第 {report['task_revision']} 版  ",
        f"> 结果版本：`{report['result_id']}`  ",
        f"> 总结编号：`{report['summary_id']}`  ",
        f"> 生成时间：{report['generated_at']}",
        "",
        "## 1. 执行摘要",
        "",
        report["overview"],
        "",
    ]
    if recommended:
        lines.extend([
            f"**建议优先推进：{recommended.get('supplier_name', '—')}**  ",
            f"确认总成本：{recommended['cost_text']}；预计到货：{recommended.get('estimated_arrival_date') or '待确认'}。",
            "",
        ])
    lines.extend([
        "## 2. 采购需求",
        "",
        _requirement_paragraph(requirement),
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 制造商 | {_md(requirement.get('manufacturer'))} |",
        f"| 料号 | {_md(requirement.get('manufacturer_part_number'))} |",
        f"| 数量 | {_md(requirement.get('required_quantity'))} {_md(requirement.get('quantity_unit'))} |",
        f"| 封装 / 版本 | {_md(requirement.get('package'))} / {_md(requirement.get('revision'))} |",
        f"| 预算 | {_money(report['currency'], requirement.get('budget_amount'))} |",
        f"| 交付截止 | {_md(requirement.get('delivery_deadline'))} |",
        f"| 交付地点 | {_md(requirement.get('delivery_location'))} |",
        "",
        "## 3. 报价、成本与关键取舍",
        "",
        "| 指标 | " + " | ".join(_md(row.get("supplier_name")) for row in report["quotes"]) + " |",
        "| --- | " + " | ".join("---" for _ in report["quotes"]) + " |",
    ])
    matrix_rows = [
        ("制度资格", lambda row: row["policy_status_text"]),
        ("确认总成本", lambda row: row["cost_text"] + ("（推荐）" if row["recommended"] else "")),
        ("相对首选差额", lambda row: row["cost_delta"]),
        ("实际采购量", lambda row: row["quantity_text"]),
        ("预计到货", lambda row: row.get("estimated_arrival_date") or "待确认"),
        ("付款条件", lambda row: row["payment_text"]),
        ("可行性", lambda row: row["status_label"]),
        ("本次取舍", lambda row: row["selection_text"]),
    ]
    for label, getter in matrix_rows:
        lines.append(f"| {label} | " + " | ".join(_md(getter(row)) for row in report["quotes"]) + " |")
    lines.extend(["", "### 成本位置", ""])
    finite_costs = [_decimal(row.get("total_cost")) for row in report["quotes"]]
    budget = _decimal(requirement.get("budget_amount"))
    maximum = max([value for value in [*finite_costs, budget] if value is not None] or [Decimal("1")])
    for row, cost in zip(report["quotes"], finite_costs, strict=True):
        if cost is not None:
            bars = max(1, min(20, int((cost / maximum) * 20)))
            lines.append(f"- {row.get('supplier_name')}: {'█' * bars}{'░' * (20 - bars)} {row['cost_text']}")
    lines.extend(["", "### AI 分析补充", ""])
    for section in report["narrative_sections"]:
        lines.extend([f"**{section.get('heading', '分析')}**", "", str(section.get("text") or ""), ""])
    lines.extend(["## 4. 选择说明与建议沟通内容", ""])
    for row in report["quotes"]:
        lines.extend([
            f"### {row.get('supplier_name') or '供应商'}",
            "",
            f"**本次取舍：** {row['selection_text']}  ",
            f"**沟通目标：** {row['communication_goal']}  ",
            f"**建议沟通：** {row['communication_text']}",
            "",
        ])
    lines.extend([
        "## 5. 风险、制度与批准门槛",
        "",
        f"- 报价完整性：{sum(row.get('status') == 'PENDING' for row in report['quotes'])} 份待确认。",
        f"- 制度绑定：{_policy_text(report.get('policy_binding'))}。",
        "- 批准状态：尚未批准；本报告不具备下单、付款或采购批准权限。",
        "",
        *_compliance_lines(report.get('policy_compliance')),
        "",
        "## 6. 建议行动与留档",
        "",
        "1. 完成供应商沟通，并把回复作为新版本输入。",
        "2. 关闭制度复核项，保留条款、例外和人工确认记录。",
        "3. 附上报价原件、冻结比较结果和本报告，提交授权人员批准。",
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
        f"任务版本：第 {report['task_revision']} 版    结果：{report['result_id']}    生成时间：{report['generated_at']}",
        color="6B778C",
    ))
    body.append(_w_heading("1. 执行摘要"))
    body.append(_w_paragraph(report["overview"]))
    if recommended:
        body.append(_w_paragraph(
            f"建议优先推进 {recommended.get('supplier_name', '—')}。确认总成本 {recommended['cost_text']}，预计到货 {recommended.get('estimated_arrival_date') or '待确认'}。",
            bold=True,
            color="137A54",
        ))
    body.append(_w_heading("2. 采购需求"))
    body.append(_w_paragraph(_requirement_paragraph(requirement)))
    body.append(_w_table([
        ["项目", "内容"],
        ["制造商", _text(requirement.get("manufacturer"))],
        ["料号", _text(requirement.get("manufacturer_part_number"))],
        ["数量", f"{_text(requirement.get('required_quantity'))} {_text(requirement.get('quantity_unit'))}"],
        ["封装 / 版本", f"{_text(requirement.get('package'))} / {_text(requirement.get('revision'))}"],
        ["预算", _money(report["currency"], requirement.get("budget_amount"))],
        ["交付截止", _text(requirement.get("delivery_deadline"))],
        ["交付地点", _text(requirement.get("delivery_location"))],
    ]))
    body.append(_w_heading("3. 报价、成本与关键取舍"))
    matrix = [["指标", *[_text(row.get("supplier_name")) for row in report["quotes"]]]]
    for label, key in [
        ("制度资格", "policy_status_text"),
        ("确认总成本", "cost_text"),
        ("相对首选差额", "cost_delta"),
        ("实际采购量", "quantity_text"),
        ("付款条件", "payment_text"),
        ("可行性", "status_label"),
        ("本次取舍", "selection_text"),
    ]:
        matrix.append([label, *[_text(row.get(key)) for row in report["quotes"]]])
    matrix.insert(4, ["预计到货", *[_text(row.get("estimated_arrival_date")) for row in report["quotes"]]])
    body.append(_w_table(matrix))
    body.append(_w_heading("成本位置", level=2))
    finite_costs = [_decimal(row.get("total_cost")) for row in report["quotes"]]
    budget = _decimal(requirement.get("budget_amount"))
    maximum = max([value for value in [*finite_costs, budget] if value is not None] or [Decimal("1")])
    for row, cost in zip(report["quotes"], finite_costs, strict=True):
        if cost is not None:
            bars = max(1, min(20, int((cost / maximum) * 20)))
            body.append(_w_paragraph(
                f"{row.get('supplier_name')}: {'■' * bars}{'□' * (20 - bars)} {row['cost_text']}"
            ))
    body.append(_w_heading("AI 分析补充", level=2))
    for section in report["narrative_sections"]:
        body.append(_w_paragraph(_text(section.get("heading")), bold=True))
        body.append(_w_paragraph(_text(section.get("text"))))
    body.append(_w_heading("4. 选择说明与建议沟通内容"))
    for row in report["quotes"]:
        body.append(_w_heading(_text(row.get("supplier_name")), level=2))
        body.append(_w_paragraph(f"本次取舍：{row['selection_text']}"))
        body.append(_w_paragraph(f"沟通目标：{row['communication_goal']}", bold=True))
        body.append(_w_paragraph(f"建议沟通：{row['communication_text']}"))
    body.append(_w_heading("5. 风险、制度与批准门槛"))
    body.append(_w_table([
        ["项目", "状态", "说明"],
        ["报价完整性", f"{sum(row.get('status') == 'PENDING' for row in report['quotes'])} 份待确认", "未知值不会自动按零或合格处理。"],
        ["制度绑定", _policy_text(report.get("policy_binding")), "制度引用不替代人工适用性判断。"],
        ["批准状态", "尚未批准", "本报告不具备下单、付款或采购批准权限。"],
    ]))
    for line in _compliance_lines(report.get('policy_compliance')):
        body.append(_w_paragraph(line))
    body.append(_w_heading("6. 建议行动与留档"))
    for text in [
        "1. 完成供应商沟通，并把回复作为新版本输入。",
        "2. 关闭制度复核项，保留条款、例外和人工确认记录。",
        "3. 附上报价原件、冻结比较结果和本报告，提交授权人员批准。",
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
        f"本次采购对象为 {_text(requirement.get('manufacturer'))} 的 "
        f"{_text(requirement.get('manufacturer_part_number'))}，需求数量为 "
        f"{_text(requirement.get('required_quantity'))} {_text(requirement.get('quantity_unit'))}，"
        f"预算上限为 {_money(str(requirement.get('currency') or ''), requirement.get('budget_amount'))}，"
        f"并要求在 {_text(requirement.get('delivery_deadline'))} 前完成交付。"
    )


def _selection_text(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str,
    assessment: dict[str, Any] | None,
) -> str:
    commercial = _commercial_selection_text(row, recommended, currency)
    status, issues = _policy_assessment_summary(assessment)
    issue_text = "、".join(issues) or "制度要求"
    if status == "EXCLUDED":
        return f"制度检查不通过：{issue_text}；已从推荐候选中排除。商业比较：{commercial}"
    if status == "UNVERIFIED":
        return f"{issue_text}尚未完成核验；有已核验合格候选时不优先推荐。商业比较：{commercial}"
    if status == "VERIFIED":
        return f"制度检查已通过；{commercial}"
    return commercial


def _commercial_selection_text(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str
) -> str:
    if row.get("recommended"):
        return "推荐依据：当前排序下优先。"
    failed = row.get("failed_reasons") or []
    pending = row.get("pending_reasons") or []
    reason = (failed or pending or [None])[0]
    if isinstance(reason, dict) and reason.get("message"):
        return str(reason["message"])
    delta = _cost_delta(row, recommended, currency)
    return f"未选原因：相对首选成本差额为 {delta}。" if delta != "—" else "当前排序下未优先。"


def _communication(
    row: dict[str, Any], recommended: dict[str, Any] | None, currency: str,
    assessment: dict[str, Any] | None,
) -> tuple[str, str]:
    supplier = _text(row.get("supplier_name"))
    status, issues = _policy_assessment_summary(assessment)
    issue_text = "、".join(issues) or "制度要求"
    if status == "EXCLUDED":
        return (
            "补正制度证明后重新评估",
            f"当前因{issue_text}检查不通过，不能进入推荐。请由 {supplier} 补交或更正对应证明，完成复核后再重新分析。",
        )
    if status == "UNVERIFIED":
        return (
            "补齐制度材料并完成复核",
            f"当前{issue_text}尚未完成核验。请由 {supplier} 补齐有效材料或确认记录，完成复核后再比较价格与交期。",
        )
    if row.get("recommended"):
        return (
            "锁定价格、交期和报价有效期",
            f"请书面确认 {row['cost_text']} 的总成本、{_text(row.get('estimated_arrival_date'))} 的预计到货日期及当前付款条件在报价有效期内保持不变，并说明订单确认后是否存在任何新增费用。",
        )
    reasons = (row.get("failed_reasons") or []) + (row.get("pending_reasons") or [])
    if reasons and isinstance(reasons[0], dict):
        message = _text(reasons[0].get("message"))
        return (
            "补齐或修正影响可行性的信息",
            f"请书面澄清“{message}”，并由 {supplier} 提供可满足采购要求的修订报价、交付日期及有效期。",
        )
    delta = _cost_delta(row, recommended, currency)
    return (
        "缩小与首选方案的商务差距",
        f"当前方案与首选方案的成本差额为 {delta}。请确认是否可优化价格、运费或付款条件，并提交更新后的完整报价与有效期。",
    )


_CONTROL_LABELS = {
    "APPROVED_SUPPLIER": "供应商资质",
    "ROHS_COMPLIANCE": "RoHS 合规",
    "AMOUNT_APPROVAL": "金额审批",
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
        label = _CONTROL_LABELS.get(str(check.get("control_code")), "其他制度要求")
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
        "EXCLUDED": "制度排除",
        "UNVERIFIED": "待补充或复核",
        "VERIFIED": "已核验候选",
    }.get(status, "未记录")


def _payment_text(row: dict[str, Any]) -> str:
    payment = row.get("payment_term") or {}
    if not isinstance(payment, dict):
        return "—"
    return _text(
        payment.get("normalized_text")
        or payment.get("raw_text")
        or (f"{payment['net_days']} 天" if payment.get("net_days") is not None else None)
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
        return "基准"
    sign = "+" if delta > 0 else "−"
    return f"{sign}{_money(currency, abs(delta))}"


def _status_label(value: Any) -> str:
    return {
        "FEASIBLE": "符合采购要求",
        "PENDING": "待确认",
        "INFEASIBLE": "不符合采购要求",
    }.get(str(value), _text(value))


def _policy_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "未绑定制度"
    return f"{_text(value.get('policy_set_version'))} / {_text(value.get('policy_index_version'))}"


def _compliance_lines(assessment: Any) -> list[str]:
    if not isinstance(assessment, dict):
        return ['旧流程结果：未执行新的材料核验，历史事实保持原样。']
    if not assessment.get('policy_enabled'):
        return ['制度检查：本次未启用；仅作采购比较，不代表合规或采购批准。']
    lines = [f"核验单：{assessment.get('assessment_id')}；任务版本：{assessment.get('task_revision')}。",
             '推荐策略：已核验候选优先；核验基于人工确认材料，不鉴定材料真伪。']
    status_labels = {'COMPLIANT': '本次适用检查已核验', 'NON_COMPLIANT': '存在不满足的推荐前条件',
                     'REVIEW_REQUIRED': '资料不足，待核验', 'NOT_EVALUATED': '未评估'}
    for row in assessment.get('assessments', []):
        lines.append(f"{row.get('supplier_name') or row.get('quote_id')}：{status_labels.get(row.get('status'), row.get('status'))}。")
        for check in row.get('checks', []):
            lines.append(f"条款 {check.get('clause_id')}：{check.get('status')}；原因 {', '.join(check.get('reason_codes', []))}；"
                         f"材料 {', '.join(check.get('evidence_ids', [])) or '尚未提供'}；引用 {', '.join(check.get('citation_ids', [])) or '无'}。")
    for amount in assessment.get('amount_requirements', []):
        approval = ('未达到审批门槛' if amount.get('triggered') is False else
                    '审批记录已核对' if amount.get('approval_confirmed') else '审批记录待补充或不满足')
        lines.append(f"金额要求 {amount.get('quote_id')}：{amount.get('currency')} {amount.get('amount')}；"
                     f"门槛 {amount.get('threshold')}；状态 {approval}；"
                     f"后续动作 {amount.get('action') or '无已确认触发动作'}。系统记录审批事实，不代替采购审批。")
    lines.append(f"待补充项：{len(assessment.get('missing_item_ids', []))} 项。")
    return lines


def _money(currency: str, value: Any) -> str:
    amount = _decimal(value)
    if amount is None:
        return "—"
    return f"{currency} {amount:,.2f}".strip()


def _quantity(value: Any, unit: Any) -> str:
    if value is None:
        return "—"
    labels = {"piece": "件", "pieces": "件", "unit": "件", "units": "件"}
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
