"""Routing is tested separately from deterministic preview and factual narration."""
import json
from decimal import Decimal

import pytest

from supplier_comparison.backend import conversations
from supplier_comparison.backend.conversations import ConversationModelConfig, process_conversation_turn
from supplier_comparison.backend.decision_intents import route_conversation_intent
from supplier_comparison.backend.investigation_answers import compose_investigation_answer
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


@pytest.mark.parametrize("prompt", [
    "为什么推荐 Schwarzwald Circuits？请同时核对报价、交期、历史表现和制度要求。",
    "Great Wall Components 价格最低却没有被推荐，主要原因是什么？请查证后回答。",
    "Schwarzwald 的到货日期可靠吗？请核对报价原文和历史准时率，发现异常继续追查。",
    "如果优先最快到货，哪两家最值得比较？请核对它们的交期证据和历史履约表现。",
    "Great Wall 真的是总成本最低吗？请检查单价、数量、运费、税费和其他费用。",
    "Schwarzwald 比最低报价贵多少？多付的钱是否换来了更快交期或更低履约风险？",
    "哪家供应商历史表现最好？请综合评级、准时率和拒收率后判断。",
    "当前推荐供应商的历史记录中有没有不利信息？如果有，请说明是否影响推荐。",
    "当前推荐供应商是否满足供应商准入、RoHS 和金额审批要求？请引用规则和证明材料。",
    "哪些供应商因制度检查未通过而不能推荐？分别缺少或违反了什么要求？",
    "如果报价内容、供应商记录和证明材料存在冲突，请找出冲突并判断应该相信哪一项。",
    "检查当前推荐依据是否存在前后矛盾；如果发现异常，请继续核对原始文件和制度规则。",
    "哪些供应商的证明材料缺失、过期或供应商编号不匹配？这会怎样影响推荐？",
    "当前还有哪些问题无法通过现有工具确认？请列出需要补传的文件或需要人工确认的事项。",
    "Why is Schwarzwald Circuits recommended? Please verify its quotation, delivery, historical performance, and policy requirements.",
    "Great Wall Components has the lowest price but was not recommended. What is the main reason? Verify the evidence before answering.",
    "Is Schwarzwald's arrival date reliable? Check the source quotation and historical on-time rate, and continue investigating if you find an anomaly.",
    "If fastest delivery is prioritised, which two suppliers are most worth comparing? Verify their delivery evidence and historical fulfilment performance.",
    "Does Great Wall really have the lowest total cost? Check unit price, quantity, freight, taxes, and other charges.",
    "How much more expensive is Schwarzwald than the lowest quotation? Does the premium buy faster delivery or lower fulfilment risk?",
    "Which supplier has the best historical performance? Judge using rating, on-time rate, and rejection rate.",
    "Does the recommended supplier's historical record contain adverse information? If so, explain whether it affects the recommendation.",
    "Does the recommended supplier satisfy supplier eligibility, RoHS, and amount approval requirements? Cite the rules and supporting evidence.",
    "Which suppliers cannot be recommended because they failed policy checks? State which requirement each one lacks or violates.",
    "If the quotation, supplier records, and supporting evidence conflict, identify the conflict and decide which source should be relied on.",
    "Check whether the current recommendation basis contains contradictions. If you find an anomaly, continue checking source documents and policy rules.",
    "Which suppliers have missing or expired evidence, or mismatched supplier IDs? How does this affect the recommendation?",
    "Which issues cannot be confirmed with the available tools? List the files to upload or the items requiring manual confirmation.",
    "请把两份最早交付的报价拉出来，结合原始交期和履约记录做横向核验。",
    "请核对 Great Wall 的落地成本构成，不要只看汇总价格。",
    "Audit the two earliest offers against source lead times and supplier performance records.",
    "Audit the recommended vendor's eligibility, RoHS coverage, and approval-threshold evidence.",
])
def test_bilingual_procurement_evidence_questions_route_without_model(monkeypatch, prompt):
    monkeypatch.setattr(
        conversations,
        "_call_conversation_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit evidence question must not need model routing")
        ),
    )
    ctx = context()
    ctx["recent_messages"] = [{"role": "USER", "content": prompt}]

    intent, calls = route_conversation_intent(ctx, CONFIG)

    assert calls == 0
    assert intent.route == "INVESTIGATE"


