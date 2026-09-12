#!/usr/bin/env python3
"""Run the stage 4 parser with real local OCR and a declared fixed model output."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter

from supplier_comparison.extraction.adapters import FixedOutputAdapter, ModelCallBudget
from supplier_comparison.extraction.contracts import DocumentContext, SourceKind
from supplier_comparison.extraction.criticality import CriticalityContext
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.extraction.pdf_ocr import PdfOcrConfig
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser
from supplier_comparison.extraction.pdf_quality import PdfQualityConfig
from supplier_comparison.extraction.review import review_extraction_batch
from supplier_comparison.extraction.service import extract_quote_candidates
from supplier_comparison.ocr.engine import TesseractEngine


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = REPO_ROOT / ".ocr-cache/stage4/mixed_scan_hybrid_v5_a.pdf"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "evaluation/results/local/2026-09-11/v7_stage4_ocr/mixed_scan_hybrid_v5_a_validation.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixed_payload(dictionary: QuoteDictionary, price_source_id: str) -> dict[str, object]:
    candidates = []
    for definition in dictionary.extractable_fields:
        if definition.field_name == "unit_price":
            candidates.append(
                {
                    "field_name": "unit_price",
                    "raw_value": "SGD 6.42 per each single piece",
                    "normalized_value": "6.42",
                    "unit": "SGD",
                    "validation_status": "EXTRACTED",
                    "source_refs": [
                        {"source_id": price_source_id, "quoted_text": "6.42"}
                    ],
                }
            )
        else:
            candidates.append(
                {
                    "field_name": definition.field_name,
                    "raw_value": None,
                    "normalized_value": None,
                    "unit": None,
                    "validation_status": "MISSING",
                    "source_refs": [],
                }
            )
    return {"candidates": candidates}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--tesseract-binary",
        type=Path,
        default=REPO_ROOT / ".ocr-tools/tesseract/bin/tesseract",
    )
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        default=REPO_ROOT / ".ocr-tools/tesseract/share/tessdata",
    )
    args = parser.parse_args()

    context = DocumentContext(
        task_id="TASK-V7-STAGE4-LOCAL",
        task_revision=1,
        scenario_id="V7-STAGE4-SYNTHETIC",
        quote_id="QUOTE-V7-STAGE4-MIXED-A",
        quote_version=1,
        document_id="DOC-V7-STAGE4-MIXED-A",
        document_version=1,
        supplier_id="V5-SUP-A",
    )
    ocr_config = PdfOcrConfig(
        tesseract_binary=str(args.tesseract_binary.resolve()),
        tessdata_dir=str(args.tessdata_dir.resolve()),
    )
    quote_parser = PdfQuoteParser(
        quality_config=PdfQualityConfig(ocr_enabled=True),
        ocr_config=ocr_config,
    )
    first_started = time.perf_counter()
    first = quote_parser.parse(args.input, context)
    first_parse_seconds = time.perf_counter() - first_started
    second_started = time.perf_counter()
    second = quote_parser.parse(args.input, context)
    second_parse_seconds = time.perf_counter() - second_started
    if first.parser_fingerprint != second.parser_fingerprint:
        raise AssertionError("same OCR configuration produced different fingerprints")
    if [source.source_id for source in first.sources] != [
        source.source_id for source in second.sources
    ]:
        raise AssertionError("same OCR parse produced unstable source IDs")
    expected_routes = ["NATIVE_TEXT", "OCR", "HYBRID"]
    if [analysis.route.value for analysis in first.page_analyses] != expected_routes:
        raise AssertionError("mixed fixture did not produce the expected page routes")
    ocr_sources = [
        source for source in first.sources if source.kind == SourceKind.PDF_OCR_BLOCK
    ]
    if not ocr_sources or {source.page_number for source in ocr_sources} != {2, 3}:
        raise AssertionError("mixed fixture OCR sources do not cover scan and hybrid pages")
    conflict_reasons = [
        reason
        for reason in first.page_analyses[2].quality_reasons
        if reason.startswith("NATIVE_IMAGE_CRITICAL_TOKEN_CONFLICT:")
    ]
    if not conflict_reasons:
        raise AssertionError("hybrid price conflict was not detected")
    price_source = next(
        (
            source
            for source in ocr_sources
            if source.page_number == 2 and "6.42" in source.raw_text
        ),
        None,
    )
    if price_source is None:
        raise AssertionError("real OCR did not find the expected 6.42 price token")

    dictionary = QuoteDictionary.load(
        REPO_ROOT / "data/contracts/quote_data_field.csv"
    )
    batch = extract_quote_candidates(
        first,
        dictionary,
        FixedOutputAdapter(
            {context.document_id: _fixed_payload(dictionary, price_source.source_id)}
        ),
        ModelCallBudget(graph_run_id="GRAPH-V7-STAGE4-LOCAL"),
        "EXTRACT-V7-STAGE4-LOCAL",
    )
    review = review_extraction_batch(
        batch,
        dictionary,
        CriticalityContext(required_revision=None, base_unit="piece"),
        input_is_synthetic=True,
    )
    price_findings = [
        finding.model_dump(mode="json")
        for finding in review.review.findings
        if finding.field_name == "unit_price"
    ]
    conflict_findings = [
        finding.model_dump(mode="json")
        for finding in review.review.findings
        if "pdf_native_image_conflict" in finding.codes
    ]
    if not conflict_findings or review.downstream_ready:
        raise AssertionError("hybrid conflict did not block the reviewed batch")

    document = pdfium.PdfDocument(str(args.input))
    try:
        source_image = document[1].render(scale=300 / 72).to_pil().convert("RGB")
    finally:
        document.close()
    stress_engine = TesseractEngine(
        args.tesseract_binary,
        tessdata_dir=args.tessdata_dir,
    )
    stress_variants = []
    expected_tokens = ("6.42", "QW-MCU9-DEMO", "1,000", "25 September 2026")
    with tempfile.TemporaryDirectory(prefix="supplier-stage4-stress-") as work_dir:
        variants = {
            "ROTATE_1_5_DEGREES": source_image.rotate(
                1.5,
                resample=Image.Resampling.BICUBIC,
                fillcolor="white",
            ),
            "GAUSSIAN_BLUR_0_7": source_image.filter(ImageFilter.GaussianBlur(0.7)),
        }
        for name, image in variants.items():
            image_path = Path(work_dir) / f"{name.lower()}.png"
            image.save(image_path)
            started = time.perf_counter()
            stress_result = stress_engine.recognize(image_path, timeout_seconds=30)
            elapsed = time.perf_counter() - started
            token_results = {
                token: token in stress_result.text for token in expected_tokens
            }
            if not all(token_results.values()):
                raise AssertionError(f"OCR stress variant lost a critical token: {name}")
            confidences = [
                region.confidence
                for region in stress_result.text_regions
                if region.confidence is not None
            ]
            stress_variants.append(
                {
                    "name": name,
                    "elapsed_seconds": round(elapsed, 6),
                    "critical_tokens": token_results,
                    "minimum_line_confidence": (
                        round(min(confidences), 6) if confidences else None
                    ),
                }
            )
    output = {
        "result_kind": "V7_STAGE4_REAL_LOCAL_OCR_VALIDATION",
        "status": "PASSED",
        "execution_kind": "REAL_LOCAL_OCR_WITH_FIXED_MODEL_OUTPUT",
        "synthetic": True,
        "holdout_eligible": False,
        "input_sha256": _sha256(args.input),
        "parser_version": first.parser_version,
        "parser_fingerprint": first.parser_fingerprint,
        "parse_seconds": [
            round(first_parse_seconds, 6),
            round(second_parse_seconds, 6),
        ],
        "page_routes": expected_routes,
        "rendered_page_sha256": [
            analysis.rendered_page_sha256 for analysis in first.page_analyses
        ],
        "source_count": len(first.sources),
        "source_counts_by_kind": {
            kind.value: sum(source.kind == kind for source in first.sources)
            for kind in SourceKind
        },
        "all_sources_locatable": all(
            source.page_number in {1, 2, 3}
            and source.bbox is not None
            for source in first.sources
        ),
        "all_sources_have_confidence": all(
            source.ocr_metadata is not None
            and source.ocr_metadata.confidence is not None
            for source in ocr_sources
        ),
        "engine": price_source.ocr_metadata.engine,
        "engine_version": price_source.ocr_metadata.engine_version,
        "render_dpi": price_source.ocr_metadata.render_dpi,
        "price_source_confidence": price_source.ocr_metadata.confidence,
        "price_candidate_status": next(
            candidate.validation_status.value
            for candidate in batch.candidates
            if candidate.field_name == "unit_price"
        ),
        "schema_version": batch.schema_version,
        "price_review_findings": price_findings,
        "hybrid_conflict_reasons": conflict_reasons,
        "hybrid_conflict_findings": conflict_findings,
        "review_status": review.review_status.value,
        "downstream_ready": review.downstream_ready,
        "model_output_mode": batch.run.output_mode.value,
        "model_calls": batch.run.calls_after - batch.run.calls_before,
        "stress_variants": stress_variants,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
