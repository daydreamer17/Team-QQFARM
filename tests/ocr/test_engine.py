from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from supplier_comparison.ocr.engine import (
    OcrEngineError,
    PaddleStructureV3Engine,
    PixelBox,
    TesseractEngine,
    _parse_tesseract_tsv,
)


TSV = """level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext
1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t
5\t1\t1\t1\t1\t1\t10\t20\t30\t10\t98.5\tUnit
5\t1\t1\t1\t1\t2\t45\t20\t25\t10\t87.0\tPrice
"""


def _image(path: Path) -> Path:
    Image.new("RGB", (100, 80), "white").save(path)
    return path


def test_pixel_box_and_confidence_invariants() -> None:
    assert PixelBox(1, 2, 11, 22).width == 10
    with pytest.raises(ValueError, match="positive area"):
        PixelBox(0, 0, 0, 1)


def test_tesseract_tsv_is_grouped_into_lines_with_minimum_confidence() -> None:
    regions = _parse_tesseract_tsv(TSV)

    assert len(regions) == 1
    assert regions[0].text == "Unit Price"
    assert regions[0].bbox == PixelBox(10, 20, 70, 30)
    assert regions[0].confidence == pytest.approx(0.87)


def test_tesseract_engine_uses_argument_list_and_returns_page_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "tesseract"
    binary.write_text("placeholder", encoding="utf-8")
    image_path = _image(tmp_path / "page.png")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, "tesseract 5.5.1\n", "")
        return subprocess.CompletedProcess(command, 0, TSV, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = TesseractEngine(binary).recognize(image_path, timeout_seconds=30)

    assert result.text == "Unit Price"
    assert result.width == 100
    assert result.height == 80
    assert result.image_sha256 == hashlib.sha256(image_path.read_bytes()).hexdigest()
    assert calls[0][0] == str(binary.resolve())
    assert calls[0][-1] == "tsv"
    assert isinstance(calls[0], list)


def test_tesseract_rejects_incomplete_tsv() -> None:
    with pytest.raises(OcrEngineError) as raised:
        _parse_tesseract_tsv("text\nhello\n")
    assert raised.value.code == "ocr_result_shape_invalid"


class _FakeResult:
    def __init__(self, payload: dict[str, object]) -> None:
        self.json = {"res": payload}


class _FakePipeline:
    def __init__(self, output: _FakeResult) -> None:
        self.output = output

    def predict(self, image_path: str):
        assert image_path.endswith("page.png")
        yield self.output


def test_paddle_engine_normalizes_regions_and_tables(tmp_path: Path) -> None:
    image_path = _image(tmp_path / "page.png")
    captured: dict[str, object] = {}
    output = _FakeResult(
        {
            "width": 100,
            "height": 80,
            "overall_ocr_res": {
                "rec_texts": ["PRICE", "6.42"],
                "rec_scores": [0.99, 0.91],
                "rec_boxes": [[5, 5, 35, 15], [50, 5, 75, 15]],
            },
            "table_res_list": [
                {
                    "cell_box_list": [[5, 5, 35, 15], [50, 5, 75, 15]],
                    "pred_html": "<table><tr><th>PRICE</th><td>6.42</td></tr></table>",
                }
            ],
        }
    )

    def factory(**kwargs: object) -> _FakePipeline:
        captured.update(kwargs)
        return _FakePipeline(output)

    result = PaddleStructureV3Engine(pipeline_factory=factory).recognize(
        image_path,
        timeout_seconds=30,
    )

    assert [region.text for region in result.text_regions] == ["PRICE", "6.42"]
    assert result.text_regions[1].confidence == pytest.approx(0.91)
    assert result.tables[0].cell_texts == ("PRICE", "6.42")
    assert captured["use_table_recognition"] is True
    assert captured["use_doc_unwarping"] is False


def test_paddle_engine_rejects_mismatched_arrays(tmp_path: Path) -> None:
    image_path = _image(tmp_path / "page.png")
    output = _FakeResult(
        {
            "overall_ocr_res": {
                "rec_texts": ["PRICE"],
                "rec_scores": [],
                "rec_boxes": [[1, 1, 2, 2]],
            }
        }
    )
    engine = PaddleStructureV3Engine(
        pipeline_factory=lambda **_: _FakePipeline(output)
    )

    with pytest.raises(OcrEngineError) as raised:
        engine.recognize(image_path, timeout_seconds=30)
    assert raised.value.code == "ocr_result_shape_invalid"
