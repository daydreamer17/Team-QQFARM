"""Deterministic PDF page-quality analysis and routing for the V7 boundary."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from .contracts import CoordinateSpace, PageAnalysis, PageRoute
from .errors import ContractError


QUALITY_POLICY_VERSION = "pdf-page-quality/1.0.0"


@dataclass(frozen=True, slots=True)
class PdfQualityConfig:
    min_native_chars: int = 80
    min_printable_ratio: float = 0.90
    min_alnum_ratio: float = 0.50
    primary_image_area_ratio: float = 0.50
    ocr_enabled: bool = False

    def __post_init__(self) -> None:
        if self.min_native_chars < 1:
            raise ValueError("min_native_chars must be positive")
        for name in (
            "min_printable_ratio",
            "min_alnum_ratio",
            "primary_image_area_ratio",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")

    @classmethod
    def from_env(cls, prefix: str = "SUPPLIER_PDF_") -> "PdfQualityConfig":
        return cls(
            min_native_chars=_env_int(
                f"{prefix}MIN_NATIVE_CHARS",
                os.getenv(f"{prefix}MIN_NATIVE_CHARS", "80"),
            ),
            min_printable_ratio=_env_float(
                f"{prefix}MIN_PRINTABLE_RATIO",
                os.getenv(f"{prefix}MIN_PRINTABLE_RATIO", "0.90"),
            ),
            min_alnum_ratio=_env_float(
                f"{prefix}MIN_ALNUM_RATIO",
                os.getenv(f"{prefix}MIN_ALNUM_RATIO", "0.50"),
            ),
            primary_image_area_ratio=_env_float(
                f"{prefix}PRIMARY_IMAGE_AREA_RATIO",
                os.getenv(f"{prefix}PRIMARY_IMAGE_AREA_RATIO", "0.50"),
            ),
            ocr_enabled=_env_bool(os.getenv(f"{prefix}OCR_ENABLED", "false")),
        )


def _env_bool(raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ContractError(
        "pdf_config_invalid_boolean",
        "PDF boolean configuration must be true or false",
        value=raw_value,
    )


def _env_int(name: str, raw_value: str) -> int:
    try:
        return int(raw_value)
    except ValueError as exc:
        raise ContractError(
            "pdf_config_invalid_integer",
            "PDF integer configuration must be a base-10 integer",
            name=name,
            value=raw_value,
        ) from exc


def _env_float(name: str, raw_value: str) -> float:
    try:
        return float(raw_value)
    except ValueError as exc:
        raise ContractError(
            "pdf_config_invalid_number",
            "PDF ratio configuration must be numeric",
            name=name,
            value=raw_value,
        ) from exc


def parser_fingerprint(
    *,
    parser_version: str,
    pdfplumber_version: str,
    config: PdfQualityConfig,
    layout_config: dict[str, object] | None = None,
    ocr_config: dict[str, object] | None = None,
    ocr_engine_version: str | None = None,
) -> str:
    payload = {
        "parser_version": parser_version,
        "pdfplumber_version": pdfplumber_version,
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "quality_config": asdict(config),
        "layout": layout_config,
        "ocr": ocr_config,
        "ocr_engine_version": ocr_engine_version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def analyze_page(
    page: Any,
    page_number: int,
    config: PdfQualityConfig,
) -> PageAnalysis:
    native_text = str(page.extract_text() or "")
    characters = [character for character in native_text if not character.isspace()]
    native_char_count = len(characters)
    printable_ratio = _ratio(
        sum(character.isprintable() for character in characters),
        native_char_count,
        empty_value=1.0,
    )
    alnum_ratio = _ratio(
        sum(character.isalnum() for character in characters),
        native_char_count,
        empty_value=0.0,
    )
    width = float(page.width)
    height = float(page.height)
    page_area = width * height
    image_area = sum(_image_area(image) for image in (page.images or ()))
    image_area_ratio = min(1.0, image_area / page_area) if page_area > 0 else 0.0

    reliable_text = (
        native_char_count >= config.min_native_chars
        and printable_ratio >= config.min_printable_ratio
        and alnum_ratio >= config.min_alnum_ratio
    )
    primary_image = image_area_ratio >= config.primary_image_area_ratio
    reasons: list[str] = []
    if reliable_text:
        reasons.append("NATIVE_TEXT_THRESHOLDS_MET")
    else:
        if native_char_count < config.min_native_chars:
            reasons.append("NATIVE_TEXT_BELOW_THRESHOLD")
        if printable_ratio < config.min_printable_ratio:
            reasons.append("PRINTABLE_RATIO_BELOW_THRESHOLD")
        if alnum_ratio < config.min_alnum_ratio:
            reasons.append("ALNUM_RATIO_BELOW_THRESHOLD")
    if primary_image:
        reasons.append("PRIMARY_PAGE_IMAGE_PRESENT")
    else:
        reasons.append("NO_PRIMARY_PAGE_IMAGE")

    if reliable_text and primary_image:
        route = PageRoute.HYBRID
    elif reliable_text:
        route = PageRoute.NATIVE_TEXT
    elif primary_image:
        route = PageRoute.OCR
    else:
        route = PageRoute.MANUAL_REQUIRED

    return PageAnalysis(
        page_number=page_number,
        route=route,
        native_char_count=native_char_count,
        printable_ratio=_ratio_string(printable_ratio),
        alnum_ratio=_ratio_string(alnum_ratio),
        image_area_ratio=_ratio_string(image_area_ratio),
        page_width=round(width, 3),
        page_height=round(height, 3),
        coordinate_space=CoordinateSpace.PDF_POINTS,
        quality_reasons=tuple(reasons),
    )


def _ratio(numerator: int, denominator: int, *, empty_value: float) -> float:
    return numerator / denominator if denominator else empty_value


def _ratio_string(value: float) -> str:
    return f"{value:.4f}"


def _image_area(image: dict[str, Any]) -> float:
    try:
        width = max(0.0, float(image["x1"]) - float(image["x0"]))
        if "top" in image and "bottom" in image:
            height = max(0.0, float(image["bottom"]) - float(image["top"]))
        else:
            height = max(0.0, float(image["y1"]) - float(image["y0"]))
        return width * height
    except (KeyError, TypeError, ValueError):
        return 0.0
