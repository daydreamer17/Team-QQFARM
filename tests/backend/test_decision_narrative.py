"""Fixed structured inputs; no live model calls or evaluation answers at runtime."""
from datetime import date, datetime, timezone

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
