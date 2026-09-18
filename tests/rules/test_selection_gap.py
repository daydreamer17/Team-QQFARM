from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from supplier_comparison.rules import (
    ComparisonRequest, DecisionImpactRequest, RequirementChanges, analyze_selection_gap,
    draft_clarification, simulate_requirement_change,
)
from tests.rules.test_engine import EVALUATED_AT, _requirement, _supplier_a, _supplier_b, _supplier_c


def request(*quotes):
    return DecisionImpactRequest(task_id='task-gap', task_revision=3,
        comparison=ComparisonRequest(requirement=_requirement(), quotes=quotes, evaluated_at=EVALUATED_AT))


def change(quote, **values):
    return quote.model_copy(update={'candidates': tuple(c.model_copy(update={
        'normalized_value': values[c.field_name], 'raw_value': str(values[c.field_name])
    }) if c.field_name in values else c for c in quote.candidates)})


def test_delivery_only_gap_is_computed_and_conditionally_becomes_cheapest():
    b = change(_supplier_b(shipping_status='KNOWN_AMOUNT', shipping_amount='200.00'), lead_time_days=7)
    original = request(b, _supplier_c())
    before = original.model_dump(mode='json')
    gap = analyze_selection_gap(original).gaps[0]
    assert gap.delivery_days_late == 2
    assert gap.target_lead_time_days == 5
    assert gap.cost_difference_vs_other == Decimal('-100.00')
    assert gap.would_be_quote_comparison_choice is True
    assert gap.delivery_improvement.hypothetical
    assert not gap.delivery_improvement.formal_recommendation_allowed
    assert not gap.delivery_improvement.policy_assessment_performed
    assert original.model_dump(mode='json') == before
    draft = draft_clarification(gap)
    assert draft['draft_only'] and not draft['sent']
    assert '2026-09-19' in draft['text'] and '重新审核' in draft['text']


def test_shorter_delivery_does_not_fix_budget_or_specification():
    a = change(_supplier_a(), package='QFN-48')
    gap = analyze_selection_gap(request(a, _supplier_c())).gaps[0]
    assert gap.confirmed_total_cost == Decimal('12800')
    assert gap.budget_excess == Decimal('4800')
    assert gap.total_cost_reduction_to_tie_other == Decimal('5700')
    assert len(gap.failed_reasons) >= 3
    assert gap.would_be_quote_comparison_choice is False
    assert gap.delivery_improvement.comparison.supplier_results[0].failed_reasons


def test_unknown_shipping_remains_unknown_even_if_delivery_is_improved():
    b = change(_supplier_b(), lead_time_days=7)
    gap = analyze_selection_gap(request(b, _supplier_c())).gaps[0]
    assert gap.confirmed_total_cost is None
    assert gap.budget_excess is None and gap.cost_difference_vs_other is None
    assert gap.pending_reasons and gap.failed_reasons
    assert gap.would_be_quote_comparison_choice is False
    assert not gap.delivery_improvement.comparison.final_recommendation_allowed


def test_tie_is_not_a_unique_winner_and_all_infeasible_has_no_baseline():
    b = change(_supplier_b(shipping_status='KNOWN_AMOUNT', shipping_amount='200.00'), lead_time_days=7, unit_price='6.90')
    gap = analyze_selection_gap(request(b, _supplier_c())).gaps[0]
    assert gap.would_be_quote_comparison_choice is True
    assert len(gap.delivery_improvement.comparison.recommended_quote_ids) == 2
    assert analyze_selection_gap(request(_supplier_a())).gaps[0].best_other_confirmed_cost is None


def test_unsupported_ranking_and_delivery_are_not_claimed_solved():
    b = change(_supplier_b(shipping_status='KNOWN_AMOUNT', shipping_amount='200.00'), lead_time_days=7, day_basis='BUSINESS_DAYS')
    original = request(b, _supplier_c())
    gap = analyze_selection_gap(original).gaps[0]
    assert gap.delivery_days_late is None and gap.delivery_improvement is None
    requirement = original.comparison.requirement.model_copy(update={'ranking_preference': 'ESG_FIRST'})
    report = analyze_selection_gap(original.model_copy(update={'comparison': original.comparison.model_copy(
        update={'requirement': requirement})}))
    assert report.gaps[0].comparison_reasons


def test_requirement_simulation_requires_authorization_and_never_changes_inputs():
    original = request(_supplier_a(), _supplier_c())
    before = original.model_dump(mode='json')
    changes = RequirementChanges(budget_amount='13000', delivery_deadline=date(2026, 9, 21))
    with pytest.raises(ValueError, match='authorization'):
        simulate_requirement_change(original, changes)
    trial = simulate_requirement_change(original, changes, user_authorized=True)
    assert not trial.formal_recommendation_allowed
    assert original.model_dump(mode='json') == before
    with pytest.raises(ValidationError):
        simulate_requirement_change(original, RequirementChanges(delivery_deadline=date(2026, 9, 13)), user_authorized=True)
    with pytest.raises(ValidationError):
        RequirementChanges(budget_amount=10000.0)
    with pytest.raises(ValidationError):
        RequirementChanges()
