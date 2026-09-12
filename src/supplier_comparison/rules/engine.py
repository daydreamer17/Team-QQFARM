"""Orchestrate deterministic checks and rank confirmed feasible quotations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from itertools import groupby

from .contracts import (
    ComparisonDisposition,
    ComparisonRequest,
    ComparisonResult,
    CostCalculation,
    FeasibilityStatus,
    ProcurementRequirement,
    QuoteInput,
    RuleIssue,
    SupplierEvaluation,
)
from .cost import calculate_cost
from .delivery import check_delivery
from .field_access import FieldAccess
from .quantity import calculate_quantity
from .specification import check_specification


SUPPORTED_RANKING = "LOWEST_CONFIRMED_TOTAL_COST"


def evaluate_supplier(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
    *,
    evaluated_at: datetime,
) -> SupplierEvaluation:
    """Evaluate one quote while retaining all confirmed failures."""

    fields = FieldAccess.from_quote(quote)
    pending: list[RuleIssue] = []
    failed: list[RuleIssue] = []

    supplier_name_raw, supplier_name_issue = fields.require("supplier_name")
    supplier_name: str | None = None
    if supplier_name_issue is not None:
        pending.append(supplier_name_issue)
    elif isinstance(supplier_name_raw, str):
        supplier_name = supplier_name_raw
    else:
        pending.append(
            RuleIssue(
                code="FIELD_STRING_REQUIRED",
                fields=("supplier_name",),
                message="Field supplier_name must be a normalized string.",
            )
        )

    specification = check_specification(requirement, quote)
    failed.extend(specification.failed_reasons)
    pending.extend(specification.pending_reasons)

    quantity = calculate_quantity(requirement, quote)
    pending.extend(quantity.pending_reasons)

    cost: CostCalculation | None = None
    if quantity.breakdown is not None:
        cost = calculate_cost(requirement, quote, quantity.breakdown)
        pending.extend(cost.pending_reasons)

    delivery = check_delivery(requirement, quote, evaluated_at=evaluated_at)
    failed.extend(delivery.failed_reasons)
    pending.extend(delivery.pending_reasons)

    budget_lower_bound = _budget_lower_bound(requirement, cost)
    if (
        budget_lower_bound is not None
        and budget_lower_bound > requirement.budget_amount
    ):
        failed.append(
            RuleIssue(
                code="BUDGET_EXCEEDED",
                fields=("budget_amount",),
                message="Confirmed cost already exceeds the procurement budget.",
            )
        )

    failed_reasons = _deduplicate(failed)
    pending_reasons = _deduplicate(pending)
    if failed_reasons:
        status = FeasibilityStatus.INFEASIBLE
    elif pending_reasons:
        status = FeasibilityStatus.PENDING
    else:
        status = FeasibilityStatus.FEASIBLE

    return SupplierEvaluation(
        quote_id=quote.quote_id,
        quote_version=quote.quote_version,
        supplier_name=supplier_name,
        status=status,
        actual_quantity=(
            quantity.breakdown.actual_purchase_quantity
            if quantity.breakdown is not None
            else None
        ),
        goods_cost=cost.goods_cost if cost is not None else None,
        known_cost_subtotal=(
            cost.known_cost_subtotal if cost is not None else None
        ),
        total_cost=cost.total_cost if cost is not None else None,
        estimated_arrival_date=delivery.estimated_arrival_date,
        failed_reasons=failed_reasons,
        pending_reasons=pending_reasons,
    )


def compare_suppliers(request: ComparisonRequest) -> ComparisonResult:
    """Evaluate all active quotes and apply the buyer's explicit ranking."""

    results = tuple(
        evaluate_supplier(
            request.requirement,
            quote,
            evaluated_at=request.evaluated_at,
        )
        for quote in request.quotes
    )
    if not results:
        return ComparisonResult(
            evaluated_at=request.evaluated_at,
            disposition=ComparisonDisposition.EMPTY_SCOPE,
            supplier_results=(),
            final_recommendation_allowed=False,
        )

    pending_results = tuple(
        result for result in results if result.status == FeasibilityStatus.PENDING
    )
    pending_ids = tuple(result.quote_id for result in pending_results)

    if request.requirement.ranking_preference != SUPPORTED_RANKING:
        return ComparisonResult(
            evaluated_at=request.evaluated_at,
            disposition=ComparisonDisposition.PENDING_INPUT,
            supplier_results=results,
            pending_quote_ids=pending_ids,
            comparison_reasons=(
                RuleIssue(
                    code="RANKING_PREFERENCE_UNSUPPORTED",
                    fields=("ranking_preference",),
                    message="Requested ranking preference is not supported by the MVP.",
                ),
            ),
            final_recommendation_allowed=False,
        )

    feasible = sorted(
        (
            result
            for result in results
            if result.status == FeasibilityStatus.FEASIBLE
            and result.total_cost is not None
        ),
        key=lambda result: (result.total_cost, result.quote_id),
    )
    ranked_groups = tuple(
        tuple(result.quote_id for result in group)
        for _, group in groupby(feasible, key=lambda result: result.total_cost)
    )

    if not feasible:
        if pending_results:
            return ComparisonResult(
                evaluated_at=request.evaluated_at,
                disposition=ComparisonDisposition.PENDING_INPUT,
                supplier_results=results,
                pending_quote_ids=pending_ids,
                blocking_pending_quote_ids=pending_ids,
                final_recommendation_allowed=False,
            )
        return ComparisonResult(
            evaluated_at=request.evaluated_at,
            disposition=ComparisonDisposition.NO_FEASIBLE_QUOTES,
            supplier_results=results,
            final_recommendation_allowed=False,
        )

    best_cost = feasible[0].total_cost
    blocking_pending = tuple(
        result.quote_id
        for result in pending_results
        if result.known_cost_subtotal is None
        or result.known_cost_subtotal <= best_cost
    )
    if blocking_pending:
        return ComparisonResult(
            evaluated_at=request.evaluated_at,
            disposition=ComparisonDisposition.PENDING_INPUT,
            supplier_results=results,
            ranked_quote_ids=ranked_groups,
            pending_quote_ids=pending_ids,
            blocking_pending_quote_ids=blocking_pending,
            final_recommendation_allowed=False,
        )

    best_group = ranked_groups[0]
    if len(best_group) > 1 and request.requirement.secondary_preference is not None:
        return ComparisonResult(
            evaluated_at=request.evaluated_at,
            disposition=ComparisonDisposition.PENDING_INPUT,
            supplier_results=results,
            ranked_quote_ids=ranked_groups,
            pending_quote_ids=pending_ids,
            comparison_reasons=(
                RuleIssue(
                    code="SECONDARY_PREFERENCE_UNSUPPORTED",
                    fields=("secondary_preference",),
                    message="Configured tie-break preference is not supported by the MVP.",
                ),
            ),
            final_recommendation_allowed=False,
        )

    return ComparisonResult(
        evaluated_at=request.evaluated_at,
        disposition=ComparisonDisposition.RECOMMENDATION_AVAILABLE,
        supplier_results=results,
        ranked_quote_ids=ranked_groups,
        recommended_quote_ids=best_group,
        pending_quote_ids=pending_ids,
        final_recommendation_allowed=True,
    )


def _deduplicate(issues: list[RuleIssue]) -> tuple[RuleIssue, ...]:
    unique: dict[tuple[str, tuple[str, ...], str], RuleIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.fields, issue.message)] = issue
    return tuple(unique.values())


def _budget_lower_bound(
    requirement: ProcurementRequirement,
    cost: CostCalculation | None,
) -> Decimal | None:
    if (
        cost is None
        or cost.currency != requirement.currency
        or cost.goods_cost is None
    ):
        return None
    amount = cost.goods_cost
    if requirement.includes_shipping and cost.shipping_cost is not None:
        amount += cost.shipping_cost
    if cost.other_fees_cost is not None:
        amount += cost.other_fees_cost
    return amount
