"""Stage 3 OCR engine boundary; not wired into production PDF parsing yet."""

from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import io
import shutil
import subprocess
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image


PADDLE_MODEL_NAMES = (
    "PP-DocLayout-S",
    "PP-OCRv5_mobile_det",
    "en_PP-OCRv5_mobile_rec",
    "PP-LCNet_x1_0_table_cls",
    "SLANeXt_wired",
    "SLANet_plus",
    "RT-DETR-L_wired_table_cell_det",
    "RT-DETR-L_wireless_table_cell_det",
)


class OcrEngineError(RuntimeError):
    """Stable internal failure used by the benchmark and later OCR integration."""

    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class PixelBox:
    x0: int
    top: int
    x1: int
    bottom: int

    def __post_init__(self) -> None:
        if min(self.x0, self.top, self.x1, self.bottom) < 0:
            raise ValueError("pixel coordinates cannot be negative")
        if self.x1 <= self.x0 or self.bottom <= self.top:
            raise ValueError("pixel bounding box must have positive area")

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class OcrTextRegion:
    text: str
    bbox: PixelBox
    confidence: float | None
    block_index: int | None = None
    line_index: int | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("OCR text cannot be blank")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("OCR confidence must be normalized to 0..1")


@dataclass(frozen=True)
class OcrTable:
    bbox: PixelBox
    html: str
    cell_texts: tuple[str, ...]


@dataclass(frozen=True)
class OcrPageResult:
    engine: str
    engine_version: str
    image_sha256: str
    width: int
    height: int
    text_regions: tuple[OcrTextRegion, ...]
    tables: tuple[OcrTable, ...] = ()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("OCR page dimensions must be positive")
        if len(self.image_sha256) != 64:
            raise ValueError("OCR image hash must be SHA-256")

    @property
    def text(self) -> str:
        return "\n".join(region.text for region in self.text_regions)


class OcrEngine(Protocol):
    """Common interface implemented by both stage 3 candidates."""

    name: str

    def version(self) -> str:
        ...

    def start(self) -> None:
        ...

    def recognize(self, image_path: Path, *, timeout_seconds: float) -> OcrPageResult:
        ...


