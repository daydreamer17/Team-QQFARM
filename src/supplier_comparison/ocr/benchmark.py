"""Deterministic scoring and hard-gate selection for the stage 3 OCR benchmark."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable

from .engine import OcrPageResult, OcrTextRegion


@dataclass(frozen=True)
class OcrBenchmarkGates:
    critical_token_exact_rate: float = 0.98
    table_association_rate: float = 0.95
    endurance_pages: int = 20
    peak_rss_fraction: float = 0.70
    p95_page_seconds: float = 30.0


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def character_accuracy(expected: str, actual: str) -> float:
    expected_normalized = normalized_text(expected)
    actual_normalized = normalized_text(actual)
    denominator = max(1, len(expected_normalized))
    distance = _levenshtein_distance(expected_normalized, actual_normalized)
    return max(0.0, 1.0 - (distance / denominator))


def critical_token_score(
    expected_tokens: Iterable[str],
    actual_text: str,
) -> tuple[int, int, list[str]]:
    actual = normalized_text(actual_text)
    expected_counter = Counter(normalized_text(token) for token in expected_tokens)
    matched = 0
    missing = []
    for token, expected_count in expected_counter.items():
        actual_count = len(_anchor_pattern(token).findall(actual))
        matched += min(expected_count, actual_count)
        missing.extend([token] * max(0, expected_count - actual_count))
    return matched, sum(expected_counter.values()), missing


def association_score(
    expected_associations: Iterable[dict[str, str]],
    regions: tuple[OcrTextRegion, ...],
) -> tuple[int, int, list[dict[str, str]]]:
    matched = 0
    failed = []
    for association in expected_associations:
        label = normalized_text(association["label"])
        value = normalized_text(association["value"])
        relation = association["relation"]
        label_regions = _regions_with_anchor(regions, label)
        value_regions = _regions_with_anchor(regions, value)
        is_match = any(
            _relation_matches(
                regions,
                label_index,
                value_index,
                relation,
            )
            for label_index in label_regions
            for value_index in value_regions
        )
        if is_match:
            matched += 1
        else:
            failed.append(dict(association))
    total = matched + len(failed)
    return matched, total, failed


def page_accuracy(
    result: OcrPageResult,
    *,
    expected_text: str,
    critical_tokens: Iterable[str],
    associations: Iterable[dict[str, str]],
) -> dict[str, object]:
    critical_matched, critical_total, missing_tokens = critical_token_score(
        critical_tokens,
        result.text,
    )
    association_matched, association_total, failed_associations = association_score(
        associations,
        result.text_regions,
    )
    return {
        "character_accuracy": round(character_accuracy(expected_text, result.text), 6),
        "critical_tokens_matched": critical_matched,
        "critical_tokens_total": critical_total,
        "missing_critical_tokens": missing_tokens,
        "table_associations_matched": association_matched,
        "table_associations_total": association_total,
        "failed_table_associations": failed_associations,
        "text_region_count": len(result.text_regions),
        "table_count": len(result.tables),
        "coordinates_complete": all(region.bbox.width > 0 for region in result.text_regions),
        "confidence_complete": all(
            region.confidence is not None for region in result.text_regions
        ),
    }


def percentile(values: Iterable[float], percentile_value: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if not 0 <= percentile_value <= 1:
        raise ValueError("percentile must be between 0 and 1")
    rank = max(0, int((len(ordered) * percentile_value) + 0.999999) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def apply_hard_gates(
    result: dict[str, object],
    *,
    worker_memory_limit_bytes: int,
    gates: OcrBenchmarkGates | None = None,
) -> dict[str, bool]:
    gates = gates or OcrBenchmarkGates()
    memory_ceiling = worker_memory_limit_bytes * gates.peak_rss_fraction
    return {
        "benchmark_completed": result.get("status") == "PASSED",
        "critical_token_exact_rate": float(result["critical_token_exact_rate"])
        >= gates.critical_token_exact_rate,
        "table_association_rate": float(result["table_association_rate"])
        >= gates.table_association_rate,
        "endurance_no_oom": int(result["endurance_pages_completed"])
        >= gates.endurance_pages
        and not bool(result.get("oom", False)),
        "peak_rss": int(result["peak_rss_bytes"]) < memory_ceiling,
        "p95_page_time": float(result["p95_page_seconds"])
        <= gates.p95_page_seconds,
        "offline_startup": bool(result.get("offline_startup", False)),
        "coordinates": bool(result.get("coordinates_complete", False)),
        "confidence": bool(result.get("confidence_complete", False)),
    }


def select_engine(
    results: dict[str, dict[str, object]],
    *,
    worker_memory_limit_bytes: int,
    gates: OcrBenchmarkGates | None = None,
) -> dict[str, object]:
    gate_results = {
        name: apply_hard_gates(
            result,
            worker_memory_limit_bytes=worker_memory_limit_bytes,
            gates=gates,
        )
        for name, result in results.items()
    }
    passing = [name for name, checks in gate_results.items() if all(checks.values())]
    if "pp_structure_v3" in passing:
        selected = "pp_structure_v3"
    elif len(passing) == 1:
        selected = passing[0]
    else:
        selected = None
    return {
        "selected_engine": selected,
        "passing_engines": sorted(passing),
        "gate_results": gate_results,
        "decision": "SELECTED" if selected else "STOP_OCR_REQUIRED_MANUAL_PATH",
    }


def _anchor_pattern(anchor: str) -> re.Pattern[str]:
    prefix = r"(?<![A-Za-z0-9])" if anchor and anchor[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9])" if anchor and anchor[-1].isalnum() else ""
    return re.compile(prefix + re.escape(anchor) + suffix)


def _regions_with_anchor(
    regions: tuple[OcrTextRegion, ...],
    anchor: str,
) -> list[int]:
    return [
        index
        for index, region in enumerate(regions)
        if _anchor_pattern(anchor).search(normalized_text(region.text))
    ]


def _relation_matches(
    regions: tuple[OcrTextRegion, ...],
    label_index: int,
    value_index: int,
    relation: str,
) -> bool:
    label = regions[label_index].bbox
    value = regions[value_index].bbox
    vertical_overlap = max(0, min(label.bottom, value.bottom) - max(label.top, value.top))
    overlap_ratio = vertical_overlap / max(1, min(label.height, value.height))
    if relation == "SAME_ROW":
        return overlap_ratio >= 0.40
    if relation == "NEXT_BLOCK":
        gap = value.top - label.bottom
        max_gap = max(label.height, value.height) * 4
        left_alignment_tolerance = max(label.height, value.height) * 2
        is_left_aligned = abs(value.x0 - label.x0) <= left_alignment_tolerance
        return (
            0 <= gap <= max_gap
            and 0 < value_index - label_index <= 3
            and is_left_aligned
        )
    raise ValueError(f"unknown association relation: {relation}")


def _levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]
