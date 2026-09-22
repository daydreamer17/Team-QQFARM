"""Render a proposal from the shared engine's trial, never from an LLM's arithmetic."""

from decimal import Decimal

from supplier_comparison.rules.selection_gap import HypotheticalComparison


CRITERION_LABELS = {
    "LOWEST_CONFIRMED_TOTAL_COST": "最低总成本",
    "FASTEST_CONFIRMED_DELIVERY": "最快到货",
    "LONGEST_CONFIRMED_PAYMENT_TERM": "最长付款账期",
    "HIGHEST_SUPPLIER_PERFORMANCE": "最高供应商表现",
    "HIGHEST_HISTORICAL_ON_TIME_RATE": "最高历史准时率",
    "LOWEST_HISTORICAL_REJECTED_LINE_RATE": "最低历史拒收订单行率",
}


def render_decision_preview(
    trial: HypotheticalComparison, *, currency: str,
    supplier_bindings: dict[str, str], reference: str,
) -> str:
    """Explain engine ranking rounds; ties and missing history must stay unresolved."""
    comparison = trial.comparison
    preferences = trial.decision_preferences
    trace = comparison.ranking_trace
    results = {row.quote_id: row for row in comparison.supplier_results}
    primary = CRITERION_LABELS.get(preferences.primary_criterion, "当前主指标")
    secondary = CRITERION_LABELS.get(preferences.secondary_criterion)
    tolerance = preferences.cost_tolerance_amount

    def money(value: Decimal) -> str:
        return f"{currency} {value:,.2f}"

    def name(quote_id: str) -> str:
        row = results[quote_id]
        supplier_id = supplier_bindings.get(quote_id)
        label = row.supplier_name or supplier_id or "未命名供应商"
        return f"{label}（{supplier_id}）" if supplier_id and supplier_id != label else label

    rule = f"{primary}优先"
    if tolerance is not None:
        rule += f"，并允许 {money(tolerance)} 的成本容差"
    if secondary:
        rule += f"；{'容差范围内' if tolerance is not None else '主指标并列时'}优先选择{secondary}"
    if preferences.excluded_supplier_ids:
        rule = "先排除 " + "、".join(preferences.excluded_supplier_ids) + "，再按" + rule

    winners = comparison.recommended_quote_ids
    if len(winners) == 1:
        lead = f"按照“{rule}”的规则，该情景下建议选择 **{name(winners[0])}**。"
    elif winners:
        lead = f"按照“{rule}”的规则，**{'、'.join(name(q) for q in winners)}** 并列，当前条件不足以确定唯一推荐。"
    else:
        lead = f"按照“{rule}”的规则，目前还不能确定推荐供应商。"
    lines = [lead + f"（{reference}）", ""]
    if ('cost_tolerance_amount' in trial.changes
        and trial.changes['cost_tolerance_amount'] is None):
        lines.extend(["本次方案将清除原成本容差设置；该变更尚未应用，需您确认。", ""])
    if trace and trace.rounds:
        first_round = trace.rounds[0]
        candidates = [results[q] for q in first_round.candidate_quote_ids_after]
        if tolerance is not None and first_round.reason_codes == ("COST_TOLERANCE_POOL",):
            eligible = [results[q] for q in first_round.candidate_quote_ids_before]
            cheapest = min(eligible, key=lambda row: row.total_cost)
            lines.extend([
                f"- 当前比较范围内最低总成本为 {name(cheapest.quote_id)}的 {money(cheapest.total_cost)}。（{reference}）",
                f"- 加入 {money(tolerance)} 的成本容差后，候选上限为 {money(cheapest.total_cost + tolerance)}。（{reference}）",
                "- 符合成本范围的报价包括：",
            ])
        else:
            lines.append(f"- 按{primary}筛选后的候选报价：")
        for row in candidates:
            facts = [money(row.total_cost)] if row.total_cost is not None else ["总成本待确认"]
            if row.estimated_arrival_date:
                facts.append("预计 " + row.estimated_arrival_date.isoformat() + " 到货")
            for criterion in row.criterion_evaluations:
                if criterion.criterion in {preferences.primary_criterion, preferences.secondary_criterion}:
                    if criterion.display_value and criterion.criterion not in {
                        "LOWEST_CONFIRMED_TOTAL_COST", "FASTEST_CONFIRMED_DELIVERY",
                    }:
                        facts.append(f"{CRITERION_LABELS[criterion.criterion]}：{criterion.display_value}")
            lines.append(f"  - {name(row.quote_id)}：{'，'.join(facts)}。（{reference}）")
        if len(winners) == 1:
            if trace.secondary_applied and secondary:
                lines.append(f"- 在上述候选报价中，{name(winners[0])} 按“{secondary}”排序优先，因此成为该情景下的推荐供应商。（{reference}）")
            else:
                lines.append(f"- {name(winners[0])} 在当前规则下优先，无需以次指标改变选择。（{reference}）")
    if not winners:
        if comparison.disposition == 'EMPTY_SCOPE':
            reasons = ['当前范围为空：所有供应商均已排除，请调整排除范围。']
        elif comparison.disposition == 'NO_FEASIBLE_QUOTES':
            reasons = ['当前没有满足采购条件的报价，请核对未满足的条件。']
        else:
            reasons = [
                '当前范围的历史数据未绑定或不适用，不能据此排序。'
                if issue.code == 'RANKING_CRITERION_NOT_APPLICABLE' else
                '所选排序指标存在缺失值或样本不足，需补充确认后再比较。'
                if issue.code == 'RANKING_CRITERION_NOT_COMPARABLE' else
                '当前比较存在待确认条件，请先检查决策页面的差距与未知项。'
                for issue in comparison.comparison_reasons
            ]
            if comparison.blocking_pending_quote_ids:
                reasons.append('仍有可能影响选择的报价信息待确认，暂不能确定推荐。')
        lines.extend(f"- {reason}（{reference}）" for reason in dict.fromkeys(reasons))
        if not reasons:
            lines.append("- 当前范围为空或没有满足采购条件的报价，请调整范围或检查未满足条件。")
    lines.extend(["", "以上为待确认的模拟比较，不代表已修改正式决策或通过制度审批。", "是否按上述条件生成 Scenario？"])
    return "\n".join(lines)
