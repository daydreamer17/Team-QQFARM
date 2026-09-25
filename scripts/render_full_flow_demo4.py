"""Render all demo4 PDFs with PDFium for local visual QA (no Poppler required)."""
from pathlib import Path
import json

import pdfplumber
import pypdfium2
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp/pdfs/full_flow_demo4"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pages = []
    roots = {
        "development": ROOT / "data/generated/demos/full_flow_demo4",
        "holdout": ROOT / "data/generated/fixtures/extraction/full-flow-demo4-layout-holdout",
    }
    for split, base in roots.items():
        for path in sorted(base.rglob("*.pdf")):
            pdf = pypdfium2.PdfDocument(path)
            with pdfplumber.open(path) as text_pdf:
                for i in range(len(pdf)):
                    page = pdf[i]
                    image = page.render(scale=1.5).to_pil()
                    label = f"{split}/{path.relative_to(base)} page {i+1}"
                    name = f"{split}-{str(path.relative_to(base)).replace('/', '-')}-{i+1}.png"
                    image.save(OUT / name)
                    chars = text_pdf.pages[i].chars
                    assert chars and all(-1 <= c["x0"] <= c["x1"] <= text_pdf.pages[i].width + 1 for c in chars), label
                    assert all(-1 <= c["top"] <= c["bottom"] <= text_pdf.pages[i].height + 1 for c in chars), label
                    thumb = image.copy()
                    thumb.thumbnail((390, 530))
                    pages.append((label, name, thumb))
            pdf.close()
    for start in range(0, len(pages), 6):
        sheet = Image.new("RGB", (1200, 1160), "#dde3eb")
        draw = ImageDraw.Draw(sheet)
        for pos, (label, _, thumb) in enumerate(pages[start:start+6]):
            x, y = (pos % 3)*400, (pos//3)*580
            draw.text((x+5, y+5), label[:60], fill="black")
            sheet.paste(thumb, (x+5, y+35))
        sheet.save(OUT / f"contact-{start//6+1}.png")
    (OUT / "render_manifest.json").write_text(json.dumps([{"label": label, "png": name} for label, name, _ in pages], indent=2))
    print(f"Rendered {len(pages)} pages in {OUT}")


if __name__ == "__main__":
    main()
