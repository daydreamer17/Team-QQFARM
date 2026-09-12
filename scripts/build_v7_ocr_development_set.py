#!/usr/bin/env python3
"""Rasterize the frozen eight-page stage 3 OCR development set at 300 DPI."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEFINITION = REPO_ROOT / "evaluation/ocr/v7_stage3_development_set.json"
DEFAULT_OUTPUT = REPO_ROOT / ".ocr-cache/stage3_dataset/manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_dataset(definition_path: Path, output_path: Path) -> dict[str, object]:
    definition_path = definition_path.resolve()
    output_path = output_path.resolve()
    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    cases = definition.get("cases")
    if not isinstance(cases, list) or not 6 <= len(cases) <= 8:
        raise ValueError("stage 3 development set must contain 6 to 8 pages")
    render_dpi = int(definition["render_dpi"])
    if render_dpi != 300:
        raise ValueError("stage 3 benchmark is frozen at 300 DPI")
    max_pixels = int(definition["max_pixels_per_page"])
    image_dir = output_path.parent / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    runtime_cases = []
    for case in cases:
        pdf_path = (REPO_ROOT / case["pdf_path"]).resolve()
        try:
            pdf_path.relative_to(REPO_ROOT)
        except ValueError as exc:
            raise ValueError("benchmark PDF escapes repository") from exc
        if _sha256(pdf_path) != case["pdf_sha256"]:
            raise ValueError(f"benchmark PDF hash mismatch: {case['case_id']}")
        page_index = int(case["page_number"]) - 1
        with pdfplumber.open(pdf_path) as source_pdf:
            if page_index < 0 or page_index >= len(source_pdf.pages):
                raise ValueError(f"invalid page number: {case['case_id']}")
            expected_text = source_pdf.pages[page_index].extract_text(
                x_tolerance=2,
                y_tolerance=2,
            )
        if not expected_text:
            raise ValueError(f"empty reference text: {case['case_id']}")

        rendered_pdf = pdfium.PdfDocument(pdf_path)
        try:
            image = rendered_pdf[page_index].render(scale=render_dpi / 72).to_pil()
        finally:
            rendered_pdf.close()
        pixels = image.width * image.height
        if pixels > max_pixels:
            raise ValueError(f"rendered page exceeds pixel limit: {case['case_id']}")
        image_path = image_dir / f"{case['case_id'].lower()}.png"
        image.save(image_path, format="PNG", optimize=False)
        runtime_cases.append(
            {
                **case,
                "image_path": str(image_path.relative_to(REPO_ROOT)),
                "image_sha256": _sha256(image_path),
                "width": image.width,
                "height": image.height,
                "pixels": pixels,
                "expected_text": expected_text,
                "expected_text_sha256": hashlib.sha256(
                    expected_text.encode("utf-8")
                ).hexdigest(),
            }
        )

    runtime_manifest = {
        "dataset_id": definition["dataset_id"],
        "dataset_kind": definition["dataset_kind"],
        "synthetic": definition["synthetic"],
        "holdout_eligible": definition["holdout_eligible"],
        "definition_path": str(definition_path.relative_to(REPO_ROOT)),
        "definition_sha256": _sha256(definition_path),
        "render_dpi": render_dpi,
        "page_count": len(runtime_cases),
        "cases": runtime_cases,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(runtime_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_dataset(args.definition, args.output)
    print(
        json.dumps(
            {
                "status": "PASSED",
                "manifest": str(args.output),
                "dataset_id": result["dataset_id"],
                "pages": result["page_count"],
                "render_dpi": result["render_dpi"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
