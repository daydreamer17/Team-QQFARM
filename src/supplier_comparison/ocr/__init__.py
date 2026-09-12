"""Isolated OCR engine and benchmark components for V7 stages 3 and later."""

from .benchmark import OcrBenchmarkGates, apply_hard_gates, select_engine
from .engine import (
    OcrEngine,
    OcrEngineError,
    OcrPageResult,
    OcrTable,
    OcrTextRegion,
    PaddleStructureV3Engine,
    PixelBox,
    TesseractEngine,
)

__all__ = [
    "OcrBenchmarkGates",
    "OcrEngine",
    "OcrEngineError",
    "OcrPageResult",
    "OcrTable",
    "OcrTextRegion",
    "PaddleStructureV3Engine",
    "PixelBox",
    "TesseractEngine",
    "apply_hard_gates",
    "select_engine",
]
