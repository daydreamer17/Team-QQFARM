"""Native PDF parser producing stable text/table atoms and reading contexts."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from supplier_comparison.ocr.engine import OcrEngine, OcrEngineError

from .contracts import (
    BoundingBox,
    CoordinateSpace,
    DocumentContext,
    EvidenceContextGroup,
    EvidenceSource,
    OcrMetadata,
    PageRoute,
    ParsedInput,
    SourceKind,
)
from .errors import InputLimitError, UnreadableInputError, UnsupportedInputError
from .files import FileLimits, require_pdf_magic, stable_id, validate_regular_file
from .pdf_layout import PdfLayoutConfig, extract_page_layout
from .pdf_ocr import (
    PdfOcrConfig,
    PdfPageRenderer,
    PdfiumPageRenderer,
    build_selected_engine,
    page_analysis_with_ocr,
    preflight_pixel_limits,
    run_ocr_page,
    validate_selected_engine_identity,
)
from .pdf_quality import PdfQualityConfig, analyze_page, parser_fingerprint


PARSER_VERSION = "pdfplumber-ocr/3.0.0"


class PdfQuoteParser:
    def __init__(
        self,
        limits: FileLimits | None = None,
        quality_config: PdfQualityConfig | None = None,
        layout_config: PdfLayoutConfig | None = None,
        ocr_config: PdfOcrConfig | None = None,
        ocr_engine: OcrEngine | None = None,
        page_renderer: PdfPageRenderer | None = None,
    ) -> None:
        self.limits = limits or FileLimits()
        self.quality_config = quality_config or PdfQualityConfig.from_env()
        self.layout_config = layout_config or PdfLayoutConfig.from_env()
        self.ocr_config = ocr_config or PdfOcrConfig.from_env()
        self.ocr_engine = ocr_engine
        self.page_renderer = page_renderer or PdfiumPageRenderer()

    def parse(self, path: str | Path, context: DocumentContext) -> ParsedInput:
        try:
            pdf_path, size, file_hash = validate_regular_file(path, self.limits)
        except InputLimitError as exc:
            if exc.code != "file_too_large":
                raise
            raise InputLimitError(
                "pdf_size_limit_exceeded",
                "PDF exceeds configured size limit",
                path=str(path),
                **exc.details,
            ) from exc
        require_pdf_magic(pdf_path)
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - exercised only in a broken environment
            raise UnreadableInputError(
                "pdfplumber_unavailable", "pdfplumber is required to parse PDF input"
            ) from exc

        sources: list[EvidenceSource] = []
        context_groups: list[EvidenceContextGroup] = []
        page_analyses = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                if len(pdf.pages) > self.limits.max_pdf_pages:
                    raise InputLimitError(
                        "pdf_page_limit_exceeded",
                        "PDF exceeds configured page limit",
                        path=str(pdf_path),
                        actual_pages=len(pdf.pages),
                        max_pages=self.limits.max_pdf_pages,
                    )
                page_analyses = [
                    analyze_page(page, page_number, self.quality_config)
                    for page_number, page in enumerate(pdf.pages, start=1)
                ]
                ocr_pages = [
                    analysis.page_number
                    for analysis in page_analyses
                    if analysis.route in {PageRoute.OCR, PageRoute.HYBRID}
                ]
                if ocr_pages:
                    if not self.quality_config.ocr_enabled:
                        raise UnsupportedInputError(
                            "pdf_page_requires_ocr",
                            "PDF contains image-backed pages that require OCR; OCR is disabled",
                            path=str(pdf_path),
                            page_numbers=ocr_pages,
                            ocr_enabled=False,
                            ocr_implementation_available=True,
                            page_analyses=[
                                analysis.model_dump(mode="json") for analysis in page_analyses
                            ],
                        )
                manual_pages = [
                    analysis.page_number
                    for analysis in page_analyses
                    if analysis.route == PageRoute.MANUAL_REQUIRED
                ]
                if manual_pages:
                    is_blank = all(
                        analysis.native_char_count == 0
                        and float(analysis.image_area_ratio) == 0
                        for analysis in page_analyses
                    )
                    code = "blank_pdf" if is_blank else "pdf_text_quality_insufficient"
                    message = (
                        f"{pdf_path.name} has no extractable quote text"
                        if is_blank
                        else f"{pdf_path.name} contains pages without reliable native text or a primary image"
                    )
                    raise UnreadableInputError(
                        code,
                        message,
                        path=str(pdf_path),
                        page_numbers=manual_pages,
                        page_analyses=[
                            analysis.model_dump(mode="json") for analysis in page_analyses
                        ],
                    )

                engine = None
                engine_version = None
                ocr_layouts = []
                document_started_at = time.monotonic()
                if ocr_pages:
                    try:
                        engine = self.ocr_engine or build_selected_engine(self.ocr_config)
                        engine.start()
                        engine_version = engine.version()
                        validate_selected_engine_identity(engine, engine_version)
                    except OcrEngineError as exc:
                        raise UnreadableInputError(
                            "pdf_ocr_engine_unavailable",
                            "Configured local OCR engine could not start",
                            path=str(pdf_path),
                            page_numbers=ocr_pages,
                            engine_error_code=exc.code,
                            **exc.details,
                        ) from exc

                fingerprint = parser_fingerprint(
                    parser_version=PARSER_VERSION,
                    pdfplumber_version=pdfplumber.__version__,
                    config=self.quality_config,
                    layout_config=self.layout_config.fingerprint_payload(),
                    ocr_config=self.ocr_config.fingerprint_payload(),
                    ocr_engine_version=engine_version,
                )

                if ocr_pages and engine is not None:
                    preflight_pixel_limits(
                        page_analyses,
                        set(ocr_pages),
                        self.ocr_config,
                    )
                    with tempfile.TemporaryDirectory(prefix="supplier-pdf-ocr-") as work_dir:
                        for page_number in ocr_pages:
                            native_text = str(
                                pdf.pages[page_number - 1].extract_text() or ""
                            )
                            ocr_layouts.append(
                                run_ocr_page(
                                    pdf_path=pdf_path,
                                    page_number=page_number,
                                    native_text=native_text,
                                    engine=engine,
                                    renderer=self.page_renderer,
                                    config=self.ocr_config,
                                    work_dir=Path(work_dir),
                                    document_started_at=document_started_at,
                                )
                            )
                    total_ocr_characters = sum(
                        len(atom.raw_text)
                        for layout in ocr_layouts
                        for atom in layout.atoms
                    )
                    if total_ocr_characters > self.ocr_config.max_ocr_characters:
                        raise InputLimitError(
                            "pdf_ocr_output_too_long",
                            "OCR output exceeds the configured document character limit",
                            actual_characters=total_ocr_characters,
                            max_characters=self.ocr_config.max_ocr_characters,
                        )
                    ocr_by_page = {layout.page_number: layout for layout in ocr_layouts}
                    page_analyses = [
                        page_analysis_with_ocr(analysis, ocr_by_page[analysis.page_number])
                        if analysis.page_number in ocr_by_page
                        else analysis
                        for analysis in page_analyses
                    ]

                page_layouts = [
                    extract_page_layout(page, page_number, self.layout_config)
                    for page_number, page in enumerate(pdf.pages, start=1)
                    if page_analyses[page_number - 1].route
                    in {PageRoute.NATIVE_TEXT, PageRoute.HYBRID}
                ]
                source_count = sum(len(layout.atoms) for layout in page_layouts) + sum(
                    len(layout.atoms) for layout in ocr_layouts
                )
                source_characters = sum(
                    len(atom.raw_text)
                    for layout in page_layouts
                    for atom in layout.atoms
                ) + sum(
                    len(atom.raw_text)
                    for layout in ocr_layouts
                    for atom in layout.atoms
                )
                if source_count > self.layout_config.max_sources:
                    raise InputLimitError(
                        "pdf_source_limit_exceeded",
                        "PDF layout produces more atomic sources than allowed",
                        path=str(pdf_path),
                        actual_sources=source_count,
                        max_sources=self.layout_config.max_sources,
                    )
                if source_characters > self.layout_config.max_source_characters:
                    raise InputLimitError(
                        "pdf_source_character_limit_exceeded",
                        "PDF layout source text exceeds the configured character limit",
                        path=str(pdf_path),
                        actual_characters=source_characters,
                        max_characters=self.layout_config.max_source_characters,
                    )

                atom_source_ids: dict[str, str] = {}
                for layout in page_layouts:
                    for atom_index, atom in enumerate(layout.atoms, start=1):
                        bbox = BoundingBox(
                            x0=atom.bbox[0],
                            top=atom.bbox[1],
                            x1=atom.bbox[2],
                            bottom=atom.bbox[3],
                        )
                        block_id = stable_id(
                            "cell" if atom.kind == SourceKind.PDF_TABLE_CELL else "blk",
                            {
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "page": layout.page_number,
                                "index": atom_index,
                                "atom_id": atom.local_id,
                                "bbox": bbox.model_dump(),
                                "text": atom.raw_text,
                                "table_id": atom.table_id,
                                "row_index": atom.row_index,
                                "column_index": atom.column_index,
                                "parser_fingerprint": fingerprint,
                            },
                        )
                        source_id = stable_id(
                            "src",
                            {
                                "kind": atom.kind,
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "block_id": block_id,
                                "parser_fingerprint": fingerprint,
                            },
                        )
                        atom_source_ids[atom.local_id] = source_id
                        sources.append(
                            EvidenceSource(
                                source_id=source_id,
                                kind=atom.kind,
                                document_id=context.document_id,
                                document_version=context.document_version,
                                document_sha256=file_hash,
                                parser_version=PARSER_VERSION,
                                raw_text=atom.raw_text,
                                page_number=layout.page_number,
                                block_id=block_id,
                                bbox=bbox,
                                coordinate_space=CoordinateSpace.PDF_POINTS,
                                table_id=atom.table_id,
                                row_index=atom.row_index,
                                column_index=atom.column_index,
                            )
                        )
                for layout in ocr_layouts:
                    for atom_index, atom in enumerate(layout.atoms, start=1):
                        bbox = BoundingBox(
                            x0=atom.bbox[0],
                            top=atom.bbox[1],
                            x1=atom.bbox[2],
                            bottom=atom.bbox[3],
                        )
                        block_id = stable_id(
                            "ocrblk",
                            {
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "page": layout.page_number,
                                "index": atom_index,
                                "atom_id": atom.local_id,
                                "bbox": bbox.model_dump(),
                                "text": atom.raw_text,
                                "confidence": atom.confidence,
                                "rendered_page_sha256": layout.rendered_page.sha256,
                                "engine": layout.engine_name,
                                "engine_version": layout.engine_version,
                                "parser_fingerprint": fingerprint,
                            },
                        )
                        source_id = stable_id(
                            "src",
                            {
                                "kind": SourceKind.PDF_OCR_BLOCK,
                                "document_id": context.document_id,
                                "document_version": context.document_version,
                                "sha256": file_hash,
                                "block_id": block_id,
                                "parser_fingerprint": fingerprint,
                            },
                        )
                        atom_source_ids[atom.local_id] = source_id
                        sources.append(
                            EvidenceSource(
                                source_id=source_id,
                                kind=SourceKind.PDF_OCR_BLOCK,
                                document_id=context.document_id,
                                document_version=context.document_version,
                                document_sha256=file_hash,
                                parser_version=PARSER_VERSION,
                                raw_text=atom.raw_text,
                                page_number=layout.page_number,
                                block_id=block_id,
                                bbox=bbox,
                                coordinate_space=CoordinateSpace.IMAGE_PIXELS,
                                ocr_metadata=OcrMetadata(
                                    engine=layout.engine_name,
                                    engine_version=layout.engine_version,
                                    confidence=(
                                        f"{atom.confidence:.4f}"
                                        if atom.confidence is not None
                                        else None
                                    ),
                                    rendered_page_sha256=layout.rendered_page.sha256,
                                    render_dpi=self.ocr_config.render_dpi,
                                    preprocessing_steps=self.ocr_config.preprocessing_steps,
                                ),
                            )
                        )
                for layout in page_layouts:
                    for group_index, spec in enumerate(layout.context_specs, start=1):
                        source_ids = tuple(
                            atom_source_ids[atom_id] for atom_id in spec.atom_ids
                        )
                        context_groups.append(
                            EvidenceContextGroup(
                                context_group_id=stable_id(
                                    "ctx",
                                    {
                                        "document_id": context.document_id,
                                        "document_version": context.document_version,
                                        "sha256": file_hash,
                                        "page": spec.page_number,
                                        "index": group_index,
                                        "purpose": spec.purpose.value,
                                        "source_ids": source_ids,
                                        "parser_fingerprint": fingerprint,
                                    },
                                ),
                                page_number=spec.page_number,
                                source_ids=source_ids,
                                purpose=spec.purpose,
                            )
                        )
                for layout in ocr_layouts:
                    for group_index, spec in enumerate(layout.context_specs, start=1):
                        source_ids = tuple(
                            atom_source_ids[atom_id] for atom_id in spec.atom_ids
                        )
                        context_groups.append(
                            EvidenceContextGroup(
                                context_group_id=stable_id(
                                    "ocrctx",
                                    {
                                        "document_id": context.document_id,
                                        "document_version": context.document_version,
                                        "sha256": file_hash,
                                        "page": layout.page_number,
                                        "index": group_index,
                                        "purpose": spec.purpose.value,
                                        "source_ids": source_ids,
                                        "parser_fingerprint": fingerprint,
                                    },
                                ),
                                page_number=layout.page_number,
                                source_ids=source_ids,
                                purpose=spec.purpose,
                            )
                        )
        except (InputLimitError, UnsupportedInputError, UnreadableInputError):
            raise
        except Exception as exc:
            error_text = (
                f"{type(exc).__name__}: {exc!r}: "
                f"{type(exc.__context__).__name__ if exc.__context__ else ''}"
            ).lower()
            if "password" in error_text or "encrypt" in error_text:
                raise UnsupportedInputError(
                    "encrypted_pdf_unsupported",
                    f"{pdf_path.name} is password-protected or encrypted",
                    path=str(pdf_path),
                ) from exc
            raise UnreadableInputError(
                "corrupted_pdf",
                f"{pdf_path.name} is damaged or unreadable",
                path=str(pdf_path),
            ) from exc

        if not sources:
            raise UnreadableInputError(
                "blank_pdf",
                f"{pdf_path.name} has no extractable native or OCR quote content",
                path=str(pdf_path),
            )
        return ParsedInput(
            context=context,
            original_filename=pdf_path.name,
            media_type="application/pdf",
            file_size_bytes=size,
            document_sha256=file_hash,
            parser_version=PARSER_VERSION,
            sources=tuple(sources),
            parser_fingerprint=fingerprint,
            page_analyses=tuple(page_analyses),
            context_groups=tuple(context_groups),
        )
