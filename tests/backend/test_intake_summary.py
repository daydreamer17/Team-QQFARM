from __future__ import annotations

import json
from pathlib import Path

import pytest

from supplier_comparison.backend import intake, summaries
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
