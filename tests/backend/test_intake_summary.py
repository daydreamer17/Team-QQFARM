from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from supplier_comparison.backend import conversations, decision_intents, intake, summaries
from supplier_comparison.backend.conversations import (
    ConversationModelConfig,
    generate_conversation_turn,
    narrative_chunks,
)
from supplier_comparison.backend.decision_intents import (
    DecisionIntentModelConfig,
    parse_decision_intent,
)
from supplier_comparison.backend.intake import (
    RequirementModelConfig,
    extract_requirement_candidates,
    parse_requirement_document,
    safe_filename_extension,
)
from supplier_comparison.backend.summaries import SummaryModelConfig, generate_summary_narrative
from supplier_comparison.rag.clients import ModelClientError


REPO_ROOT = Path(__file__).resolve().parents[2]


def _model_payload(body: dict, *, finish_reason: object = "stop") -> dict:
    return {
        "choices": [
            {
                "finish_reason": finish_reason,
                "message": {"content": json.dumps(body, ensure_ascii=False)},
            }
        ]
    }


def test_requirement_parser_supports_pdf_txt_and_markdown(tmp_path: Path) -> None:
    pdf = REPO_ROOT / "data/generated/fixtures/extraction/requirements/procurement_requirement_v2.pdf"
    parsed_pdf = parse_requirement_document(pdf, "application/pdf")
    assert parsed_pdf["sources"][0]["source_id"] == "requirement:page:1"
    assert "PROCUREMENT REQUIREMENT" in parsed_pdf["sources"][0]["raw_text"]

    text = tmp_path / "requirement.txt"
    text.write_text("Manufacturer: QQ Demo Components\nQuantity: 1000 pieces\n", encoding="utf-8")
    parsed_text = parse_requirement_document(text, "text/plain")
    assert [item["source_id"] for item in parsed_text["sources"]] == [
        "requirement:line:1",
        "requirement:line:2",
    ]

    markdown = tmp_path / "requirement.md"
    markdown.write_text("# Requirement\nDelivery deadline: 2026-09-19\n", encoding="utf-8")
    assert parse_requirement_document(markdown, "text/markdown")["sources"][1]["line_number"] == 2


def test_requirement_parser_rejects_empty_and_invalid_utf8(tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="requirement_text_unavailable"):
        parse_requirement_document(empty, "text/plain")

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"Requirement: \xff\xfe\xfa")
    with pytest.raises(ValueError, match="requirement_text_encoding_invalid"):
        parse_requirement_document(invalid, "text/markdown")

    scanned = REPO_ROOT / "data/generated/fixtures/extraction/ocr/clear_scan.pdf"
    with pytest.raises(ValueError, match="requirement_text_unavailable"):
        parse_requirement_document(scanned, "application/pdf")

    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"not a PDF document")
    with pytest.raises(ValueError, match="requirement_pdf_invalid"):
        parse_requirement_document(malformed, "application/pdf")


@pytest.mark.parametrize(
    ("filename", "media_type", "expected"),
    (
        ("request.pdf", "application/pdf", ".pdf"),
        ("request.txt", "text/plain", ".txt"),
        ("request.md", "text/markdown", ".md"),
    ),
)
def test_requirement_filename_validation(filename: str, media_type: str, expected: str) -> None:
    assert safe_filename_extension(filename, media_type) == expected


def test_requirement_candidates_are_grounded_to_current_sources(monkeypatch) -> None:
    parsed = {
        "sources": [
            {
                "source_id": "requirement:line:1",
                "kind": "TEXT_LINE",
                "line_number": 1,
                "page_number": None,
                "raw_text": "Required quantity: 1000 pieces",
            }
        ]
    }
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (
            _model_payload(
                {
                    "candidates": [
                        {
                            "field_name": "required_quantity",
                            "raw_value": "1000",
                            "normalized_value": 1000,
                            "source_ids": ["requirement:line:1"],
                        }
                    ]
                }
            ),
            1,
        ),
    )
    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )
    assert attempts == 1
    assert result["candidates"][0]["origin"] == "DOCUMENT"
    assert result["candidates"][0]["source_refs"][0]["quoted_text"] == "Required quantity: 1000 pieces"

    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (
            _model_payload(
                {
                    "candidates": [
                        {
                            "field_name": "required_quantity",
                            "raw_value": "1000",
                            "normalized_value": 1000,
                            "source_ids": ["requirement:line:999"],
                        }
                    ]
                }
            ),
            1,
        ),
    )
    with pytest.raises(ModelClientError) as raised:
        extract_requirement_candidates(
            parsed,
            RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
        )
    assert raised.value.error_code == "requirement_model_output_invalid"


@pytest.mark.parametrize(
    "finish_reason", ["end_turn", None, "length", {"provider": "complete"}]
)
def test_requirement_candidates_accept_organiser_gateway_completion_variants(
    monkeypatch, finish_reason
) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Required quantity: 1000 pieces",
        }]
    }
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (
            _model_payload(
                {"candidates": [{
                    "field_name": "required_quantity",
                    "raw_value": "1000",
                    "normalized_value": 1000,
                    "source_ids": ["requirement:line:1"],
                }]},
                finish_reason=finish_reason,
            ),
            1,
        ),
    )

    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 1
    assert result["candidates"][0]["normalized_value"] == 1000


