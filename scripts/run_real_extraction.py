#!/usr/bin/env python3
"""Run one explicitly selected synthetic development PDF through the configured real model API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from supplier_comparison.extraction.adapters import (
    ModelCallBudget,
    OpenAICompatibleAdapter,
    OpenAICompatibleConfig,
)
from supplier_comparison.extraction.contracts import DocumentContext, ValidationStatus
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.errors import ExtractionError
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.service import extract_quote_candidates


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = {
    "A": {
        "supplier_id": "SUP-022",
        "quote_id": "QUOTE-MCU-DEMO-001-A",
        "document_id": "DOC-MCU-DEMO-001-A-V1",
        "pdf": "supplier_a_quote_v1.pdf",
    },
    "B": {
        "supplier_id": "SUP-023",
        "quote_id": "QUOTE-MCU-DEMO-001-B",
        "document_id": "DOC-MCU-DEMO-001-B-V1",
        "pdf": "supplier_b_quote_v1.pdf",
    },
    "C": {
        "supplier_id": "SUP-024",
        "quote_id": "QUOTE-MCU-DEMO-001-C",
        "document_id": "DOC-MCU-DEMO-001-C-V1",
        "pdf": "supplier_c_quote_v1.pdf",
    },
}


def _write_result(output_path: Path, payload: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--supplier", choices=sorted(SUPPLIERS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    supplier = SUPPLIERS[args.supplier]
    config = OpenAICompatibleConfig.from_env()
    context = DocumentContext(
        task_id="TASK-MCU-DEMO-001-LOCAL-MODEL-TEST",
        task_revision=1,
        scenario_id="MCU-DEMO-001",
        quote_id=supplier["quote_id"],
        quote_version=1,
        document_id=supplier["document_id"],
        document_version=1,
        supplier_id=supplier["supplier_id"],
    )
    dictionary = QuoteDictionary.load(REPO_ROOT / "data/contracts/quote_data_field.csv")
    parsed = PdfQuoteParser().parse(
        REPO_ROOT / "data/generated/inputs/development" / supplier["pdf"],
        context,
    )
    graph_run_id = f"graph_local_{uuid4().hex}"
    extraction_run_id = f"extract_local_{uuid4().hex}"
    budget = ModelCallBudget(graph_run_id=graph_run_id)
    started_at = datetime.now(timezone.utc)
    try:
        batch = extract_quote_candidates(
            parsed,
            dictionary,
            OpenAICompatibleAdapter(config),
            budget,
            extraction_run_id,
        )
        shipping = {
            candidate.field_name: candidate
            for candidate in batch.candidates
            if candidate.field_name in {"shipping_fee_status", "shipping_fee_amount"}
        }
        shipping_missing = None
        if args.supplier == "B":
            shipping_missing = len(shipping) == 2 and all(
                candidate.validation_status == ValidationStatus.MISSING
                and candidate.normalized_value is None
                and not candidate.source_refs
                for candidate in shipping.values()
            )
        extracted_values_complete = all(
            candidate.validation_status != ValidationStatus.EXTRACTED
            or (candidate.raw_value is not None and candidate.normalized_value is not None)
            for candidate in batch.candidates
        )
        checks = {
            "candidate_count_is_30": len(batch.candidates) == 30,
            "extracted_fields_have_values": extracted_values_complete,
        }
        if shipping_missing is not None:
            checks["supplier_b_shipping_remains_missing"] = shipping_missing
        all_checks_passed = all(checks.values())
        record = {
            "result_kind": "REAL_MODEL_EXTRACTION",
            "environment": "LOCAL",
            "input_is_synthetic": True,
            "correction_state": "PRE_CORRECTION",
            "status": "PASSED" if all_checks_passed else "FAILED_OUTPUT_QUALITY",
            "checks": checks,
            "batch": batch.model_dump(mode="json"),
        }
        _write_result(args.output, record)
        print(
            json.dumps(
                {
                    "status": record["status"],
                    "output": str(args.output),
                    "calls_used": budget.calls_used,
                    "model_id": config.model_id,
                    "candidate_count": len(batch.candidates),
                    "supplier_b_shipping_missing": shipping_missing,
                    "total_tokens": batch.run.total_tokens if batch.run else None,
                },
                ensure_ascii=False,
            )
        )
        return 0 if all_checks_passed else 2
    except ExtractionError as exc:
        record = {
            "result_kind": "REAL_MODEL_EXTRACTION",
            "environment": "LOCAL",
            "input_is_synthetic": True,
            "correction_state": "PRE_CORRECTION",
            "status": "FAILED",
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "provider": config.provider,
            "model_id": config.model_id,
            "calls_used": budget.calls_used,
            "error": {"code": exc.code, "message": str(exc), "details": exc.details},
        }
        _write_result(args.output, record)
        print(json.dumps({"status": "FAILED", "output": str(args.output), "error_code": exc.code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
