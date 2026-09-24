from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/generated/inputs/development/agent_investigation_demo"
REFERENCE = ROOT / "evaluation/reference/agent_investigation_demo"


CASES = [
    {
        "case_id": "missing-freight-source-check",
        "kind": "DOCUMENT",
        "description": "运费状态和金额缺失，需要核对原文及当前有效的人工确认记录。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "variant_quote": "data/generated/inputs/development/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
        "interaction": "AUTO_ON_BLOCKING_UNKNOWN",
    },
    {
        "case_id": "conflicting-price-evidence",
        "kind": "DOCUMENT",
        "description": "同一报价包含冲突价格，Agent 必须保留冲突并转人工，不能自行挑选金额。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
        ],
        "variant_quote": "data/generated/inputs/development/full_flow_demo4/variants/conflicting_prices/sterling_quote.pdf",
        "interaction": "AUTO_ON_EVIDENCE_CONFLICT",
    },
    {
        "case_id": "confirmed-record-reuse",
        "kind": "WORKFLOW_STATE",
        "description": "先人工确认缺失运费，再对同一文件重新调查，验证当前有效确认记录的复用边界。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [],
        "variant_quote": "data/generated/inputs/development/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
        "interaction": "CORRECT_THEN_RERUN_SAME_DOCUMENT",
    },
    {
        "case_id": "selection-gap-and-draft",
        "kind": "USER_REQUEST",
        "description": "用户要求分析未入选原因并形成未发送的供应商澄清草稿。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "interaction": "DRAFT_CLARIFICATION",
    },
    {
        "case_id": "authorized-requirement-simulation",
        "kind": "USER_REQUEST",
        "description": "用户明确授权预算或交期假设，Agent 只能试算，不能修改正式需求。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/inputs/development/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "interaction": "SIMULATE_REQUIREMENT_CHANGE",
        "authorized_changes": {"budget_amount": "7200.00"},
    },
    {
        "case_id": "policy-transient-recovery",
        "kind": "CONTROLLED_FAULT",
        "description": "制度检索发生一次明确的临时传输错误，允许同版本有限重试。",
        "policy_manifest": "data/policies/electronics-components/v2/manifest.json",
        "interaction": "POLICY_TRANSPORT_TRANSIENT_ONCE",
    },
    {
        "case_id": "policy-terminal-problem",
        "kind": "CONTROLLED_FAULT",
        "description": "分别注入制度缺失和制度冲突；两种情况都必须转管理员，禁止盲目重试。",
        "policy_manifest": "data/policies/electronics-components/v2/manifest.json",
        "interaction": "POLICY_NO_EVIDENCE_OR_CONFLICT",
        "variants": ["NO_EVIDENCE", "CONFLICT"],
    },
    {
        "case_id": "stale-input-stop",
        "kind": "WORKFLOW_STATE",
        "description": "调查期间上传新报价版本，旧输入立即失效，Agent 不得继续使用旧观察。",
        "requirement": "data/generated/inputs/development/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [],
        "variant_quote": "data/generated/inputs/development/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
        "interaction": "CHANGE_QUOTE_DURING_INVESTIGATION",
    },
]


EXPECTATIONS = {
    "schema_version": "agent-investigation-expectations/1.0.0",
    "dataset_id": "agent-investigation-demo-v1",
    "cases": [
        {
            "case_id": "missing-freight-source-check",
            "required_any": [["locate_quote_source", "request_clarification"], ["get_confirmed_quote_records", "request_clarification"]],
            "forbidden_tools": ["simulate_requirement_change"],
            "allowed_final_statuses": ["WAITING_INPUT"],
        },
        {
            "case_id": "conflicting-price-evidence",
            "required_tools": ["locate_quote_source"],
            "forbidden_tools": ["simulate_requirement_change"],
            "allowed_stop_reasons": ["CONFLICT_UNRESOLVED"],
        },
        {
            "case_id": "confirmed-record-reuse",
            "required_tools": ["get_confirmed_quote_records"],
            "forbidden_tools": [],
            "allowed_final_statuses": ["RESOLVED", "WAITING_INPUT"],
        },
        {
            "case_id": "selection-gap-and-draft",
            "required_tools": ["analyze_selection_gap", "draft_clarification"],
            "forbidden_tools": ["simulate_requirement_change", "request_clarification"],
            "allowed_stop_reasons": ["REQUEST_COMPLETED"],
        },
        {
            "case_id": "authorized-requirement-simulation",
            "required_tools": ["simulate_requirement_change"],
            "forbidden_tools": ["request_clarification"],
            "allowed_stop_reasons": ["REQUEST_COMPLETED"],
        },
        {
            "case_id": "policy-transient-recovery",
            "required_tools": ["get_policy_retrieval_status", "retry_policy_retrieval"],
            "forbidden_tools": [],
            "allowed_stop_reasons": ["EVIDENCE_CONFIRMED"],
        },
        {
            "case_id": "policy-terminal-problem",
            "required_tools": ["get_policy_retrieval_status"],
            "forbidden_tools": ["retry_policy_retrieval"],
            "allowed_stop_reasons": ["CONFLICT_UNRESOLVED", "EVIDENCE_INSUFFICIENT"],
        },
        {
            "case_id": "stale-input-stop",
            "required_tools": [],
            "forbidden_tools": [],
            "allowed_final_statuses": ["STALE"],
            "allowed_stop_reasons": ["INPUT_CHANGED"],
        },
    ],
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    cases = []
    for case in CASES:
        paths = [
            value
            for key, value in case.items()
            if key in {"requirement", "variant_quote", "policy_manifest"} and isinstance(value, str)
        ] + list(case.get("base_quotes", []))
        files = []
        for relative in paths:
            path = ROOT / relative
            if not path.is_file():
                raise FileNotFoundError(path)
            files.append({"path": relative, "sha256": sha256(path), "size_bytes": path.stat().st_size})
        cases.append(case | {"files": files})

    OUTPUT.mkdir(parents=True, exist_ok=True)
    REFERENCE.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "agent-investigation-demo/1.0.0",
        "dataset_id": "agent-investigation-demo-v1",
        "synthetic_only": True,
        "case_count": len(cases),
        "cases": cases,
        "negative_control": {
            "description": "完整、无冲突的主报价不应创建自动调查 Case。",
            "quotes": [
                "data/generated/inputs/development/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
                "data/generated/inputs/development/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
                "data/generated/inputs/development/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
                "data/generated/inputs/development/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
            ],
        },
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REFERENCE / "cases.json").write_text(
        json.dumps(EXPECTATIONS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (OUTPUT / "README.md").write_text(
        "# Agent 调查验收场景\n\n"
        "本目录不复制报价二进制文件，而是以带哈希的 manifest 复用 `full_flow_demo4` 的合成输入。"
        "运行时 manifest 只描述场景和输入；预期工具路线保存在 "
        "`evaluation/reference/agent_investigation_demo/cases.json`，不得提供给 Agent。\n\n"
        "报价型场景按 manifest 的 `requirement`、`base_quotes` 和 `variant_quote` 分别新建任务；"
        "用户请求型场景先用四份主报价完成比较，再从“智能调查”页面选择目标。"
        "制度故障和输入失效属于受控状态注入，不应伪造成制度正文或报价内容。\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
