"""Build the self-contained synthetic full-flow browser demo dataset.

Runtime inputs and operator-only reference answers are deliberately written to
different roots.  Never mount ``evaluation/reference/full_flow_demo`` into the
runtime worker or Agent environment.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/generated/inputs/development/full_flow_demo"
REFERENCE_OUT = ROOT / "evaluation/reference/full_flow_demo"
SOURCE_REQUIREMENT = (
    ROOT
    / "data/generated/inputs/development/quote_V2/procurement_requirement_v2.pdf"
)
SOURCE_QUOTES = ROOT / "data/generated/inputs/development/quote_V1"
SOURCE_POLICY_ROOT = ROOT / "data/policies/electronics-v1"

POLICY_DOCUMENTS = (
    ("approved_supplier.md", "APPROVED_SUPPLIER"),
    ("electronics_rohs.md", "ROHS_COMPLIANCE"),
    ("amount_approval.md", "AMOUNT_APPROVAL"),
)
CLAUSE_HEADING = re.compile(r"^## \[([^\]]+)\]\s+(.+?)\s*$", re.MULTILINE)

REQUIREMENT = {
    "manufacturer": "QQ Demo Components",
    "manufacturer_part_number": "QW-MCU9-DEMO",
    "package": "QFN-32",
    "revision": "R1",
    "condition": "NEW",
    "allow_substitutes": False,
    "base_unit": "piece",
    "required_quantity": 1000,
    "quantity_unit": "piece",
    "budget_amount": "8000.00",
    "currency": "SGD",
    "includes_shipping": True,
    "tax_mode": "NOT_APPLICABLE",
    "other_fees_required": True,
    "planned_order_date": "2026-09-14",
    "delivery_deadline": "2026-09-19",
    "delivery_location": "SG-DEMO-01",
    "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
    "secondary_preference": None,
}

POLICY_METADATA = {
    "policy_set_id": "full-flow-demo-electronics",
    "policy_set_version": "2026.09.1-demo",
    "policy_id": "POL-FULL-FLOW-RELEASE-GATE",
    "document_id": "DOC-FULL-FLOW-RELEASE-GATE",
    "document_version": "1.0.0",
    "title": "Fictional Full Flow Electronics Release Gate Policy",
    "effective_from": "2026-01-01T00:00:00Z",
    "effective_to": None,
    "categories": ["Electronics"],
    "regions": ["SG"],
}

QUOTE_SOURCES = (
    ("A", "SUP-022", "supplier_a_quote_v1"),
    ("B", "SUP-023", "supplier_b_quote_v1"),
    ("C", "SUP-024", "supplier_c_quote_v1"),
)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy_clauses() -> tuple[str, list[dict[str, object]]]:
    sections = [
        "# Fictional Full Flow Electronics Release Gate Policy",
        "",
        "This synthetic policy exists only for the QuoteWise demonstration.",
        "It is not a real procurement policy and grants no purchasing authority.",
    ]
    reviewed: list[dict[str, object]] = []
    source_manifest = json.loads(
        (SOURCE_POLICY_ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    manifest_documents = {
        document["path"]: document for document in source_manifest["documents"]
    }
    for filename, control_code in POLICY_DOCUMENTS:
        source = (SOURCE_POLICY_ROOT / filename).read_text(encoding="utf-8")
        document = manifest_documents[filename]
        matches = list(CLAUSE_HEADING.finditer(source))
        for index, match in enumerate(matches):
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            clause_id = match.group(1).strip()
            title = match.group(2).strip()
            body = source[body_start:body_end].strip()
            sections.extend(("", f"## [{clause_id}] {title}", body))
            clause_spec = document["clauses"][clause_id]
            if clause_spec["control_code"] != control_code:
                raise ValueError(f"unexpected control code for {clause_id}")
            reviewed.append(
                {
                    "clause_id": clause_id,
                    "title": title,
                    "text": body,
                    "control_code": control_code,
                    "rule_parameters": clause_spec.get("rule_parameters", {}),
                }
            )
    return "\n".join(sections).rstrip() + "\n", reviewed


def _requirement_text() -> str:
    return """Purchase Requirement: MCU-DEMO-001

