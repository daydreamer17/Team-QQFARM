"""Deterministic decision relevance, with explicit proof and input binding."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, model_validator

from .contracts import (
    ComparisonRequest,
    ComparisonResult,
    DecisionPreferences,
    FeasibilityStatus,
    FrozenModel,
    ProcurementRequirement,
    RankingCriterion,
    SupplierEvaluation,
    ranking_pair,
)


IMPACT_VERSION = "decision-impact/1.1.0"
COST_RANKING = "LOWEST_CONFIRMED_TOTAL_COST"
DELIVERY_RANKING = "FASTEST_CONFIRMED_DELIVERY"
SUPPORTED_IMPACT_RANKINGS = frozenset({COST_RANKING, DELIVERY_RANKING})
FEE_FIELDS = frozenset(
    {"shipping_fee_status", "shipping_fee_amount", "other_fees_status", "other_fees_amount"}
)
MISSING_FEE_CODES = frozenset({"FIELD_MISSING", "FEE_AMOUNT_UNKNOWN"})


class ImpactStatus(StrEnum):
    NO_ISSUE = "NO_ISSUE"
    REQUIRES_INVESTIGATION = "REQUIRES_INVESTIGATION"
    NON_BLOCKING = "NON_BLOCKING"
    UNDETERMINED = "UNDETERMINED"


class QuoteDecisionImpact(FrozenModel):
    quote_id: str
    quote_version: int = Field(ge=1)
    status: ImpactStatus
    reason_code: str
    message: str
    unknown_fields: tuple[str, ...] = ()
    cost_lower_bound: Decimal | None = Field(default=None, ge=0)
    best_confirmed_cost: Decimal | None = Field(default=None, ge=0)
    additional_cost_to_tie: Decimal | None = Field(default=None, ge=0)
    assumptions: tuple[str, ...] = ()


class DecisionImpactRequest(FrozenModel):
    task_id: str = Field(min_length=1)
    task_revision: int = Field(ge=1)
    comparison: ComparisonRequest
    policy_binding: dict[str, str | None] = Field(default_factory=dict)
    review_bindings: dict[str, str] = Field(default_factory=dict)
    supplier_bindings: dict[str, str] = Field(default_factory=dict)
    decision_preferences: DecisionPreferences = Field(default_factory=DecisionPreferences)

    @model_validator(mode="after")
    def bindings_reference_current_quotes(self) -> "DecisionImpactRequest":
        quote_ids = {quote.quote_id for quote in self.comparison.quotes}
        if not set(self.supplier_bindings).issubset(quote_ids):
            raise ValueError("supplier bindings reference a quote outside the comparison")
        return self


class DecisionImpactResult(FrozenModel):
    schema_version: str = IMPACT_VERSION
    task_id: str
    task_revision: int
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison: ComparisonResult
    quote_impacts: tuple[QuoteDecisionImpact, ...]
    blocking_quote_ids: tuple[str, ...]
    nonblocking_unknown_quote_ids: tuple[str, ...]
    scope: str = "QUOTE_COMPARISON_ONLY; mandatory review and policy gates remain authoritative"


def assess_quote_impact(
    requirement: ProcurementRequirement,
    evaluation: SupplierEvaluation,
    best_confirmed_cost: Decimal | None,
) -> QuoteDecisionImpact:
    """Only legal, nonnegative missing fees support a cost dominance proof.

    A known subtotal is NOT a safe bound for currency/price-basis/tax errors,
    conflicting evidence, unknown discounts, or unsupported ranking rules.
    """
    fields = tuple(sorted({f for issue in evaluation.pending_reasons for f in issue.fields}))
    common = dict(
        quote_id=evaluation.quote_id,
        quote_version=evaluation.quote_version,
        unknown_fields=fields,
        best_confirmed_cost=best_confirmed_cost,
    )
    if evaluation.status == FeasibilityStatus.FEASIBLE:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.NO_ISSUE,
            reason_code="CONFIRMED_FEASIBLE", message="No unresolved quote facts.",
        )
    if evaluation.status == FeasibilityStatus.INFEASIBLE:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.NON_BLOCKING,
            reason_code="CONFIRMED_INFEASIBLE",
            message="Confirmed failures already exclude this quote; missing facts remain unknown.",
        )
    if requirement.ranking_preference not in SUPPORTED_IMPACT_RANKINGS:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.UNDETERMINED,
            reason_code="RANKING_UNSUPPORTED", message="No proof for this ranking preference.",
        )
    if requirement.ranking_preference == DELIVERY_RANKING:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.UNDETERMINED,
            reason_code="DELIVERY_BOUND_NOT_PROVEN",
            message="Unresolved quote facts may change delivery-first ranking.",
        )
    legal_missing_fees = bool(evaluation.pending_reasons) and all(
        issue.code in MISSING_FEE_CODES and issue.fields
        and set(issue.fields).issubset(FEE_FIELDS)
        for issue in evaluation.pending_reasons
    )
    if not legal_missing_fees or evaluation.known_cost_subtotal is None:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.UNDETERMINED,
            reason_code="COST_BOUND_NOT_PROVEN",
            message="Unresolved non-fee facts or invalid cost inputs prevent a dominance proof.",
        )
    bound = evaluation.known_cost_subtotal
    assumptions = (
        "Same confirmed currency, quantity and price basis; tax NOT_APPLICABLE.",
        "Only nonnegative fees are unknown; no modeled discount or negative fee.",
        "Lowest confirmed total cost ranking; other quote checks have no unresolved issues.",
    )
    if best_confirmed_cost is None:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.REQUIRES_INVESTIGATION,
            reason_code="NO_CONFIRMED_FEASIBLE_BASELINE",
            message="No confirmed feasible supplier exists; this quote may be needed.",
            cost_lower_bound=bound, assumptions=assumptions,
        )
    if bound > best_confirmed_cost:
        return QuoteDecisionImpact(
            **common, status=ImpactStatus.NON_BLOCKING,
            reason_code="COST_LOWER_BOUND_DOMINATED",
            message="Even zero additional fees cannot beat the confirmed feasible cost.",
            cost_lower_bound=bound, assumptions=assumptions,
        )
    return QuoteDecisionImpact(
        **common, status=ImpactStatus.REQUIRES_INVESTIGATION,
        reason_code="UNKNOWN_FEES_CAN_CHANGE_WINNER_OR_TIE",
        message="Unknown fees may change the winner or create a tie; investigate before publishing.",
        cost_lower_bound=bound, additional_cost_to_tie=best_confirmed_cost - bound,
        assumptions=assumptions,
    )


def analyze_decision_impact(request: DecisionImpactRequest) -> DecisionImpactResult:
    """Tool-ready pure interface; does not mutate facts or call an LLM."""
    from .engine import compare_suppliers

    effective = decision_comparison_request(request)
    comparison = compare_suppliers(effective)
    eligible_results = tuple(result for result in comparison.supplier_results
                            if result.quote_id not in effective.excluded_quote_ids)
    if effective.policy_eligibility is not None:
        eligible_results = tuple(result for result in eligible_results
            if effective.policy_eligibility.get(result.quote_id) is None
            or effective.policy_eligibility[result.quote_id].status != "EXCLUDED")
        verified = tuple(result for result in eligible_results
            if result.status == FeasibilityStatus.FEASIBLE
            and effective.policy_eligibility.get(result.quote_id) is not None
            and effective.policy_eligibility[result.quote_id].status == "VERIFIED")
        cost_cohort = verified or eligible_results
    else:
        cost_cohort = eligible_results
    confirmed_costs = [result.total_cost for result in cost_cohort
        if result.status == FeasibilityStatus.FEASIBLE and result.total_cost is not None]
    best = min(confirmed_costs) if confirmed_costs else None
    impacts = tuple(
        assess_quote_impact(effective.requirement, result, best)
        for result in comparison.supplier_results
    )
    identity = request.model_dump(mode="json") | {"impact_version": IMPACT_VERSION,
                                                  "rule_version": comparison.rule_version}
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DecisionImpactResult(
        task_id=request.task_id, task_revision=request.task_revision,
        input_sha256=digest, comparison=comparison, quote_impacts=impacts,
        blocking_quote_ids=tuple(
            impact.quote_id for impact in impacts
            if impact.status in {ImpactStatus.REQUIRES_INVESTIGATION, ImpactStatus.UNDETERMINED}
        ),
        nonblocking_unknown_quote_ids=tuple(
            impact.quote_id for impact in impacts
            if impact.status == ImpactStatus.NON_BLOCKING and impact.unknown_fields
        ),
    )


def decision_comparison_request(
    request: DecisionImpactRequest,
    *,
    preferences: DecisionPreferences | None = None,
) -> ComparisonRequest:
    """Apply system-bound decision preferences without mutating official inputs."""

    selected = preferences or request.decision_preferences
    requirement = request.comparison.requirement
    primary = selected.primary_criterion
    secondary = selected.secondary_criterion
    if primary is None and selected.ranking_mode is not None:
        primary, secondary = ranking_pair(selected.ranking_mode)
    if primary is not None:
        requirement = requirement.model_copy(update={
            "ranking_preference": primary,
            "secondary_preference": secondary,
        })
    excluded = set(selected.excluded_supplier_ids)
    excluded_quote_ids = tuple(
        quote.quote_id for quote in request.comparison.quotes
        if request.supplier_bindings.get(quote.quote_id) in excluded
    )
    return ComparisonRequest(
        requirement=requirement,
        quotes=request.comparison.quotes,
        evaluated_at=request.comparison.evaluated_at,
        cost_tolerance_amount=selected.cost_tolerance_amount,
        excluded_quote_ids=excluded_quote_ids,
        supplier_history_snapshots=request.comparison.supplier_history_snapshots,
        history_dataset_context=request.comparison.history_dataset_context,
        policy_eligibility=request.comparison.policy_eligibility,
        policy_strategy=request.comparison.policy_strategy,
    )
