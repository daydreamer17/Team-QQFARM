"""Live PDF extraction smoke test; reads upload inputs ONLY, never answer files.

Outputs are ignored local evidence, labelled REAL_MODEL and pre-human. This does
not change databases, model prompts, provider selection or production rules.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplier_comparison.extraction.adapters import ModelCallBudget, OpenAICompatibleAdapter, OpenAICompatibleConfig
from supplier_comparison.extraction.contracts import DocumentContext
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.service import extract_quote_candidates

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/generated/inputs/development/full_flow_demo4"


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--include-variants", action="store_true")
    cli.add_argument("--only", help="Supplier key or variant case_id; restrict this run to one input")
    cli.add_argument("--output-dir", type=Path)
    args = cli.parse_args()
    out = args.output_dir or ROOT / "evaluation/results/local/full_flow_demo4" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=False)
    config = OpenAICompatibleConfig.from_env()
    adapter = OpenAICompatibleAdapter(config)
    dictionary = QuoteDictionary.load(ROOT / "data/contracts/quote_data_field.csv")
    manifest = json.loads((DATA / "manifest.json").read_text())
    jobs = [{"id": Path(q["pdf"]).stem.removesuffix("_quote"), "path": q["pdf"], "supplier_id": q["supplier_id"]} for q in manifest["primary_quotes"]]
    if args.include_variants:
        jobs += [{"id": v["case_id"], "path": v["upload"], "supplier_id": v["replaces_supplier_id"]} for v in manifest["variants"]]
    if args.only:
        jobs = [j for j in jobs if j["id"] == args.only]
        if not jobs:
            cli.error("--only did not match a selected input")
    records = []
    for job in jobs:
        path = DATA / job["path"]
        budget = ModelCallBudget(graph_run_id=f"demo4-eval-{uuid4().hex}", max_calls=8)
        record = {"case_id": job["id"], "input": job["path"], "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "mode": "REAL_MODEL", "provider": config.provider, "model_id": config.model_id,
                  "environment": config.environment.value, "reference_answers_loaded": False, "human_correction_applied": False}
        try:
            context = DocumentContext(task_id="DEMO4-LIVE-EVAL", task_revision=1, scenario_id=manifest["scenario_id"],
                quote_id=f"Q-{job['id']}", quote_version=1, document_id=f"DOC-{job['id']}", document_version=1, supplier_id=job["supplier_id"])
            parsed = PdfQuoteParser().parse(path, context)
            batch = extract_quote_candidates(parsed, dictionary, adapter, budget, f"extract_{uuid4().hex}")
            envelope = review_extraction_batch(batch, dictionary, CriticalityContext(required_revision="R1", base_unit="piece"),
                                              input_is_synthetic=True, environment=config.environment)
            record.update(status="EXTRACTED", batch=batch.model_dump(mode="json"), review=envelope.model_dump(mode="json"))
        except Exception as exc:
            # Do not dump provider HTTP bodies, URLs or credentials to console.
            record.update(status="FAILED", error_code=getattr(exc, "code", type(exc).__name__))
        record["calls_used"] = budget.calls_used
        (out / f"{job['id']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        records.append({k: record[k] for k in ("case_id", "status", "calls_used")})
        print(json.dumps(records[-1]), flush=True)
    (out / "summary.json").write_text(json.dumps({"mode": "REAL_MODEL", "reference_answers_loaded": False, "results": records}, indent=2) + "\n")
    print(f"Local evidence: {out}", flush=True)
    return 1 if any(r["status"] == "FAILED" for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