def test_general_investigation_renderer_uses_intent_and_fictional_supplier_evidence():
    reference = "INVESTIGATION:fictional-case"
    context_data = {
        "result_id": "fictional-result",
        "recent_messages": [{
            "role": "USER",
            "content": "How much more expensive is Northstar than the cheapest offer, and does it reduce delivery risk?",
        }],
        "allowed_reference_ids": [reference, "RESULT:fictional-result"],
        "frozen_references": {},
    }
    observations = [{
        "result": {
            "tool_name": "read_decision_overview",
            "status": "OK",
            "data": {
                "recommended_quote_ids": ["q-north"],
                "ranking_preference": "FASTEST_CONFIRMED_DELIVERY",
                "investigation_plan": {
                    "analysis_goal": "DELTA_COMPARISON",
                    "dimensions": ["COST", "DELIVERY", "HISTORY"],
                },
                "suppliers": [
                    {"quote_id": "q-north", "supplier_name": "Northstar Industrial", "total_cost": "120.00", "estimated_arrival_date": "2026-10-01"},
                    {"quote_id": "q-value", "supplier_name": "Value Harbor", "total_cost": "100.00", "estimated_arrival_date": "2026-10-04"},
                ],
            },
        },
    }, {
        "result": {
            "tool_name": "inspect_supplier_history",
            "status": "OK",
            "data": {"quote_id": "q-north", "supplier": {"history_snapshot": {
                "overall_grade": "A", "on_time": {"rate": "0.98"}, "rejected_lines": {"rate": "0.01"},
            }}},
        },
    }, {
        "result": {
            "tool_name": "inspect_supplier_history",
            "status": "OK",
            "data": {"quote_id": "q-value", "supplier": {"history_snapshot": {
                "overall_grade": "C", "on_time": {"rate": "0.80"}, "rejected_lines": {"rate": "0.06"},
            }}},
        },
    }, {
        "result": {
            "tool_name": "compile_decision_brief",
            "status": "OK",
            "data": {"checked_quote_ids": ["q-north", "q-value"], "verified_risks": [], "unresolved_items": []},
        },
    }]

    turn = compose_investigation_answer(
        context_data,
        reference_id=reference,
        record={"observations": observations},
        observations=observations,
    )

    assert turn is not None
    assert "Northstar Industrial" in turn["assistant_text"]
    assert "Value Harbor" in turn["assistant_text"]
    assert "SGD 20.00" in turn["assistant_text"]
    assert "3 days earlier" in turn["assistant_text"]
    assert "lower historical fulfilment risk" in turn["assistant_text"]
    assert all(mark not in turn["assistant_text"] for mark in ("（", "）", "。", "："))
    assert turn["reference_ids"] == [reference]


