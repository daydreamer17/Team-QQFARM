"""Native PDF word, table-cell, and non-citable context extraction."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .contracts import EvidenceContextPurpose, SourceKind
from .errors import ContractError
from .files import stable_id


LAYOUT_POLICY_VERSION = "pdf-native-layout/1.1.0"


@dataclass(frozen=True, slots=True)
class PdfLayoutConfig:
    word_x_tolerance: float = 2.0
    word_y_tolerance: float = 2.0
    line_y_tolerance: float = 2.0
    horizontal_cell_gap: float = 32.0
    key_value_max_vertical_gap: float = 14.0
    key_value_column_tolerance: float = 15.0
    content_top_margin: float = 90.0
    content_bottom_margin: float = 40.0
    max_sources: int = 400
    max_source_characters: int = 50_000

    def __post_init__(self) -> None:
        positive_values = (
            "word_x_tolerance",
            "word_y_tolerance",
            "line_y_tolerance",
            "horizontal_cell_gap",
            "key_value_max_vertical_gap",
            "key_value_column_tolerance",
            "max_sources",
            "max_source_characters",
        )
        for name in positive_values:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.content_top_margin < 0 or self.content_bottom_margin < 0:
            raise ValueError("content margins cannot be negative")

    @classmethod
    def from_env(cls, prefix: str = "SUPPLIER_PDF_") -> "PdfLayoutConfig":
        return cls(
            max_sources=_env_int(
                f"{prefix}MAX_SOURCES",
                os.getenv(f"{prefix}MAX_SOURCES", "400"),
            ),
            max_source_characters=_env_int(
                f"{prefix}MAX_SOURCE_CHARACTERS",
                os.getenv(f"{prefix}MAX_SOURCE_CHARACTERS", "50000"),
            ),
        )

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "layout_policy_version": LAYOUT_POLICY_VERSION,
            "layout_config": asdict(self),
        }


@dataclass(frozen=True, slots=True)
class LayoutAtom:
    local_id: str
    kind: SourceKind
    raw_text: str
    bbox: tuple[float, float, float, float]
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None


@dataclass(frozen=True, slots=True)
class LayoutContextSpec:
    page_number: int
    atom_ids: tuple[str, ...]
    purpose: EvidenceContextPurpose


@dataclass(frozen=True, slots=True)
class PageLayout:
    page_number: int
    atoms: tuple[LayoutAtom, ...]
    context_specs: tuple[LayoutContextSpec, ...]


@dataclass(frozen=True, slots=True)
class _TextSegment:
    segment_id: str
    text: str
    bbox: tuple[float, float, float, float]

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def top(self) -> float:
        return self.bbox[1]

    @property
    def bottom(self) -> float:
        return self.bbox[3]


def extract_page_layout(
    page: Any,
    page_number: int,
    config: PdfLayoutConfig,
) -> PageLayout:
    segments = _extract_text_segments(page, page_number, config)
    atoms: list[LayoutAtom] = []
    contexts: list[LayoutContextSpec] = []
    consumed_segment_ids: set[str] = set()

    ruled_atoms, ruled_contexts, ruled_boxes = _extract_ruled_tables(page, page_number)
    atoms.extend(ruled_atoms)
    contexts.extend(ruled_contexts)
    for segment in segments:
        if any(_bbox_center_inside(segment.bbox, table_bbox) for table_bbox in ruled_boxes):
            consumed_segment_ids.add(segment.segment_id)

    remaining = [
        segment for segment in segments if segment.segment_id not in consumed_segment_ids
    ]
    row_atoms, row_contexts, row_consumed = _extract_unruled_row_pairs(
        remaining,
        page_number,
        float(page.width),
        float(page.height),
        config,
    )
    atoms.extend(row_atoms)
    contexts.extend(row_contexts)
    consumed_segment_ids.update(row_consumed)

    remaining = [
        segment for segment in remaining if segment.segment_id not in consumed_segment_ids
    ]
    key_value_atoms, key_value_contexts, key_value_consumed = _extract_key_value_regions(
        remaining,
        page_number,
        float(page.width),
        float(page.height),
        config,
    )
    atoms.extend(key_value_atoms)
    contexts.extend(key_value_contexts)
    consumed_segment_ids.update(key_value_consumed)

    for segment in remaining:
        if segment.segment_id in consumed_segment_ids:
            continue
        atoms.append(
            _atom(
                page_number=page_number,
                kind=SourceKind.PDF_TEXT_BLOCK,
                raw_text=segment.text,
                bbox=segment.bbox,
            )
        )

    atoms.sort(key=lambda item: (item.bbox[1], item.bbox[0], item.kind.value, item.local_id))
    return PageLayout(
        page_number=page_number,
        atoms=tuple(atoms),
        context_specs=tuple(contexts),
    )


def _extract_text_segments(
    page: Any,
    page_number: int,
    config: PdfLayoutConfig,
) -> list[_TextSegment]:
    words = page.extract_words(
        x_tolerance=config.word_x_tolerance,
        y_tolerance=config.word_y_tolerance,
        keep_blank_chars=False,
    ) or []
    lines: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        matching_line = next(
            (
                line
                for line in reversed(lines[-4:])
                if abs(float(line[0]["top"]) - float(word["top"]))
                <= config.line_y_tolerance
            ),
            None,
        )
        if matching_line is None:
            lines.append([word])
        else:
            matching_line.append(word)

    segments: list[_TextSegment] = []
    segment_index = 0
    for line in lines:
        current_words: list[dict[str, Any]] = []
        for word in sorted(line, key=lambda item: float(item["x0"])):
            if (
                current_words
                and float(word["x0"]) - float(current_words[-1]["x1"])
                > config.horizontal_cell_gap
            ):
                segment_index += 1
                segments.append(_segment(page_number, segment_index, current_words))
                current_words = []
            current_words.append(word)
        if current_words:
            segment_index += 1
            segments.append(_segment(page_number, segment_index, current_words))
    return segments


def _segment(
    page_number: int,
    segment_index: int,
    words: list[dict[str, Any]],
) -> _TextSegment:
    text = _normalize_text(" ".join(str(word["text"]) for word in words))
    bbox = (
        min(float(word["x0"]) for word in words),
        min(float(word["top"]) for word in words),
        max(float(word["x1"]) for word in words),
        max(float(word["bottom"]) for word in words),
    )
    return _TextSegment(
        segment_id=f"p{page_number}:segment:{segment_index}",
        text=text,
        bbox=bbox,
    )


def _extract_ruled_tables(
    page: Any,
    page_number: int,
) -> tuple[list[LayoutAtom], list[LayoutContextSpec], list[tuple[float, float, float, float]]]:
    atoms: list[LayoutAtom] = []
    contexts: list[LayoutContextSpec] = []
    table_boxes: list[tuple[float, float, float, float]] = []
    for table_index, table in enumerate(page.find_tables() or (), start=1):
        extracted_rows = table.extract() or []
        # A single detected row is commonly a decorative box or separator,
        # not enough structure to justify replacing normal text atoms.
        if len(extracted_rows) < 2:
            continue
        table_bbox = tuple(float(value) for value in table.bbox)
        table_boxes.append(table_bbox)
        header_texts = [
            _normalize_text(value or "") for value in extracted_rows[0]
        ]
        table_id = stable_id(
            "tbl",
            {
                "kind": "ruled",
                "header": header_texts,
                "columns": len(header_texts),
                "x0": round(table_bbox[0], 2),
                "x1": round(table_bbox[2], 2),
                "widths": [
                    round(float(cell[2]) - float(cell[0]), 2) if cell else None
                    for cell in table.rows[0].cells
                ],
            },
        )
        matrix: list[list[str | None]] = []
        for row_index, row in enumerate(table.rows):
            values = extracted_rows[row_index] if row_index < len(extracted_rows) else []
            row_ids: list[str | None] = []
            for column_index, cell_bbox in enumerate(row.cells):
                raw_value = values[column_index] if column_index < len(values) else None
                text = _normalize_text(raw_value or "")
                if cell_bbox is None or not text:
                    row_ids.append(None)
                    continue
                atom = _atom(
                    page_number=page_number,
                    kind=SourceKind.PDF_TABLE_CELL,
                    raw_text=text,
                    bbox=tuple(float(value) for value in cell_bbox),
                    table_id=table_id,
                    row_index=row_index,
                    column_index=column_index,
                )
                atoms.append(atom)
                row_ids.append(atom.local_id)
            matrix.append(row_ids)

        header_ids = matrix[0] if matrix else []
        for row_ids in matrix:
            current_ids = tuple(source_id for source_id in row_ids if source_id)
            if not current_ids:
                continue
            if len(row_ids) == 2 and all(row_ids):
                contexts.append(
                    LayoutContextSpec(
                        page_number=page_number,
                        atom_ids=(row_ids[0], row_ids[1]),
                        purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
                    )
                )
        for row_ids in matrix[1:]:
            current_ids = tuple(source_id for source_id in row_ids if source_id)
            if not current_ids:
                continue
            row_context_ids = _unique_ids(
                [source_id for source_id in header_ids if source_id] + list(current_ids)
            )
            if len(row_context_ids) > 1:
                contexts.append(
                    LayoutContextSpec(
                        page_number=page_number,
                        atom_ids=row_context_ids,
                        purpose=EvidenceContextPurpose.TABLE_ROW,
                    )
                )
            # Two-column tables in supported quotes are commonly key/value
            # grids. Their row pair above is the semantic relationship; using
            # the first row as column headers would incorrectly connect every
            # later value to the first quoted value.
            if len(header_ids) == 2:
                continue
            for column_index, current_id in enumerate(row_ids):
                if current_id is None:
                    continue
                matching_header = (
                    header_ids[column_index]
                    if column_index < len(header_ids)
                    else None
                )
                pair_ids = _unique_ids(
                    [source_id for source_id in (matching_header, current_id) if source_id]
                )
                if len(pair_ids) > 1:
                    contexts.append(
                        LayoutContextSpec(
                            page_number=page_number,
                            atom_ids=pair_ids,
                            purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
                        )
                    )
    return atoms, contexts, table_boxes


def _extract_unruled_row_pairs(
    segments: list[_TextSegment],
    page_number: int,
    page_width: float,
    page_height: float,
    config: PdfLayoutConfig,
) -> tuple[list[LayoutAtom], list[LayoutContextSpec], set[str]]:
    """Recognize repeated two-column rows without inventing combined source text."""

    content = [
        segment
        for segment in segments
        if segment.top >= config.content_top_margin
        and segment.bottom <= page_height - config.content_bottom_margin
    ]
    lines: list[list[_TextSegment]] = []
    for segment in sorted(content, key=lambda item: (item.top, item.x0)):
        line = next(
            (
                candidate
                for candidate in reversed(lines[-4:])
                if abs(candidate[0].top - segment.top) <= config.line_y_tolerance
            ),
            None,
        )
        if line is None:
            lines.append([segment])
        else:
            line.append(segment)
    candidates = []
    for line in lines:
        ordered = sorted(line, key=lambda item: item.x0)
        if len(ordered) != 2:
            continue
        left, right = ordered
        if right.x0 - left.bbox[2] <= config.horizontal_cell_gap:
            continue
        candidates.append((left, right))

    clusters: list[list[tuple[_TextSegment, _TextSegment]]] = []
    for pair in candidates:
        matching = next(
            (
                cluster
                for cluster in clusters
                if abs(cluster[0][0].x0 - pair[0].x0) <= config.key_value_column_tolerance
                and abs(cluster[0][1].x0 - pair[1].x0) <= config.key_value_column_tolerance
            ),
            None,
        )
        if matching is None:
            clusters.append([pair])
        else:
            matching.append(pair)
    repeated_pairs = [
        pair
        for cluster in clusters
        if len(cluster) >= 4
        # Existing V5 all-uppercase key/value regions use the vertical-pair
        # parser below. This branch is for title-case unruled row tables.
        and sum(not _looks_like_label(pair[0].text) for pair in cluster) * 4
        >= len(cluster) * 3
        for pair in cluster
    ]
    if not repeated_pairs:
        return [], [], set()

    table_id = stable_id(
        "tbl",
        {
            "kind": "unruled_row_pairs",
            "page_width": round(page_width, 2),
            "columns": [
                round(sum(pair[index].x0 for pair in repeated_pairs) / len(repeated_pairs), 2)
                for index in (0, 1)
            ],
            "row_count": len(repeated_pairs),
        },
    )
    atoms: list[LayoutAtom] = []
    contexts: list[LayoutContextSpec] = []
    consumed: set[str] = set()
    for row_index, (label, value) in enumerate(
        sorted(repeated_pairs, key=lambda pair: (pair[0].top, pair[0].x0))
    ):
        label_atom = _atom(
            page_number=page_number,
            kind=SourceKind.PDF_TABLE_CELL,
            raw_text=label.text,
            bbox=label.bbox,
            table_id=table_id,
            row_index=row_index,
            column_index=0,
        )
        value_atom = _atom(
            page_number=page_number,
            kind=SourceKind.PDF_TABLE_CELL,
            raw_text=value.text,
            bbox=value.bbox,
            table_id=table_id,
            row_index=row_index,
            column_index=1,
        )
        atoms.extend((label_atom, value_atom))
        contexts.append(
            LayoutContextSpec(
                page_number=page_number,
                atom_ids=(label_atom.local_id, value_atom.local_id),
                purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
            )
        )
        consumed.update((label.segment_id, value.segment_id))
    return atoms, contexts, consumed


def _extract_key_value_regions(
    segments: list[_TextSegment],
    page_number: int,
    page_width: float,
    page_height: float,
    config: PdfLayoutConfig,
) -> tuple[list[LayoutAtom], list[LayoutContextSpec], set[str]]:
    content_segments = [
        segment
        for segment in segments
        if segment.top >= config.content_top_margin
        and segment.bottom <= page_height - config.content_bottom_margin
    ]
    label_like = [segment for segment in content_segments if _looks_like_label(segment.text)]
    column_starts = _repeated_x_positions(
        [segment.x0 for segment in label_like],
        config.key_value_column_tolerance,
    )
    if not column_starts:
        return [], [], set()

    columns: list[list[_TextSegment]] = [[] for _ in column_starts]
    boundaries = [
        (column_starts[index] + column_starts[index + 1]) / 2
        for index in range(len(column_starts) - 1)
    ]
    for segment in content_segments:
        column_index = next(
            (index for index, boundary in enumerate(boundaries) if segment.x0 < boundary),
            len(column_starts) - 1,
        )
        if segment.x0 >= column_starts[column_index] - config.key_value_column_tolerance:
            columns[column_index].append(segment)

    pairs: list[tuple[_TextSegment, list[_TextSegment]]] = []
    for column in columns:
        ordered = sorted(column, key=lambda item: (item.top, item.x0))
        header_indexes = [
            index
            for index, segment in enumerate(ordered[:-1])
            if _looks_like_label(segment.text)
            and 0 < ordered[index + 1].top - segment.top
            <= config.key_value_max_vertical_gap
        ]
        for header_position, header_index in enumerate(header_indexes):
            next_header_index = (
                header_indexes[header_position + 1]
                if header_position + 1 < len(header_indexes)
                else len(ordered)
            )
            value_segments = ordered[header_index + 1 : next_header_index]
            if not value_segments:
                continue
            pairs.append((ordered[header_index], value_segments))

    pairs.sort(key=lambda item: (item[0].top, item[0].x0))
    if not pairs:
        return [], [], set()
    table_id = stable_id(
        "tbl",
        {
            "kind": "key_value",
            "labels": [header.text for header, _ in pairs],
            "column_starts": [round(value, 2) for value in column_starts],
            "page_width": round(page_width, 2),
        },
    )
    atoms: list[LayoutAtom] = []
    contexts: list[LayoutContextSpec] = []
    consumed: set[str] = set()
    for row_index, (header, value_segments) in enumerate(pairs):
        value_text = _normalize_text(" ".join(segment.text for segment in value_segments))
        value_bbox = _union_bbox([segment.bbox for segment in value_segments])
        header_atom = _atom(
            page_number=page_number,
            kind=SourceKind.PDF_TABLE_CELL,
            raw_text=header.text,
            bbox=header.bbox,
            table_id=table_id,
            row_index=row_index,
            column_index=0,
        )
        value_atom = _atom(
            page_number=page_number,
            kind=SourceKind.PDF_TABLE_CELL,
            raw_text=value_text,
            bbox=value_bbox,
            table_id=table_id,
            row_index=row_index,
            column_index=1,
        )
        atoms.extend((header_atom, value_atom))
        contexts.append(
            LayoutContextSpec(
                page_number=page_number,
                atom_ids=(header_atom.local_id, value_atom.local_id),
                purpose=EvidenceContextPurpose.FIELD_AND_VALUE,
            )
        )
        consumed.add(header.segment_id)
        consumed.update(segment.segment_id for segment in value_segments)
    return atoms, contexts, consumed


def _atom(
    *,
    page_number: int,
    kind: SourceKind,
    raw_text: str,
    bbox: tuple[float, float, float, float],
    table_id: str | None = None,
    row_index: int | None = None,
    column_index: int | None = None,
) -> LayoutAtom:
    rounded_bbox = tuple(round(value, 3) for value in bbox)
    local_id = stable_id(
        "atom",
        {
            "page": page_number,
            "kind": kind.value,
            "text": raw_text,
            "bbox": rounded_bbox,
            "table_id": table_id,
            "row_index": row_index,
            "column_index": column_index,
        },
    )
    return LayoutAtom(
        local_id=local_id,
        kind=kind,
        raw_text=raw_text,
        bbox=rounded_bbox,
        table_id=table_id,
        row_index=row_index,
        column_index=column_index,
    )


def _looks_like_label(text: str) -> bool:
    letters = [character for character in text if character.isalpha()]
    return (
        len(letters) >= 2
        and not any(character.isdigit() for character in text)
        and text == text.upper()
        and len(text) <= 100
    )


def _repeated_x_positions(values: list[float], tolerance: float) -> list[float]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        matching = next(
            (
                cluster
                for cluster in clusters
                if abs(value - sum(cluster) / len(cluster)) <= tolerance
            ),
            None,
        )
        if matching is None:
            clusters.append([value])
        else:
            matching.append(value)
    return [
        sum(cluster) / len(cluster)
        for cluster in clusters
        if len(cluster) >= 2
    ]


def _bbox_center_inside(
    bbox: tuple[float, float, float, float],
    container: tuple[float, float, float, float],
) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    return container[0] <= center_x <= container[2] and container[1] <= center_y <= container[3]


def _union_bbox(
    boxes: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _unique_ids(source_ids: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(source_ids))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