Manufacturer: QQ Demo Components
Manufacturer part number: QW-MCU9-DEMO
Package: QFN-32
Revision: R1
Condition: NEW
Substitutes allowed: No
Required quantity: 1,000 pieces
Budget: SGD 8,000.00 including shipping
Tax mode: Not applicable
Other fees must be included: Yes
Planned order date: 14 September 2026
Required delivery date: 19 September 2026
Delivery location: SG-DEMO-01
Ranking preference: Lowest confirmed total cost
"""


def _runtime_readme() -> str:
    return """# full_flow_demo

本目录是一套用于浏览器完整流程演示的自包含合成数据包。
其中的采购需求、供应商报价和采购制度均为虚构测试数据，不是真实商业资料。

## 推荐上传顺序

1. 进入“规则资源库”，上传 `policy/electronics_procurement_full_flow.txt`。
   根据 `policy/upload_metadata.json` 填写 Policy 元数据，并使用
   `policy/reviewed_clauses.json` 核对所有自动拆分的条款和控制码，然后发布索引。
2. 创建采购任务，上传 `requirement/procurement_requirement.pdf`。
   使用 `requirement/confirmed_requirement.json` 核对自动填入的采购需求字段。
3. 在新任务中绑定刚刚发布的 Policy，分类选择 `Electronics`，地区选择 `SG`。
   Policy 索引版本会在发布时生成，请直接选择页面显示的版本，不要手动填写固定值。
4. 按 A、B、C 的顺序上传并正式提交三份报价。供应商编号记录在
   `manifest.json` 中。推荐上传 PDF，也可以使用对应的 CSV 作为替代输入。
5. 正式提交全部报价后启动分析。流程暂停并要求人工输入时，使用
   `evaluation/reference/full_flow_demo/` 中仅供操作员查看的演示回答。