def test_english_current_supplier_compliance_answer_is_scoped_and_localized():
    reference = "INVESTIGATION:current-compliance"
    context_data = {
        "result_id": "result-current-compliance",
        "recent_messages": [{
            "role": "USER",
            "content": "Does the current recommended supplier satisfy eligibility, RoHS, and amount approval requirements?",
        }],
        "allowed_reference_ids": [reference],
        "frozen_references": {},
    }
    observations = [{"result": {
        "tool_name": "read_decision_overview",
        "status": "OK",
        "data": {
            "recommended_quote_ids": ["q-current"],
            "investigation_plan": {
                "analysis_goal": "COMPLIANCE_REVIEW",
                "dimensions": ["COMPLIANCE"],
            },
            "suppliers": [
                {"quote_id": "q-current", "supplier_name": "Current Parts"},
                {"quote_id": "q-other", "supplier_name": "Other Components"},
            ],
        },
    }}, {"result": {
        "tool_name": "inspect_policy_evidence",
        "status": "OK",
        "data": {"compliance": {"assessments": [
            {"quote_id": "q-current", "supplier_name": "Current Parts", "status": "COMPLIANT", "checks": [
                {"control_code": "APPROVED_SUPPLIER", "status": "PASS"},
                {"control_code": "ROHS_COMPLIANCE", "status": "PASS"},
                {"control_code": "AMOUNT_APPROVAL", "status": "PASS"},
            ]},
            {"quote_id": "q-other", "supplier_name": "Other Components", "status": "NON_COMPLIANT", "checks": [
                {"control_code": "ROHS_COMPLIANCE", "status": "FAIL"},
            ]},
        ]}},
    }}, {"result": {
        "tool_name": "compile_decision_brief",
        "status": "OK",
        "data": {"checked_quote_ids": ["q-current"], "verified_risks": [], "unresolved_items": []},
    }}]

    turn = compose_investigation_answer(
        context_data,
        reference_id=reference,
        record={"observations": observations},
        observations=observations,
    )

    assert turn is not None
    assert "Current Parts' policy status is COMPLIANT" in turn["assistant_text"]
    assert "Other Components" not in turn["assistant_text"]
    assert all(mark not in turn["assistant_text"] for mark in ("（", "）", "。", "：", "的制度结论为"))


def test_grounding_accepts_normalized_tool_cost_fields_and_computed_cost_deltas():
    assert conversations._monetary_values({
        "field_name": "shipping_fee_amount", "normalized_value": "200.00",
    }) == {Decimal("200.00")}
    assert conversations._monetary_values({
        "cost_difference_vs_other": "400.00",
    }) == {Decimal("400.00")}


@pytest.mark.parametrize(("prompt", "field", "expected"), [
    ("如果预算降到 SGD 6,800，当前推荐会发生什么变化？先试算，不要修改正式需求。", "budget_amount", "6800"),
    ("If the budget is reduced to SGD 6,800, how would the current recommendation change? Simulate it without changing the official requirement.", "budget_amount", "6800"),
    ("如果要求提前到 2026-11-09 前到货，哪些供应商仍然可选？制度结论是否会改变？", "delivery_deadline", "2026-11-09"),
    ("If delivery is required on or before 2026-11-09, which suppliers remain eligible? Would the compliance conclusion change?", "delivery_deadline", "2026-11-09"),
])
def test_bilingual_explicit_scenarios_route_deterministically(monkeypatch, prompt, field, expected):
    monkeypatch.setattr(
        conversations,
        "_call_conversation_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit numeric scenario must not need model routing")
        ),
    )
    ctx = context()
    ctx["recent_messages"] = [{"role": "USER", "content": prompt}]

    intent, calls = route_conversation_intent(ctx, CONFIG)

    assert calls == 0
    assert intent.route == "SIMULATE"
    assert str(getattr(intent.changes, field)) == expected


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


