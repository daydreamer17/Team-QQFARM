#!/usr/bin/env python3
"""Build one three-page synthetic native/scan/hybrid stage 4 PDF fixture."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    REPO_ROOT
    / "data/generated/inputs/development/quote_V5/supplier_a_quote_v5.pdf"
)
DEFAULT_OUTPUT = REPO_ROOT / ".ocr-cache/stage4/mixed_scan_hybrid_v5_a.pdf"
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
NATIVE_LINES = (
    "SUPPLIER QUOTATION",
    "MANUFACTURER PART NUMBER",
    "QW-MCU9-DEMO",
    "UNIT PRICE SGD 6.42 PER PIECE",
    "MINIMUM QUANTITY 1,000 PIECES",
    "VALID UNTIL 25 SEPTEMBER 2026",
    "SHIPPING SGD 120.00 PER ORDER",
    "SYNTHETIC DEVELOPMENT FIXTURE - NOT A COMMERCIAL OFFER",
)
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_text(lines: tuple[str, ...], *, invisible: bool) -> bytes:
    rendering = "3 Tr " if invisible else ""
    operations = [f"BT /F1 12 Tf {rendering}40 790 Td 16 TL"]
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        operations.append(f"({escaped}) Tj T*")
    operations.append("ET")
    return ("\n".join(operations) + "\n").encode("ascii")


def _stream(payload: bytes, *, attributes: str = "") -> bytes:
    suffix = f" {attributes}" if attributes else ""
    return (
        f"<< /Length {len(payload)}{suffix} >>\nstream\n".encode("ascii")
        + payload
        + b"\nendstream"
    )


def _write_fixture(
    output: Path,
    image: Image.Image,
    hybrid_hidden_lines: tuple[str, ...],
) -> None:
    jpeg = io.BytesIO()
    image.save(jpeg, format="JPEG", quality=92, optimize=True)
    jpeg_bytes = jpeg.getvalue()
    draw_image = f"q {PAGE_WIDTH} 0 0 {PAGE_HEIGHT} 0 0 cm /Im0 Do Q\n".encode("ascii")
    hybrid_content = draw_image + _pdf_text(hybrid_hidden_lines, invisible=True)
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Count 3 /Kids [4 0 R 7 0 R 9 0 R] >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        5: _stream(_pdf_text(NATIVE_LINES, invisible=False)),
        6: _stream(
            jpeg_bytes,
            attributes=(
                f"/Type /XObject /Subtype /Image /Width {image.width} "
                f"/Height {image.height} /ColorSpace /DeviceRGB "
                "/BitsPerComponent 8 /Filter /DCTDecode"
            ),
        ),
        7: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /XObject << /Im0 6 0 R >> >> /Contents 8 0 R >>"
        ).encode("ascii"),
        8: _stream(draw_image),
        9: (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 3 0 R >> /XObject << /Im0 6 0 R >> >> "
            "/Contents 10 0 R >>"
        ).encode("ascii"),
        10: _stream(hybrid_content),
    }
    payload = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number in range(1, len(objects) + 1):
        offsets.append(len(payload))
        payload.extend(f"{object_number} 0 obj\n".encode("ascii"))
        payload.extend(objects[object_number])
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()
    if args.dpi != 300:
        raise ValueError("stage 4 validation fixture is frozen at 300 DPI")

    source = args.source.resolve()
    output = args.output.resolve()
    document = pdfium.PdfDocument(str(source))
    try:
        if len(document) != 1:
            raise ValueError("stage 4 fixture source must contain exactly one page")
        image = document[0].render(scale=args.dpi / 72).to_pil().convert("RGB")
    finally:
        document.close()
    if image.width * image.height > 12_000_000:
        raise ValueError("stage 4 fixture exceeds 12 MP")
    with pdfplumber.open(source) as source_pdf:
        source_text = str(source_pdf.pages[0].extract_text() or "")
    if "6.42" not in source_text:
        raise ValueError("stage 4 source fixture is missing the expected 6.42 price")
    hybrid_hidden_lines = tuple(
        line.replace("6.42", "9.99") for line in source_text.splitlines() if line.strip()
    )
    _write_fixture(output, image, hybrid_hidden_lines)
    if output.stat().st_size > 5 * 1024 * 1024:
        raise ValueError("stage 4 fixture exceeds 5 MB")

    with pdfplumber.open(output) as fixture:
        if len(fixture.pages) != 3:
            raise ValueError("stage 4 fixture must contain exactly three pages")
        native_text = [str(page.extract_text() or "") for page in fixture.pages]
        image_counts = [len(page.images) for page in fixture.pages]
        if not native_text[0] or native_text[1] or not native_text[2]:
            raise ValueError("stage 4 fixture text-layer arrangement is invalid")
        if image_counts != [0, 1, 1]:
            raise ValueError("stage 4 fixture image arrangement is invalid")

    result = {
        "result_kind": "V7_STAGE4_SYNTHETIC_MIXED_FIXTURE",
        "synthetic": True,
        "holdout_eligible": False,
        "source": str(source.relative_to(REPO_ROOT)),
        "source_sha256": _sha256(source),
        "output": str(output.relative_to(REPO_ROOT)),
        "output_sha256": _sha256(output),
        "page_kinds": ["NATIVE_TEXT", "OCR", "HYBRID_CONFLICT"],
        "render_dpi": args.dpi,
        "image_width": image.width,
        "image_height": image.height,
        "image_pixels": image.width * image.height,
        "bytes": output.stat().st_size,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
