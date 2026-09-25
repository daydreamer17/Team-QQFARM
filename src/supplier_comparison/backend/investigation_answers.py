"""Deterministic rendering of verified procurement investigation facts.

The renderer operates on general business intents and normalized tool output.
It deliberately does not contain supplier-specific or benchmark-question
answers. The Agent remains responsible for planning and collecting evidence;
this module only turns the resulting frozen audit record into a concise answer.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .response_language import conversation_response_language


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "N": 4}
_CRITERION_ZH = {
    "LOWEST_CONFIRMED_TOTAL_COST": "最低确认总成本",
    "FASTEST_CONFIRMED_DELIVERY": "最快确认到货",
    "LONGEST_CONFIRMED_PAYMENT_TERM": "最长确认付款账期",
    "HIGHEST_SUPPLIER_PERFORMANCE": "最高综合供应商表现",
    "HIGHEST_HISTORICAL_ON_TIME_RATE": "最高历史准时率",
    "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "最低历史拒收率",
}
_CRITERION_EN = {
    "LOWEST_CONFIRMED_TOTAL_COST": "lowest confirmed total cost",
    "FASTEST_CONFIRMED_DELIVERY": "fastest confirmed delivery",
    "LONGEST_CONFIRMED_PAYMENT_TERM": "longest confirmed payment term",
    "HIGHEST_SUPPLIER_PERFORMANCE": "highest overall supplier performance",
    "HIGHEST_HISTORICAL_ON_TIME_RATE": "highest historical on-time rate",
    "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "lowest historical rejection rate",
}


def _supplier_name_mentioned(name: str, text: str) -> bool:
    normalized_name = name.casefold().strip()
    normalized_text = text.casefold()
    if normalized_name and normalized_name in normalized_text:
        return True
    generic = {"components", "circuits", "company", "limited", "ltd", "supplier"}
    distinctive = [
        token for token in re.findall(r"[a-z0-9]+", normalized_name)
        if len(token) >= 4 and token not in generic
    ]
    return bool(distinctive and distinctive[0] in normalized_text)


def compose_investigation_answer(
    context: dict[str, Any], *, reference_id: str, record: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compose an answer from a completed audit record and a general intent profile."""

    language = conversation_response_language(context)
    zh = language == "zh"
    question = next((
        str(item.get("content") or "")
        for item in reversed(context.get("recent_messages") or [])
        if isinstance(item, dict) and item.get("role") == "USER"
    ), "")
    lowered = question.casefold()

    def results_for(tool_name: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            result = observation.get("result") or {}
            data = result.get("data") or {}
            if (
                result.get("tool_name") == tool_name
                and result.get("status") == "OK"
                and isinstance(data, dict)
            ):
                results.append(data)
        return results

    overview = next(iter(results_for("read_decision_overview")), {})
    rows = {
        str(item.get("quote_id")): item
        for item in overview.get("suppliers") or []
        if isinstance(item, dict) and item.get("quote_id")
    }
    if not rows:
        return None
    names = {
        quote_id: str(item.get("supplier_name") or quote_id)
        for quote_id, item in rows.items()
    }
    recommended_ids = [
        str(quote_id) for quote_id in overview.get("recommended_quote_ids") or []
        if str(quote_id) in rows
    ]
    recommended_id = recommended_ids[0] if len(recommended_ids) == 1 else None

    plan = overview.get("investigation_plan") or {}
    goal = str(plan.get("analysis_goal") or "EVIDENCE_REVIEW")
    dimensions = {str(value) for value in plan.get("dimensions") or []}
    criterion = str(overview.get("ranking_preference") or plan.get("criterion") or "")

    quote_fields: dict[str, dict[str, Any]] = {}
    for data in results_for("inspect_quote_evidence"):
        quote_id = str(data.get("quote_id") or "")
        if not quote_id:
            continue
        quote_fields[quote_id] = {
            str(field.get("field_name")): field.get("normalized_value")
            for field in data.get("fields") or []
            if isinstance(field, dict) and field.get("field_name")
        }

    histories: dict[str, dict[str, Any]] = {}
    for data in results_for("inspect_supplier_history"):
        quote_id = str(data.get("quote_id") or "")
        supplier = data.get("supplier") or {}
        snapshot = supplier.get("history_snapshot") if isinstance(supplier, dict) else None
        if quote_id and isinstance(snapshot, dict):
            histories[quote_id] = snapshot

    policy_data = next(iter(results_for("inspect_policy_evidence")), {})
    compliance = policy_data.get("compliance") or {}
    assessments = {
        str(item.get("quote_id")): item
        for item in compliance.get("assessments") or []
        if isinstance(item, dict) and item.get("quote_id")
    }
    checks = {
        quote_id: {
            str(check.get("control_code")): check
            for check in item.get("checks") or []
            if isinstance(check, dict) and check.get("control_code")
        }
        for quote_id, item in assessments.items()
    }
    retrievals = [
        item for item in policy_data.get("retrievals") or [] if isinstance(item, dict)
    ]
    brief = next(iter(reversed(results_for("compile_decision_brief"))), {})

    explicit_ids = [
        quote_id for quote_id, name in names.items()
        if _supplier_name_mentioned(name, lowered)
    ]
    checked_ids = [
        str(value) for value in brief.get("checked_quote_ids") or []
        if str(value) in rows
    ]
    relevant_ids = list(dict.fromkeys(
        explicit_ids
        + ([recommended_id] if recommended_id else [])
        + checked_ids
        + list(histories)
        + list(assessments)
    ))

    requirement_reference, requirement = _requirement_reference(context)
    result_reference = (
        f"RESULT:{context['result_id']}" if context.get("result_id") else next((
            str(reference_id)
            for reference_id in context.get("allowed_reference_ids") or []
            if str(reference_id).startswith("RESULT:")
        ), None)
    )
    reference_ids = [reference_id]

    def citation(*extra: str | None) -> str:
        ids = list(dict.fromkeys([reference_id, *(item for item in extra if item)]))
        for item in ids:
            if item not in reference_ids:
                reference_ids.append(item)
        # Semicolons are sentence boundaries in the grounding validator.
        # Keep multiple source IDs in one sentence separated by a comma so
        # every cited source remains attached to the same factual claim.
        return ("，" if zh else ", ").join(ids)

    def sentence(text: str, *extra: str | None) -> str:
        # One citation belongs to one sentence. Semicolons would split it during
        # persistence validation, so grouped comparisons use commas instead.
        clean = text.replace("；", "，").replace("; ", ", ").rstrip("。.!；; ")
        if zh:
            return f"{clean}（{citation(*extra)}）。"
        return f"{clean} ({citation(*extra)})."

    def possessive(name: str) -> str:
        return f"{name}'" if name.endswith(("s", "S")) else f"{name}'s"

    def label(name: str, details: str) -> str:
        return f"{name}：{details}" if zh else f"{name}: {details}"

    def money(value: Any) -> str | None:
        try:
            return f"SGD {Decimal(str(value)):,.2f}"
        except (InvalidOperation, TypeError, ValueError):
            return None

    def percentage(value: Any) -> str | None:
        try:
            return f"{Decimal(str(value)) * Decimal('100'):.2f}%"
        except (InvalidOperation, TypeError, ValueError):
            return None

    def history_parts(quote_id: str) -> list[str]:
        history = histories.get(quote_id) or {}
        parts: list[str] = []
        if history.get("overall_grade"):
            parts.append(
                f"评级 {history['overall_grade']}"
                if zh else f"grade {history['overall_grade']}"
            )
        on_time = percentage((history.get("on_time") or {}).get("rate"))
        rejected = percentage((history.get("rejected_lines") or {}).get("rate"))
        if on_time:
            parts.append(f"历史准时率 {on_time}" if zh else f"historical on-time rate {on_time}")
        if rejected:
            parts.append(f"拒收率 {rejected}" if zh else f"rejection rate {rejected}")
        return parts

    def status(quote_id: str, control: str) -> str:
        return str((checks.get(quote_id) or {}).get(control, {}).get("status") or "NOT_EVALUATED")

    def quote_summary(quote_id: str) -> str:
        row = rows[quote_id]
        facts: list[str] = []
        total = money(row.get("total_cost"))
        if total:
            facts.append(f"确认总成本 {total}" if zh else f"confirmed total cost {total}")
        if row.get("estimated_arrival_date"):
            facts.append(
                f"预计到货 {row['estimated_arrival_date']}"
                if zh else f"estimated arrival {row['estimated_arrival_date']}"
            )
        return "、".join(facts) if zh else ", ".join(facts)

    sentences: list[str] = []

    if goal == "RECOMMENDATION_EXPLANATION":
        asserted = explicit_ids[0] if explicit_ids else None
        if asserted and recommended_id and asserted != recommended_id:
            sentences.append(sentence(
                f"先纠正前提：当前正式推荐是 {names[recommended_id]}，不是 {names[asserted]}"
                if zh else
                f"First, the premise is incorrect: the current official recommendation is {names[recommended_id]}, not {names[asserted]}"
            ))
        elif recommended_id:
            sentences.append(sentence(
                f"当前正式推荐是 {names[recommended_id]}"
                if zh else f"The current official recommendation is {names[recommended_id]}"
            ))

        criterion_label = (_CRITERION_ZH if zh else _CRITERION_EN).get(criterion, criterion)
        if recommended_id and criterion_label:
            fields = quote_fields.get(recommended_id) or {}
            detail = ""
            if criterion == "LONGEST_CONFIRMED_PAYMENT_TERM" and fields.get("payment_terms"):
                detail = (
                    f"，报价原文付款条件为 {fields['payment_terms']}"
                    if zh else f", with source payment terms of {fields['payment_terms']}"
                )
            sentences.append(sentence(
                f"推荐依据是“{criterion_label}”{detail}"
                if zh else f"The recommendation follows {criterion_label}{detail}"
            ))

        subject_ids = explicit_ids or ([recommended_id] if recommended_id else relevant_ids[:1])
        for quote_id in subject_ids:
            if quote_id not in rows:
                continue
            parts = [quote_summary(quote_id)] if quote_summary(quote_id) else []
            if "HISTORY" in dimensions:
                parts.extend(history_parts(quote_id))
            if "COMPLIANCE" in dimensions and quote_id in assessments:
                parts.extend([
                    f"供应商准入 {status(quote_id, 'APPROVED_SUPPLIER')}" if zh else f"supplier eligibility {status(quote_id, 'APPROVED_SUPPLIER')}",
                    f"RoHS {status(quote_id, 'ROHS_COMPLIANCE')}",
                    f"金额审批 {status(quote_id, 'AMOUNT_APPROVAL')}" if zh else f"amount approval {status(quote_id, 'AMOUNT_APPROVAL')}",
                ])
            if parts:
                sentences.append(sentence(label(names[quote_id], ("、" if zh else ", ").join(parts))))

        if "HISTORY" in dimensions and recommended_id and not explicit_ids:
            sentences.append(sentence(
                f"这些历史风险需要保留披露，但当前主要排序依据是“{criterion_label}”，因此不会单独改变现有推荐"
                if zh else
                f"These historical risks remain disclosed, but the primary ranking basis is {criterion_label}, so they do not by themselves change the current recommendation"
            ))

        # A named non-recommended supplier needs the actual blocking or ranking
        # distinction, not a generic statement that it merely ranked lower.
        if asserted and asserted != recommended_id and asserted in assessments:
            assessment_status = str(assessments[asserted].get("status") or "NOT_EVALUATED")
            failed_controls = [
                control for control, check in checks.get(asserted, {}).items()
                if check.get("status") == "FAIL"
            ]
            if failed_controls:
                sentences.append(sentence(
                    f"{names[asserted]} 的制度结论为 {assessment_status}，失败项为 {'、'.join(failed_controls)}，因此不具备推荐资格"
                    if zh else
                    f"{possessive(names[asserted])} policy status is {assessment_status}, with failed controls {', '.join(failed_controls)}, so it is not recommendation-eligible"
                ))

    elif goal == "RANKING_COMPARISON":
        if "DELIVERY" in dimensions:
            ranked = sorted(
                (quote_id for quote_id in rows if rows[quote_id].get("estimated_arrival_date")),
                key=lambda quote_id: (
                    str(rows[quote_id]["estimated_arrival_date"]),
                    Decimal(str(rows[quote_id].get("total_cost") or "Infinity")),
                ),
            )
            count = 2 if plan.get("requests_two_candidates") else 1
            selected = ranked[:count]
            sentences.append(sentence(
                ("按预计到货从早到晚，优先比较：" if zh else "Ordered by estimated arrival, the leading candidates are: ")
                + ("，" if zh else "; ").join(
                    (
                        f"{names[quote_id]}（{rows[quote_id]['estimated_arrival_date']}，{money(rows[quote_id].get('total_cost'))}）"
                        if zh else
                        f"{names[quote_id]} ({rows[quote_id]['estimated_arrival_date']}, {money(rows[quote_id].get('total_cost'))})"
                    )
                    for quote_id in selected
                )
            ))
            for quote_id in selected:
                parts = history_parts(quote_id)
                if parts:
                    sentences.append(sentence(label(names[quote_id], ("、" if zh else ", ").join(parts))))
        elif "HISTORY" in dimensions and histories:
            ranked = sorted(histories, key=lambda quote_id: _history_rank(histories[quote_id]))
            best = ranked[0]
            sentences.append(sentence(
                f"综合评级、准时率和拒收率，{names.get(best, best)} 的历史表现最好：{'、'.join(history_parts(best))}"
                if zh else
                f"Considering grade, on-time rate, and rejection rate, {names.get(best, best)} ranks best: {', '.join(history_parts(best))}"
            ))
            remaining = [quote_id for quote_id in ranked[1:] if history_parts(quote_id)]
            if remaining:
                sentences.append(sentence(
                    ("其他供应商：" if zh else "Other suppliers: ")
                    + ("，" if zh else "; ").join(
                        (
                            f"{names.get(quote_id, quote_id)}（{'、'.join(history_parts(quote_id))}）"
                            if zh else
                            f"{names.get(quote_id, quote_id)} ({', '.join(history_parts(quote_id))})"
                        )
                        for quote_id in remaining
                    )
                ))
        elif "COST" in dimensions:
            ranked = sorted(
                (quote_id for quote_id in rows if rows[quote_id].get("total_cost") is not None),
                key=lambda quote_id: Decimal(str(rows[quote_id]["total_cost"])),
            )
            if ranked:
                cheapest = ranked[0]
                sentences.append(sentence(
                    f"{names[cheapest]} 的确认总成本最低，为 {money(rows[cheapest]['total_cost'])}"
                    if zh else
                    f"{names[cheapest]} has the lowest confirmed total cost at {money(rows[cheapest]['total_cost'])}"
                ))
                detail_id = explicit_ids[0] if explicit_ids else cheapest
                detail = _cost_detail(
                    quote_fields.get(detail_id) or {}, requirement=requirement,
                    language=language,
                )
                if detail:
                    sentences.append(sentence(
                        f"{names[detail_id]} 的费用依据：{detail}"
                        if zh else f"{possessive(names[detail_id])} cost basis: {detail}",
                        requirement_reference,
                    ))

    elif goal == "DELTA_COMPARISON":
        comparison_ids = list(dict.fromkeys(explicit_ids + checked_ids))
        if len(comparison_ids) < 2 and explicit_ids:
            available = [quote_id for quote_id in rows if rows[quote_id].get("total_cost") is not None]
            if available:
                cheapest = min(available, key=lambda quote_id: Decimal(str(rows[quote_id]["total_cost"])))
                comparison_ids = list(dict.fromkeys(explicit_ids + [cheapest]))
        if len(comparison_ids) >= 2:
            first, second = comparison_ids[:2]
            first_cost = Decimal(str(rows[first]["total_cost"]))
            second_cost = Decimal(str(rows[second]["total_cost"]))
            delta = abs(first_cost - second_cost)
            first_date = date.fromisoformat(str(rows[first]["estimated_arrival_date"]))
            second_date = date.fromisoformat(str(rows[second]["estimated_arrival_date"]))
            days = abs((first_date - second_date).days)
            higher = first if first_cost >= second_cost else second
            earlier = first if first_date <= second_date else second
            sentences.append(sentence(
                f"{names[higher]} 比另一方案贵 {money(delta)}，{names[earlier]} 预计早 {days} 天到货"
                if zh else
                f"{names[higher]} costs {money(delta)} more, while {names[earlier]} is estimated to arrive {days} days earlier",
            ))
            for quote_id in (first, second):
                parts = history_parts(quote_id)
                if parts:
                    sentences.append(sentence(label(names[quote_id], ("、" if zh else ", ").join(parts))))
            if first in histories and second in histories:
                stronger = min((first, second), key=lambda quote_id: _history_rank(histories[quote_id]))
                sentences.append(sentence(
                    f"综合评级、准时率和拒收率，{names[stronger]} 在这两家中的历史履约风险相对较低，但仍需保留其自身已确认风险"
                    if zh else
                    f"Considering grade, on-time rate, and rejection rate, {names[stronger]} has the lower historical fulfilment risk of the two, while its own verified risks still remain relevant"
                ))

    elif goal == "COMPLIANCE_REVIEW":
        non_compliant = [
            quote_id for quote_id, item in assessments.items()
            if item.get("status") == "NON_COMPLIANT"
        ]
        pending = [
            (quote_id, control)
            for quote_id, by_control in checks.items()
            for control, item in by_control.items()
            if item.get("status") == "REVIEW_REQUIRED"
        ]
        asks_current = "当前" in question or "current" in lowered or "recommended supplier" in lowered
        asks_failures = any(token in lowered for token in (
            "未通过", "不能推荐", "制度失败", "failed policy", "fail policy",
            "cannot be recommended", "ineligible", "excluded by policy",
        ))
        subject_ids = explicit_ids or (
            [recommended_id] if recommended_id and asks_current else
            ([] if asks_failures else list(assessments))
        )
        include_portfolio_findings = not explicit_ids and not asks_current
        for quote_id in subject_ids:
            if quote_id not in assessments:
                continue
            parts = [
                f"供应商准入 {status(quote_id, 'APPROVED_SUPPLIER')}" if zh else f"supplier eligibility {status(quote_id, 'APPROVED_SUPPLIER')}",
                f"RoHS {status(quote_id, 'ROHS_COMPLIANCE')}",
                f"金额审批 {status(quote_id, 'AMOUNT_APPROVAL')}" if zh else f"amount approval {status(quote_id, 'AMOUNT_APPROVAL')}",
            ]
            supplier_name = names.get(quote_id, quote_id)
            sentences.append(sentence(
                f"{supplier_name} 的制度结论为 {assessments[quote_id].get('status')}：" + "、".join(parts)
                if zh else
                f"{possessive(supplier_name)} policy status is {assessments[quote_id].get('status')}: " + ", ".join(parts)
            ))
        if non_compliant and include_portfolio_findings:
            details = []
            for quote_id in non_compliant:
                failed = [control for control, item in checks.get(quote_id, {}).items() if item.get("status") == "FAIL"]
                details.append(
                    f"{names.get(quote_id, quote_id)}（{', '.join(failed)} FAIL）"
                    if zh else
                    f"{names.get(quote_id, quote_id)} ({', '.join(failed)} FAIL)"
                )
            sentences.append(sentence(
                "当前因制度失败不能推荐：" + "、".join(details)
                if zh else "Currently excluded by policy: " + ", ".join(details)
            ))
        if pending and include_portfolio_findings:
            descriptions = [f"{names.get(quote_id, quote_id)} {control}" for quote_id, control in pending]
            sentences.append(sentence(
                "仍需人工处理但不等同于当前制度淘汰：" + "、".join(descriptions)
                if zh else "Still requiring manual handling, but not equivalent to current policy exclusion: " + ", ".join(descriptions)
            ))
        rule_texts = [
            str(citation.get("text"))
            for retrieval in retrievals
            for citation in retrieval.get("citations") or []
            if isinstance(citation, dict) and citation.get("text")
        ]
        if rule_texts and (subject_ids or asks_failures):
            summary = _policy_rule_summary(rule_texts, language=language)
            if summary:
                sentences.append(sentence(summary))

    elif goal == "CONFLICT_REVIEW":
        unresolved = [item for item in brief.get("unresolved_items") or [] if isinstance(item, dict)]
        conflicts = [item for item in unresolved if "CONFLICT" in str(item.get("type") or item.get("reason_code") or "")]
        if conflicts:
            descriptions = [
                f"{item.get('supplier_name') or names.get(str(item.get('quote_id')), 'supplier')} {item.get('control_code') or item.get('type')}"
                for item in conflicts
            ]
            sentences.append(sentence(
                "发现待解决冲突：" + "、".join(descriptions)
                if zh else "Unresolved conflicts found: " + ", ".join(descriptions)
            ))
        else:
            sentences.append(sentence(
                "在本次实际核对的冻结报价、历史记录和当前生效证明中，没有发现同一事实的直接冲突"
                if zh else
                "No direct conflict on the same fact was found in the frozen quotations, history records, and currently effective evidence actually checked"
            ))
        if recommended_id:
            fields = quote_fields.get(recommended_id) or {}
            if criterion == "LONGEST_CONFIRMED_PAYMENT_TERM" and fields.get("payment_terms"):
                sentences.append(sentence(
                    f"当前推荐 {names[recommended_id]} 与排序依据一致，其报价原文付款条件为 {fields['payment_terms']}"
                    if zh else
                    f"The current recommendation of {names[recommended_id]} is consistent with the ranking basis, with source payment terms of {fields['payment_terms']}"
                ))

    elif goal == "GAP_REVIEW":
        unresolved = [item for item in brief.get("unresolved_items") or [] if isinstance(item, dict)]
        if unresolved:
            descriptions = [label(
                str(item.get('supplier_name') or names.get(str(item.get('quote_id')), '相关供应商' if zh else 'relevant supplier')),
                str(item.get('control_code') or item.get('type')),
            ) for item in unresolved]
            sentences.append(sentence(
                "仍无法通过现有工具确认：" + "、".join(descriptions)
                if zh else "Still not confirmable with the available tools: " + ", ".join(descriptions)
            ))
            sentences.append(sentence(
                "应补充相应证明或审批原件，并人工核对供应商、料号和有效期后重新分析"
                if zh else
                "Add the corresponding source evidence or approval record, manually verify supplier, part number, and validity, then analyse again"
            ))
        else:
            sentences.append(sentence(
                "本次核查范围内没有待补传或待人工确认的事项"
                if zh else "No upload or manual-confirmation item remains in the scope checked"
            ))
        failed = [
            (quote_id, control)
            for quote_id, by_control in checks.items()
            for control, item in by_control.items() if item.get("status") == "FAIL"
        ]
        if failed:
            sentences.append(sentence(
                "另需区分已确认失败项：" + "、".join(f"{names.get(qid, qid)} {control}" for qid, control in failed)
                if zh else "Separately confirmed failures: " + ", ".join(f"{names.get(qid, qid)} {control}" for qid, control in failed)
            ))
        asks_expiry_or_mismatch = any(token in lowered for token in (
            "过期", "不匹配", "expired", "mismatch",
        ))
        has_expiry_or_mismatch = any(
            any(token in str(item.get("type") or item.get("reason_code") or "").upper()
                for token in ("EXPIRED", "MISMATCH"))
            for item in unresolved
        )
        if asks_expiry_or_mismatch and not has_expiry_or_mismatch:
            sentences.append(sentence(
                "当前核查记录没有标记证明过期或供应商编号不匹配"
                if zh else
                "The current audit record does not flag expired evidence or a supplier-ID mismatch"
            ))

    else:  # EVIDENCE_VALIDATION and generic evidence review
        subject_ids = explicit_ids or ([recommended_id] if recommended_id else checked_ids[:1])
        for quote_id in subject_ids:
            if quote_id not in rows:
                continue
            if "COST" in dimensions:
                sentences.append(sentence(label(names[quote_id], quote_summary(quote_id))))
                detail = _cost_detail(quote_fields.get(quote_id) or {}, requirement=requirement, language=language)
                if detail:
                    sentences.append(sentence(
                        f"费用原文依据：{detail}" if zh else f"Source cost basis: {detail}",
                        requirement_reference, result_reference,
                    ))
            if "DELIVERY" in dimensions:
                lead = (quote_fields.get(quote_id) or {}).get("lead_time_days")
                arrival = rows[quote_id].get("estimated_arrival_date")
                sentences.append(sentence(
                    f"{names[quote_id]} 的报价原文交付提前期为 {lead} 个日历日，冻结结果预计到货 {arrival}"
                    if zh else
                    f"{possessive(names[quote_id])} source quotation states a lead time of {lead} calendar days, and the frozen result estimates arrival on {arrival}"
                ))
            if "HISTORY" in dimensions and history_parts(quote_id):
                sentences.append(sentence(label(names[quote_id], ("、" if zh else ", ").join(history_parts(quote_id)))))
            if "COMPLIANCE" in dimensions and quote_id in assessments:
                sentences.append(sentence(
                    f"{names[quote_id]} 的制度结论为 {assessments[quote_id].get('status')}，供应商准入 {status(quote_id, 'APPROVED_SUPPLIER')}、RoHS {status(quote_id, 'ROHS_COMPLIANCE')}、金额审批 {status(quote_id, 'AMOUNT_APPROVAL')}"
                    if zh else
                    f"{possessive(names[quote_id])} policy status is {assessments[quote_id].get('status')}: supplier eligibility {status(quote_id, 'APPROVED_SUPPLIER')}, RoHS {status(quote_id, 'ROHS_COMPLIANCE')}, amount approval {status(quote_id, 'AMOUNT_APPROVAL')}"
                ))

    if not sentences:
        return None

    # Never erase adverse findings just because all requested tools completed.
    current_text = "".join(sentences)
    omitted_risks: list[str] = []
    verified_risks = [
        item for item in brief.get("verified_risks") or [] if isinstance(item, dict)
    ]
    needs_explicit_risk_label = bool(verified_risks) and (
        "风险" not in current_text if zh else "risk" not in current_text.casefold()
    )
    for item in verified_risks:
        summary = str(item.get("summary") or "").strip().rstrip("。.!；;")
        supplier_name = str(item.get("supplier_name") or "").strip()
        rates = re.findall(r"\d+(?:\.\d+)?%", summary)
        if summary and (needs_explicit_risk_label or
            (supplier_name and supplier_name.casefold() not in current_text.casefold())
            or any(rate not in current_text for rate in rates)
        ):
            omitted_risks.append(summary)
    if omitted_risks:
        sentences.append(sentence(
            ("同时保留的已确认风险：" if zh else "Other verified risks retained: ")
            + ("、" if zh else "; ").join(omitted_risks)
        ))

    unresolved = [item for item in brief.get("unresolved_items") or [] if isinstance(item, dict)]
    if unresolved:
        suppliers = "、".join(dict.fromkeys(
            str(item.get("supplier_name") or names.get(str(item.get("quote_id")), "相关供应商"))
            for item in unresolved
        ))
        sentences.append(sentence(
            f"待补事项：{suppliers} 仍有调查记录列明的证明或审批待补充"
            if zh else
            f"Outstanding follow-up: {suppliers} still has evidence or approval identified as missing"
        ))
    else:
        sentences.append(sentence(
            "待补事项：本次核查范围内没有未解决的证据缺失或冲突"
            if zh else "Outstanding follow-up: no unresolved evidence gap or conflict remains in the scope checked"
        ))
    sentences.append(sentence(
        "停止原因：当前问题所需的只读核查已经完成，重复调用现有工具不会增加新事实"
        if zh else
        "Stopping reason: the read-only checks needed for this question are complete, and repeating the available tools would not add new facts"
    ))

    return {
        "assistant_text": "".join(sentences) if zh else " ".join(sentences),
        "reference_ids": reference_ids,
        "changes": None,
        "clarification": None,
    }


def _history_rank(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    grade = _GRADE_ORDER.get(str(snapshot.get("overall_grade") or "N"), 9)
    try:
        on_time = Decimal(str((snapshot.get("on_time") or {}).get("rate") or 0))
    except (InvalidOperation, TypeError, ValueError):
        on_time = Decimal("0")
    try:
        rejected = Decimal(str((snapshot.get("rejected_lines") or {}).get("rate") or 1))
    except (InvalidOperation, TypeError, ValueError):
        rejected = Decimal("1")
    return grade, -on_time, rejected


def _requirement_reference(context: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    references = context.get("frozen_references") or {}
    for reference_id, payload in references.items():
        if str(reference_id).startswith("REQUIREMENT:") and isinstance(payload, dict):
            return str(reference_id), payload
    return None, {}


def _recursive_value(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _recursive_value(value, key)
            if found is not None:
                return found
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found = _recursive_value(value, key)
            if found is not None:
                return found
    return None


def _cost_detail(
    fields: dict[str, Any], *, requirement: dict[str, Any], language: str,
) -> str:
    zh = language == "zh"
    parts: list[str] = []
    try:
        if fields.get("unit_price") is not None:
            parts.append(
                f"单价 SGD {Decimal(str(fields['unit_price'])):,.2f}"
                if zh else f"unit price SGD {Decimal(str(fields['unit_price'])):,.2f}"
            )
    except (InvalidOperation, TypeError, ValueError):
        pass
    quantity = _recursive_value(requirement, "required_quantity")
    if quantity is not None:
        parts.append(f"数量 {quantity} 件" if zh else f"quantity {quantity} pieces")
    for key, zh_label, en_label in (
        ("shipping_fee_amount", "运费", "shipping"),
        ("other_fees_amount", "其他费用", "other fees"),
    ):
        try:
            if fields.get(key) is not None:
                parts.append(
                    f"{zh_label} SGD {Decimal(str(fields[key])):,.2f}"
                    if zh else f"{en_label} SGD {Decimal(str(fields[key])):,.2f}"
                )
        except (InvalidOperation, TypeError, ValueError):
            pass
    if fields.get("tax_mode"):
        parts.append(
            f"税费模式 {fields['tax_mode']}"
            if zh else f"tax mode {fields['tax_mode']}"
        )
    return "、".join(parts) if zh else ", ".join(parts)


def _policy_rule_summary(rule_texts: list[str], *, language: str) -> str:
    lowered = " ".join(rule_texts).casefold()
    if language == "zh":
        controls: list[str] = []
        if "exact supplier id" in lowered:
            controls.append("供应商准入要求供应商编号与当前名录精确匹配")
        if "manufacturer part number" in lowered:
            controls.append("RoHS 证明必须覆盖所报价的制造商料号")
        threshold = re.search(r"sgd\s*([0-9]+(?:\.[0-9]+)?)", lowered)
        if threshold:
            controls.append(f"确认总成本达到 SGD {Decimal(threshold.group(1)):,.2f} 时需在选定后记录金额审批")
        return "规则依据：" + "、".join(controls) if controls else ""
    controls = []
    if "exact supplier id" in lowered:
        controls.append("supplier eligibility requires an exact supplier-ID match to a current registry record")
    if "manufacturer part number" in lowered:
        controls.append("RoHS evidence must cover the offered manufacturer part number")
    threshold = re.search(r"sgd\s*([0-9]+(?:\.[0-9]+)?)", lowered)
    if threshold:
        controls.append(f"confirmed total cost at or above SGD {Decimal(threshold.group(1)):,.2f} requires recorded approval after selection")
    return "Policy basis: " + "; ".join(controls) if controls else ""