def test_investigation_route_runs_agent_then_narrates_with_audit_reference(monkeypatch):
    from supplier_comparison.backend.decision_intents import ConversationIntent

    monkeypatch.setattr(
        "supplier_comparison.backend.decision_intents.route_conversation_intent",
        lambda *_args, **_kwargs: (ConversationIntent(route="INVESTIGATE"), 1),
    )
    seen = {}

    def investigate(ctx):
        seen["question"] = ctx["recent_messages"][-1]["content"]
        enriched = dict(ctx)
        enriched["frozen_references"] = {
            "INVESTIGATION:case-1": {"status": "RESOLVED", "observations": []},
        }
        enriched["allowed_reference_ids"] = ["INVESTIGATION:case-1"]
        enriched["investigation_reference_id"] = "INVESTIGATION:case-1"
        return enriched, 3

    def narrate(ctx, config, **_kwargs):
        assert ctx["response_mode"] == "INVESTIGATION_ONLY"
        assert config.max_attempts == CONFIG.max_attempts
        return {
            "assistant_text": "已完成证据核查（INVESTIGATION:case-1）。",
            "reference_ids": ["INVESTIGATION:case-1"],
            "changes": None,
            "clarification": None,
        }, 1

    monkeypatch.setattr(conversations, "generate_conversation_turn", narrate)
    ctx = context()
    ctx["recent_messages"] = [{"role": "USER", "content": "请核查这个价格是否有报价原文依据"}]
    turn, calls = process_conversation_turn(ctx, CONFIG, investigate=investigate)

    assert seen["question"] == "请核查这个价格是否有报价原文依据"
    assert turn["reference_ids"] == ["INVESTIGATION:case-1"]
    assert calls == 5  # intent + three Agent decisions + grounded narration


def test_investigation_route_uses_grounded_fallback_when_model_narration_is_invalid(monkeypatch):
    from supplier_comparison.backend.decision_intents import ConversationIntent

    monkeypatch.setattr(
        "supplier_comparison.backend.decision_intents.route_conversation_intent",
        lambda *_args, **_kwargs: (ConversationIntent(route="INVESTIGATE"), 1),
    )

    def investigate(ctx):
        enriched = dict(ctx)
        reference = "INVESTIGATION:case-fallback"
        enriched["frozen_references"] = {reference: {
            "status": "RESOLVED",
            "observations": [{
                "result": {
                    "tool_name": "inspect_quote_evidence",
                    "status": "OK",
                    "data": {"supplier_name": "Alpha", "focus": "COST"},
                },
            }, {
                "result": {
                    "tool_name": "compile_decision_brief",
                    "status": "OK",
                    "data": {
                        "requires_follow_up": False,
                        "unresolved_items": [],
                        "verified_advantages": [
                            {"summary": "Alpha 的交期证据已核实。"},
                            {"summary": "Alpha 的报价原文已核实。"},
                        ],
                        "verified_risks": [
                            {"summary": "Alpha 的历史拒收率不为零。"},
                            {"summary": "Alpha 的供应商评级仍需关注。"},
                        ],
                        "stop_reason": "The required investigation is complete, with no missing or conflicting evidence found.",
                    },
                },
            }],
        }}
        enriched["allowed_reference_ids"] = [reference]
        enriched["investigation_reference_id"] = reference
        return enriched, 3

    def invalid_narration(*_args, **_kwargs):
        raise ModelClientError(
            "unsupported compliance claim", attempts=2,
            error_code="conversation_model_output_invalid",
        )

    monkeypatch.setattr(conversations, "generate_conversation_turn", invalid_narration)
    ctx = context()
    ctx["recent_messages"] = [{"role": "USER", "content": "核查报价成本证据"}]
    turn, calls = process_conversation_turn(ctx, CONFIG, investigate=investigate)

    assert calls == 6
    assert turn["reference_ids"] == ["INVESTIGATION:case-fallback"]
    assert "已核实优势" in turn["assistant_text"]
    assert "已核实风险" in turn["assistant_text"]
    assert "历史拒收率不为零" in turn["assistant_text"]
    assert "供应商评级仍需关注" in turn["assistant_text"]
    assert "Alpha" in turn["assistant_text"]
    assert "待补事项" in turn["assistant_text"]
    assert "停止原因" in turn["assistant_text"]


