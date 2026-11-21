"""Adapter biblioteki Docling do ekstrakcji tekstu, tabel i znormalizowanych bboxów 0..1."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import (
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.extractors.exceptions import ExtractionError


@dataclass(frozen=True, slots=True)
class DoclingPageContent:
    """Elementy wyekstrahowane przez Docling dla pojedynczej strony."""

    blocks: list[ExtractedTextBlock] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    width: float | None = None
    height: float | None = None


@dataclass(frozen=True, slots=True)
class DoclingExtractionResult:
    """Ustrukturyzowany wynik konwersji dokumentu przez Docling."""

    pages: dict[int, DoclingPageContent] = field(default_factory=dict)
    markdown: str = ""
    warnings: list[str] = field(default_factory=list)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_bbox(
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    page_width: float,
    page_height: float,
    coord_origin: str = "TOPLEFT",
) -> BoundingBox | None:
    """Przelicza prostokąt Docling na zakres 0.0–1.0 z początkiem w lewym górnym rogu (0, 0)."""
    if page_width <= 0.0 or page_height <= 0.0:
        return None

    x_min = min(left, right)
    x_max = max(left, right)
    origin = str(coord_origin).upper()
    if "BOTTOMLEFT" in origin:
        y_top = page_height - max(top, bottom)
        y_bottom = page_height - min(top, bottom)
    else:
        y_top = min(top, bottom)
        y_bottom = max(top, bottom)

    x0 = _clamp01(x_min / page_width)
    x1 = _clamp01(x_max / page_width)
    y0 = _clamp01(y_top / page_height)
    y1 = _clamp01(y_bottom / page_height)
    if x1 < x0 or y1 < y0:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _extract_page_sizes(
    doc: Any,
    fallback_page_sizes: Mapping[int, tuple[float, float]] | None,
) -> dict[int, tuple[float, float]]:
    sizes: dict[int, tuple[float, float]] = {}
    if fallback_page_sizes:
        sizes.update(fallback_page_sizes)

    raw_pages = getattr(doc, "pages", None)
    if isinstance(raw_pages, Mapping):
        for raw_no, page_obj in raw_pages.items():
            page_no = int(raw_no)
            size = getattr(page_obj, "size", None)
            if size is None:
                continue
            width = float(getattr(size, "width", 0.0) or 0.0)
            height = float(getattr(size, "height", 0.0) or 0.0)
            if width > 0.0 and height > 0.0:
                sizes[page_no] = (width, height)
    return sizes


def _item_page_and_bbox(
    item: Any,
    page_sizes: Mapping[int, tuple[float, float]],
) -> tuple[int, BoundingBox | None]:
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return 1, None

    prov = prov_list[0]
    page_no = max(1, int(getattr(prov, "page_no", 1) or 1))
    raw_bbox = getattr(prov, "bbox", None)
    size = page_sizes.get(page_no)
    if raw_bbox is None or size is None:
        return page_no, None

    coord_origin = getattr(raw_bbox, "coord_origin", "TOPLEFT")
    origin_val = getattr(coord_origin, "value", str(coord_origin))
    bbox = normalize_bbox(
        float(raw_bbox.l),
        float(raw_bbox.t),
        float(raw_bbox.r),
        float(raw_bbox.b),
        page_width=size[0],
        page_height=size[1],
        coord_origin=str(origin_val),
    )
    return page_no, bbox


def _extract_table(
    item: Any,
    *,
    page_no: int,
    bbox: BoundingBox | None,
) -> ExtractedTable:
    data = getattr(item, "data", None)
    raw_cells = getattr(data, "table_cells", None) or []
    num_rows = int(getattr(data, "num_rows", 0) or 0)
    num_cols = int(getattr(data, "num_cols", 0) or 0)

    cells: list[ExtractedTableCell] = []
    max_row = num_rows
    max_col = num_cols
    for cell in raw_cells:
        r0 = max(0, int(getattr(cell, "start_row_offset_idx", 0) or 0))
        r1 = max(r0 + 1, int(getattr(cell, "end_row_offset_idx", r0 + 1) or (r0 + 1)))
        c0 = max(0, int(getattr(cell, "start_col_offset_idx", 0) or 0))
        c1 = max(c0 + 1, int(getattr(cell, "end_col_offset_idx", c0 + 1) or (c0 + 1)))
        text = str(getattr(cell, "text", "") or "").strip()
        cells.append(
            ExtractedTableCell(
                row=r0,
                col=c0,
                row_span=r1 - r0,
                col_span=c1 - c0,
                text=text,
            )
        )
        max_row = max(max_row, r1)
        max_col = max(max_col, c1)

    rows: list[list[str]] = [["" for _ in range(max_col)] for _ in range(max_row)]
    for parsed_cell in cells:
        if parsed_cell.row < max_row and parsed_cell.col < max_col:
            rows[parsed_cell.row][parsed_cell.col] = parsed_cell.text

    if not rows:
        grid = getattr(data, "grid", None) or []
        for grid_row in grid:
            rows.append([str(getattr(c, "text", "") or "").strip() for c in grid_row])

    sheet_name: str | None = None
    raw_sheet = getattr(item, "sheet_name", None)
    if isinstance(raw_sheet, str) and raw_sheet.strip():
        sheet_name = raw_sheet.strip()

    return ExtractedTable(
        page=page_no,
        rows=rows,
        cells=cells,
        bbox=bbox,
        sheet_name=sheet_name,
    )


def _heading_level(item: Any) -> int | None:
    label = str(getattr(getattr(item, "label", ""), "value", getattr(item, "label", ""))).lower()
    type_name = type(item).__name__
    raw_level = getattr(item, "level", None)

    if (
        isinstance(raw_level, int)
        and 1 <= raw_level <= 6
        and ("header" in label or "title" in label or "SectionHeader" in type_name)
    ):
        return raw_level
    if "title" in label or "TitleItem" in type_name:
        return 1
    if "section_header" in label or "SectionHeader" in type_name:
        return 1
    return None


def parse_docling_document(
    doc: Any,
    *,
    fallback_page_sizes: Mapping[int, tuple[float, float]] | None = None,
) -> DoclingExtractionResult:
    """Konwertuje obiekt ``DoclingDocument`` na bloki tekstu i tabele ze współrzędnymi 0..1."""
    page_sizes = _extract_page_sizes(doc, fallback_page_sizes)
    blocks_by_page: dict[int, list[ExtractedTextBlock]] = {}
    tables_by_page: dict[int, list[ExtractedTable]] = {}

    iterate_items = getattr(doc, "iterate_items", None)
    if callable(iterate_items):
        for item, _depth in iterate_items():
            page_no, bbox = _item_page_and_bbox(item, page_sizes)
            data = getattr(item, "data", None)
            if data is not None and (hasattr(data, "table_cells") or hasattr(data, "grid")):
                table = _extract_table(item, page_no=page_no, bbox=bbox)
                tables_by_page.setdefault(page_no, []).append(table)
                continue

            raw_text = getattr(item, "text", None)
            if isinstance(raw_text, str):
                cleaned = raw_text.strip()
                if cleaned:
                    blocks_by_page.setdefault(page_no, []).append(
                        ExtractedTextBlock(
                            text=cleaned,
                            page=page_no,
                            bbox=bbox,
                            channel=SourceChannel.TEXT,
                            heading_level=_heading_level(item),
                        )
                    )

    export_md = getattr(doc, "export_to_markdown", None)
    markdown = str(export_md()).strip() if callable(export_md) else ""

    all_page_nos = sorted(set(page_sizes) | set(blocks_by_page) | set(tables_by_page))
    pages: dict[int, DoclingPageContent] = {}
    for page_no in all_page_nos:
        size = page_sizes.get(page_no)
        pages[page_no] = DoclingPageContent(
            blocks=blocks_by_page.get(page_no, []),
            tables=tables_by_page.get(page_no, []),
            width=size[0] if size else None,
            height=size[1] if size else None,
        )

    return DoclingExtractionResult(pages=pages, markdown=markdown)


@lru_cache(maxsize=2)
def _get_converter(accuracy_mode: Literal["max", "balanced"]) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )
    from docling.document_converter import (
        DocumentConverter,
        ExcelFormatOption,
        ImageFormatOption,
        PdfFormatOption,
        WordFormatOption,
    )

    table_mode = TableFormerMode.ACCURATE if accuracy_mode == "max" else TableFormerMode.FAST
    pipeline_options = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        table_structure_options=TableStructureOptions(mode=table_mode),
    )
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
            InputFormat.DOCX: WordFormatOption(),
            InputFormat.XLSX: ExcelFormatOption(),
        }
    )


def convert_with_docling(
    path: Path,
    *,
    settings: Settings,
    fallback_page_sizes: Mapping[int, tuple[float, float]] | None = None,
) -> DoclingExtractionResult:
    """Uruchamia konwersję Docling dla pliku na dysku (wewnątrz TemporaryDirectory)."""
    try:
        converter = _get_converter(settings.accuracy_mode)
        result = converter.convert(path)
    except Exception as exc:
        raise ExtractionError(
            f"Błąd ekstrakcji struktury dokumentu (Docling): {exc}",
            code="DOCLING_EXTRACTION_ERROR",
        ) from exc

    doc = getattr(result, "document", None)
    if doc is None:
        return DoclingExtractionResult(warnings=["Docling nie zwrócił obiektu dokumentu."])

    return parse_docling_document(doc, fallback_page_sizes=fallback_page_sizes)
