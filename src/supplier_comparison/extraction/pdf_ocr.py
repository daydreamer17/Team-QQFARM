"""Bounded PDF rendering and OCR layout conversion for V7 stage 4."""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from supplier_comparison.ocr.engine import (
    OcrEngine,
    OcrEngineError,
    OcrPageResult,
    TesseractEngine,
)

from .contracts import CoordinateSpace, EvidenceContextPurpose, PageAnalysis
from .errors import ContractError, InputLimitError, UnreadableInputError


OCR_POLICY_VERSION = "pdf-ocr/1.1.0"
SELECTED_OCR_ENGINE = "tesseract"
SELECTED_OCR_ENGINE_VERSION = "tesseract 5.5.1"
OCR_CONFLICT_REASON_PREFIX = "NATIVE_IMAGE_CRITICAL_TOKEN_CONFLICT"


@dataclass(frozen=True, slots=True)
class PdfOcrConfig:
    render_dpi: int = 300
    max_pixels_per_page: int = 12_000_000
    max_pixels_per_document: int = 50_000_000
    page_timeout_seconds: float = 30.0
    document_timeout_seconds: float = 120.0
    max_ocr_characters: int = 50_000
    min_usable_characters: int = 80
    min_printable_ratio: float = 0.90
    min_alnum_ratio: float = 0.50
    tesseract_binary: str | None = None
    tessdata_dir: str | None = None
    preprocessing_steps: tuple[str, ...] = ("PDFIUM_RENDER_RGB",)

    def __post_init__(self) -> None:
        for name in (
            "render_dpi",
            "max_pixels_per_page",
            "max_pixels_per_document",
            "max_ocr_characters",
            "min_usable_characters",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_pixels_per_document < self.max_pixels_per_page:
            raise ValueError("document pixel limit cannot be below the page pixel limit")
        if self.page_timeout_seconds <= 0 or self.document_timeout_seconds <= 0:
            raise ValueError("OCR timeouts must be positive")
        if self.document_timeout_seconds < self.page_timeout_seconds:
            raise ValueError("document timeout cannot be below page timeout")
        for name in ("min_printable_ratio", "min_alnum_ratio"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not self.preprocessing_steps:
            raise ValueError("preprocessing_steps cannot be empty")

    @classmethod
    def from_env(cls, prefix: str = "SUPPLIER_PDF_OCR_") -> "PdfOcrConfig":
        return cls(
            render_dpi=_env_int(
                f"{prefix}RENDER_DPI",
                os.getenv(f"{prefix}RENDER_DPI", "300"),
            ),
            max_pixels_per_page=_env_int(
                f"{prefix}MAX_PIXELS_PER_PAGE",
                os.getenv(f"{prefix}MAX_PIXELS_PER_PAGE", "12000000"),
            ),
            max_pixels_per_document=_env_int(
                f"{prefix}MAX_PIXELS_PER_DOCUMENT",
                os.getenv(f"{prefix}MAX_PIXELS_PER_DOCUMENT", "50000000"),
            ),
            page_timeout_seconds=_env_float(
                f"{prefix}PAGE_TIMEOUT_SECONDS",
                os.getenv(f"{prefix}PAGE_TIMEOUT_SECONDS", "30"),
            ),
            document_timeout_seconds=_env_float(
                f"{prefix}DOCUMENT_TIMEOUT_SECONDS",
                os.getenv(f"{prefix}DOCUMENT_TIMEOUT_SECONDS", "120"),
            ),
            max_ocr_characters=_env_int(
                f"{prefix}MAX_CHARACTERS",
                os.getenv(f"{prefix}MAX_CHARACTERS", "50000"),
            ),
            min_usable_characters=_env_int(
                f"{prefix}MIN_USABLE_CHARACTERS",
                os.getenv(f"{prefix}MIN_USABLE_CHARACTERS", "80"),
            ),
            min_printable_ratio=_env_float(
                f"{prefix}MIN_PRINTABLE_RATIO",
                os.getenv(f"{prefix}MIN_PRINTABLE_RATIO", "0.90"),
            ),
            min_alnum_ratio=_env_float(
                f"{prefix}MIN_ALNUM_RATIO",
                os.getenv(f"{prefix}MIN_ALNUM_RATIO", "0.50"),
            ),
            tesseract_binary=os.getenv(f"{prefix}TESSERACT_BINARY") or None,
            tessdata_dir=os.getenv(f"{prefix}TESSDATA_DIR") or None,
        )

    def fingerprint_payload(self) -> dict[str, object]:
        payload = asdict(self)
        # Installation paths do not change parsing behavior. The exact engine
        # and language-data identities are represented by engine_version.
        payload.pop("tesseract_binary", None)
        payload.pop("tessdata_dir", None)
        return {
            "ocr_policy_version": OCR_POLICY_VERSION,
            "selected_engine": SELECTED_OCR_ENGINE,
            "ocr_config": payload,
        }


@dataclass(frozen=True, slots=True)
class RenderedPage:
    path: Path
    width: int
    height: int
    pixels: int
    sha256: str


class PdfPageRenderer(Protocol):
    def render(
        self,
        pdf_path: Path,
        *,
        page_index: int,
        output_path: Path,
        dpi: int,
    ) -> RenderedPage:
        ...


class PdfiumPageRenderer:
    """Render one page without constructing a command from the user filename."""

    def render(
        self,
        pdf_path: Path,
        *,
        page_index: int,
        output_path: Path,
        dpi: int,
    ) -> RenderedPage:
        try:
            import pypdfium2 as pdfium
        except ImportError as exc:  # pragma: no cover - broken deployment only
            raise OcrEngineError(
                "ocr_renderer_unavailable",
                "pypdfium2 is unavailable",
                error_type=type(exc).__name__,
            ) from exc
        try:
            document = pdfium.PdfDocument(str(pdf_path))
            try:
                if page_index < 0 or page_index >= len(document):
                    raise OcrEngineError(
                        "ocr_page_index_invalid",
                        "OCR render page index is outside the PDF",
                        page_index=page_index,
                    )
                image = document[page_index].render(scale=dpi / 72).to_pil().convert("RGB")
            finally:
                document.close()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG", optimize=False)
        except OcrEngineError:
            raise
        except Exception as exc:
            raise OcrEngineError(
                "ocr_render_failed",
                "PDF page could not be rendered for OCR",
                page_index=page_index,
                error_type=type(exc).__name__,
            ) from exc
        return RenderedPage(
            path=output_path,
            width=image.width,
            height=image.height,
            pixels=image.width * image.height,
            sha256=_sha256(output_path),
        )


@dataclass(frozen=True, slots=True)
class OcrAtom:
    local_id: str
    raw_text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None


@dataclass(frozen=True, slots=True)
class OcrContextSpec:
    atom_ids: tuple[str, ...]
    purpose: EvidenceContextPurpose


@dataclass(frozen=True, slots=True)
class OcrPageLayout:
    page_number: int
    rendered_page: RenderedPage
    engine_name: str
    engine_version: str
    atoms: tuple[OcrAtom, ...]
    context_specs: tuple[OcrContextSpec, ...]
    conflict_categories: tuple[str, ...]


def build_selected_engine(config: PdfOcrConfig) -> OcrEngine:
    return TesseractEngine(
        binary=config.tesseract_binary,
        language="eng",
        page_segmentation_mode=3,
        tessdata_dir=Path(config.tessdata_dir) if config.tessdata_dir else None,
    )


def validate_selected_engine_identity(engine: OcrEngine, version: str) -> None:
    if engine.name != SELECTED_OCR_ENGINE or version != SELECTED_OCR_ENGINE_VERSION:
        raise OcrEngineError(
            "ocr_engine_version_mismatch",
            "Configured OCR engine does not match the frozen stage 3 selection",
            expected_engine=SELECTED_OCR_ENGINE,
            actual_engine=engine.name,
            expected_version=SELECTED_OCR_ENGINE_VERSION,
            actual_version=version,
        )


def preflight_pixel_limits(
    page_analyses: tuple[PageAnalysis, ...] | list[PageAnalysis],
    page_numbers: set[int],
    config: PdfOcrConfig,
) -> None:
    total = 0
    for analysis in page_analyses:
        if analysis.page_number not in page_numbers:
            continue
        width = math.ceil(analysis.page_width * config.render_dpi / 72)
        height = math.ceil(analysis.page_height * config.render_dpi / 72)
        pixels = width * height
        if pixels > config.max_pixels_per_page:
            raise InputLimitError(
                "pdf_ocr_page_pixel_limit_exceeded",
                "Rendered OCR page would exceed the configured pixel limit",
                page_number=analysis.page_number,
                expected_pixels=pixels,
                max_pixels=config.max_pixels_per_page,
            )
        total += pixels
    if total > config.max_pixels_per_document:
        raise InputLimitError(
            "pdf_ocr_document_pixel_limit_exceeded",
            "Rendered OCR document would exceed the configured pixel limit",
            expected_pixels=total,
            max_pixels=config.max_pixels_per_document,
        )


def run_ocr_page(
    *,
    pdf_path: Path,
    page_number: int,
    native_text: str,
    engine: OcrEngine,
    renderer: PdfPageRenderer,
    config: PdfOcrConfig,
    work_dir: Path,
    document_started_at: float,
) -> OcrPageLayout:
    page_started_at = time.monotonic()
    remaining = config.document_timeout_seconds - (
        time.monotonic() - document_started_at
    )
    if remaining <= 0:
        raise UnreadableInputError(
            "pdf_ocr_document_timeout",
            "OCR document processing exceeded its configured timeout",
            page_number=page_number,
            timeout_seconds=config.document_timeout_seconds,
        )
    try:
        rendered = renderer.render(
            pdf_path,
            page_index=page_number - 1,
            output_path=work_dir / f"page-{page_number:04d}.png",
            dpi=config.render_dpi,
        )
        if rendered.pixels > config.max_pixels_per_page:
            raise InputLimitError(
                "pdf_ocr_page_pixel_limit_exceeded",
                "Rendered OCR page exceeds the configured pixel limit",
                page_number=page_number,
                actual_pixels=rendered.pixels,
                max_pixels=config.max_pixels_per_page,
            )
        page_remaining = config.page_timeout_seconds - (
            time.monotonic() - page_started_at
        )
        document_remaining = config.document_timeout_seconds - (
            time.monotonic() - document_started_at
        )
        if page_remaining <= 0:
            raise UnreadableInputError(
                "pdf_ocr_page_timeout",
                "OCR page processing exceeded its configured timeout",
                page_number=page_number,
                timeout_seconds=config.page_timeout_seconds,
            )
        if document_remaining <= 0:
            raise UnreadableInputError(
                "pdf_ocr_document_timeout",
                "OCR document processing exceeded its configured timeout",
                page_number=page_number,
                timeout_seconds=config.document_timeout_seconds,
            )
        result = engine.recognize(
            rendered.path,
            timeout_seconds=min(page_remaining, document_remaining),
        )
    except InputLimitError:
        raise
    except OcrEngineError as exc:
        raise _mapped_ocr_error(exc, page_number=page_number) from exc

    if result.image_sha256 != rendered.sha256:
        raise UnreadableInputError(
            "pdf_ocr_image_hash_mismatch",
            "OCR result does not belong to the rendered page image",
            page_number=page_number,
        )
    if result.width != rendered.width or result.height != rendered.height:
        raise UnreadableInputError(
            "pdf_ocr_image_dimension_mismatch",
            "OCR result dimensions do not match the rendered page image",
            page_number=page_number,
        )
    if time.monotonic() - page_started_at > config.page_timeout_seconds:
        raise UnreadableInputError(
            "pdf_ocr_page_timeout",
            "OCR page processing exceeded its configured timeout",
            page_number=page_number,
            timeout_seconds=config.page_timeout_seconds,
        )
    if time.monotonic() - document_started_at > config.document_timeout_seconds:
        raise UnreadableInputError(
            "pdf_ocr_document_timeout",
            "OCR document processing exceeded its configured timeout",
            page_number=page_number,
            timeout_seconds=config.document_timeout_seconds,
        )
    if not _usable_ocr_text(result.text, config):
        raise UnreadableInputError(
            "pdf_ocr_no_usable_content",
            "OCR found no reliable quote content; manual entry is required",
            page_number=page_number,
            text_characters=len(result.text),
        )
    if len(result.text) > config.max_ocr_characters:
        raise InputLimitError(
            "pdf_ocr_output_too_long",
            "OCR output exceeds the configured character limit",
            page_number=page_number,
            actual_characters=len(result.text),
            max_characters=config.max_ocr_characters,
        )

    atoms = tuple(
        OcrAtom(
            local_id=f"p{page_number}:ocr-line:{index}",
            raw_text=_normalize_text(region.text),
            bbox=(
                float(region.bbox.x0),
                float(region.bbox.top),
                float(region.bbox.x1),
                float(region.bbox.bottom),
            ),
            confidence=region.confidence,
        )
        for index, region in enumerate(result.text_regions, start=1)
        if _normalize_text(region.text)
    )
    contexts = _ocr_contexts(atoms)
    conflicts = native_image_conflict_categories(native_text, result.text)
    return OcrPageLayout(
        page_number=page_number,
        rendered_page=rendered,
        engine_name=result.engine,
        engine_version=result.engine_version,
        atoms=atoms,
        context_specs=contexts,
        conflict_categories=conflicts,
    )


def page_analysis_with_ocr(
    analysis: PageAnalysis,
    layout: OcrPageLayout,
) -> PageAnalysis:
    reasons = list(analysis.quality_reasons)
    reasons.append("OCR_COMPLETED")
    reasons.extend(
        f"{OCR_CONFLICT_REASON_PREFIX}:{category}"
        for category in layout.conflict_categories
    )
    return analysis.model_copy(
        update={
            "page_width": float(layout.rendered_page.width),
            "page_height": float(layout.rendered_page.height),
            "coordinate_space": CoordinateSpace.IMAGE_PIXELS,
            "quality_reasons": tuple(reasons),
            "rendered_page_sha256": layout.rendered_page.sha256,
        }
    )


def native_image_conflict_categories(
    native_text: str,
    ocr_text: str,
) -> tuple[str, ...]:
    native = _critical_tokens(native_text)
    visible = _critical_tokens(ocr_text)
    return tuple(
        category
        for category in ("MONEY", "QUANTITY", "DATE", "PART_NUMBER")
        if native[category] and visible[category] and native[category] != visible[category]
    )


def _critical_tokens(text: str) -> dict[str, frozenset[str]]:
    normalized = _normalize_text(text)
    money = frozenset(
        _normalize_token(match.group(0))
        for match in re.finditer(
            r"(?<![A-Za-z0-9])(?:(?:SGD|USD|EUR|S\$|US\$|\$|€|£)\s*)?"
            r"\d[\d,]*\.\d{2}(?![A-Za-z0-9])",
            normalized,
            re.IGNORECASE,
        )
    )
    date_patterns = (
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}[ -](?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)[ -]\d{4}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
    )
    dates = frozenset(
        _normalize_token(match.group(0))
        for pattern in date_patterns
        for match in re.finditer(pattern, normalized, re.IGNORECASE)
    )
    part_numbers = frozenset(
        _normalize_token(match.group(0))
        for match in re.finditer(
            r"\b(?=[A-Z0-9-]{5,}\b)(?=[A-Z0-9-]*[A-Z])(?=[A-Z0-9-]*\d)"
            r"[A-Z0-9]+(?:-[A-Z0-9]+)+\b",
            normalized.upper(),
        )
    )
    quantities: set[str] = set()
    lines = normalized.splitlines()
    for index, line in enumerate(lines):
        for match in re.finditer(
            r"(?<![.\d])(?P<value>\d[\d,]*)(?![.\d])\s+"
            r"(?:pieces?|units?|packs?|trays?|reels?)\b",
            line,
            re.IGNORECASE,
        ):
            quantities.add(match.group("value").replace(",", ""))
        if not re.search(
            r"\b(?:qty|quantity|minimum|moq|order\s+(?:increment|multiple))\b",
            line,
            re.IGNORECASE,
        ):
            continue
        window = " ".join(lines[index : index + 2])
        match = re.search(
            r"\b(?:qty|quantity|minimum(?:\s+(?:qty|quantity|commitment|buy|order))?|moq|"
            r"order\s+(?:increment|multiple))\b[^\d]{0,80}"
            r"(?P<value>\d[\d,]*)",
            window,
            re.IGNORECASE,
        )
        if match:
            quantities.add(match.group("value").replace(",", ""))
    return {
        "MONEY": money,
        "QUANTITY": frozenset(quantities),
        "DATE": dates,
        "PART_NUMBER": part_numbers,
    }


def _ocr_contexts(atoms: tuple[OcrAtom, ...]) -> tuple[OcrContextSpec, ...]:
    contexts: list[OcrContextSpec] = []
    paired: set[tuple[str, str]] = set()
    # OCR table engines commonly emit the label and value as two regions on
    # the same visual row. Geometry is stronger evidence than capitalization.
    for label in atoms:
        candidates = []
        label_height = max(1.0, label.bbox[3] - label.bbox[1])
        for value in atoms:
            if value.local_id == label.local_id or value.bbox[0] <= label.bbox[2]:
                continue
            value_height = max(1.0, value.bbox[3] - value.bbox[1])
            overlap = max(0.0, min(label.bbox[3], value.bbox[3]) - max(label.bbox[1], value.bbox[1]))
            overlap_ratio = overlap / min(label_height, value_height)
            if overlap_ratio >= 0.50:
                candidates.append((value.bbox[0] - label.bbox[2], value))
        if candidates:
            value = min(candidates, key=lambda item: item[0])[1]
            paired.add((label.local_id, value.local_id))
            contexts.append(
                OcrContextSpec(
                    atom_ids=(label.local_id, value.local_id),
                    purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
                )
            )
    for index, label in enumerate(atoms[:-1]):
        if not _looks_like_label(label.raw_text):
            continue
        candidates = []
        for value in atoms[index + 1 : index + 4]:
            height = max(
                1.0,
                label.bbox[3] - label.bbox[1],
                value.bbox[3] - value.bbox[1],
            )
            vertical_gap = value.bbox[1] - label.bbox[3]
            x_gap = abs(value.bbox[0] - label.bbox[0])
            if 0 <= vertical_gap <= height * 4 and x_gap <= height * 2:
                candidates.append((vertical_gap, x_gap, value))
        if not candidates:
            continue
        value = min(candidates, key=lambda item: (item[0], item[1]))[2]
        if (label.local_id, value.local_id) in paired:
            continue
        contexts.append(
            OcrContextSpec(
                atom_ids=(label.local_id, value.local_id),
                purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
            )
        )
    return tuple(contexts)


def _looks_like_label(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return bool(letters) and len(text) <= 80 and all(character.isupper() for character in letters)


def _usable_ocr_text(text: str, config: PdfOcrConfig) -> bool:
    characters = [character for character in text if not character.isspace()]
    if len(characters) < config.min_usable_characters:
        return False
    printable = sum(character.isprintable() for character in characters) / len(characters)
    alnum = sum(character.isalnum() for character in characters) / len(characters)
    return printable >= config.min_printable_ratio and alnum >= config.min_alnum_ratio


def _mapped_ocr_error(exc: OcrEngineError, *, page_number: int) -> UnreadableInputError:
    if exc.code in {"ocr_engine_unavailable", "ocr_engine_startup_failed", "ocr_renderer_unavailable"}:
        code = "pdf_ocr_engine_unavailable"
        message = "Configured local OCR engine is unavailable"
    elif exc.code == "ocr_page_timeout":
        code = "pdf_ocr_page_timeout"
        message = "OCR page processing exceeded its configured timeout"
    elif exc.code == "ocr_no_content":
        code = "pdf_ocr_no_usable_content"
        message = "OCR returned no usable content; manual entry is required"
    else:
        code = "pdf_ocr_failed"
        message = "OCR page processing failed; manual entry is required"
    return UnreadableInputError(
        code,
        message,
        page_number=page_number,
        engine_error_code=exc.code,
        **exc.details,
    )


def _normalize_text(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())


def _normalize_token(value: str) -> str:
    return " ".join(value.upper().replace(",", "").split())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _env_int(name: str, raw_value: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ContractError(
            "pdf_ocr_config_invalid_integer",
            "PDF OCR integer configuration must be a base-10 integer",
            name=name,
            value=raw_value,
        ) from exc


def _env_float(name: str, raw_value: str) -> float:
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ContractError(
            "pdf_ocr_config_invalid_number",
            "PDF OCR numeric configuration is invalid",
            name=name,
            value=raw_value,
        ) from exc