不要把 `evaluation/reference/full_flow_demo/` 挂载到运行时 Worker 或 Agent。
运行时必须只根据用户上传的文件解析字段、计算结果并形成推荐。
"""


def _reference_answers() -> dict[str, object]:
    return {
        "schema_version": "1.0.0",
        "dataset_id": "full_flow_demo",
        "scenario_id": "MCU-DEMO-001",
        "is_synthetic": True,
        "runtime_access": "FORBIDDEN",
        "requirement": REQUIREMENT,
        "operator_answers": [
            {
                "issue_type": "CONFIRM_MISSING",
                "answer": {"answer_type": "CONFIRM_MISSING"},
                "reason": "Supplier B's source offer does not state shipping.",
            },
            {
                "issue_type": "SHIPPING_AMOUNT",
                "answer": {
                    "answer_type": "SHIPPING_AMOUNT",
                    "amount": "200.00",
                    "currency": "SGD",
                },
                "reason": "Synthetic operator confirmation for the demo only.",
            },
        ],
        "expected_quotes": [
            {
                "supplier_alias": "A",
                "supplier_id": "SUP-022",
                "ordered_quantity": 2000,
                "confirmed_total": "12800.00",
                "feasibility": "INFEASIBLE",
                "reason_codes": ["BUDGET_EXCEEDED", "DELIVERY_DEADLINE_EXCEEDED"],
            },
            {
                "supplier_alias": "B",
                "supplier_id": "SUP-023",
                "ordered_quantity": 1000,
                "known_subtotal_before_answer": "6800.00",
                "confirmed_total_after_answer": "7000.00",
                "feasibility_after_answer": "FEASIBLE",
                "reason_codes_after_answer": [],
            },
            {
                "supplier_alias": "C",
                "supplier_id": "SUP-024",
                "ordered_quantity": 1000,
                "confirmed_total": "7100.00",
                "feasibility": "FEASIBLE",
                "reason_codes": [],
            },
        ],
        "expected_final": {
            "disposition": "RECOMMENDATION_AVAILABLE",
            "recommended_supplier_ids": ["SUP-023"],
            "recommended_total": "7000.00",
            "currency": "SGD",
            "formal_recommendation_allowed": True,
        },
        "policy_gate": {
            "required_control_codes": [
                "APPROVED_SUPPLIER",
                "ROHS_COMPLIANCE",
                "AMOUNT_APPROVAL",
            ],
            "expected_retrieval_status": "OK",
            "scope": {"category": "Electronics", "region": "SG"},
            "note": "This gate proves policy evidence retrieval, not supplier compliance or procurement approval.",
        },
    }


def generate() -> None:
    requirement_dir = OUT / "requirement"
    quotes_dir = OUT / "quotes"
    policy_dir = OUT / "policy"
    for directory in (requirement_dir, quotes_dir, policy_dir, REFERENCE_OUT):
        directory.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(SOURCE_REQUIREMENT, requirement_dir / "procurement_requirement.pdf")
    _write_text(requirement_dir / "procurement_requirement.txt", _requirement_text())
    _write_json(requirement_dir / "confirmed_requirement.json", REQUIREMENT)

    quote_entries = []
    for alias, supplier_id, stem in QUOTE_SOURCES:
        pdf_target = quotes_dir / f"supplier_{alias.lower()}_quote.pdf"
        csv_target = quotes_dir / f"supplier_{alias.lower()}_quote.csv"
        shutil.copyfile(SOURCE_QUOTES / f"{stem}.pdf", pdf_target)
        shutil.copyfile(SOURCE_QUOTES / f"{stem}.csv", csv_target)
        quote_entries.append(
            {
                "supplier_alias": alias,
                "supplier_id": supplier_id,
                "is_synthetic": True,
                "recommended_upload": pdf_target.relative_to(ROOT).as_posix(),
                "csv_alternative": csv_target.relative_to(ROOT).as_posix(),
            }
        )

    policy_text, reviewed_clauses = _policy_clauses()
    _write_text(policy_dir / "electronics_procurement_full_flow.txt", policy_text)
    _write_json(policy_dir / "upload_metadata.json", POLICY_METADATA)
    _write_json(policy_dir / "reviewed_clauses.json", {"clauses": reviewed_clauses})
    _write_text(OUT / "README.md", _runtime_readme())
    _write_json(REFERENCE_OUT / "reference_answers.json", _reference_answers())
    _write_text(
        REFERENCE_OUT / "README.md",
        "# full_flow_demo operator reference\n\n"
        "This directory contains synthetic answers and expected results for operator QA.\n"
        "It must not be mounted into the runtime worker or Agent environment.\n",
    )

    manifest_path = OUT / "manifest.json"
    runtime_files = sorted(path for path in OUT.rglob("*") if path.is_file() and path != manifest_path)
    manifest = {
        "schema_version": "1.0.0",
        "dataset_id": "full_flow_demo",
        "scenario_id": "MCU-DEMO-001",
        "is_synthetic": True,
        "runtime_safe": True,
        "requirement": {
            "recommended_upload": (requirement_dir / "procurement_requirement.pdf").relative_to(ROOT).as_posix(),
            "text_alternative": (requirement_dir / "procurement_requirement.txt").relative_to(ROOT).as_posix(),
            "confirmed_values": (requirement_dir / "confirmed_requirement.json").relative_to(ROOT).as_posix(),
        },
        "quotes": quote_entries,
        "policy": {
            "upload_file": (policy_dir / "electronics_procurement_full_flow.txt").relative_to(ROOT).as_posix(),
            "metadata_file": (policy_dir / "upload_metadata.json").relative_to(ROOT).as_posix(),
            "reviewed_clauses_file": (policy_dir / "reviewed_clauses.json").relative_to(ROOT).as_posix(),
            "required_control_codes": ["APPROVED_SUPPLIER", "ROHS_COMPLIANCE", "AMOUNT_APPROVAL"],
            "binding": {
                "policy_set_id": POLICY_METADATA["policy_set_id"],
                "policy_set_version": POLICY_METADATA["policy_set_version"],
                "policy_index_version": "SELECT_FROM_PUBLISHED_POLICY",
                "category": "Electronics",
                "region": "SG",
            },
        },
        "files": [
            {
                "path": path.relative_to(ROOT).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in runtime_files
        ],
    }
    _write_json(manifest_path, manifest)
    print(f"Generated {len(runtime_files) + 1} runtime files in {OUT}")
    print(f"Generated operator reference in {REFERENCE_OUT}")


if __name__ == "__main__":
    generate()