def test_persistence_rejects_an_answer_in_the_wrong_question_language():
    chinese_context = context()
    chinese_context["recent_messages"] = [{"role": "USER", "content": "为什么推荐这家供应商？"}]
    with pytest.raises(ValueError, match="Simplified Chinese"):
        conversations.validate_conversation_turn({
            "assistant_text": "This answer is in the wrong language.",
            "reference_ids": [],
            "changes": None,
            "clarification": None,
        }, chinese_context)

    english_context = context()
    english_context["recent_messages"] = [{"role": "USER", "content": "Why is this supplier recommended?"}]
    with pytest.raises(ValueError, match="English"):
        conversations.validate_conversation_turn({
            "assistant_text": "这个回答使用了错误的语言。",
            "reference_ids": [],
            "changes": None,
            "clarification": None,
        }, english_context)


def _investigation_risk_context():
    reference = "INVESTIGATION:case-risk-summary"
    ctx = context()
    ctx.update({
        "response_mode": "INVESTIGATION_ONLY",
        "investigation_reference_id": reference,
        "allowed_reference_ids": [reference],
        "frozen_references": {reference: {
            "status": "RESOLVED",
            "observations": [{
                "result": {
                    "tool_name": "compile_decision_brief",
                    "status": "OK",
                    "data": {
                        "requires_follow_up": False,
                        "unresolved_items": [],
                        "verified_advantages": [{
                            "supplier_name": "Schwarzwald Circuits",
                            "summary": "Schwarzwald Circuits：预计到货最早。",
                        }],
                        "verified_risks": [{
                            "supplier_name": "Schwarzwald Circuits",
                            "summary": "Schwarzwald Circuits：综合评级 C、历史拒收率 9.09%。",
                        }],
                        "stop_reason": (
                            "当前排序依据要求的核查已完成，未发现证据缺失或冲突；"
                            "已核实风险仍保留在结论中。"
                        ),
                    },
                },
            }],
        }},
    })
    return ctx, reference


def test_investigation_answer_cannot_equate_no_evidence_gap_with_no_risk():
    ctx, reference = _investigation_risk_context()
    turn = {
        "assistant_text": (
            f"已核实风险：未发现风险（{reference}）。"
            f"尚待追查事项：未发现证据缺失或冲突（{reference}）。"
            f"停止原因：必要核查已完成（{reference}）。"
        ),
        "reference_ids": [reference],
        "changes": None,
        "clarification": None,
    }

    with pytest.raises(ValueError, match="confused no unresolved evidence with no risk"):
        conversations.validate_conversation_turn(turn, ctx)


def test_investigation_fallback_keeps_risks_when_there_is_no_evidence_gap():
    ctx, reference = _investigation_risk_context()
    ctx["recent_messages"] = [{"role": "USER", "content": "请核对交期证据和历史履约表现"}]

    turn = conversations.deterministic_investigation_explanation(ctx)

    assert turn is not None
    assert "历史拒收率 9.09%" in turn["assistant_text"]
    assert "未发现需要继续调用现有工具处理的证据缺失或冲突" in turn["assistant_text"]
    assert "已核实风险仍保留在结论中" in turn["assistant_text"]


def test_investigation_answer_cannot_hide_a_verified_adverse_rate():
    ctx, reference = _investigation_risk_context()
    turn = {
        "assistant_text": (
            f"已核实风险：Schwarzwald Circuits 综合评级 C（{reference}）。"
            f"尚待追查事项：未发现证据缺失或冲突（{reference}）。"
            f"停止原因：必要核查已完成（{reference}）。"
        ),
        "reference_ids": [reference],
        "changes": None,
        "clarification": None,
    }

    with pytest.raises(ValueError, match="omitted a verified adverse rate"):
        conversations.validate_conversation_turn(turn, ctx)


def test_investigation_answer_keeps_risk_separate_from_unresolved_items():
    ctx, reference = _investigation_risk_context()
    turn = {
        "assistant_text": (
            f"已核实风险：Schwarzwald Circuits 综合评级 C，历史拒收率 9.09%（{reference}）。"
            f"尚待追查事项：未发现证据缺失或冲突（{reference}）。"
            f"停止原因：必要核查已完成，已确认风险继续保留（{reference}）。"
        ),
        "reference_ids": [reference],
        "changes": None,
        "clarification": None,
    }

    output = conversations.validate_conversation_turn(turn, ctx)

    assert output.reference_ids == [reference]


