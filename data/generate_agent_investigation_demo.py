from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/generated/demos/agent_investigation_demo"
REFERENCE = ROOT / "evaluation/reference/agent_investigation_demo"


CASES = [
    {
        "case_id": "missing-freight-source-check",
        "kind": "DOCUMENT",
        "description": "Freight status and amount are missing; inspect the source and any current human-confirmed record.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/demos/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "variant_quote": "data/generated/demos/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
        "interaction": "AUTO_ON_BLOCKING_UNKNOWN",
    },
    {
        "case_id": "conflicting-price-evidence",
        "kind": "DOCUMENT",
        "description": "One quotation contains conflicting prices; the Agent must retain the conflict and escalate instead of choosing an amount.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/demos/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
        ],
        "variant_quote": "data/generated/demos/full_flow_demo4/variants/conflicting_prices/sterling_quote.pdf",
        "interaction": "AUTO_ON_EVIDENCE_CONFLICT",
    },
    {
        "case_id": "confirmed-record-reuse",
        "kind": "WORKFLOW_STATE",
        "description": "Confirm missing freight manually, then reinvestigate the same file to verify reuse boundaries for current confirmed records.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [],
        "variant_quote": "data/generated/demos/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
        "interaction": "CORRECT_THEN_RERUN_SAME_DOCUMENT",
    },
    {
        "case_id": "selection-gap-and-draft",
        "kind": "USER_REQUEST",
        "description": "The user asks for non-selection analysis and an unsent supplier clarification draft.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/demos/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "interaction": "DRAFT_CLARIFICATION",
    },
    {
        "case_id": "authorized-requirement-simulation",
        "kind": "USER_REQUEST",
        "description": "The user explicitly authorises a budget or delivery hypothesis; the Agent may simulate it but cannot modify the official requirement.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [
            "data/generated/demos/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
            "data/generated/demos/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
        ],
        "interaction": "SIMULATE_REQUIREMENT_CHANGE",
        "authorized_changes": {"budget_amount": "7200.00"},
    },
    {
        "case_id": "policy-transient-recovery",
        "kind": "CONTROLLED_FAULT",
        "description": "Policy retrieval encounters one explicit transient transport error and may retry within the same version and bounded budget.",
        "policy_manifest": "data/policies/compliance-closure-demo/v2/manifest.json",
        "interaction": "POLICY_TRANSPORT_TRANSIENT_ONCE",
    },
    {
        "case_id": "policy-terminal-problem",
        "kind": "CONTROLLED_FAULT",
        "description": "Inject missing-policy and conflicting-policy results; both must escalate to an administrator without blind retries.",
        "policy_manifest": "data/policies/compliance-closure-demo/v2/manifest.json",
        "interaction": "POLICY_NO_EVIDENCE_OR_CONFLICT",
        "variants": ["NO_EVIDENCE", "CONFLICT"],
    },
    {
        "case_id": "stale-input-stop",
        "kind": "WORKFLOW_STATE",
        "description": "Upload a new quotation version during investigation; the old input becomes stale and the Agent must stop using earlier observations.",
        "requirement": "data/generated/demos/full_flow_demo4/requirement/procurement_requirement.txt",
        "base_quotes": [],
        "variant_quote": "data/generated/demos/full_flow_demo4/variants/missing_freight/great_wall_quote.pdf",
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
            "description": "A complete, conflict-free primary quotation must not create an automatic investigation case.",
            "quotes": [
                "data/generated/demos/full_flow_demo4/quotes/pdf/great_wall_quote.pdf",
                "data/generated/demos/full_flow_demo4/quotes/pdf/redwood_quote.pdf",
                "data/generated/demos/full_flow_demo4/quotes/pdf/schwarzwald_quote.pdf",
                "data/generated/demos/full_flow_demo4/quotes/pdf/sterling_quote.pdf",
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
        "# Agent Investigation Acceptance Scenarios\n\n"
        "This directory does not duplicate quotation binaries. Its hash-backed manifest reuses synthetic inputs from "
        "`full_flow_demo4`. The runtime manifest describes only scenarios and inputs; expected tool routes are stored in "
        "`evaluation/reference/agent_investigation_demo/cases.json` and must not be exposed to the Agent.\n\n"
        "For quotation scenarios, create separate tasks using the manifest's `requirement`, `base_quotes`, and "
        "`variant_quote`. For user-request scenarios, first complete a comparison with the four primary quotations, then "
        "select a target from the Investigation page. Policy failures and invalid inputs are controlled state injections "
        "and must not be fabricated as policy or quotation content.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