class TesseractEngine:
    name = "tesseract"

    def __init__(
        self,
        binary: Path | str | None = None,
        *,
        language: str = "eng",
        page_segmentation_mode: int = 3,
        tessdata_dir: Path | None = None,
    ) -> None:
        resolved = str(binary) if binary is not None else shutil.which("tesseract")
        if not resolved:
            raise OcrEngineError(
                "ocr_engine_unavailable",
                "Tesseract executable is unavailable",
                engine=self.name,
            )
        self.binary = Path(resolved).resolve()
        self.language = language
        self.page_segmentation_mode = page_segmentation_mode
        self.tessdata_dir = tessdata_dir.resolve() if tessdata_dir else None
        self._version: str | None = None

    def version(self) -> str:
        if self._version is None:
            try:
                completed = subprocess.run(
                    [str(self.binary), "--version"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise OcrEngineError(
                    "ocr_engine_startup_failed",
                    "Tesseract version probe failed",
                    engine=self.name,
                    error_type=type(exc).__name__,
                ) from exc
            self._version = completed.stdout.splitlines()[0].strip()
        return self._version

    def start(self) -> None:
        self.version()

    def recognize(self, image_path: Path, *, timeout_seconds: float) -> OcrPageResult:
        image_path = _validated_image_path(image_path)
        with Image.open(image_path) as image:
            width, height = image.size
        command = [
            str(self.binary),
            str(image_path),
            "stdout",
            "-l",
            self.language,
            "--psm",
            str(self.page_segmentation_mode),
        ]
        if self.tessdata_dir is not None:
            command.extend(["--tessdata-dir", str(self.tessdata_dir)])
        command.append("tsv")
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrEngineError(
                "ocr_page_timeout",
                "Tesseract page recognition timed out",
                engine=self.name,
                timeout_seconds=timeout_seconds,
            ) from exc
        except OSError as exc:
            raise OcrEngineError(
                "ocr_engine_crashed",
                "Tesseract could not be executed",
                engine=self.name,
                error_type=type(exc).__name__,
            ) from exc
        if completed.returncode != 0:
            raise OcrEngineError(
                "ocr_engine_failed",
                "Tesseract returned a non-zero exit code",
                engine=self.name,
                returncode=completed.returncode,
                stderr_summary=completed.stderr.strip()[:300],
            )
        regions = _parse_tesseract_tsv(completed.stdout)
        if not regions:
            raise OcrEngineError(
                "ocr_no_content",
                "Tesseract returned no usable text regions",
                engine=self.name,
            )
        return OcrPageResult(
            engine=self.name,
            engine_version=self.version(),
            image_sha256=_sha256(image_path),
            width=width,
            height=height,
            text_regions=regions,
        )


class PaddleStructureV3Engine:
    name = "pp_structure_v3"

    def __init__(
        self,
        *,
        pipeline_factory: Callable[..., Any] | None = None,
        device: str = "cpu",
    ) -> None:
        self._pipeline_factory = pipeline_factory
        self._pipeline: Any | None = None
        self.device = device

    def version(self) -> str:
        versions = []
        for package in ("paddleocr", "paddlepaddle", "paddlex"):
            try:
                versions.append(f"{package}={importlib.metadata.version(package)}")
            except importlib.metadata.PackageNotFoundError:
                versions.append(f"{package}=unavailable")
        return ";".join(versions) + ";models=" + ",".join(PADDLE_MODEL_NAMES)

    def start(self) -> None:
        if self._pipeline is not None:
            return
        factory = self._pipeline_factory
        if factory is None:
            try:
                from paddleocr import PPStructureV3
            except (ImportError, OSError) as exc:
                raise OcrEngineError(
                    "ocr_engine_unavailable",
                    "PP-StructureV3 dependencies are unavailable",
                    engine=self.name,
                    error_type=type(exc).__name__,
                ) from exc
            factory = PPStructureV3
        try:
            self._pipeline = factory(
                device=self.device,
                layout_detection_model_name=PADDLE_MODEL_NAMES[0],
                text_detection_model_name=PADDLE_MODEL_NAMES[1],
                text_recognition_model_name=PADDLE_MODEL_NAMES[2],
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                use_seal_recognition=False,
                use_table_recognition=True,
                use_formula_recognition=False,
                use_chart_recognition=False,
                use_region_detection=False,
            )
        except Exception as exc:
            raise OcrEngineError(
                "ocr_engine_startup_failed",
                "PP-StructureV3 initialization failed",
                engine=self.name,
                error_type=type(exc).__name__,
            ) from exc

    def recognize(self, image_path: Path, *, timeout_seconds: float) -> OcrPageResult:
        # The local Paddle API has no per-call timeout. Stage 3 records elapsed
        # time and applies the 30-second hard gate; stage 4 will isolate workers
        # so a hung inference can be terminated safely.
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        image_path = _validated_image_path(image_path)
        self.start()
        try:
            outputs = list(self._pipeline.predict(str(image_path)))
        except Exception as exc:
            raise OcrEngineError(
                "ocr_engine_failed",
                "PP-StructureV3 page recognition failed",
                engine=self.name,
                error_type=type(exc).__name__,
            ) from exc
        if len(outputs) != 1:
            raise OcrEngineError(
                "ocr_result_shape_invalid",
                "PP-StructureV3 must return exactly one result for one image",
                engine=self.name,
                result_count=len(outputs),
            )
        payload = _paddle_payload(outputs[0])
        regions = _paddle_regions(payload)
        if not regions:
            raise OcrEngineError(
                "ocr_no_content",
                "PP-StructureV3 returned no usable text regions",
                engine=self.name,
            )
        with Image.open(image_path) as image:
            default_width, default_height = image.size
        return OcrPageResult(
            engine=self.name,
            engine_version=self.version(),
            image_sha256=_sha256(image_path),
            width=int(payload.get("width") or default_width),
            height=int(payload.get("height") or default_height),
            text_regions=regions,
            tables=_paddle_tables(payload),
        )


def _parse_tesseract_tsv(tsv_text: str) -> tuple[OcrTextRegion, ...]:
    groups: dict[tuple[int, int, int], list[tuple[int, str, PixelBox, float]]] = {}
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    required = {
        "level",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
    }
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise OcrEngineError(
            "ocr_result_shape_invalid",
            "Tesseract TSV header is incomplete",
            engine="tesseract",
        )
    for row in reader:
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        try:
            confidence_raw = float(row["conf"])
            left = int(row["left"])
            top = int(row["top"])
            width = int(row["width"])
            height = int(row["height"])
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            word_index = int(row["word_num"])
        except (TypeError, ValueError, KeyError) as exc:
            raise OcrEngineError(
                "ocr_result_shape_invalid",
                "Tesseract TSV contains invalid numeric fields",
                engine="tesseract",
            ) from exc
        if confidence_raw < 0 or width <= 0 or height <= 0:
            continue
        confidence = min(1.0, max(0.0, confidence_raw / 100.0))
        groups.setdefault(key, []).append(
            (
                word_index,
                row["text"].strip(),
                PixelBox(left, top, left + width, top + height),
                confidence,
            )
        )
    regions = []
    for (block, _paragraph, line), words in groups.items():
        ordered = sorted(words, key=lambda item: item[0])
        boxes = [item[2] for item in ordered]
        regions.append(
            OcrTextRegion(
                text=" ".join(item[1] for item in ordered),
                bbox=_union_boxes(boxes),
                confidence=min(item[3] for item in ordered),
                block_index=block,
                line_index=line,
            )
        )
    return tuple(sorted(regions, key=lambda item: (item.bbox.top, item.bbox.x0)))


def _paddle_payload(result: Any) -> dict[str, Any]:
    try:
        result_json = result.json
        payload = result_json["res"]
    except (AttributeError, KeyError, TypeError) as exc:
        raise OcrEngineError(
            "ocr_result_shape_invalid",
            "PP-StructureV3 result does not expose json['res']",
            engine="pp_structure_v3",
        ) from exc
    if not isinstance(payload, dict):
        raise OcrEngineError(
            "ocr_result_shape_invalid",
            "PP-StructureV3 result payload must be an object",
            engine="pp_structure_v3",
        )
    return payload


def _paddle_regions(payload: dict[str, Any]) -> tuple[OcrTextRegion, ...]:
    overall = payload.get("overall_ocr_res")
    if not isinstance(overall, dict):
        raise OcrEngineError(
            "ocr_result_shape_invalid",
            "PP-StructureV3 result is missing overall_ocr_res",
            engine="pp_structure_v3",
        )
    texts = overall.get("rec_texts") or []
    scores = overall.get("rec_scores") or []
    boxes = overall.get("rec_boxes") or []
    if not (len(texts) == len(scores) == len(boxes)):
        raise OcrEngineError(
            "ocr_result_shape_invalid",
            "PP-StructureV3 OCR arrays have different lengths",
            engine="pp_structure_v3",
        )
    regions = []
    for index, (text, score, box) in enumerate(zip(texts, scores, boxes)):
        if not str(text).strip():
            continue
        try:
            confidence = float(score)
            pixel_box = _box_from_sequence(box)
        except (TypeError, ValueError) as exc:
            raise OcrEngineError(
                "ocr_result_shape_invalid",
                "PP-StructureV3 OCR region is invalid",
                engine="pp_structure_v3",
                region_index=index,
            ) from exc
        regions.append(
            OcrTextRegion(
                text=str(text).strip(),
                bbox=pixel_box,
                confidence=min(1.0, max(0.0, confidence)),
                line_index=index,
            )
        )
    return tuple(sorted(regions, key=lambda item: (item.bbox.top, item.bbox.x0)))


def _paddle_tables(payload: dict[str, Any]) -> tuple[OcrTable, ...]:
    tables = []
    for table in payload.get("table_res_list") or []:
        if not isinstance(table, dict):
            continue
        boxes = []
        for box in table.get("cell_box_list") or []:
            try:
                boxes.append(_box_from_sequence(box))
            except (TypeError, ValueError):
                continue
        table_ocr = table.get("table_ocr_pred") or {}
        if not boxes:
            for box in table_ocr.get("rec_boxes") or []:
                try:
                    boxes.append(_box_from_sequence(box))
                except (TypeError, ValueError):
                    continue
        if not boxes:
            continue
        html = str(table.get("pred_html") or "")
        parser = _CellTextParser()
        parser.feed(html)
        cell_texts = tuple(text for text in parser.cells if text)
        if not cell_texts:
            cell_texts = tuple(str(text) for text in table_ocr.get("rec_texts") or [])
        tables.append(OcrTable(bbox=_union_boxes(boxes), html=html, cell_texts=cell_texts))
    return tuple(tables)


class _CellTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_cell = False
        self._pieces: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"td", "th"}:
            self._in_cell = True
            self._pieces = []

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._pieces.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._in_cell:
            self.cells.append(" ".join("".join(self._pieces).split()))
            self._in_cell = False
            self._pieces = []


def _box_from_sequence(value: Any) -> PixelBox:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise TypeError("box must be a sequence")
    if len(value) == 4 and all(not isinstance(item, (list, tuple)) for item in value):
        x0, top, x1, bottom = value
    else:
        points = value
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        x0, top, x1, bottom = min(xs), min(ys), max(xs), max(ys)
    coordinates = [int(round(float(item))) for item in (x0, top, x1, bottom)]
    return PixelBox(*coordinates)


def _union_boxes(boxes: list[PixelBox]) -> PixelBox:
    return PixelBox(
        min(box.x0 for box in boxes),
        min(box.top for box in boxes),
        max(box.x1 for box in boxes),
        max(box.bottom for box in boxes),
    )


def _validated_image_path(image_path: Path) -> Path:
    path = image_path.resolve()
    if not path.is_file():
        raise OcrEngineError(
            "ocr_image_missing",
            "OCR image is missing",
            path=str(path),
        )
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError) as exc:
        raise OcrEngineError(
            "ocr_image_invalid",
            "OCR image is unreadable",
            path=str(path),
        ) from exc
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