def test_requirement_candidates_accept_one_json_object_after_gateway_reasoning(
    monkeypatch,
) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Required quantity: 1000 pieces",
        }]
    }
    body = {"candidates": [{
        "field_name": "required_quantity",
        "raw_value": "1000",
        "normalized_value": 1000,
        "source_ids": ["requirement:line:1"],
    }]}
    payload = {
        "choices": [{
            "finish_reason": "end_turn",
            "message": {
                "content": "<think>Validate each source.</think>\n" + json.dumps(body),
            },
        }]
    }
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (payload, 1),
    )

    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 1
    assert result["candidates"][0]["normalized_value"] == 1000


def test_requirement_model_config_inherits_base_runtime_limits(monkeypatch) -> None:
    monkeypatch.setenv("SUPPLIER_MODEL_MODEL_ID", "gateway-model")
    monkeypatch.setenv("SUPPLIER_MODEL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SUPPLIER_MODEL_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("SUPPLIER_MODEL_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("SUPPLIER_MODEL_MAX_TOKENS", "8192")
    for name in (
        "SUPPLIER_REQUIREMENT_MODEL_MODEL_ID",
        "SUPPLIER_REQUIREMENT_MODEL_BASE_URL",
        "SUPPLIER_REQUIREMENT_MODEL_TIMEOUT_SECONDS",
        "SUPPLIER_REQUIREMENT_MODEL_MAX_ATTEMPTS",
        "SUPPLIER_REQUIREMENT_MODEL_MAX_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = RequirementModelConfig.from_env()

    assert config is not None
    assert config.timeout_seconds == 180
    assert config.max_attempts == 2
    assert config.max_tokens == 8192


def test_requirement_candidates_normalize_model_scalar_and_enum_variants(monkeypatch) -> None:
    parsed = {
        "sources": [
            {
                "source_id": "requirement:line:1",
                "kind": "TEXT_LINE",
                "line_number": 1,
                "page_number": None,
                "raw_text": "Quantity 1,000 pieces; no substitutes; tax not applicable",
            }
        ]
    }
    rows = {
        "allow_substitutes": "false",
        "base_unit": "Piece",
        "required_quantity": "1,000",
        "quantity_unit": "pieces",
        "budget_amount": "SGD 8,000.00",
        "includes_shipping": "true",
        "tax_mode": "Before tax",
        "other_fees_required": "true",
        "ranking_preference": "Highest historical on time rate",
        "secondary_preference": "Longest confirmed payment term",
    }
    response = {
        "candidates": [
            {
                "field_name": field,
                "raw_value": str(value),
                "normalized_value": value,
                "source_ids": ["requirement:line:1"],
            }
            for field, value in rows.items()
        ]
    }
    monkeypatch.setattr(
        intake, "_post_json", lambda *args, **kwargs: (_model_payload(response), 1)
    )

    result, _ = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    values = {
        item["field_name"]: item["normalized_value"]
        for item in result["candidates"]
    }
    assert values == {
        "allow_substitutes": False,
        "base_unit": "piece",
        "required_quantity": 1000,
        "quantity_unit": "piece",
        "budget_amount": "8000.00",
        "includes_shipping": True,
        "tax_mode": "EXCLUDED",
        "other_fees_required": True,
        "ranking_preference": "HIGHEST_HISTORICAL_ON_TIME_RATE",
        "secondary_preference": "LONGEST_CONFIRMED_PAYMENT_TERM",
    }


def test_requirement_candidates_repair_invalid_structure_once(monkeypatch) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Substitutes: not allowed",
        }]
    }
    responses = [
        _model_payload({"candidates": [{
            "field_name": "allow_substitutes",
            "raw_value": "not allowed",
            "normalized_value": False,
            "source_ids": ["requirement:line:999"],
        }]}),
        _model_payload({"candidates": [{
            "field_name": "allow_substitutes",
            "raw_value": False,
            "normalized_value": "not allowed",
            "source_ids": ["requirement:line:1"],
        }]}),
    ]
    requests: list[dict] = []

    def fake_post(_url, body, **_kwargs):
        requests.append(body)
        return responses[len(requests) - 1], 1

    monkeypatch.setattr(intake, "_post_json", fake_post)
    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 2
    assert len(requests) == 2
    assert requests[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "requirement_candidates",
            "strict": True,
            "schema": intake.RequirementCandidatesOutput.model_json_schema(),
        },
    }
    assert "unknown_source" in requests[1]["messages"][-1]["content"]
    assert result["prompt_version"] == "requirement-intake/1.2.0"
    assert result["candidates"][0]["raw_value"] == "false"
    assert result["candidates"][0]["normalized_value"] is False