def test_investigation_route_without_enabled_agent_is_explicit(monkeypatch):
    from supplier_comparison.backend.decision_intents import ConversationIntent

    monkeypatch.setattr(
        "supplier_comparison.backend.decision_intents.route_conversation_intent",
        lambda *_args, **_kwargs: (ConversationIntent(route="INVESTIGATE"), 1),
    )
    turn, calls = process_conversation_turn(context(), CONFIG)
    assert calls == 1
    assert turn["clarification"] == "INVESTIGATION_UNAVAILABLE"
    assert turn["changes"] is None


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


def test_generic_evidence_counts_are_not_treated_as_procurement_quantities():
    assert conversations._quantity_values('已核对1个报价字段和2个证据来源。') == set()
    assert conversations._quantity_values('采购数量为1,000个。') == {1000}
    assert conversations._quantity_values('实际采购1,000件。') == {1000}
    assert conversations._payload_quantity_values({'raw_text': '单价 SGD 660 / 100 pieces'}) == {100}


def comparison_context(question: str):
    result_id = 'RESULT:comparison-1'
    requirement_id = 'REQUIREMENT:requirement-1'
    comparison = {
        'recommended_quote_ids': ['q-fast'],
        'supplier_results': [
            {'quote_id': 'q-cheap', 'supplier_name': 'Great Wall Components', 'status': 'FEASIBLE',
             'total_cost': '6500.00', 'estimated_arrival_date': '2026-11-12'},
            {'quote_id': 'q-fast', 'supplier_name': 'Schwarzwald Circuits', 'status': 'FEASIBLE',
             'total_cost': '6900.00', 'estimated_arrival_date': '2026-11-07'},
        ],
    }
    return {
        'recent_messages': [{'role': 'USER', 'content': question}],
        'current_decision_preferences': {'primary_criterion': 'FASTEST_CONFIRMED_DELIVERY'},
        'available_supplier_ids': ['SUP-023', 'SUP-029'],
        'frozen_references': {
            result_id: comparison,
            requirement_id: {'primary_criterion': 'FASTEST_CONFIRMED_DELIVERY'},
        },
        'allowed_reference_ids': [result_id, requirement_id],
    }


@pytest.mark.parametrize('question,expected', [
    ('四家供应商里，成本和交期分别谁最好？', ('Great Wall Components', 'Schwarzwald Circuits')),
    ('Great Wall Components 明明最便宜，为什么没有排第一？',
     ('确认总成本最低', '最快确认到货', '当前推荐供应商是 Schwarzwald Circuits')),
    ('Why is Schwarzwald Circuits recommended instead of the cheapest supplier?',
     ('lowest confirmed total cost', 'fastest confirmed delivery', 'current recommendation is Schwarzwald Circuits')),
])
def test_common_comparison_questions_use_stable_grounded_answer(monkeypatch, question, expected):
    from supplier_comparison.backend.decision_intents import ConversationIntent
    monkeypatch.setattr(
        'supplier_comparison.backend.decision_intents.route_conversation_intent',
        lambda *_args, **_kwargs: (ConversationIntent(route='EXPLAIN'), 1),
    )
    monkeypatch.setattr(
        conversations, 'generate_conversation_turn',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('common comparison must not need narration model')),
    )
    ctx = comparison_context(question)
    turn, calls = process_conversation_turn(ctx, CONFIG)
    assert calls == 1
    assert all(value in turn['assistant_text'] for value in expected)
    conversations.validate_conversation_turn(turn, ctx)


