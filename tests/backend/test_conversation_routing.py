"""Routing is tested separately from deterministic preview and factual narration."""
import json

import pytest

from supplier_comparison.backend import conversations
from supplier_comparison.backend.conversations import ConversationModelConfig, process_conversation_turn
from supplier_comparison.rag.clients import ModelClientError


CONFIG = ConversationModelConfig("fixed-test", "fixed-model", "https://invalid.test", "UNUSED")


def context():
    return {
        "recent_messages": [{"role": "USER", "content": "最低价加10新币以内选最快的"}],
        "current_decision_preferences": {
            "primary_criterion": "HIGHEST_HISTORICAL_ON_TIME_RATE",
            "secondary_criterion": "FASTEST_CONFIRMED_DELIVERY",
            "excluded_supplier_ids": ["SUP-030"],
        },
        "available_supplier_ids": ["SUP-024", "SUP-030"],
        "frozen_references": {"RESULT:old": {"recommendation": "old result must not enter routing"}},
    }


def payload(value):
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}


def test_simulation_routes_without_fact_narration(monkeypatch):
    captured = []
    def call(config, messages, **kwargs):
        captured.append(messages)
        assert config.max_attempts == 1
        return payload({"route": "SIMULATE", "changes": {
            "primary_criterion": "LOWEST_CONFIRMED_TOTAL_COST",
            "secondary_criterion": "FASTEST_CONFIRMED_DELIVERY",
            "cost_tolerance_amount": "10",
        }}), 1
    monkeypatch.setattr(conversations, "_call_conversation_model", call)
    turn, calls = process_conversation_turn(context(), CONFIG)
    assert calls == len(captured) == 1
    assert turn["assistant_text"] == ""
    assert turn["changes"]["cost_tolerance_amount"] == "10"
    assert "excluded_supplier_ids" not in turn["changes"]  # omitted means preserve
    assert "old result must not enter routing" not in str(captured)


@pytest.mark.parametrize('prompt', [
    '设置三个排序指标：先成本最低，再最快到货，再最长账期。',
    'Use three ranking criteria: cost, delivery, payment terms.',
])
def test_more_than_two_requested_criteria_are_not_silently_dropped(monkeypatch, prompt):
    def unexpected_call(*args, **kwargs):
        raise AssertionError('Explicit unsupported criterion count needs no model call')
    monkeypatch.setattr(conversations, '_call_conversation_model', unexpected_call)
    ctx = context()
    ctx['recent_messages'] = [{'role': 'USER', 'content': prompt}]
    turn, calls = process_conversation_turn(ctx, CONFIG)
    assert calls == 0
    assert turn['changes'] is None
    assert turn['clarification'] == 'CHANGE_DETAILS'


@pytest.mark.parametrize('prompt', [
    '我只能在2026年11月8日当天收货，不能提前，也不能延后。',
    '我只在那天有时间收货',
    '我就想在10月18号那天收到货，我就那天有时间',
    '请安排恰好当天送达',
    '我那天才有时间收货',
])
def test_exact_day_request_is_clarified_before_model_can_turn_it_into_deadline(monkeypatch, prompt):
    def unexpected_call(*args, **kwargs):
        raise AssertionError('Exact-day request must be clarified before model routing')
    monkeypatch.setattr(conversations, '_call_conversation_model', unexpected_call)
    ctx = context()
    ctx['recent_messages'] = [{'role': 'USER', 'content': prompt}]
    turn, calls = process_conversation_turn(ctx, CONFIG)
    assert calls == 0
    assert turn['changes'] is None
    assert turn['clarification'] == 'EXACT_DELIVERY_DAY'


