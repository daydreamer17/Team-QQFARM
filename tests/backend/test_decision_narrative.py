"""Fixed structured inputs; no live model calls or evaluation answers at runtime."""
from datetime import date, datetime, timezone
from decimal import Decimal

from supplier_comparison.backend.decision_narrative import render_decision_preview
from supplier_comparison.rules import ComparisonRequest, DecisionImpactRequest, RequirementChanges, simulate_requirement_change
from tests.rules.test_engine import _quote, _requirement


def trial_for(**changes):
    quotes = []
    for supplier_id, name, price, days in (
        ('SUP-023', 'Schwarzwald Circuits', '9.65375', 5),
        ('SUP-024', 'Sterling Components', '9.66', 3),
        ('SUP-030', 'Sterling Semitech', '9.66', 1),
    ):
        quote = _quote(supplier_id, name, moq_quantity=1000, moq_unit='piece',
                       units_per_pack=1, order_multiple=1, unit_price=price,
                       price_basis_quantity=1, shipping_status='FREE', shipping_amount='0', lead_days=days)
        quote = quote.model_copy(update={'supplier_id': supplier_id, 'candidates': tuple(
            c.model_copy(update={'normalized_value': '2026-10-30', 'raw_value': '2026-10-30'})
            if c.field_name == 'valid_until' else c for c in quote.candidates
        )})
        quotes.append(quote)
    requirement = type(_requirement()).model_validate(_requirement().model_dump() | {
        'budget_amount': '10000', 'planned_order_date': date(2026, 10, 14),
        'delivery_deadline': date(2026, 10, 24),
    })
    request = DecisionImpactRequest(task_id='task-preview', task_revision=1,
        comparison=ComparisonRequest(requirement=requirement, quotes=tuple(quotes),
                                     evaluated_at=datetime(2026, 10, 14, tzinfo=timezone.utc)),
        supplier_bindings={q.quote_id: q.supplier_id for q in quotes})
    trial = simulate_requirement_change(request, RequirementChanges(**changes), user_authorized=True)
    return trial, render_decision_preview(trial, currency='SGD',
        supplier_bindings=request.supplier_bindings, reference='SIMULATION:test-preview')


def test_tolerance_preview_uses_engine_pool_and_actual_winner():
    trial, text = trial_for(primary_criterion='LOWEST_CONFIRMED_TOTAL_COST',
                           secondary_criterion='FASTEST_CONFIRMED_DELIVERY', cost_tolerance_amount='10')
    assert trial.comparison.recommended_quote_ids == ('SUP-030',)
    assert 'this scenario recommends **Sterling Semitech (SUP-030)**' in text
    assert 'The lowest total cost in the current comparison scope is SGD 9,653.75 from Schwarzwald Circuits (SUP-023)' in text
    assert 'candidate ceiling is SGD 9,663.75' in text
    assert 'Sterling Components (SUP-024): SGD 9,660.00, estimated arrival 2026-10-17' in text
    assert 'Sterling Semitech (SUP-030): SGD 9,660.00, estimated arrival 2026-10-15' in text
    assert all(mark not in text for mark in ('：', '（', '）', '。'))
    assert text.endswith('Generate a scenario using these conditions?')


def test_tolerance_without_secondary_keeps_tie():
    trial, text = trial_for(primary_criterion='LOWEST_CONFIRMED_TOTAL_COST', cost_tolerance_amount='10')
    assert len(trial.comparison.recommended_quote_ids) == 3
    assert 'are tied' in text and 'this scenario recommends' not in text


def test_clearing_tolerance_is_visible_before_application():
    trial, text = trial_for(primary_criterion='LONGEST_CONFIRMED_PAYMENT_TERM',
                           secondary_criterion='LOWEST_CONFIRMED_TOTAL_COST', cost_tolerance_amount=None)
    assert trial.changes['cost_tolerance_amount'] is None
    assert 'clears the existing cost-tolerance setting' in text
    assert 'has not been applied and requires your confirmation' in text


def test_history_missing_and_empty_scope_never_invent_winner():
    trial, text = trial_for(primary_criterion='HIGHEST_HISTORICAL_ON_TIME_RATE',
                           excluded_supplier_ids=('SUP-030',))
    assert not trial.comparison.recommended_quote_ids
    assert 'a recommended supplier cannot yet be determined' in text
    trial, text = trial_for(excluded_supplier_ids=('SUP-023', 'SUP-024', 'SUP-030'))
    assert not trial.comparison.recommended_quote_ids
    assert 'current scope is empty' in text


def test_budget_preview_does_not_call_a_non_compliant_quote_policy_eligible():
    trial, _text = trial_for(budget_amount='10000')
    non_compliant_id = trial.comparison.supplier_results[0].quote_id
    comparison = trial.comparison.model_copy(update={
        'compliance_assessment': {
            'assessments': [{
                'quote_id': non_compliant_id,
                'status': 'NON_COMPLIANT',
            }],
        },
    })
    trial = trial.model_copy(update={'comparison': comparison})

    text = render_decision_preview(
        trial,
        currency='SGD',
        supplier_bindings={row.quote_id: row.quote_id for row in comparison.supplier_results},
        reference='SIMULATION:test-policy-filter',
    )

    eligible_line = next(
        line for line in text.splitlines()
        if 'policy-eligible quotations that remain in the ranking' in line
    )
    assert non_compliant_id not in eligible_line


def test_scenario_preview_discloses_amount_approval_boundary():
    trial, _text = trial_for(primary_criterion='LOWEST_CONFIRMED_TOTAL_COST')
    winner_id = trial.comparison.recommended_quote_ids[0]
    supplier_results = tuple(
        row.model_copy(update={'total_cost': Decimal('7100.00')})
        if row.quote_id == winner_id else row
        for row in trial.comparison.supplier_results
    )
    comparison = trial.comparison.model_copy(update={
        'supplier_results': supplier_results,
        'compliance_assessment': {
            'assessments': [],
            'amount_requirements': [{
                'quote_id': winner_id,
                'triggered': True,
                'approval_confirmed': False,
                'currency': 'SGD',
                'threshold': '7000.00',
            }],
        },
    })
    trial = trial.model_copy(update={'comparison': comparison})

    text = render_decision_preview(
        trial,
        currency='SGD',
        supplier_bindings={row.quote_id: row.quote_id for row in comparison.supplier_results},
        reference='SIMULATION:test-approval-boundary',
    )

    assert 'reaches the SGD 7,000.00 amount-approval threshold' in text
    assert 'requires separate amount approval' in text
    assert 'this simulation does not grant approval' in text
    assert text.endswith('Generate a scenario using these conditions?')
