"""Render a proposal from the shared engine's trial, never from an LLM's arithmetic."""

from decimal import Decimal

from supplier_comparison.rules.selection_gap import HypotheticalComparison


CRITERION_LABELS = {
    "LOWEST_CONFIRMED_TOTAL_COST": "lowest total cost",
    "FASTEST_CONFIRMED_DELIVERY": "fastest delivery",
    "LONGEST_CONFIRMED_PAYMENT_TERM": "longest payment term",
    "HIGHEST_SUPPLIER_PERFORMANCE": "highest supplier performance",
    "HIGHEST_HISTORICAL_ON_TIME_RATE": "highest historical on-time rate",
    "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "lowest historical rejected-line rate",
}

CRITERION_LABELS_ZH = {
    "LOWEST_CONFIRMED_TOTAL_COST": "最低总成本",
    "FASTEST_CONFIRMED_DELIVERY": "最快到货",
    "LONGEST_CONFIRMED_PAYMENT_TERM": "最长付款账期",
    "HIGHEST_SUPPLIER_PERFORMANCE": "最高供应商表现",
    "HIGHEST_HISTORICAL_ON_TIME_RATE": "最高历史准时率",
    "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "最低历史拒收率",
}


def render_decision_preview(
    trial: HypotheticalComparison, *, currency: str,
    supplier_bindings: dict[str, str], reference: str, language: str = "en",
) -> str:
    """Explain engine ranking rounds; ties and missing history must stay unresolved."""
    comparison = trial.comparison
    preferences = trial.decision_preferences
    trace = comparison.ranking_trace
    results = {row.quote_id: row for row in comparison.supplier_results}
    compliance = comparison.compliance_assessment or {}
    policy_status_by_quote = {
        str(item.get("quote_id")): str(item.get("status") or "")
        for item in compliance.get("assessments") or []
        if isinstance(item, dict) and item.get("quote_id")
    }
    zh = language == "zh"
    labels = CRITERION_LABELS_ZH if zh else CRITERION_LABELS
    primary = labels.get(
        preferences.primary_criterion,
        "当前主要指标" if zh else "current primary criterion",
    )
    secondary = labels.get(preferences.secondary_criterion)
    tolerance = preferences.cost_tolerance_amount

    def money(value: Decimal) -> str:
        return f"{currency} {value:,.2f}"

    def name(quote_id: str) -> str:
        row = results[quote_id]
        supplier_id = supplier_bindings.get(quote_id)
        label = row.supplier_name or supplier_id or ("未命名供应商" if zh else "Unnamed supplier")
        return f"{label} ({supplier_id})" if supplier_id and supplier_id != label else label

    def feasible_and_policy_eligible_rows():
        return [
            row for row in comparison.supplier_results
            if getattr(row.status, "value", row.status) == "FEASIBLE"
            and policy_status_by_quote.get(row.quote_id) != "NON_COMPLIANT"
        ]

    rule = f"优先考虑{primary}" if zh else f"prioritise {primary}"
    if tolerance is not None:
        rule += (f"，成本容差为 {money(tolerance)}" if zh else
                 f" with a cost tolerance of {money(tolerance)}")
    if secondary:
        if zh:
            rule += f"；{'在成本容差范围内' if tolerance is not None else '主要指标并列时'}优先考虑{secondary}"
        else:
            rule += f"; prioritise {secondary} {'within the tolerance band' if tolerance is not None else 'when the primary criterion is tied'}"
    if preferences.excluded_supplier_ids:
        excluded = "、".join(preferences.excluded_supplier_ids)
        rule = (f"先排除 {excluded}，再{rule}" if zh else
                "first exclude " + ", ".join(preferences.excluded_supplier_ids) + ", then " + rule)

    winners = comparison.recommended_quote_ids
    if len(winners) == 1:
        lead = (f"按照“{rule}”的规则，本情景推荐 **{name(winners[0])}**。" if zh else
                f"Under the rule “{rule}”, this scenario recommends **{name(winners[0])}**.")
    elif winners:
        winner_names = ("、" if zh else ", ").join(name(q) for q in winners)
        lead = (f"按照“{rule}”的规则，**{winner_names}** 并列；当前条件无法形成唯一推荐。" if zh else
                f"Under the rule “{rule}”, **{winner_names}** are tied; the current conditions do not support a unique recommendation.")
    else:
        lead = (f"按照“{rule}”的规则，目前还无法确定推荐供应商。" if zh else
                f"Under the rule “{rule}”, a recommended supplier cannot yet be determined.")
    lines = [lead + f" ({reference})", ""]
    if ('cost_tolerance_amount' in trial.changes
        and trial.changes['cost_tolerance_amount'] is None):
        lines.extend([(
            "本方案会清除现有成本容差设置。该调整尚未应用，需要你的确认。"
            if zh else
            "This proposal clears the existing cost-tolerance setting. The change has not been applied and requires your confirmation."
        ), ""])
    if trace and trace.rounds:
        first_round = trace.rounds[0]
        candidates = [results[q] for q in first_round.candidate_quote_ids_after]
        if tolerance is not None and first_round.reason_codes == ("COST_TOLERANCE_POOL",):
            eligible = [results[q] for q in first_round.candidate_quote_ids_before]
            cheapest = min(eligible, key=lambda row: row.total_cost)
            if zh:
                lines.extend([
                    f"- 当前比较范围内最低总成本为 {money(cheapest.total_cost)}，对应 {name(cheapest.quote_id)}（{reference}）。",
                    f"- 成本容差为 {money(tolerance)} 时，候选上限为 {money(cheapest.total_cost + tolerance)}（{reference}）。",
                    "- 成本范围内的报价包括：",
                ])
            else:
                lines.extend([
                    f"- The lowest total cost in the current comparison scope is {money(cheapest.total_cost)} from {name(cheapest.quote_id)}. ({reference})",
                    f"- With a cost tolerance of {money(tolerance)}, the candidate ceiling is {money(cheapest.total_cost + tolerance)}. ({reference})",
                    "- Quotations within the cost band include:",
                ])
        else:
            lines.append(
                f"- 按{primary}筛选后的候选报价："
                if zh else f"- Candidate quotations after filtering by {primary}:"
            )
        for row in candidates:
            facts = [money(row.total_cost)] if row.total_cost is not None else [
                "总成本待确认" if zh else "total cost pending confirmation"
            ]
            if row.estimated_arrival_date:
                facts.append(
                    ("预计到货 " if zh else "estimated arrival ")
                    + row.estimated_arrival_date.isoformat()
                )
            for criterion in row.criterion_evaluations:
                if criterion.criterion in {preferences.primary_criterion, preferences.secondary_criterion}:
                    if criterion.display_value and criterion.criterion not in {
                        "LOWEST_CONFIRMED_TOTAL_COST", "FASTEST_CONFIRMED_DELIVERY",
                    }:
                        separator = "：" if zh else ": "
                        facts.append(f"{labels[criterion.criterion]}{separator}{criterion.display_value}")
            if zh:
                lines.append(f"  - {name(row.quote_id)}：{'、'.join(facts)}（{reference}）。")
            else:
                lines.append(f"  - {name(row.quote_id)}: {', '.join(facts)}. ({reference})")
        if len(winners) == 1:
            if trace.secondary_applied and secondary:
                lines.append(
                    f"- 在这些候选报价中，{name(winners[0])} 按“{secondary}”排名第一，因此成为本情景的推荐项（{reference}）。"
                    if zh else
                    f"- Among these candidate quotations, {name(winners[0])} ranks first by “{secondary}” and is therefore recommended in this scenario. ({reference})"
                )
            else:
                lines.append(
                    f"- {name(winners[0])} 按当前规则排名第一；次要指标未改变选择结果（{reference}）。"
                    if zh else
                    f"- {name(winners[0])} ranks first under the current rule; the secondary criterion does not change the selection. ({reference})"
                )
    if not winners:
        if comparison.disposition == 'EMPTY_SCOPE':
            reasons = [
                '当前范围为空，因为所有供应商都被排除。请调整排除范围。'
                if zh else
                'The current scope is empty because every supplier is excluded. Adjust the exclusion scope.'
            ]
        elif comparison.disposition == 'NO_FEASIBLE_QUOTES':
            reasons = [
                '当前没有报价满足采购条件。请核对未满足的条件。'
                if zh else
                'No quotation currently meets the procurement conditions. Review the unmet conditions.'
            ]
        else:
            reasons = [
                ('历史数据未绑定到当前范围或不适用，因此不能用于排序。' if zh else
                 'Historical data is not bound to the current scope or is not applicable, so it cannot be used for ranking.')
                if issue.code == 'RANKING_CRITERION_NOT_APPLICABLE' else
                ('所选排序指标存在缺失值或样本不足，请补充确认后重新比较。' if zh else
                 'The selected ranking criterion has missing values or insufficient samples. Add confirmation before comparing again.')
                if issue.code == 'RANKING_CRITERION_NOT_COMPARABLE' else
                ('当前比较仍有待处理条件，请先在决策页面核对缺口和未知项。' if zh else
                 'The current comparison contains pending conditions. Review gaps and unknowns on the decision page first.')
                for issue in comparison.comparison_reasons
            ]
            if comparison.blocking_pending_quote_ids:
                reasons.append(
                    '仍有可能影响选择的报价信息需要确认，因此目前无法确定推荐结果。'
                    if zh else
                    'Quotation information that may affect selection still requires confirmation, so a recommendation cannot yet be determined.'
                )
        lines.extend(
            f"- {reason}（{reference}）。" if zh else f"- {reason} ({reference})"
            for reason in dict.fromkeys(reasons)
        )
        if not reasons:
            lines.append(
                "- 当前范围为空，或没有报价满足采购条件。请调整范围或核对未满足的条件。"
                if zh else
                "- The current scope is empty or no quotation meets procurement conditions. Adjust the scope or review unmet conditions."
            )
    if "delivery_deadline" in trial.changes:
        feasible_rows = feasible_and_policy_eligible_rows()
        feasible_names = ("、" if zh else ", ").join(name(row.quote_id) for row in feasible_rows)
        if feasible_names:
            lines.append(
                f"- 新到货期限下仍可选的制度合格报价为：{feasible_names}（{reference}）。"
                if zh else
                f"- Under the new arrival deadline, the policy-eligible quotations that remain feasible are: {feasible_names} ({reference})."
            )
        lines.append(
            f"- 本次只试算到货期限，未修改证明材料或制度判断，因此各供应商原有制度结论不变（{reference}）。"
            if zh else
            f"- This simulation changes only the arrival deadline, not evidence or policy assessments, so the existing policy conclusions remain unchanged ({reference})."
        )
    elif "budget_amount" in trial.changes:
        feasible_rows = feasible_and_policy_eligible_rows()
        feasible_names = ("、" if zh else ", ").join(name(row.quote_id) for row in feasible_rows)
        if feasible_names:
            lines.append(
                f"- 新预算下仍可进入排序的制度合格报价为：{feasible_names}（{reference}）。"
                if zh else
                f"- Under the new budget, the policy-eligible quotations that remain in the ranking are: {feasible_names} ({reference})."
            )
    lines.extend([
        "",
        ("这是一个等待确认的模拟情景，不会改变正式决策，也不代表制度审批通过。" if zh else
         "This is a simulation awaiting confirmation. It does not change the official decision or indicate policy approval."),
        ("是否按这些条件生成情景？" if zh else "Generate a scenario using these conditions?"),
    ])
    return "\n".join(lines)
