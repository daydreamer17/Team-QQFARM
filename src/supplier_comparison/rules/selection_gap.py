"""Read-only selection gaps and explicitly hypothetical comparisons."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    ComparisonRequest,
    ComparisonResult,
    DecisionPreferences,
    FeasibilityStatus,
    FrozenModel,
    RankingCriterion,
    RankingMode,
    RuleIssue,
    ranking_mode_for,
    ranking_pair,
)
from .decision_impact import (
    COST_RANKING,
    DecisionImpactRequest,
    analyze_decision_impact,
    decision_comparison_request,
)
from .engine import compare_suppliers


GAP_VERSION = "selection-gap/1.1.0"


class RequirementChanges(FrozenModel):
    budget_amount: Decimal | None = Field(default=None, ge=0)
    delivery_deadline: date | None = None
    primary_criterion: RankingCriterion | None = None
    secondary_criterion: RankingCriterion | None = None
    ranking_mode: RankingMode | None = Field(default=None, exclude=True)
    excluded_supplier_ids: tuple[str, ...] | None = None
    cost_tolerance_amount: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def translate_legacy_mode(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not value.get("ranking_mode"):
            return value
        payload = dict(value)
        primary, secondary = ranking_pair(RankingMode(payload["ranking_mode"]))
        if payload.get("primary_criterion") not in (None, primary):
            raise ValueError("legacy and V2 primary ranking fields conflict")
        if payload.get("secondary_criterion") not in (None, secondary):
            raise ValueError("legacy and V2 secondary ranking fields conflict")
        payload["primary_criterion"] = primary
        payload["secondary_criterion"] = secondary
        return payload

    @field_validator("budget_amount", "cost_tolerance_amount", mode="before")
    @classmethod
    def no_float(cls, value: Any):
        if isinstance(value, float):
            raise ValueError("money must be a decimal string")
        return value

    @field_validator("excluded_supplier_ids")
    @classmethod
    def supplier_ids_are_unique_and_nonempty(cls, value: tuple[str, ...] | None):
        if value is None:
            return None
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("excluded supplier IDs cannot be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("excluded supplier IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def nonempty(self):
        if not self.model_fields_set:
            raise ValueError("provide a requirement or decision-preference change")
        if (
            self.primary_criterion is not None
            and self.primary_criterion == self.secondary_criterion
        ):
            raise ValueError("primary and secondary criteria must differ")
        return self


class HypotheticalComparison(FrozenModel):
    hypothetical: Literal[True] = True
    formal_recommendation_allowed: Literal[False] = False
    policy_assessment_performed: Literal[False] = False
    changes: dict[str, Any]
    decision_preferences: DecisionPreferences
    excluded_quote_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...]
    comparison: ComparisonResult


class QuoteSelectionGap(FrozenModel):
    quote_id: str
    quote_version: int
    status: FeasibilityStatus
    failed_reasons: tuple[RuleIssue, ...]
    pending_reasons: tuple[RuleIssue, ...]
    comparison_reasons: tuple[RuleIssue, ...]
    actual_quantity: int | None
    currency: str
    known_cost_subtotal: Decimal | None
    confirmed_total_cost: Decimal | None
    best_other_confirmed_cost: Decimal | None
    cost_difference_vs_other: Decimal | None
    budget_excess: Decimal | None
    total_cost_reduction_to_tie_other: Decimal | None
    delivery_days_late: int | None
    target_arrival_deadline: date
    target_lead_time_days: int | None
    delivery_improvement: HypotheticalComparison | None
    would_be_quote_comparison_choice: bool | None
    assumptions: tuple[str, ...]


class SelectionGapResult(FrozenModel):
    schema_version: str = GAP_VERSION
    task_id: str
    task_revision: int
    input_sha256: str
    gaps: tuple[QuoteSelectionGap, ...]
    scope: str = "QUOTE_COMPARISON_ONLY; no formal fact changes or policy approval"


def analyze_selection_gap(request: DecisionImpactRequest) -> SelectionGapResult:
    impact = analyze_decision_impact(request)
    effective = decision_comparison_request(request)
    requirement = effective.requirement
    gaps = []
    for evaluation in impact.comparison.supplier_results:
        quote = next(q for q in effective.quotes if q.quote_id == evaluation.quote_id)
        others = [r.total_cost for r in impact.comparison.supplier_results
                  if r.quote_id != evaluation.quote_id and r.status == FeasibilityStatus.FEASIBLE
                  and r.total_cost is not None]
        best_other = min(others) if others else None
        cost = evaluation.total_cost
        late = ((evaluation.estimated_arrival_date - requirement.delivery_deadline).days
                if evaluation.estimated_arrival_date else None)
        late = max(0, late) if late is not None else None
        target_lead = None
        hypothesis = None
        choice = None
        if requirement.planned_order_date is not None and evaluation.estimated_arrival_date is not None:
            target_lead = (requirement.delivery_deadline - requirement.planned_order_date).days
        if late is not None and late > 0 and target_lead is not None:
            # Clone for calculation only. No correction/review event is invented,
            # and these temporary candidates are never exported as confirmed facts.
            candidates = tuple(c.model_copy(update={"normalized_value": target_lead, "raw_value": str(target_lead)})
                               if c.field_name == "lead_time_days" else c for c in quote.candidates)
            improved = quote.model_copy(update={"candidates": candidates})
            trial = effective.model_copy(update={"quotes": tuple(
                improved if q.quote_id == quote.quote_id else q for q in effective.quotes
            )})
            result = compare_suppliers(trial)
            hypothesis = HypotheticalComparison(
                changes={"quote_id": quote.quote_id, "lead_time_days": target_lead},
                decision_preferences=request.decision_preferences,
                assumptions=("Supplier has NOT confirmed this faster arrival.",
                             "Price, fees, MOQ, quantity, validity and competing quotes remain unchanged; no expedite fee.",
                             "Calendar-day ARRIVAL from ORDER_DATE; policy gates have not been assessed."),
                comparison=result,
            )
            choice = result.final_recommendation_allowed and quote.quote_id in result.recommended_quote_ids
        gaps.append(QuoteSelectionGap(
            quote_id=quote.quote_id, quote_version=quote.quote_version, status=evaluation.status,
            failed_reasons=evaluation.failed_reasons, pending_reasons=evaluation.pending_reasons,
            comparison_reasons=impact.comparison.comparison_reasons, actual_quantity=evaluation.actual_quantity,
            currency=requirement.currency, known_cost_subtotal=evaluation.known_cost_subtotal,
            confirmed_total_cost=cost, best_other_confirmed_cost=best_other,
            cost_difference_vs_other=cost - best_other if cost is not None and best_other is not None else None,
            budget_excess=max(Decimal(0), cost - requirement.budget_amount) if cost is not None else None,
            total_cost_reduction_to_tie_other=max(Decimal(0), cost - best_other) if cost is not None and best_other is not None else None,
            delivery_days_late=late, target_arrival_deadline=requirement.delivery_deadline,
            target_lead_time_days=target_lead, delivery_improvement=hypothesis,
            would_be_quote_comparison_choice=choice,
            assumptions=("All known failures and unknown facts must be resolved; a numeric gap alone is not sufficient.",
                         "Only lowest confirmed total cost is supported; equality can yield a tie, not a sole winner.",
                         "This is not a global minimum-change optimization or a policy/qualification approval."),
        ))
    return SelectionGapResult(task_id=request.task_id, task_revision=request.task_revision,
                              input_sha256=impact.input_sha256, gaps=tuple(gaps))


def simulate_requirement_change(request: DecisionImpactRequest, changes: RequirementChanges,
                                *, user_authorized: bool = False) -> HypotheticalComparison:
    if user_authorized is not True:
        raise ValueError("requirement simulation requires explicit user authorization")
    values = request.comparison.requirement.model_dump(mode="python")
    supplied = changes.model_dump(mode="python", exclude_unset=True)
    for field in ("budget_amount", "delivery_deadline"):
        if field in supplied:
            values[field] = supplied[field]
    requirement = type(request.comparison.requirement).model_validate(values)
    base_preferences = request.decision_preferences
    legacy_primary = legacy_secondary = None
    if base_preferences.ranking_mode is not None:
        legacy_primary, legacy_secondary = ranking_pair(base_preferences.ranking_mode)
    elif ranking_mode_for(requirement) is not None:
        legacy_primary, legacy_secondary = ranking_pair(ranking_mode_for(requirement))
    base_primary = base_preferences.primary_criterion or legacy_primary or requirement.ranking_preference
    base_secondary = (
        base_preferences.secondary_criterion
        if base_preferences.primary_criterion is not None
        else legacy_secondary if legacy_primary is not None else requirement.secondary_preference
    )
    effective_preferences = DecisionPreferences(
        primary_criterion=(
            changes.primary_criterion
            if "primary_criterion" in changes.model_fields_set
            else base_primary
        ),
        secondary_criterion=(
            changes.secondary_criterion
            if "secondary_criterion" in changes.model_fields_set
            else base_secondary
        ),
        excluded_supplier_ids=(
            changes.excluded_supplier_ids or ()
            if "excluded_supplier_ids" in changes.model_fields_set
            else base_preferences.excluded_supplier_ids
        ),
        cost_tolerance_amount=(
            changes.cost_tolerance_amount
            if "cost_tolerance_amount" in changes.model_fields_set
            else base_preferences.cost_tolerance_amount
        ),
    )
    if (
        effective_preferences.cost_tolerance_amount is not None
        and effective_preferences.primary_criterion != COST_RANKING
    ):
        raise ValueError("cost tolerance requires cost-primary ranking")
    requested_suppliers = set(effective_preferences.excluded_supplier_ids)
    available_suppliers = set(request.supplier_bindings.values()) | set(base_preferences.excluded_supplier_ids)
    explicitly_requested = (
        set(changes.excluded_supplier_ids or ())
        if "excluded_supplier_ids" in changes.model_fields_set else set()
    )
    unknown_suppliers = explicitly_requested - available_suppliers
    if unknown_suppliers:
        raise ValueError("excluded supplier does not belong to the current comparison")
    excluded_quote_ids = tuple(sorted(
        quote_id for quote_id, supplier_id in request.supplier_bindings.items()
        if supplier_id in requested_suppliers
    ))
    simulated_request = request.model_copy(update={
        "comparison": request.comparison.model_copy(update={"requirement": requirement}),
        "decision_preferences": effective_preferences,
    })
    comparison = compare_suppliers(decision_comparison_request(simulated_request))
    assumptions = [
        "User-authorized hypothetical only; official requirement and decision preferences are unchanged.",
        "All quote facts, quantity, prices, fees, validity and evaluation time remain unchanged.",
        "No policy or approval assessment; confirmed inputs and formal changes are still required.",
    ]
    if effective_preferences.cost_tolerance_amount is not None:
        assumptions.append(
            f"Candidate pool is minimum confirmed total cost plus {effective_preferences.cost_tolerance_amount} "
            f"{requirement.currency}; the pool remains tied unless the user selected a secondary criterion."
        )
    if excluded_quote_ids:
        assumptions.append("Excluded suppliers are omitted only from this hypothetical comparison.")
    return HypotheticalComparison(
        changes=changes.model_dump(mode="json", exclude_unset=True),
        decision_preferences=effective_preferences,
        excluded_quote_ids=excluded_quote_ids,
        comparison=comparison,
        assumptions=tuple(assumptions),
    )


def draft_clarification(gap: QuoteSelectionGap) -> dict[str, Any]:
    lines = ["请确认以下报价条件；本函为沟通草稿，未发送，不代表贵司已承诺："]
    if gap.delivery_days_late:
        lines.append(f"在价格及其他报价条件不变、没有加急费的前提下，能否在 {gap.target_arrival_deadline.isoformat()} 前到货（目前超期 {gap.delivery_days_late} 天）？")
    if gap.budget_excess:
        lines.append(f"当前已确认总成本超预算 {gap.budget_excess}；请提供可核对的新报价，不直接修改原报价金额。")
    if gap.total_cost_reduction_to_tie_other:
        lines.append(f"与另一家当前可行方案总成本持平需降低 {gap.total_cost_reduction_to_tie_other}；持平不代表唯一优选。")
    if gap.pending_reasons:
        names = sorted({name for reason in gap.pending_reasons for name in reason.fields})
        lines.append("请补充或核对待确认项：" + "、".join(names) + "。")
    if gap.failed_reasons:
        lines.append("还需处理全部已知不符合项：" + "、".join(sorted({r.code for r in gap.failed_reasons})) + "。")
    lines.append("如改善交付需要增加费用或改变其他条件，请重新报价；任何入选结论须重新审核、计算并满足制度门禁。")
    return {"quote_id": gap.quote_id, "draft_only": True, "sent": False, "text": "\n".join(lines)}
