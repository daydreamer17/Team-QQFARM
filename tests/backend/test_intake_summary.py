from __future__ import annotations

import json
from pathlib import Path

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


def _model_payload(body: dict) -> dict:
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(body, ensure_ascii=False)},
            }
        ]
    }


def test_requirement_parser_supports_pdf_txt_and_markdown(tmp_path: Path) -> None:
    pdf = REPO_ROOT / "data/generated/inputs/development/quote_V2/procurement_requirement_v2.pdf"
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

    scanned = REPO_ROOT / "data/generated/inputs/holdout/quote_V7_2/v72_hold_02_clear_scan.pdf"
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
        "required_quantity": "1000",
        "quantity_unit": "pieces",
        "budget_amount": "8000.00",
        "includes_shipping": "true",
        "tax_mode": "Not applicable",
        "other_fees_required": "true",
        "ranking_preference": "Lowest confirmed total cost",
        "secondary_preference": "None",
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
        "tax_mode": "NOT_APPLICABLE",
        "other_fees_required": True,
        "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
        "secondary_preference": None,
    }


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
        "ranking_mode": "LOWEST_COST_THEN_FASTEST_DELIVERY",
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
        "assistant_text": "当前仍有待确认信息，因此不能形成正式推荐。",
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
        "assistant_text": "制度规定金额达到 SGD 10,000 时需要主管审批。",
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