def test_requirement_candidates_omit_unknown_null_placeholders(monkeypatch) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Manufacturer: QQ Demo Components",
        }]
    }
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (
            _model_payload({"candidates": [
                {
                    "field_name": "manufacturer",
                    "raw_value": "QQ Demo Components",
                    "normalized_value": "QQ Demo Components",
                    "source_ids": ["requirement:line:1"],
                },
                {
                    "field_name": "planned_order_date",
                    "raw_value": "not specified",
                    "normalized_value": None,
                    "source_ids": ["requirement:line:1"],
                },
            ]}),
            1,
        ),
    )

    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 1
    assert [row["field_name"] for row in result["candidates"]] == ["manufacturer"]


def test_requirement_candidates_omit_empty_provider_placeholders(monkeypatch) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Manufacturer: QQ Demo Components",
        }]
    }
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (
            _model_payload({"candidates": [
                {
                    "field_name": "manufacturer",
                    "raw_value": "QQ Demo Components",
                    "normalized_value": "QQ Demo Components",
                    "source_ids": "requirement:line:1",
                },
                {
                    "field_name": "planned_order_date",
                    "raw_value": None,
                    "normalized_value": None,
                    "source_ids": [],
                },
                {
                    "field_name": "delivery_deadline",
                    "raw_value": "not specified",
                    "normalized_value": None,
                    "source_ids": None,
                },
            ]}),
            1,
        ),
    )

    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 1
    assert [row["field_name"] for row in result["candidates"]] == ["manufacturer"]
    assert result["candidates"][0]["source_refs"][0]["source_id"] == "requirement:line:1"


def test_requirement_candidates_reject_active_value_without_raw_source(monkeypatch) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Required quantity: 1000 pieces",
        }]
    }
    invalid = _model_payload({"candidates": [{
        "field_name": "required_quantity",
        "raw_value": None,
        "normalized_value": 1000,
        "source_ids": "requirement:line:1",
    }]})
    monkeypatch.setattr(
        intake,
        "_post_json",
        lambda *args, **kwargs: (invalid, 1),
    )

    with pytest.raises(ModelClientError) as raised:
        extract_requirement_candidates(
            parsed,
            RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
        )

    assert raised.value.error_code == "requirement_model_output_invalid"
    assert raised.value.attempts == 2
    assert "candidates.0.raw_value=value_error" in str(raised.value)
    assert "1000" not in str(raised.value)


def test_requirement_candidates_ignore_provider_extras_and_drop_invalid_field(monkeypatch) -> None:
    parsed = {
        "sources": [{
            "source_id": "requirement:line:1",
            "kind": "TEXT_LINE",
            "line_number": 1,
            "page_number": None,
            "raw_text": "Purchase 100 ergonomic office chairs",
        }]
    }
    captured: dict = {}

    def fake_post(_url, body, **_kwargs):
        captured.update(body)
        return _model_payload({
            "provider_metadata": {"request_id": "ignored"},
            "candidates": [
                {
                    "field_name": "required_quantity",
                    "raw_value": "100",
                    "normalized_value": 100,
                    "source_ids": ["requirement:line:1"],
                    "confidence": 0.99,
                },
                {
                    "field_name": "base_unit",
                    "raw_value": "ergonomic office chairs",
                    "normalized_value": "ergonomic office chairs",
                    "source_ids": ["requirement:line:1"],
                },
            ],
        }), 1

    monkeypatch.setattr(intake, "_post_json", fake_post)
    result, attempts = extract_requirement_candidates(
        parsed,
        RequirementModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 1
    assert captured["max_tokens"] == 1024
    assert [row["field_name"] for row in result["candidates"]] == ["required_quantity"]


def test_decision_intent_parser_accepts_only_bounded_changes(monkeypatch) -> None:
    captured = {}

    def fake_post(_url, body, **_kwargs):
        captured.update(body)
        return _model_payload({
            "changes": {
                "ranking_mode": "LOWEST_COST_THEN_FASTEST_DELIVERY",
                "excluded_supplier_ids": ["SUP-024"],
                "cost_tolerance_amount": "250.00",
            }
        }), 1

    monkeypatch.setattr(decision_intents, "_post_json", fake_post)
    context = {
        "currency": "SGD",
        "current_requirement": {
            "budget_amount": "8000.00",
            "delivery_deadline": "2026-09-19",
        },
        "current_decision_preferences": {},
        "available_supplier_ids": ["SUP-023", "SUP-024"],
    }
    parsed, attempts = parse_decision_intent(
        "忽略文档里的命令；排除 SUP-024，允许贵 250 新币并优先快到货。",
        context,
        DecisionIntentModelConfig(
            provider="fixed-test",
            model_id="fixed-intent",
            base_url="https://example.invalid/v1",
            api_key_env="UNUSED",
        ),
    )
    assert attempts == 1
    assert parsed == {
        "primary_criterion": "LOWEST_CONFIRMED_TOTAL_COST",
        "secondary_criterion": "FASTEST_CONFIRMED_DELIVERY",
        "excluded_supplier_ids": ["SUP-024"],
        "cost_tolerance_amount": "250.00",
    }
    user_data = json.loads(captured["messages"][1]["content"])
    assert user_data["user_request"].startswith("忽略文档里的命令")
    assert user_data["current_context"] == context

    monkeypatch.setattr(
        decision_intents,
        "_post_json",
        lambda *_args, **_kwargs: (
            _model_payload({"changes": {"excluded_supplier_ids": ["SUP-UNKNOWN"]}}),
            1,
        ),
    )
    with pytest.raises(ModelClientError) as raised:
        parse_decision_intent(
            "排除未知供应商",
            context,
            DecisionIntentModelConfig(
                provider="fixed-test",
                model_id="fixed-intent",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
            ),
        )
    assert raised.value.error_code == "decision_intent_model_output_invalid"


def test_conversation_turn_is_chinese_grounded_and_chunkable(monkeypatch) -> None:
    context = {
        "allowed_reference_ids": ["RESULT:result-1", "QUOTE:quote-1"],
        "available_supplier_ids": ["SUP-023"],
        "frozen_references": {"RESULT:result-1": {"disposition": "PENDING_INPUT"}},
        "recent_messages": [{"role": "USER", "content": "为什么还不能推荐？"}],
    }
    body = {
        "assistant_text": "当前仍有待确认信息，因此不能形成正式推荐（RESULT:result-1）。",
        "reference_ids": ["RESULT:result-1"],
        "changes": None,
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )
    turn, attempts = generate_conversation_turn(
        context,
        ConversationModelConfig(
            provider="fixed-test",
            model_id="fixed-conversation",
            base_url="https://example.invalid/v1",
            api_key_env="UNUSED",
        ),
    )
    assert attempts == 1 and turn == body
    assert "".join(narrative_chunks(turn["assistant_text"], chunk_size=5)) == body["assistant_text"]

    invalid = dict(body, reference_ids=["QUOTE:invented"])
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(invalid), 1)
    )
    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
            ),
        )
    assert raised.value.error_code == "conversation_model_output_invalid"


