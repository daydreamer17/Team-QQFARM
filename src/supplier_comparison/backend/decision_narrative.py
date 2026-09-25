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


def render_decision_preview(
    trial: HypotheticalComparison, *, currency: str,
    supplier_bindings: dict[str, str], reference: str,
) -> str:
    """Explain engine ranking rounds; ties and missing history must stay unresolved."""
    comparison = trial.comparison
    preferences = trial.decision_preferences
    trace = comparison.ranking_trace
    results = {row.quote_id: row for row in comparison.supplier_results}
    primary = CRITERION_LABELS.get(preferences.primary_criterion, "current primary criterion")
    secondary = CRITERION_LABELS.get(preferences.secondary_criterion)
    tolerance = preferences.cost_tolerance_amount

    def money(value: Decimal) -> str:
        return f"{currency} {value:,.2f}"

    def name(quote_id: str) -> str:
        row = results[quote_id]
        supplier_id = supplier_bindings.get(quote_id)
        label = row.supplier_name or supplier_id or "Unnamed supplier"
        return f"{label} ({supplier_id})" if supplier_id and supplier_id != label else label

    rule = f"prioritise {primary}"
    if tolerance is not None:
        rule += f" with a cost tolerance of {money(tolerance)}"
    if secondary:
        rule += f"; prioritise {secondary} {'within the tolerance band' if tolerance is not None else 'when the primary criterion is tied'}"
    if preferences.excluded_supplier_ids:
        rule = "first exclude " + ", ".join(preferences.excluded_supplier_ids) + ", then " + rule

    winners = comparison.recommended_quote_ids
    if len(winners) == 1:
        lead = f"Under the rule “{rule}”, this scenario recommends **{name(winners[0])}**."
    elif winners:
        lead = f"Under the rule “{rule}”, **{', '.join(name(q) for q in winners)}** are tied; the current conditions do not support a unique recommendation."
    else:
        lead = f"Under the rule “{rule}”, a recommended supplier cannot yet be determined."
    lines = [lead + f" ({reference})", ""]
    if ('cost_tolerance_amount' in trial.changes
        and trial.changes['cost_tolerance_amount'] is None):
        lines.extend(["This proposal clears the existing cost-tolerance setting. The change has not been applied and requires your confirmation.", ""])
    if trace and trace.rounds:
        first_round = trace.rounds[0]
        candidates = [results[q] for q in first_round.candidate_quote_ids_after]
        if tolerance is not None and first_round.reason_codes == ("COST_TOLERANCE_POOL",):
            eligible = [results[q] for q in first_round.candidate_quote_ids_before]
            cheapest = min(eligible, key=lambda row: row.total_cost)
            lines.extend([
                f"- The lowest total cost in the current comparison scope is {money(cheapest.total_cost)} from {name(cheapest.quote_id)}. ({reference})",
                f"- With a cost tolerance of {money(tolerance)}, the candidate ceiling is {money(cheapest.total_cost + tolerance)}. ({reference})",
                "- Quotations within the cost band include:",
            ])
        else:
            lines.append(f"- Candidate quotations after filtering by {primary}:")
        for row in candidates:
            facts = [money(row.total_cost)] if row.total_cost is not None else ["total cost pending confirmation"]
            if row.estimated_arrival_date:
                facts.append("estimated arrival " + row.estimated_arrival_date.isoformat())
            for criterion in row.criterion_evaluations:
                if criterion.criterion in {preferences.primary_criterion, preferences.secondary_criterion}:
                    if criterion.display_value and criterion.criterion not in {
                        "LOWEST_CONFIRMED_TOTAL_COST", "FASTEST_CONFIRMED_DELIVERY",
                    }:
                        facts.append(f"{CRITERION_LABELS[criterion.criterion]}: {criterion.display_value}")
            lines.append(f"  - {name(row.quote_id)}: {', '.join(facts)}. ({reference})")
        if len(winners) == 1:
            if trace.secondary_applied and secondary:
                lines.append(f"- Among these candidate quotations, {name(winners[0])} ranks first by “{secondary}” and is therefore recommended in this scenario. ({reference})")
            else:
                lines.append(f"- {name(winners[0])} ranks first under the current rule; the secondary criterion does not change the selection. ({reference})")
    if not winners:
        if comparison.disposition == 'EMPTY_SCOPE':
            reasons = ['The current scope is empty because every supplier is excluded. Adjust the exclusion scope.']
        elif comparison.disposition == 'NO_FEASIBLE_QUOTES':
            reasons = ['No quotation currently meets the procurement conditions. Review the unmet conditions.']
        else:
            reasons = [
                'Historical data is not bound to the current scope or is not applicable, so it cannot be used for ranking.'
                if issue.code == 'RANKING_CRITERION_NOT_APPLICABLE' else
                'The selected ranking criterion has missing values or insufficient samples. Add confirmation before comparing again.'
                if issue.code == 'RANKING_CRITERION_NOT_COMPARABLE' else
                'The current comparison contains pending conditions. Review gaps and unknowns on the decision page first.'
                for issue in comparison.comparison_reasons
            ]
            if comparison.blocking_pending_quote_ids:
                reasons.append('Quotation information that may affect selection still requires confirmation, so a recommendation cannot yet be determined.')
        lines.extend(f"- {reason} ({reference})" for reason in dict.fromkeys(reasons))
        if not reasons:
            lines.append("- The current scope is empty or no quotation meets procurement conditions. Adjust the scope or review unmet conditions.")
    lines.extend(["", "This is a simulation awaiting confirmation. It does not change the official decision or indicate policy approval.", "Generate a scenario using these conditions?"])
    return "\n".join(lines)
