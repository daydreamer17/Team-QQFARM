import json
import os
from collections import Counter
from pathlib import Path

import pytest

from supplier_comparison.backend.conversations import ConversationModelConfig
from supplier_comparison.backend.decision_intents import route_conversation_intent


def test_live_model_benchmark_catalog_is_fixed_and_auditable():
    path = Path(__file__).resolve().parents[2] / "evaluation/reference/live_model_benchmark_cases.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert payload["schema_version"] == "live-model-benchmark/1.0.0"
    assert payload["dataset_id"] == "quotewise-live-model-en-16-v1"
    assert len(cases) == 16
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["question"] for case in cases}) == len(cases)
    assert all(case["expected_route"] in {"INVESTIGATE", "SIMULATE"} for case in cases)
    assert sum(case["expected_route"] == "INVESTIGATE" for case in cases) == 14
    assert sum(case["expected_route"] == "SIMULATE" for case in cases) == 2
    assert all(
        bool(case["required_tools"]) == (case["expected_route"] == "INVESTIGATE")
        for case in cases
    )
    assert all(
        case.get("expected_change_fields") == [
            "budget_amount" if case["id"].endswith("budget-6800") else "delivery_deadline"
        ]
        for case in cases if case["expected_route"] == "SIMULATE"
    )
    assert all(case["id"].startswith("en-") for case in cases)
    assert all(
        not any("\u4e00" <= character <= "\u9fff" for character in case["question"])
        for case in cases
    )
    assert all(
        set(case["required_tools"])
        <= {"inspect_quote_evidence", "inspect_supplier_history", "inspect_policy_evidence"}
        for case in cases
    )


def test_chatbot_agent_acceptance_catalog_is_complete_and_balanced():
    path = Path(__file__).resolve().parents[2] / "evaluation/reference/chatbot_agent_questions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert len(cases) == 20
    assert len({case["id"] for case in cases}) == 20
    assert len({case["question"] for case in cases}) == 20
    assert Counter(case["category"] for case in cases) == {
        "解释当前结果": 4,
        "按需深入核查": 4,
        "给出可验证的回答": 4,
        "做条件试算": 4,
        "处理无法回答的情况": 4,
    }
    assert all(case["expected_route"] in {
        "EXPLAIN", "INVESTIGATE", "SIMULATE", "CLARIFY", "UNSUPPORTED",
    } for case in cases)
    assert all(len(case["expected"]) >= 3 for case in cases)


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).resolve().parents[2] / "evaluation/reference/chatbot_agent_questions.json")
               .read_text(encoding="utf-8"))["cases"],
    ids=lambda case: case["id"],
)
def test_live_chatbot_agent_question_routing(case):
    if os.getenv("RUN_CHATBOT_AGENT_LIVE") != "1":
        pytest.skip("Set RUN_CHATBOT_AGENT_LIVE=1 for paid 20-question routing acceptance")
    config = ConversationModelConfig.from_env()
    assert config is not None
    context = {
        "recent_messages": [{"role": "USER", "content": case["question"]}],
        "current_requirement": {
            "budget_amount": "8000.00", "currency": "SGD",
            "delivery_deadline": "2026-11-15",
        },
        "current_decision_preferences": {
            "primary_criterion": "FASTEST_CONFIRMED_DELIVERY",
            "secondary_criterion": None,
            "cost_tolerance_amount": None,
            "excluded_supplier_ids": [],
        },
        "available_supplier_ids": ["SUP-022", "SUP-023", "SUP-024", "SUP-029"],
        "supplier_directory": [
            {"supplier_id": "SUP-022", "name": "Redwood Components"},
            {"supplier_id": "SUP-023", "name": "Schwarzwald Circuits"},
            {"supplier_id": "SUP-024", "name": "Sterling Components"},
            {"supplier_id": "SUP-029", "name": "Great Wall Components"},
        ],
        "prior_user_context": [],
    }
    intent, calls = route_conversation_intent(context, config)
    assert intent.route == case["expected_route"], (case, intent)
    assert calls in {0, 1}