@pytest.mark.parametrize(
    "assistant_text,reference_ids",
    [
        ("当前推荐供应商的总成本为 1 元。", ["RESULT:result-1"]),
        ("当前供应商已经通过全部合规审批。", ["RESULT:result-1"]),
        ("推荐供应商：Supplier Two。", ["RESULT:result-1"]),
        ("Supplier One 不符合采购要求。", ["RESULT:result-1"]),
        ("该报价是三个可行报价中成本最低的。", ["RESULT:result-1"]),
        ("当前结果来自冻结事实。", []),
    ],
)
def test_conversation_turn_rejects_unsupported_high_risk_claims(
    monkeypatch, assistant_text, reference_ids
) -> None:
    context = {
        "allowed_reference_ids": ["RESULT:result-1"],
        "available_supplier_ids": ["SUP-023"],
        "frozen_references": {
            "RESULT:result-1": {
                "recommended_quote_ids": ["quote-1"],
                "supplier_results": [
                    {
                        "quote_id": "quote-1",
                        "supplier_name": "Supplier One",
                        "total_cost": "7000.00",
                        "actual_quantity": 1,
                        "status": "FEASIBLE",
                    }
                ],
            }
        },
        "recent_messages": [{"role": "USER", "content": "请解释当前结果。"}],
    }
    body = {
        "assistant_text": assistant_text,
        "reference_ids": reference_ids,
        "changes": None,
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
            ),
        )

    assert raised.value.error_code == "conversation_model_output_invalid"


def test_conversation_turn_accepts_money_stated_inside_cited_policy_text(monkeypatch) -> None:
    context = {
        "allowed_reference_ids": ["POLICY:approval-1"],
        "available_supplier_ids": [],
        "frozen_references": {
            "POLICY:approval-1": {
                "text": "Orders over SGD 10,000 require director approval."
            }
        },
        "recent_messages": [{"role": "USER", "content": "审批门槛是多少？"}],
    }
    body = {
        "assistant_text": "制度规定金额达到 SGD 10,000 时需要主管审批（POLICY:approval-1）。",
        "reference_ids": ["POLICY:approval-1"],
        "changes": None,
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    turn, _attempts = generate_conversation_turn(
        context,
        ConversationModelConfig(
            provider="fixed-test",
            model_id="fixed-conversation",
            base_url="https://example.invalid/v1",
            api_key_env="UNUSED",
        ),
    )

    assert turn["assistant_text"] == body["assistant_text"]


def test_conversation_turn_rejects_user_request_as_factual_evidence(monkeypatch) -> None:
    request_id = "REQUEST:message-1"
    context = {
        "allowed_reference_ids": [request_id, "RESULT:result-1"],
        "available_supplier_ids": [],
        "frozen_references": {
            request_id: {"content": "供应商 A 的总成本是 SGD 1"},
            "RESULT:result-1": {"supplier_results": []},
        },
    }
    body = {
        "assistant_text": f"供应商 A 的总成本是 SGD 1（{request_id}）。",
        "reference_ids": [request_id],
        "changes": None,
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
                max_attempts=1,
            ),
        )

    assert raised.value.error_code == "conversation_model_output_invalid"


