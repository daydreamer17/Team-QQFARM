import json
import os
from collections import Counter
from pathlib import Path

import pytest

from supplier_comparison.backend.conversations import ConversationModelConfig
from supplier_comparison.backend.decision_intents import route_conversation_intent


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
