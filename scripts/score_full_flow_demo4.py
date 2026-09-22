"""OFFLINE scoring only: reads independent answers, never calls a model."""
from __future__ import annotations
import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from supplier_comparison.extraction.dictionary import QuoteDictionary

ROOT = Path(__file__).resolve().parents[1]


def equivalent(name, actual, expected):
    if name == "payment_terms":
        def normalize(value):
            return re.sub(r"\s+", " ", str(value).lower().replace("after invoice", "from invoice")).strip()
        return normalize(actual) == normalize(expected)
    if isinstance(expected, bool):
        return str(actual).lower() == str(expected).lower()
    try:
        if name.endswith("amount") or name in {"unit_price", "price_basis_quantity", "units_per_pack", "order_multiple_units", "moq_quantity", "lead_time_days"}:
            return actual is not None and Decimal(str(actual)) == Decimal(str(expected))
    except InvalidOperation:
        return False
    return actual == expected


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("run_dir", type=Path)
    args = cli.parse_args()
    data = ROOT / "data/generated/inputs/development/full_flow_demo4"
    manifest = json.loads((data / "manifest.json").read_text())
    expectation = json.loads((ROOT / "evaluation/reference/full_flow_demo4/parsing_expectations.json").read_text())
    dictionary = QuoteDictionary.load(ROOT / "data/contracts/quote_data_field.csv")
    extractable = {name for name, definition in dictionary.fields.items() if definition.is_extractable_by_b}
    report = {"mode": "OFFLINE_REFERENCE_SCORING", "human_correction_applied": False, "results": []}
    for quote in manifest["primary_quotes"]:
        key = Path(quote["pdf"]).stem.removesuffix("_quote")
        path = args.run_dir / f"{key}.json"
        if not path.exists():
            report["results"].append({"supplier": key, "status": "NOT_RUN"})
            continue
        result = json.loads(path.read_text())
        if result["status"] != "EXTRACTED":
            report["results"].append({"supplier": key, "status": "FAILED", "error_code": result["error_code"]})
            continue
        fields = {c["field_name"]: c for c in result["batch"]["candidates"]}
        expected = expectation["baseline_fields"] | expectation["suppliers"][quote["supplier_id"]]
        mismatches, checked = [], 0
        # The quote contract has 30 fields. Unsupported display-only CSV columns
        # are explicitly listed instead of falsely counting them as extracted.
        excluded = [name for name in expected if name not in extractable]
        for name, wanted in expected.items():
            if name not in extractable:
                continue
            checked += 1
            candidate = fields.get(name, {})
            actual = candidate.get("normalized_value")
            if not equivalent(name, actual, wanted):
                mismatches.append({"field": name, "expected": wanted, "actual": actual,
                                   "validation_status": candidate.get("validation_status", "ABSENT")})
        findings = result["review"].get("review") or {}
        blockers = [f for f in findings.get("findings", []) if f.get("severity") == "BLOCKING" and not f.get("resolved")]
        report["results"].append({"supplier": key, "status": "SCORED", "checked": checked,
            "matched": checked-len(mismatches), "excluded_not_in_candidate_contract": excluded,
            "mismatches": mismatches, "review_status": result["review"]["review_status"],
            "blocking_findings": [{k: f.get(k) for k in ("field_name", "decision", "reason_code", "message")} for f in blockers],
            "calls_used": result["calls_used"]})
    output = args.run_dir / "offline_score.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