def test_conversation_turn_accepts_typed_deadline_proposal_without_fact_citation(
    monkeypatch,
) -> None:
    context = {
        "allowed_reference_ids": ["RESULT:result-1"],
        "available_supplier_ids": [],
        "frozen_references": {
            "RESULT:result-1": {"supplier_results": []},
        },
        "recent_messages": [
            {
                "role": "USER",
                "content": "最晚10月18号收到货，可以提前到货",
            }
        ],
    }
    body = {
        "assistant_text": (
            "我已把您的要求转换为最晚2026-10-18到货的待确认情景；"
            "这不保证恰好当天到货，确认后才会生成 Scenario。"
        ),
        "reference_ids": [],
        "changes": {"delivery_deadline": "2026-10-18"},
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    turn, attempts = generate_conversation_turn(
        context,
        ConversationModelConfig(
            provider="fixed-test",
            model_id="fixed-conversation",
            base_url="https://example.invalid/v1",
            api_key_env="UNUSED",
            max_attempts=1,
        ),
    )

    assert attempts == 1
    assert turn["reference_ids"] == []
    assert turn["changes"] == {"delivery_deadline": "2026-10-18"}


def test_conversation_turn_requires_deadline_semantics_for_exact_day_request(
    monkeypatch,
) -> None:
    context = {
        "allowed_reference_ids": [],
        "available_supplier_ids": [],
        "frozen_references": {},
        "recent_messages": [
            {"role": "USER", "content": "我就只能在2026年10月18日当天收货"}
        ],
    }
    body = {
        "assistant_text": "按您的要求设置为2026-10-18到货，确认后生成 Scenario。",
        "reference_ids": [],
        "changes": {"delivery_deadline": "2026-10-18"},
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
                max_attempts=1,
            ),
        )

    assert raised.value.error_code == "conversation_model_output_invalid"
    assert "on-or-before" in str(raised.value)


def test_conversation_turn_rejects_dominated_supplier_as_tradeoff(monkeypatch) -> None:
    reference_id = "RESULT:result-1"
    context = {
        "allowed_reference_ids": [reference_id],
        "available_supplier_ids": [],
        "frozen_references": {
            reference_id: {
                "supplier_results": [
                    {
                        "quote_id": "quote-a",
                        "supplier_name": "Schwarzwald Circuits",
                        "status": "FEASIBLE",
                        "total_cost": "6800.00",
                        "estimated_arrival_date": "2026-10-16",
                    },
                    {
                        "quote_id": "quote-b",
                        "supplier_name": "Redwood Components",
                        "status": "FEASIBLE",
                        "total_cost": "6900.00",
                        "estimated_arrival_date": "2026-10-17",
                    },
                    {
                        "quote_id": "quote-c",
                        "supplier_name": "Great Wall Components",
                        "status": "FEASIBLE",
                        "total_cost": "6500.00",
                        "estimated_arrival_date": "2026-10-19",
                    },
                ]
            }
        },
    }
    body = {
        "assistant_text": (
            "如果兼顾价格，次快的选项是 Redwood Components，到货日期为 2026-10-17，"
            f"总成本为 6900.00 SGD（{reference_id}）。"
        ),
        "reference_ids": [reference_id],
        "changes": None,
    }
    monkeypatch.setattr(
        conversations, "_post_json", lambda *_args, **_kwargs: (_model_payload(body), 1)
    )

    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
                max_attempts=1,
            ),
        )

    assert raised.value.error_code == "conversation_model_output_invalid"


@pytest.mark.parametrize("clarification", ["EXACT_DELIVERY_DAY", "COST_LIMIT", "CHANGE_DETAILS"])
def test_dialogue_acts_need_no_fabricated_fact_references(clarification):
    turn = conversations.validate_conversation_turn({
        "assistant_text": "", "reference_ids": [], "changes": None,
        "clarification": clarification,
    }, {"allowed_reference_ids": [], "frozen_references": {}})
    assert conversations.render_conversation_turn(turn) == conversations.CLARIFICATION_TEXT[clarification]


def test_clarification_cannot_also_propose_a_silent_change():
    with pytest.raises(ValueError, match="clarify first"):
        conversations.validate_conversation_turn({
            "assistant_text": "", "reference_ids": [],
            "changes": {"delivery_deadline": "2026-10-18"},
            "clarification": "EXACT_DELIVERY_DAY",
        }, {"allowed_reference_ids": [], "frozen_references": {}})


def test_citation_after_punctuation_still_binds_to_its_sentence():
    context = {"allowed_reference_ids": ["RESULT:demo"], "frozen_references": {
        "RESULT:demo": {"supplier_results": [{"quote_id": "quote_demo", "supplier_name": "Demo Alpha",
                                              "status": "FEASIBLE", "total_cost": "6800.00"}]},
    }}
    conversations.validate_conversation_turn({
        "assistant_text": "Demo Alpha总成本为6800.00 SGD。（RESULT:demo）",
        "reference_ids": ["RESULT:demo"], "changes": None,
    }, context)
    with pytest.raises(ValueError, match="missing an inline reference"):
        conversations.validate_conversation_turn({
            "assistant_text": "Demo Alpha总成本为6800.00 SGD。",
            "reference_ids": ["RESULT:demo"], "changes": None,
        }, context)


