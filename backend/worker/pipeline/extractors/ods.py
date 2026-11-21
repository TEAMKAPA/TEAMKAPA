"""Ekstraktor arkuszy OpenDocument (.ods) w pamięci z użyciem biblioteki odfdo (Apache-2.0)."""

import io
import zipfile
from typing import Any

import odfdo

from backend.shared.schemas.pipeline import (
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    ExtractedTableCell,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import SourceChannel
from backend.worker.pipeline.extractors.base import ExtractionContext
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
# Zabezpieczenie przed „repeat bomb” (np. puste wiersze/kolumny powtórzone 1_048_576 razy w Calc).
_MAX_ROWS_PER_SHEET = 10_000
_MAX_COLS_PER_ROW = 512
_MAX_INTERIOR_EMPTY_ROW_GAP = 100


def _validate_ods_bytes(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
            if "META-INF/manifest.xml" in names:
                manifest_info = archive.getinfo("META-INF/manifest.xml")
                if manifest_info.file_size <= _MAX_MANIFEST_BYTES:
                    manifest_bytes = archive.read(manifest_info)
                    if b"encryption-data" in manifest_bytes:
                        raise EncryptedFileError("Plik ODS jest zaszyfrowany lub chroniony hasłem.")

            if "mimetype" in names:
                mime_info = archive.getinfo("mimetype")
                if mime_info.file_size > 128 or archive.read(mime_info).strip() != _ODS_MIMETYPE:
                    raise CorruptedFileError(
                        "Plik ODS zawiera nieprawidłowy wpis mimetype w archiwum ODF."
                    )

            if "content.xml" not in names:
                raise CorruptedFileError("Plik ODS nie zawiera wymaganego wpisu content.xml.")
    except EncryptedFileError:
        raise
    except CorruptedFileError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise CorruptedFileError("Plik ODS nie jest prawidłowym archiwum ZIP/ODF.") from exc


def _parse_positive_int(raw: Any, default: int = 1) -> int:
    if raw is None:
        return default
    try:
        val = int(raw)
    except TypeError, ValueError:
        return default
    return val if val >= 1 else default


def _cell_text(cell: Any) -> str:
    text_content = getattr(cell, "text_content", None)
    if isinstance(text_content, str) and text_content.strip():
        return text_content.strip()
    value = getattr(cell, "value", None)
    if value is not None:
        return str(value).strip()
    return ""


def _extract_row_values(
    row: Any,
) -> tuple[list[str], list[tuple[int, int, int, str]]]:
    """Zwraca ``(wartości_kolumn, lista (col, row_span, col_span, tekst))`` bez ogona pustych."""
    values: list[str] = []
    cell_specs: list[tuple[int, int, int, str]] = []
    pending_empty_cols = 0

    # Używamy XPath zamiast row.get_cells(), bo get_cells() rozwija w pamięci nawet
    # 16 384 powtórzonych pustych kolumn na końcu wiersza w arkuszach LibreOffice Calc.
    for cell in row.xpath("table:table-cell | table:covered-table-cell"):
        if len(values) >= _MAX_COLS_PER_ROW:
            break

        text = _cell_text(cell)
        col_span = _parse_positive_int(cell.get_attribute("table:number-columns-spanned"), 1)
        row_span = _parse_positive_int(cell.get_attribute("table:number-rows-spanned"), 1)
        repeat = _parse_positive_int(getattr(cell, "repeated", None), 1)

        if not text and col_span == 1 and row_span == 1:
            pending_empty_cols += repeat
            continue

        if pending_empty_cols > 0:
            gap = min(pending_empty_cols, _MAX_COLS_PER_ROW - len(values))
            values.extend("" for _ in range(gap))
            pending_empty_cols = 0

        copies = min(repeat, _MAX_COLS_PER_ROW - len(values))
        for _ in range(copies):
            col_idx = len(values)
            values.append(text)
            cell_specs.append((col_idx, row_span, col_span, text))

    return values, cell_specs


def _extract_sheet_table(
    table: Any,
    *,
    page_number: int,
) -> tuple[ExtractedTable, bool]:
    """Iteruje po wierszach i komórkach arkusza ``odfdo``, zachowując pełną siatkę wartości."""
    sheet_name = str(getattr(table, "name", "") or "").strip() or f"Arkusz {page_number}"
    raw_rows: list[list[str]] = []
    cells: list[ExtractedTableCell] = []
    pending_empty_rows = 0
    truncated = False

    # XPath unika rozwijania table:number-rows-repeated="1048576" z końca arkusza Calc.
    row_xpath = (
        "table:table-row | table:table-header-rows/table:table-row "
        "| table:table-row-group/table:table-row"
    )
    for row in table.xpath(row_xpath):
        if len(raw_rows) >= _MAX_ROWS_PER_SHEET:
            truncated = True
            break

        row_repeat = _parse_positive_int(getattr(row, "repeated", None), 1)
        row_values, row_cell_specs = _extract_row_values(row)

        if not row_values:
            pending_empty_rows += row_repeat
            continue

        if pending_empty_rows > 0:
            gap_rows = min(
                pending_empty_rows,
                _MAX_INTERIOR_EMPTY_ROW_GAP,
                _MAX_ROWS_PER_SHEET - len(raw_rows),
            )
            for _ in range(gap_rows):
                raw_rows.append([])
            pending_empty_rows = 0

        copies = min(row_repeat, _MAX_ROWS_PER_SHEET - len(raw_rows))
        if copies < row_repeat:
            truncated = True

        for _ in range(copies):
            row_idx = len(raw_rows)
            raw_rows.append(list(row_values))
            for col_idx, row_span, col_span, text in row_cell_specs:
                cells.append(
                    ExtractedTableCell(
                        row=row_idx,
                        col=col_idx,
                        row_span=row_span,
                        col_span=col_span,
                        text=text,
                    )
                )

    grid_width = max((len(r) for r in raw_rows), default=0)
    grid_rows = [row + [""] * (grid_width - len(row)) for row in raw_rows]

    return (
        ExtractedTable(
            page=page_number,
            rows=grid_rows,
            cells=cells,
            sheet_name=sheet_name,
        ),
        truncated,
    )


def _format_sheet_markdown(table: ExtractedTable) -> str:
    heading = f"## {table.sheet_name}" if table.sheet_name else f"## Arkusz {table.page}"
    if not table.rows:
        return heading

    col_count = max(len(r) for r in table.rows)
    if col_count == 0:
        return heading

    def _esc(val: str) -> str:
        return val.replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        heading,
        "",
        "| " + " | ".join(_esc(c) for c in table.rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(col_count)) + " |",
    ]
    for row in table.rows[1:]:
        lines.append("| " + " | ".join(_esc(c) for c in row) + " |")
    return "\n".join(lines)


def _format_sheet_plain(table: ExtractedTable) -> str:
    lines: list[str] = []
    if table.sheet_name:
        lines.append(table.sheet_name)
    for row in table.rows:
        if any(cell.strip() for cell in row):
            lines.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(lines)


def extract_ods(ctx: ExtractionContext) -> ExtractedDocument:
    """Parsuje arkusz ODS w pamięci (odfdo), zamieniając arkusze na bloki tabelaryczne."""
    _validate_ods_bytes(ctx.content)

    try:
        with io.BytesIO(ctx.content) as ods_stream:
            doc = odfdo.Document(ods_stream)
            raw_tables = doc.body.get_tables()
    except EncryptedFileError:
        raise
    except CorruptedFileError:
        raise
    except Exception as exc:
        raise CorruptedFileError(f"Nie udało się sparsować pliku ODS (odfdo): {exc}") from exc

    pages: list[ExtractedPage] = []
    all_tables: list[ExtractedTable] = []
    sheet_names: list[str] = []
    plain_sections: list[str] = []
    md_sections: list[str] = []
    warnings: list[str] = []

    for idx, raw_table in enumerate(raw_tables):
        page_number = idx + 1
        extracted_table, truncated = _extract_sheet_table(raw_table, page_number=page_number)
        sheet_name = extracted_table.sheet_name or f"Arkusz {page_number}"
        sheet_names.append(sheet_name)
        if truncated:
            warnings.append(
                f"Arkusz '{sheet_name}' przekroczył limit {_MAX_ROWS_PER_SHEET} wierszy "
                "i został obcięty."
            )

        md_block = _format_sheet_markdown(extracted_table)
        plain_text = _format_sheet_plain(extracted_table)
        blocks: list[ExtractedTextBlock] = [
            ExtractedTextBlock(
                text=sheet_name,
                page=page_number,
                channel=SourceChannel.TEXT,
                heading_level=2,
            )
        ]
        if extracted_table.rows:
            table_body = "\n".join(
                " | ".join(cell.strip() for cell in row)
                for row in extracted_table.rows
                if any(cell.strip() for cell in row)
            )
            if table_body:
                blocks.append(
                    ExtractedTextBlock(
                        text=table_body,
                        page=page_number,
                        channel=SourceChannel.TEXT,
                    )
                )

        pages.append(
            ExtractedPage(
                page_number=page_number,
                text=plain_text,
                blocks=blocks,
                tables=[extracted_table],
                source_channels=[SourceChannel.TEXT],
            )
        )
        all_tables.append(extracted_table)
        if plain_text:
            plain_sections.append(plain_text)
        if md_block:
            md_sections.append(md_block)

    channels = [SourceChannel.TEXT] if pages else []
    return ExtractedDocument(
        document_sha256=ctx.sha256,
        filename=ctx.filename,
        extension=ctx.extension,
        mime_type=ctx.mime_type,
        document_format=ctx.document_format,
        page_count=len(pages),
        full_text="\n\n".join(plain_sections),
        markdown="\n\n".join(md_sections),
        pages=pages,
        tables=all_tables,
        source_channels=channels,
        warnings=warnings,
        metadata={
            "extractor": "odfdo",
            "sheet_count": str(len(pages)),
            "sheet_names": ", ".join(sheet_names),
        },
    )
