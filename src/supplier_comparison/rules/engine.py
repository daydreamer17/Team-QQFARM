"""Orchestrate deterministic hard checks and auditable multi-criterion ranking."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from itertools import groupby
from typing import Any, Iterable

from .contracts import (
    ComparisonDisposition,
    ComparisonRequest,
    ComparisonResult,
    CostCalculation,
    CriterionDirection,
    CriterionEvaluation,
    CriterionStatus,
    FeasibilityStatus,
    HistoryAvailabilityStatus,
    IdentityMatchStatus,
    PaymentParseStatus,
    ProcurementRequirement,
    QuoteInput,
    RankingCriterion,
    RankingRound,
    RankingTrace,
    RuleIssue,
    SupplierEvaluation,
    SupplierHistorySnapshot,
)
from .cost import calculate_cost
from .decision_impact import ImpactStatus, assess_quote_impact
from .delivery import check_delivery
from .field_access import FieldAccess
from .payment import evaluate_payment_term
from .quantity import calculate_quantity
from .specification import check_specification


COST_RANKING = RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST
DELIVERY_RANKING = RankingCriterion.FASTEST_CONFIRMED_DELIVERY
PAYMENT_RANKING = RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM
HISTORY_RANKINGS = frozenset(
    {
        RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
        RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE,
        RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE,
    }
)
SUPPORTED_RANKINGS = frozenset(RankingCriterion)
DIRECTIONS = {
    RankingCriterion.LOWEST_CONFIRMED_TOTAL_COST: CriterionDirection.ASCENDING,
    RankingCriterion.FASTEST_CONFIRMED_DELIVERY: CriterionDirection.ASCENDING,
    RankingCriterion.LONGEST_CONFIRMED_PAYMENT_TERM: CriterionDirection.DESCENDING,
    RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE: CriterionDirection.DESCENDING,
    RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE: CriterionDirection.DESCENDING,
    RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE: CriterionDirection.ASCENDING,
}
GRADE_ORDER = {"D": 1, "C": 2, "B": 3, "A": 4}


def evaluate_supplier(
    requirement: ProcurementRequirement,
    quote: QuoteInput,
    *,
    evaluated_at: datetime,
    history_snapshot: SupplierHistorySnapshot | None = None,
) -> SupplierEvaluation:
    """Evaluate hard requirements and independently expose all six metrics."""

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
    if budget_lower_bound is not None and budget_lower_bound > requirement.budget_amount:
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

    payment = evaluate_payment_term(quote)
    evaluations = _criterion_evaluations(
        quote=quote,
        cost=cost,
        delivery_date=delivery.estimated_arrival_date,
        payment=payment,
        history=history_snapshot,
    )
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
        known_cost_subtotal=cost.known_cost_subtotal if cost is not None else None,
        total_cost=cost.total_cost if cost is not None else None,
        estimated_arrival_date=delivery.estimated_arrival_date,
        payment_term=payment,
        history_snapshot=history_snapshot,
        criterion_evaluations=evaluations,
        failed_reasons=failed_reasons,
        pending_reasons=pending_reasons,
    )


def compare_suppliers(request: ComparisonRequest) -> ComparisonResult:
    """Apply hard requirements, explicit exclusions, and primary/secondary ranking."""

    history = {item.quote_id: item for item in request.supplier_history_snapshots}
    results = tuple(
        evaluate_supplier(
            request.requirement,
            quote,
            evaluated_at=request.evaluated_at,
            history_snapshot=history.get(quote.quote_id),
        )
        for quote in request.quotes
    )
    excluded = tuple(request.excluded_quote_ids)
    excluded_set = set(excluded)
    in_scope = tuple(result for result in results if result.quote_id not in excluded_set)
    primary = request.requirement.ranking_preference
    secondary = request.requirement.secondary_preference
    criteria = tuple(
        item
        for item in (primary, secondary)
        if isinstance(item, RankingCriterion)
    )

    if not in_scope:
        reasons = (
            (
                RuleIssue(
                    code="ALL_CANDIDATES_EXCLUDED",
                    fields=("excluded_quote_ids",),
                    message="All submitted candidates were explicitly excluded.",
                ),
            )
            if results
            else ()
        )
        return _comparison_result(
            request,
            results,
            ComparisonDisposition.EMPTY_SCOPE,
            criteria=criteria,
            excluded=excluded,
            comparison_reasons=reasons,
        )

    pending_results = tuple(
        result for result in in_scope if result.status == FeasibilityStatus.PENDING
    )
    pending_ids = tuple(result.quote_id for result in pending_results)
    ranking_issue = _ranking_issue(request)
    if ranking_issue is not None:
        return _comparison_result(
            request,
            results,
            ComparisonDisposition.PENDING_INPUT,
            criteria=criteria,
            excluded=excluded,
            pending_ids=pending_ids,
            comparison_reasons=(ranking_issue,),
            blockers=(ranking_issue.code,),
        )

    feasible = tuple(
        result for result in in_scope if result.status == FeasibilityStatus.FEASIBLE
    )
    if not feasible:
        if pending_results:
            return _comparison_result(
                request,
                results,
                ComparisonDisposition.PENDING_INPUT,
                criteria=criteria,
                excluded=excluded,
                pending_ids=pending_ids,
                blocking_pending_ids=pending_ids,
                blockers=("HARD_REQUIREMENT_PENDING",),
            )
        return _comparison_result(
            request,
            results,
            ComparisonDisposition.NO_FEASIBLE_QUOTES,
            criteria=criteria,
            excluded=excluded,
        )

    best_costs = [item.total_cost for item in feasible if item.total_cost is not None]
    best_cost = min(best_costs) if best_costs else None
    if primary == COST_RANKING and request.cost_tolerance_amount is None:
        blocking_pending = tuple(
            result.quote_id
            for result in pending_results
            if assess_quote_impact(request.requirement, result, best_cost).status
            != ImpactStatus.NON_BLOCKING
        )
    else:
        # No verified dominance proof exists for these ranking modes.
        blocking_pending = pending_ids
    if blocking_pending:
        provisional_groups: tuple[tuple[str, ...], ...] = ()
        if _criterion_group_issue(feasible, primary, field="ranking_preference") is None:
            provisional_groups = tuple(
                tuple(item.quote_id for item in group)
                for group in _groups_for_criterion(
                    feasible,
                    primary,
                    tolerance=(
                        request.cost_tolerance_amount
                        if primary == COST_RANKING
                        else None
                    ),
                )
            )
        return _comparison_result(
            request,
            results,
            ComparisonDisposition.PENDING_INPUT,
            criteria=criteria,
            excluded=excluded,
            pending_ids=pending_ids,
            blocking_pending_ids=blocking_pending,
            ranked_groups=provisional_groups,
            blockers=("HARD_PENDING_CAN_CHANGE_SELECTION",),
        )

    primary_issue = _criterion_group_issue(feasible, primary, field="ranking_preference")
    if primary_issue is not None:
        return _comparison_result(
            request,
            results,
            ComparisonDisposition.PENDING_INPUT,
            criteria=criteria,
            excluded=excluded,
            pending_ids=pending_ids,
            comparison_reasons=(primary_issue,),
            blockers=(primary_issue.code,),
        )

    primary_groups = _groups_for_criterion(
        feasible,
        primary,
        tolerance=(request.cost_tolerance_amount if primary == COST_RANKING else None),
    )
    best_group = primary_groups[0]
    rounds = [
        RankingRound(
            criterion=primary,
            candidate_quote_ids_before=tuple(item.quote_id for item in feasible),
            candidate_quote_ids_after=tuple(item.quote_id for item in best_group),
            applied=True,
            reason_codes=("COST_TOLERANCE_POOL",)
            if primary == COST_RANKING and request.cost_tolerance_amount is not None
            else (),
        )
    ]
    ranked_groups = tuple(tuple(item.quote_id for item in group) for group in primary_groups)
    secondary_applied = False

    if secondary is not None and len(best_group) > 1:
        secondary_issue = _criterion_group_issue(
            best_group, secondary, field="secondary_preference"
        )
        if secondary_issue is not None:
            rounds.append(
                RankingRound(
                    criterion=secondary,
                    candidate_quote_ids_before=tuple(item.quote_id for item in best_group),
                    candidate_quote_ids_after=tuple(item.quote_id for item in best_group),
                    applied=False,
                    reason_codes=(secondary_issue.code,),
                )
            )
            return _comparison_result(
                request,
                results,
                ComparisonDisposition.PENDING_INPUT,
                criteria=criteria,
                excluded=excluded,
                pending_ids=pending_ids,
                ranked_groups=ranked_groups,
                rounds=tuple(rounds),
                comparison_reasons=(secondary_issue,),
                blockers=(secondary_issue.code,),
            )
        secondary_groups = _groups_for_criterion(best_group, secondary)
        best_group = secondary_groups[0]
        ranked_groups = (
            *(tuple(item.quote_id for item in group) for group in secondary_groups),
            *ranked_groups[1:],
        )
        secondary_applied = True
        rounds.append(
            RankingRound(
                criterion=secondary,
                candidate_quote_ids_before=tuple(item.quote_id for item in primary_groups[0]),
                candidate_quote_ids_after=tuple(item.quote_id for item in best_group),
                applied=True,
            )
        )
    elif secondary is not None:
        rounds.append(
            RankingRound(
                criterion=secondary,
                candidate_quote_ids_before=tuple(item.quote_id for item in best_group),
                candidate_quote_ids_after=tuple(item.quote_id for item in best_group),
                applied=False,
                reason_codes=("SECONDARY_NOT_TRIGGERED",),
            )
        )

    recommended = tuple(item.quote_id for item in best_group)
    return _comparison_result(
        request,
        results,
        ComparisonDisposition.RECOMMENDATION_AVAILABLE,
        criteria=criteria,
        excluded=excluded,
        pending_ids=pending_ids,
        ranked_groups=ranked_groups,
        recommended=recommended,
        rounds=tuple(rounds),
        secondary_applied=secondary_applied,
        tie_group=recommended if len(recommended) > 1 else (),
    )


def _criterion_evaluations(
    *,
    quote: QuoteInput,
    cost: CostCalculation | None,
    delivery_date: date | None,
    payment,
    history: SupplierHistorySnapshot | None,
) -> tuple[CriterionEvaluation, ...]:
    cost_refs = _source_refs(
        quote, {"unit_price", "currency", "shipping_fee_amount", "other_fees_amount"}
    )
    delivery_refs = _source_refs(
        quote, {"lead_time_days", "day_basis", "delivery_semantics", "start_event"}
    )
    rows = [
        CriterionEvaluation(
            criterion=COST_RANKING,
            status=CriterionStatus.COMPARABLE
            if cost and cost.total_cost is not None
            else CriterionStatus.MISSING,
            exact_value=cost.total_cost if cost else None,
            display_value=str(cost.total_cost)
            if cost and cost.total_cost is not None
            else None,
            direction=DIRECTIONS[COST_RANKING],
            reason_codes=()
            if cost and cost.total_cost is not None
            else ("CONFIRMED_TOTAL_COST_MISSING",),
            evidence_refs=cost_refs,
        ),
        CriterionEvaluation(
            criterion=DELIVERY_RANKING,
            status=CriterionStatus.COMPARABLE
            if delivery_date is not None
            else CriterionStatus.MISSING,
            exact_value=delivery_date,
            display_value=delivery_date.isoformat() if delivery_date else None,
            direction=DIRECTIONS[DELIVERY_RANKING],
            reason_codes=() if delivery_date else ("CONFIRMED_DELIVERY_MISSING",),
            evidence_refs=delivery_refs,
        ),
        CriterionEvaluation(
            criterion=PAYMENT_RANKING,
            status=(
                CriterionStatus.COMPARABLE
                if payment.parse_status == PaymentParseStatus.COMPARABLE
                else CriterionStatus.MISSING
                if payment.parse_status == PaymentParseStatus.MISSING
                else CriterionStatus.INCOMPARABLE
            ),
            exact_value=payment.net_days
            if payment.parse_status == PaymentParseStatus.COMPARABLE
            else None,
            display_value=payment.normalized_text,
            direction=DIRECTIONS[PAYMENT_RANKING],
            reason_codes=payment.reason_codes,
            evidence_refs=payment.evidence_refs,
        ),
    ]
    rows.extend(_history_evaluations(history))
    return tuple(rows)


def _history_evaluations(
    history: SupplierHistorySnapshot | None,
) -> tuple[CriterionEvaluation, ...]:
    criteria = (
        RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
        RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE,
        RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE,
    )
    if history is None:
        return tuple(
            CriterionEvaluation(
                criterion=item,
                status=CriterionStatus.NOT_APPLICABLE,
                direction=DIRECTIONS[item],
                reason_codes=("HISTORY_NOT_BOUND",),
            )
            for item in criteria
        )
    if history.identity_match_status != IdentityMatchStatus.MATCHED:
        code = f"SUPPLIER_IDENTITY_{history.identity_match_status.value}"
        return tuple(
            CriterionEvaluation(
                criterion=item,
                status=CriterionStatus.INCOMPARABLE,
                direction=DIRECTIONS[item],
                reason_codes=(code,),
                evidence_refs=history.evidence_refs,
            )
            for item in criteria
        )
    if history.history_availability_status == HistoryAvailabilityStatus.OUT_OF_SCOPE:
        return tuple(
            CriterionEvaluation(
                criterion=item,
                status=CriterionStatus.NOT_APPLICABLE,
                direction=DIRECTIONS[item],
                reason_codes=("HISTORY_OUT_OF_SCOPE",),
                evidence_refs=history.evidence_refs,
            )
            for item in criteria
        )
    if history.history_availability_status == HistoryAvailabilityStatus.NO_DATA:
        return tuple(
            CriterionEvaluation(
                criterion=item,
                status=CriterionStatus.MISSING,
                direction=DIRECTIONS[item],
                reason_codes=("HISTORY_NO_DATA",),
                evidence_refs=history.evidence_refs,
            )
            for item in criteria
        )

    grade_comparable = history.overall_grade in GRADE_ORDER
    on_time_comparable = history.on_time is not None and history.on_time.denominator >= 10
    rejected_comparable = (
        history.rejected_lines is not None and history.rejected_lines.denominator >= 10
    )
    return (
        CriterionEvaluation(
            criterion=RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE,
            status=CriterionStatus.COMPARABLE
            if grade_comparable
            else CriterionStatus.INCOMPARABLE,
            exact_value=history.overall_grade if grade_comparable else None,
            display_value=history.overall_grade,
            direction=DIRECTIONS[RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE],
            reason_codes=() if grade_comparable else ("HISTORY_SAMPLE_INSUFFICIENT",),
            evidence_refs=history.evidence_refs,
        ),
        CriterionEvaluation(
            criterion=RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE,
            status=CriterionStatus.COMPARABLE
            if on_time_comparable
            else CriterionStatus.INCOMPARABLE,
            exact_value=history.on_time.rate
            if on_time_comparable and history.on_time
            else None,
            display_value=_rate_display(history.on_time),
            direction=DIRECTIONS[RankingCriterion.HIGHEST_HISTORICAL_ON_TIME_RATE],
            reason_codes=()
            if on_time_comparable
            else ("HISTORY_DELIVERY_SAMPLE_INSUFFICIENT",),
            evidence_refs=history.evidence_refs,
        ),
        CriterionEvaluation(
            criterion=RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE,
            status=CriterionStatus.COMPARABLE
            if rejected_comparable
            else CriterionStatus.INCOMPARABLE,
            exact_value=history.rejected_lines.rate
            if rejected_comparable and history.rejected_lines
            else None,
            display_value=_rate_display(history.rejected_lines),
            direction=DIRECTIONS[RankingCriterion.LOWEST_HISTORICAL_REJECTED_LINE_RATE],
            reason_codes=()
            if rejected_comparable
            else ("HISTORY_QUALITY_SAMPLE_INSUFFICIENT",),
            evidence_refs=history.evidence_refs,
        ),
    )


def _ranking_issue(request: ComparisonRequest) -> RuleIssue | None:
    primary = request.requirement.ranking_preference
    secondary = request.requirement.secondary_preference
    if primary not in SUPPORTED_RANKINGS:
        return RuleIssue(
            code="RANKING_PREFERENCE_UNSUPPORTED",
            fields=("ranking_preference",),
            message="Requested primary ranking preference is not supported.",
        )
    if secondary is not None and (secondary not in SUPPORTED_RANKINGS or secondary == primary):
        return RuleIssue(
            code="SECONDARY_PREFERENCE_UNSUPPORTED",
            fields=("secondary_preference",),
            message="Secondary ranking must be a different supported criterion.",
        )
    if request.cost_tolerance_amount is not None and primary != COST_RANKING:
        return RuleIssue(
            code="COST_TOLERANCE_REQUIRES_COST_RANKING",
            fields=("cost_tolerance_amount", "ranking_preference"),
            message="Cost tolerance requires lowest confirmed total cost as primary.",
        )
    if primary in HISTORY_RANKINGS and request.history_dataset_context is None:
        return RuleIssue(
            code="RANKING_CRITERION_NOT_APPLICABLE",
            fields=("ranking_preference",),
            message="The selected history criterion has no applicable dataset binding.",
        )
    return None


def _criterion_group_issue(
    group: Iterable[SupplierEvaluation],
    criterion: RankingCriterion,
    *,
    field: str,
) -> RuleIssue | None:
    values = tuple(group)
    evaluations = [_criterion(item, criterion) for item in values]
    unusable = [item for item in evaluations if item.status != CriterionStatus.COMPARABLE]
    if unusable:
        codes = sorted({code for item in unusable for code in item.reason_codes})
        return RuleIssue(
            code="RANKING_CRITERION_NOT_COMPARABLE",
            fields=(field,),
            message=f"Selected criterion is not comparable: {', '.join(codes) or 'missing value'}.",
        )
    if criterion == PAYMENT_RANKING:
        bases = {
            item.payment_term.payment_start_event
            for item in values
            if item.payment_term is not None
        }
        if len(bases) != 1:
            return RuleIssue(
                code="PAYMENT_BASIS_MISMATCH",
                fields=("payment_terms", "payment_start_event"),
                message="Payment terms use different or unknown start events.",
            )
    return None


def _groups_for_criterion(
    values: tuple[SupplierEvaluation, ...],
    criterion: RankingCriterion,
    *,
    tolerance: Decimal | None = None,
) -> tuple[tuple[SupplierEvaluation, ...], ...]:
    if criterion == COST_RANKING and tolerance is not None:
        costs = [_criterion(item, criterion).exact_value for item in values]
        minimum = min(cost for cost in costs if isinstance(cost, Decimal))
        pool = tuple(
            item
            for item in values
            if isinstance(_criterion(item, criterion).exact_value, Decimal)
            and _criterion(item, criterion).exact_value <= minimum + tolerance
        )
        outside = tuple(item for item in values if item not in pool)
        outside_groups = _groups_for_criterion(outside, criterion) if outside else ()
        return (tuple(sorted(pool, key=lambda item: item.quote_id)), *outside_groups)

    direction = DIRECTIONS[criterion]

    def business_value(item: SupplierEvaluation) -> Any:
        exact = _criterion(item, criterion).exact_value
        if criterion == RankingCriterion.HIGHEST_SUPPLIER_PERFORMANCE:
            return GRADE_ORDER[str(exact)]
        return exact

    ordered = sorted(
        values,
        key=lambda item: business_value(item),
        reverse=direction == CriterionDirection.DESCENDING,
    )
    return tuple(
        tuple(sorted(group, key=lambda item: item.quote_id))
        for _, group in groupby(ordered, key=business_value)
    )


def _comparison_result(
    request: ComparisonRequest,
    results: tuple[SupplierEvaluation, ...],
    disposition: ComparisonDisposition,
    *,
    criteria: tuple[RankingCriterion, ...],
    excluded: tuple[str, ...],
    pending_ids: tuple[str, ...] = (),
    blocking_pending_ids: tuple[str, ...] = (),
    ranked_groups: tuple[tuple[str, ...], ...] = (),
    recommended: tuple[str, ...] = (),
    rounds: tuple[RankingRound, ...] = (),
    comparison_reasons: tuple[RuleIssue, ...] = (),
    blockers: tuple[str, ...] = (),
    secondary_applied: bool = False,
    tie_group: tuple[str, ...] = (),
) -> ComparisonResult:
    trace = RankingTrace(
        ordered_criteria=criteria,
        criterion_directions={item.value: DIRECTIONS[item] for item in criteria},
        candidate_values={
            result.quote_id: {
                item.criterion.value: {
                    "status": item.status.value,
                    "exact_value": item.exact_value,
                    "display_value": item.display_value,
                    "reason_codes": list(item.reason_codes),
                }
                for item in result.criterion_evaluations
            }
            for result in results
        },
        rounds=rounds,
        cost_tolerance_applied=(
            request.cost_tolerance_amount is not None
            and request.requirement.ranking_preference == COST_RANKING
        ),
        secondary_applied=secondary_applied,
        tie_group=tie_group,
        blocker_reason_codes=blockers,
        comparison_disposition=disposition,
        original_scope_count=len(results),
        excluded_quote_ids=excluded,
    )
    public_results = () if disposition == ComparisonDisposition.EMPTY_SCOPE else results
    return ComparisonResult(
        evaluated_at=request.evaluated_at,
        disposition=disposition,
        supplier_results=public_results,
        ranked_quote_ids=ranked_groups,
        recommended_quote_ids=recommended,
        pending_quote_ids=pending_ids,
        blocking_pending_quote_ids=blocking_pending_ids,
        comparison_reasons=comparison_reasons,
        final_recommendation_allowed=(
            disposition == ComparisonDisposition.RECOMMENDATION_AVAILABLE
        ),
        ranking_trace=trace,
    )


def _criterion(
    result: SupplierEvaluation, criterion: RankingCriterion
) -> CriterionEvaluation:
    return next(item for item in result.criterion_evaluations if item.criterion == criterion)


def _source_refs(quote: QuoteInput, fields: set[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            reference.source_id
            for candidate in quote.candidates
            if candidate.field_name in fields
            for reference in candidate.source_refs
        )
    )


def _rate_display(metric) -> str | None:
    if metric is None or metric.rate is None:
        return None
    return f"{metric.numerator}/{metric.denominator} ({metric.rate * 100:.2f}%)"


def _deduplicate(issues: list[RuleIssue]) -> tuple[RuleIssue, ...]:
    unique: dict[tuple[str, tuple[str, ...], str], RuleIssue] = {}
    for issue in issues:
        unique[(issue.code, issue.fields, issue.message)] = issue
    return tuple(unique.values())


def _budget_lower_bound(
    requirement: ProcurementRequirement,
    cost: CostCalculation | None,
) -> Decimal | None:
    if cost is None or cost.currency != requirement.currency or cost.goods_cost is None:
        return None
    amount = cost.goods_cost
    if requirement.includes_shipping and cost.shipping_cost is not None:
        amount += cost.shipping_cost
    if cost.other_fees_cost is not None:
        amount += cost.other_fees_cost
    return amount