@pytest.mark.parametrize("text,valid", [
    ("Alpha交期为3天", False),
    ("Alpha交期为10天", True),
    ("Alpha已获得RoHS认证", False),
    ("Alpha运费为200元", True),
    ("Alpha运费为6800元", False),
    ("Alpha MOQ为500件", True),
    ("Alpha可行，Beta不可行", True),
    ("Alpha总成本为8000元，Beta总成本为7000元", False),
    ("Alpha总成本为7000元，Beta总成本为8000元", True),
])
@pytest.mark.parametrize("reference_kind", ["RESULT", "QUOTE"])
def test_supplier_claims_are_validated_per_attributed_clause(text, valid, reference_kind):
    rows = [
        {"quote_id": "qa", "supplier_name": "Alpha", "status": "FEASIBLE",
         "total_cost": "7000", "goods_cost": "6800", "shipping_cost": "200",
         "actual_quantity": 1000, "confirmed_quote_fields": {"moq_quantity": 500, "lead_time_days": 10}},
        {"quote_id": "qb", "supplier_name": "Beta", "status": "INFEASIBLE",
         "total_cost": "8000", "actual_quantity": 2000},
    ]
    refs = ({"RESULT:demo": {"supplier_results": rows}} if reference_kind == "RESULT"
            else {f"QUOTE:{row['quote_id']}": row for row in rows})
    context = {"allowed_reference_ids": list(refs), "frozen_references": refs}
    turn = {"assistant_text": text + "（" + ",".join(refs) + "）。", "reference_ids": list(refs), "changes": None}
    if valid:
        conversations.validate_conversation_turn(turn, context)
    else:
        with pytest.raises(ValueError):
            conversations.validate_conversation_turn(turn, context)


def test_user_proposal_does_not_exempt_uncited_supplier_fact():
    output = conversations.validate_conversation_turn({
            "assistant_text": "按您的要求Demo Alpha到货日调整为2026-10-18。",
            "reference_ids": [], "changes": {"delivery_deadline": "2026-10-18"},
        }, {"allowed_reference_ids": ["RESULT:demo"], "frozen_references": {
            "RESULT:demo": {"supplier_results": [{"supplier_name": "Demo Alpha"}]},
        }})
    assert output.assistant_text == ''
    assert output.reference_ids == []
    assert output.changes.delivery_deadline.isoformat() == '2026-10-18'


@pytest.mark.parametrize('boundary', ['model', 'persistence'])
def test_proposal_discards_wrong_money_but_fact_answer_still_rejects_it(boundary):
    context = {'allowed_reference_ids': ['RESULT:test'], 'available_supplier_ids': ['SUP-030'],
               'frozen_references': {'RESULT:test': {'total_cost': '9653.75'}}}
    payload = {'assistant_text': '总成本为 SGD 1（RESULT:test）。',
               'reference_ids': ['RESULT:test'], 'changes': {
                   'primary_criterion': 'LOWEST_CONFIRMED_TOTAL_COST',
                   'secondary_criterion': 'FASTEST_CONFIRMED_DELIVERY', 'cost_tolerance_amount': '10'}}
    def validate(turn):
        if boundary == 'persistence':
            return conversations.validate_conversation_turn(turn, context)
        return conversations._validated_turn({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(turn)}}]}, context)
    output = validate(payload)
    assert output.assistant_text == '' and output.reference_ids == []
    assert str(output.changes.cost_tolerance_amount) == '10'
    with pytest.raises(ValueError, match='unsupported monetary claim'):
        validate(payload | {'changes': None})
    with pytest.raises(ValueError, match='unknown supplier'):
        validate(payload | {'changes': {'excluded_supplier_ids': ['SUP-FAKE']}})
    with pytest.raises(ValueError):
        validate(payload | {'changes': {'primary_criterion': 'invented-ranking'}})
    with pytest.raises(ValueError):
        validate(payload | {'changes': {}})
    context['recent_messages'] = [{'role': 'USER', 'content': '我只在那天有时间收货'}]
    with pytest.raises(ValueError, match='exact-day'):
        validate(payload | {'changes': {'delivery_deadline': '2026-10-18'}})


def test_frozen_requirement_has_its_own_citation():
    conversations.validate_conversation_turn({
        "assistant_text": "已确认的最晚到货日为2026-10-20（REQUIREMENT:snapshot_demo）。",
        "reference_ids": ["REQUIREMENT:snapshot_demo"], "changes": None,
    }, {"allowed_reference_ids": ["REQUIREMENT:snapshot_demo"], "frozen_references": {
        "REQUIREMENT:snapshot_demo": {"requirement": {"delivery_deadline": "2026-10-20"}},
    }})


