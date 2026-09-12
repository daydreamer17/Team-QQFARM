from __future__ import annotations

import pytest

from supplier_comparison.ocr.benchmark import (
    apply_hard_gates,
    association_score,
    character_accuracy,
    critical_token_score,
    percentile,
    select_engine,
)
from supplier_comparison.ocr.engine import OcrTextRegion, PixelBox


def _region(text: str, box: tuple[int, int, int, int]) -> OcrTextRegion:
    return OcrTextRegion(text=text, bbox=PixelBox(*box), confidence=0.99)


def _passing_result(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "status": "PASSED",
        "critical_token_exact_rate": 0.99,
        "table_association_rate": 0.96,
        "endurance_pages_completed": 20,
        "oom": False,
        "peak_rss_bytes": 100,
        "p95_page_seconds": 2.0,
        "offline_startup": True,
        "coordinates_complete": True,
        "confidence_complete": True,
    }
    result.update(overrides)
    return result


def test_character_accuracy_and_percentile_are_deterministic() -> None:
    assert character_accuracy("Unit  Price", "Unit Price") == 1.0
    assert character_accuracy("ABC", "AXC") == pytest.approx(2 / 3)
    assert percentile([1, 2, 3, 4], 0.95) == 4


def test_critical_token_score_counts_duplicate_occurrences() -> None:
    matched, total, missing = critical_token_score(
        ["0.00", "0.00", "QW-MCU9-DEMO"],
        "QW-MCU9-DEMO 0.00",
    )

    assert (matched, total) == (2, 3)
    assert missing == ["0.00"]


def test_association_score_checks_geometry_not_only_text_presence() -> None:
    regions = (
        _region("UNIT PRICE", (0, 0, 40, 10)),
        _region("6.42", (60, 1, 90, 11)),
        _region("PART", (0, 30, 30, 40)),
        _region("QW-MCU9-DEMO", (0, 45, 80, 55)),
    )
    matched, total, failed = association_score(
        [
            {"label": "UNIT PRICE", "value": "6.42", "relation": "SAME_ROW"},
            {"label": "PART", "value": "QW-MCU9-DEMO", "relation": "NEXT_BLOCK"},
        ],
        regions,
    )

    assert (matched, total, failed) == (2, 2, [])


def test_next_block_does_not_match_a_value_in_another_column() -> None:
    regions = (
        _region("PART", (0, 0, 30, 10)),
        _region("QW-MCU9-DEMO", (300, 15, 390, 25)),
    )
    matched, total, failed = association_score(
        [{"label": "PART", "value": "QW-MCU9-DEMO", "relation": "NEXT_BLOCK"}],
        regions,
    )

    assert (matched, total) == (0, 1)
    assert failed


def test_hard_gates_require_successful_completed_benchmark() -> None:
    checks = apply_hard_gates(
        _passing_result(status="FAILED"),
        worker_memory_limit_bytes=1000,
    )

    assert checks["benchmark_completed"] is False
    assert not all(checks.values())


def test_selection_prefers_pp_structure_when_both_pass() -> None:
    decision = select_engine(
        {"tesseract": _passing_result(), "pp_structure_v3": _passing_result()},
        worker_memory_limit_bytes=1000,
    )

    assert decision["selected_engine"] == "pp_structure_v3"
    assert decision["decision"] == "SELECTED"


def test_selection_stops_when_no_candidate_passes() -> None:
    decision = select_engine(
        {
            "tesseract": _passing_result(critical_token_exact_rate=0.97),
            "pp_structure_v3": _passing_result(p95_page_seconds=31.0),
        },
        worker_memory_limit_bytes=1000,
    )

    assert decision["selected_engine"] is None
    assert decision["decision"] == "STOP_OCR_REQUIRED_MANUAL_PATH"