@pytest.mark.parametrize('prompt', [
    '为什么那天才到货？',
    '为什么当天收货会比较晚？',
    '那天收货的依据是什么？',
    '为什么就那天到货，不能提前吗？',
])
def test_delivery_explanation_reaches_routing_and_grounded_narration(monkeypatch, prompt):
    ctx = context()
    ctx['recent_messages'] = [{'role': 'USER', 'content': prompt}]
    ctx['allowed_reference_ids'] = ['RESULT:delivery']
    ctx['frozen_references'] = {
        'RESULT:delivery': {'estimated_arrival_date': '2026-11-08'},
    }
    answer = {'assistant_text': '已确认预计到货日为2026-11-08（RESULT:delivery）。',
              'reference_ids': ['RESULT:delivery'], 'changes': None, 'clarification': None}
    captured = []

    def call(config, messages, **kwargs):
        captured.append(messages)
        return payload({'route': 'EXPLAIN'} if len(captured) == 1 else answer), 1

    monkeypatch.setattr(conversations, '_call_conversation_model', call)
    turn, calls = process_conversation_turn(ctx, CONFIG)
    assert calls == len(captured) == 2
    assert json.loads(captured[0][1]['content'])['latest_request'] == prompt
    assert turn['assistant_text'] == answer['assistant_text']
    assert turn['changes'] is None
    assert turn['clarification'] is None
    conversations.validate_conversation_turn(turn, ctx)


@pytest.mark.parametrize("value", [
    {"route": "SIMULATE", "changes": None},
    {"route": "EXPLAIN", "changes": {"budget_amount": "1"}},
    {"route": "SIMULATE", "changes": {"excluded_supplier_ids": ["fake"]}},
    {"route": "SIMULATE", "price_constraint": "UNSPECIFIED", "changes": {"primary_criterion": "FASTEST_CONFIRMED_DELIVERY"}},
])
def test_invalid_route_never_falls_back_to_old_answer(monkeypatch, value):
    monkeypatch.setattr(conversations, "_call_conversation_model", lambda *a, **k: (payload(value), 1))
    with pytest.raises(ModelClientError) as caught:
        process_conversation_turn(context(), CONFIG)
    assert caught.value.error_code == "conversation_intent_invalid"


def test_merged_preference_conflict_requires_clarification(monkeypatch):
    value = {"route": "SIMULATE", "changes": {"primary_criterion": "FASTEST_CONFIRMED_DELIVERY"}}
    monkeypatch.setattr(conversations, "_call_conversation_model", lambda *a, **k: (payload(value), 1))
    turn, _ = process_conversation_turn(context(), CONFIG)
    assert turn["changes"] is None
    assert turn["clarification"] == "CHANGE_DETAILS"


@pytest.mark.parametrize('primary', [
    'LONGEST_CONFIRMED_PAYMENT_TERM', 'FASTEST_CONFIRMED_DELIVERY',
    'HIGHEST_SUPPLIER_PERFORMANCE', 'HIGHEST_HISTORICAL_ON_TIME_RATE',
    'LOWEST_HISTORICAL_REJECTED_LINE_RATE',
])
def test_new_primary_proposes_explicit_removal_of_inherited_tolerance(monkeypatch, primary):
    ctx = context()
    ctx['current_decision_preferences'].update(
        primary_criterion='LOWEST_CONFIRMED_TOTAL_COST',
        secondary_criterion='FASTEST_CONFIRMED_DELIVERY', cost_tolerance_amount='20')
    value = {'route': 'SIMULATE', 'changes': {
        'primary_criterion': primary, 'secondary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST'}}
    monkeypatch.setattr(conversations, '_call_conversation_model', lambda *a, **k: (payload(value), 1))
    turn, _ = process_conversation_turn(ctx, CONFIG)
    assert turn['changes'] == {**value['changes'], 'cost_tolerance_amount': None}
    assert 'excluded_supplier_ids' not in turn['changes']
    assert ctx['current_decision_preferences']['cost_tolerance_amount'] == '20'


def test_tolerance_is_preserved_when_cost_remains_primary(monkeypatch):
    ctx = context()
    ctx['current_decision_preferences'].update(primary_criterion='LOWEST_CONFIRMED_TOTAL_COST', cost_tolerance_amount='20')
    value = {'route': 'SIMULATE', 'changes': {'secondary_criterion': 'LONGEST_CONFIRMED_PAYMENT_TERM'}}
    monkeypatch.setattr(conversations, '_call_conversation_model', lambda *a, **k: (payload(value), 1))
    turn, _ = process_conversation_turn(ctx, CONFIG)
    assert turn['changes'] == value['changes']


def test_narration_budget_counts_routing_call(monkeypatch):
    monkeypatch.setattr(conversations, "_call_conversation_model", lambda *a, **k:
                        (payload({"route": "EXPLAIN"}), 1))
    def fail(ctx, config, **kwargs):
        assert config.max_attempts == 2
        assert ctx["response_mode"] == "EXPLAIN_ONLY"
        raise ModelClientError("bad references", attempts=2, error_code="conversation_model_output_invalid")
    monkeypatch.setattr(conversations, "generate_conversation_turn", fail)
    with pytest.raises(ModelClientError) as caught:
        process_conversation_turn(context(), CONFIG)
    assert caught.value.attempts == 3