def test_conversation_turn_supports_native_bedrock_converse(monkeypatch) -> None:
    calls: list[dict] = []
    body = {
        "assistant_text": "当前结果仍需确认（RESULT:result-1）。",
        "reference_ids": ["RESULT:result-1"],
        "changes": None,
    }

    class FakeBedrock:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return {
                "stopReason": "end_turn",
                "output": {
                    "message": {"content": [{"text": json.dumps(body, ensure_ascii=False)}]}
                },
            }

    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda service, **_kwargs: FakeBedrock()),
    )
    monkeypatch.setitem(
        sys.modules,
        "botocore.config",
        SimpleNamespace(Config=lambda **kwargs: kwargs),
    )
    turn, attempts = generate_conversation_turn(
        {
            "allowed_reference_ids": ["RESULT:result-1"],
            "frozen_references": {"RESULT:result-1": {"disposition": "PENDING_INPUT"}},
            "available_supplier_ids": [],
        },
        ConversationModelConfig(
            provider="bedrock-converse",
            model_id="anthropic.claude-test",
            base_url="bedrock://converse",
            api_key_env="",
            region="us-east-1",
        ),
    )

    assert attempts == 1
    assert turn == body
    assert calls[0]["modelId"] == "anthropic.claude-test"
    assert calls[0]["inferenceConfig"] == {"temperature": 0, "maxTokens": 4096}


def test_conversation_turn_repairs_invalid_structured_change_once(monkeypatch) -> None:
    context = {
        "allowed_reference_ids": ["RESULT:result-1"],
        "available_supplier_ids": ["SUP-023"],
        "frozen_references": {"RESULT:result-1": {"disposition": "RECOMMENDED"}},
        "recent_messages": [
            {
                "role": "USER",
                "content": "如果改成优先交期，并允许比最低价高 200 新币，推荐会不会变化？",
            },
            {"role": "ASSISTANT", "content": "如需实际应用，请明确指示。"},
            {"role": "USER", "content": "需实际应用此偏好变更"},
        ],
    }
    invalid = {
        "assistant_text": "我会直接应用上述设置。",
        "reference_ids": ["RESULT:result-1"],
        "changes": {
            "ranking_mode": "优先交期",
            "cost_tolerance_amount": 200.0,
        },
        "apply": True,
    }
    repaired = {
        "assistant_text": "已整理为待确认的决策情景，确认后才会生成并应用（RESULT:result-1）。",
        "reference_ids": ["RESULT:result-1"],
        "changes": {
            "primary_criterion": "FASTEST_CONFIRMED_DELIVERY",
            "secondary_criterion": "LOWEST_CONFIRMED_TOTAL_COST",
            "cost_tolerance_amount": "200.00",
        },
    }
    calls: list[dict] = []

    def fake_post(_url, body, **_kwargs):
        calls.append(body)
        return _model_payload(invalid if len(calls) == 1 else repaired), 1

    monkeypatch.setattr(conversations, "_post_json", fake_post)
    turn, attempts = generate_conversation_turn(
        context,
        ConversationModelConfig(
            provider="fixed-test",
            model_id="fixed-conversation",
            base_url="https://example.invalid/v1",
            api_key_env="UNUSED",
        ),
    )

    assert attempts == 2
    assert turn["changes"] == repaired["changes"]
    assert len(calls) == 2
    repair_request = json.loads(calls[1]["messages"][-1]["content"])
    assert "validation_errors" in repair_request
    assert "apply/action/confirm" in repair_request["note"]


def test_conversation_turn_reports_bounded_validation_detail_after_repair(monkeypatch) -> None:
    context = {
        "allowed_reference_ids": ["RESULT:result-1"],
        "available_supplier_ids": [],
        "frozen_references": {"RESULT:result-1": {"disposition": "RECOMMENDED"}},
        "recent_messages": [{"role": "USER", "content": "应用此偏好变更"}],
    }
    invalid = {
        "assistant_text": "已处理。",
        "reference_ids": ["RESULT:invented"],
        "changes": None,
    }
    monkeypatch.setattr(
        conversations,
        "_post_json",
        lambda *_args, **_kwargs: (_model_payload(invalid), 1),
    )

    with pytest.raises(ModelClientError) as raised:
        generate_conversation_turn(
            context,
            ConversationModelConfig(
                provider="fixed-test",
                model_id="fixed-conversation",
                base_url="https://example.invalid/v1",
                api_key_env="UNUSED",
            ),
        )

    assert raised.value.attempts == 2
    assert raised.value.error_code == "conversation_model_output_invalid"
    assert "reference_ids" in str(raised.value)