@pytest.mark.parametrize("question", [
    "如果优先最早到货，哪两份报价最值得重点比较？",
    "If the priority is the earliest arrival, which two quotes should be compared most closely?",
])
def test_two_delivery_candidates_are_compared_without_creating_a_scenario(monkeypatch, question):
    monkeypatch.setattr(
        conversations,
        "_call_conversation_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("two-quotation comparison must not need model routing or narration")
        ),
    )
    ctx = comparison_context(question)
    result = ctx["frozen_references"]["RESULT:comparison-1"]
    result["recommended_quote_ids"] = ["q-redwood"]
    result["supplier_results"].extend([
        {"quote_id": "q-sterling", "supplier_name": "Sterling Components", "status": "FEASIBLE",
         "total_cost": "7100.00", "estimated_arrival_date": "2026-11-09"},
        {"quote_id": "q-redwood", "supplier_name": "Redwood Components", "status": "FEASIBLE",
         "total_cost": "6700.00", "estimated_arrival_date": "2026-11-10"},
    ])

    turn, calls = process_conversation_turn(ctx, CONFIG)

    assert calls == 0
    assert turn["changes"] is None
    assert turn["clarification"] is None
    assert "Schwarzwald Circuits" in turn["assistant_text"]
    assert "Sterling Components" in turn["assistant_text"]
    assert "Redwood Components" not in turn["assistant_text"]
    conversations.validate_conversation_turn(turn, ctx)


def test_superlative_is_checked_only_against_the_supplier_it_describes():
    comparison = comparison_context('')['frozen_references']['RESULT:comparison-1']
    conversations._validate_comparison_claims(
        'Schwarzwald Circuits 到货最快，而 Great Wall Components 成本最低。', [comparison],
    )
    with pytest.raises(ValueError, match='fastest-supplier'):
        conversations._validate_comparison_claims(
            'Great Wall Components 到货最快，而 Schwarzwald Circuits 成本更高。', [comparison],
        )


@pytest.mark.parametrize('other_status', ['FAIL', 'REVIEW_REQUIRED', 'NOT_EVALUATED'])
@pytest.mark.parametrize('text', ['Redwood RoHS通过', '所有供应商的RoHS通过'])
def test_control_pass_requires_every_applicable_clause(other_status, text):
    payload = {'assessments': [{'supplier_name': 'Redwood', 'status': 'REVIEW_REQUIRED',
        'checks': [{'clause_id': 'rohs-a', 'control_code': 'ROHS_COMPLIANCE', 'status': 'PASS'},
                   {'clause_id': 'rohs-b', 'control_code': 'ROHS_COMPLIANCE', 'status': other_status}]}]}
    with pytest.raises(ValueError, match='control pass'):
        conversations._validate_compliance_claims(text, [payload])


def test_control_claims_are_scoped_to_each_supplier_clause():
    payload = {'assessments': [
        {'supplier_name': 'Redwood', 'status': 'COMPLIANT',
         'checks': [{'control_code': 'ROHS_COMPLIANCE', 'status': 'PASS'}]},
        {'supplier_name': 'Sterling', 'status': 'REVIEW_REQUIRED',
         'checks': [{'control_code': 'ROHS_COMPLIANCE', 'status': 'REVIEW_REQUIRED'}]},
    ]}
    conversations._validate_compliance_claims('Redwood RoHS通过，而 Sterling RoHS待复核', [payload])
    with pytest.raises(ValueError, match='control pass'):
        conversations._validate_compliance_claims('Redwood RoHS通过，而 Sterling RoHS通过', [payload])


def test_control_failure_is_not_mistaken_for_pass_and_checks_all_clauses():
    payload = {'assessments': [{'supplier_name': 'Redwood', 'status': 'NON_COMPLIANT',
        'checks': [{'control_code': 'ROHS_COMPLIANCE', 'status': 'PASS'},
                   {'control_code': 'ROHS_COMPLIANCE', 'status': 'FAIL'}]}]}
    conversations._validate_compliance_claims('Redwood RoHS不通过', [payload])


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