def test_cost_difference_is_not_confused_with_supplier_total():
    comparison = {'recommended_quote_ids': ['q1'], 'supplier_results': [
        {'quote_id': 'q1', 'supplier_name': 'Alpha', 'status': 'FEASIBLE', 'total_cost': '7000'},
        {'quote_id': 'q2', 'supplier_name': 'Beta', 'status': 'FEASIBLE', 'total_cost': '7100'},
    ]}
    conversations._validate_comparison_claims('Beta总成本为 SGD 7100，比推荐报价高 SGD 100。', [comparison])
    with pytest.raises(ValueError, match='cost difference'):
        conversations._validate_comparison_claims('Beta总成本为 SGD 7100，比推荐报价低 SGD 100。', [comparison])
    with pytest.raises(ValueError, match='wrong supplier'):
        conversations._validate_comparison_claims('Beta总成本为 SGD 100，比推荐报价高 SGD 100。', [comparison])


@pytest.mark.parametrize('messages,expected', [
    (['总价最低优先；如果比最低价最多贵10新币，就在这个范围内选最快到货的。', '就按刚才的来'],
     {'primary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST', 'secondary_criterion': 'FASTEST_CONFIRMED_DELIVERY', 'cost_tolerance_amount': '10'}),
    (['总价最低优先，容差10新币，范围内选最快。', '容差改成20新币，其他照旧'],
     {'primary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST', 'secondary_criterion': 'FASTEST_CONFIRMED_DELIVERY', 'cost_tolerance_amount': '20'}),
    (['重新纳入 Sterling Semitech，并按最低价加10新币的范围选最快到货'],
     {'excluded_supplier_ids': [], 'primary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST', 'secondary_criterion': 'FASTEST_CONFIRMED_DELIVERY', 'cost_tolerance_amount': '10'}),
])
def test_live_followup_intents(messages, expected):
    """Only synthetic identity/preferences; no existing task or reference answers."""
    import os
    if os.getenv('RUN_CONVERSATION_LIVE') != '1':
        pytest.skip('Set RUN_CONVERSATION_LIVE=1 for paid intent follow-up acceptance')
    ctx = context()
    ctx['supplier_directory'] = [
        {'supplier_id': 'SUP-024', 'name': 'Sterling Components'},
        {'supplier_id': 'SUP-030', 'name': 'Sterling Semitech'},
    ]
    ctx['recent_messages'] = [{'role': 'USER', 'content': message} for message in messages]
    ctx.pop('frozen_references')
    config = ConversationModelConfig.from_env()
    assert config is not None
    turn, calls = process_conversation_turn(ctx, config)
    assert calls <= 3
    assert turn['changes'] is not None, turn
    for key, value in expected.items():
        assert turn['changes'].get(key) == value, turn
    if 'excluded_supplier_ids' not in expected:
        assert turn['changes'].get('excluded_supplier_ids', ['SUP-030']) == ['SUP-030']


def test_live_payment_priority_replaces_cost_tolerance():
    import os
    if os.getenv('RUN_CONVERSATION_LIVE') != '1':
        pytest.skip('Set RUN_CONVERSATION_LIVE=1 for paid synthetic payment preference acceptance')
    ctx = context()
    ctx.pop('frozen_references')
    ctx['current_decision_preferences'].update(primary_criterion='LOWEST_CONFIRMED_TOTAL_COST',
        secondary_criterion='FASTEST_CONFIRMED_DELIVERY', cost_tolerance_amount='20')
    ctx['recent_messages'] = [{'role': 'USER', 'content': '账期最长优先，如果并列再选确认总成本最低的。'}]
    config = ConversationModelConfig.from_env()
    assert config is not None
    turn, calls = process_conversation_turn(ctx, config)
    assert turn['clarification'] is None, turn
    assert turn['changes'] == {'primary_criterion': 'LONGEST_CONFIRMED_PAYMENT_TERM',
        'secondary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST', 'cost_tolerance_amount': None}, turn
    assert calls == 1