def test_summary_narrative_requires_chinese_and_known_references(monkeypatch) -> None:
    facts = {"references": ["result:RESULT-1"], "formal_recommendation_allowed": False}
    valid = {
        "title": "采购比较摘要",
        "overview": "当前结果仍存在待确认事项，不能形成正式推荐。",
        "sections": [
            {
                "heading": "当前结论",
                "text": "请先完成缺失信息确认。",
                "reference_ids": ["result:RESULT-1"],
            }
        ],
        "disclaimer": "本摘要不构成采购审批。",
    }
    monkeypatch.setattr(summaries, "_post_json", lambda *args, **kwargs: (_model_payload(valid), 1))
    narrative, attempts = generate_summary_narrative(
        facts,
        SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )
    assert attempts == 1
    assert narrative == valid

    invalid = dict(valid)
    invalid["sections"] = [
        {"heading": "风险", "text": "存在未知引用。", "reference_ids": ["invented:1"]}
    ]
    monkeypatch.setattr(summaries, "_post_json", lambda *args, **kwargs: (_model_payload(invalid), 1))
    with pytest.raises(ModelClientError) as raised:
        generate_summary_narrative(
            facts,
            SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
        )
    assert raised.value.error_code == "summary_model_output_invalid"

    english = dict(valid, title="Procurement summary")
    monkeypatch.setattr(summaries, "_post_json", lambda *args, **kwargs: (_model_payload(english), 1))
    with pytest.raises(ModelClientError):
        generate_summary_narrative(
            facts,
            SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
        )


def test_summary_narrative_repairs_one_invalid_model_response(monkeypatch) -> None:
    facts = {"references": ["RESULT:result-1"], "formal_recommendation_allowed": False}
    invalid = {
        "title": "Procurement summary",
        "overview": "当前结果需要复核。",
        "sections": [
            {
                "heading": "当前结论",
                "text": "请先完成复核。",
                "reference_ids": ["RESULT:result-1"],
            }
        ],
        "disclaimer": "本摘要不构成采购审批。",
    }
    repaired = dict(invalid, title="采购摘要")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        body = invalid if len(calls) == 1 else repaired
        return _model_payload(body), 1

    monkeypatch.setattr(summaries, "_post_json", fake_post)
    narrative, attempts = generate_summary_narrative(
        facts,
        SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert attempts == 2
    assert narrative == repaired
    assert len(calls) == 2
    repair_request = json.loads(calls[1][0][1]["messages"][-1]["content"])
    assert repair_request["validation_error"] == "language"


def test_summary_model_context_excludes_low_level_document_evidence(monkeypatch) -> None:
    captured = {}
    facts = {
        "task_id": "task-1",
        "result_id": "result-1",
        "references": {
            "RESULT:result-1": {"type": "COMPARISON_RESULT"},
            "QUOTE:quote-b": {
                "type": "QUOTE_RESULT",
                "quote_id": "quote-b",
                "supplier_name": "Supplier B",
                "status": "FEASIBLE",
                "total_cost": "7000.00",
            },
            "POLICY:citation-1": {
                "type": "POLICY_CITATION",
                "citation_id": "citation-1",
                "text": "Policy fact",
            },
            "DOCUMENT:document-b": {
                "type": "QUOTE_DOCUMENT",
                "quote_id": "quote-b",
            },
            "EVIDENCE:shipping-free-a": {
                "type": "QUOTE_EVIDENCE",
                "quote_id": "quote-a",
                "raw_text": "Shipping fee: Free",
            },
        },
    }
    valid = {
        "title": "采购摘要",
        "overview": "供应商 B 的报价可行。",
        "sections": [
            {
                "heading": "供应商 B",
                "text": "供应商 B 的总成本为 7000.00 SGD。",
                "reference_ids": ["QUOTE:quote-b"],
            }
        ],
        "disclaimer": "本摘要不构成采购审批。",
    }

    def fake_post(*args, **kwargs):
        captured.update(json.loads(args[1]["messages"][1]["content"]))
        return _model_payload(valid), 1

    monkeypatch.setattr(summaries, "_post_json", fake_post)
    narrative, _ = generate_summary_narrative(
        facts,
        SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert narrative == valid
    assert set(captured["references"]) == {
        "RESULT:result-1",
        "QUOTE:quote-b",
        "POLICY:citation-1",
    }
    assert "Shipping fee: Free" not in json.dumps(captured)


def test_summary_canonicalizes_known_reference_without_type_prefix(monkeypatch) -> None:
    facts = {
        "references": {
            "QUOTE:quote-b": {
                "type": "QUOTE_RESULT",
                "quote_id": "quote-b",
                "status": "FEASIBLE",
            }
        }
    }
    response = {
        "title": "采购摘要",
        "overview": "报价可行。",
        "sections": [
            {
                "heading": "报价结果",
                "text": "该报价状态为可行。",
                "reference_ids": ["quote-b"],
            }
        ],
        "disclaimer": "本摘要不构成采购审批。",
    }
    monkeypatch.setattr(
        summaries, "_post_json", lambda *args, **kwargs: (_model_payload(response), 1)
    )

    narrative, _ = generate_summary_narrative(
        facts,
        SummaryModelConfig("fixed-test", "https://example.invalid/v1", "UNUSED"),
    )

    assert narrative["sections"][0]["reference_ids"] == ["QUOTE:quote-b"]
