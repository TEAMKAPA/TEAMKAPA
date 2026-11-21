"""Ekstraktor dokumentów OOXML (.docx, .xlsx) oparty na bibliotece Docling."""

import io
import zipfile

from backend.shared.schemas.pipeline import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import SourceChannel
from backend.worker.pipeline.extractors.base import ExtractionContext, temporary_document_file
from backend.worker.pipeline.extractors.docling_adapter import convert_with_docling
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ENCRYPTED_PACKAGE_UTF16 = "EncryptedPackage".encode("utf-16-le")
_ENCRYPTION_INFO_UTF16 = "EncryptionInfo".encode("utf-16-le")

_REQUIRED_PARTS: dict[DocumentFormat, str] = {
    DocumentFormat.DOCX: "word/document.xml",
    DocumentFormat.XLSX: "xl/workbook.xml",
}


def _validate_ooxml_bytes(content: bytes, fmt: DocumentFormat) -> None:
    if content.startswith(_OLE_MAGIC) and (
        _ENCRYPTED_PACKAGE_UTF16 in content or _ENCRYPTION_INFO_UTF16 in content
    ):
        raise EncryptedFileError(
            f"Plik {fmt.value} jest zaszyfrowany hasłem (kontener OLE EncryptedPackage)."
        )

    required_part = _REQUIRED_PARTS[fmt]
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise CorruptedFileError(
            f"Plik {fmt.value} nie jest prawidłowym archiwum ZIP/OOXML."
        ) from exc

    if "[Content_Types].xml" not in names or required_part not in names:
        raise CorruptedFileError(
            f"Plik {fmt.value} ma uszkodzoną strukturę OOXML (brak {required_part})."
        )


def _format_table_plain(table: ExtractedTable) -> str:
    lines: list[str] = []
    if table.sheet_name:
        lines.append(table.sheet_name)
    for row in table.rows:
        if any(cell.strip() for cell in row):
            lines.append(" | ".join(cell.strip() for cell in row))
    return "\n".join(lines)


def _format_table_markdown(table: ExtractedTable) -> str:
    if not table.rows:
        return ""
    col_count = max((len(row) for row in table.rows), default=0)
    if col_count == 0:
        return ""

    def _escape(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", " ").strip()

    normalized = [
        [_escape(row[i]) if i < len(row) else "" for i in range(col_count)] for row in table.rows
    ]
    lines: list[str] = []
    if table.sheet_name:
        lines.append(f"## {table.sheet_name}")
        lines.append("")
    lines.append("| " + " | ".join(normalized[0]) + " |")
    lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _build_page_text(blocks: list[ExtractedTextBlock], tables: list[ExtractedTable]) -> str:
    parts = [block.text for block in blocks if block.text.strip()]
    for table in tables:
        table_text = _format_table_plain(table)
        if table_text:
            parts.append(table_text)
    return "\n\n".join(parts)


def extract_ooxml(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje plik .docx/.xlsx, uruchamia Docling i mapuje tekst, nagłówki oraz tabele."""
    _validate_ooxml_bytes(ctx.content, ctx.document_format)

    with temporary_document_file(
        ctx.content,
        extension=ctx.extension,
        temp_dir=ctx.settings.temp_dir,
    ) as ooxml_path:
        docling_result = convert_with_docling(ooxml_path, settings=ctx.settings)

    pages: list[ExtractedPage] = []
    all_tables: list[ExtractedTable] = []
    page_texts: list[str] = []
    md_fallback_parts: list[str] = []
    headings_count = 0

    for page_number in sorted(docling_result.pages):
        docling_page = docling_result.pages[page_number]
        blocks = list(docling_page.blocks)
        tables = list(docling_page.tables)
        headings_count += sum(1 for b in blocks if b.heading_level is not None)

        page_text = _build_page_text(blocks, tables)
        channels = [SourceChannel.TEXT] if (blocks or tables or page_text) else []

        pages.append(
            ExtractedPage(
                page_number=page_number,
                width=docling_page.width,
                height=docling_page.height,
                text=page_text,
                blocks=blocks,
                tables=tables,
                source_channels=channels,
            )
        )
        all_tables.extend(tables)
        if page_text:
            page_texts.append(page_text)

        for block in blocks:
            if block.heading_level is not None:
                md_fallback_parts.append(f"{'#' * block.heading_level} {block.text}")
            else:
                md_fallback_parts.append(block.text)
        for table in tables:
            md_table = _format_table_markdown(table)
            if md_table:
                md_fallback_parts.append(md_table)

    full_text = "\n\n".join(page_texts)
    markdown = docling_result.markdown or "\n\n".join(md_fallback_parts)
    doc_channels = [SourceChannel.TEXT] if (pages and any(p.source_channels for p in pages)) else []

    return ExtractedDocument(
        document_sha256=ctx.sha256,
        filename=ctx.filename,
        extension=ctx.extension,
        mime_type=ctx.mime_type,
        document_format=ctx.document_format,
        page_count=len(pages),
        full_text=full_text,
        markdown=markdown,
        pages=pages,
        tables=all_tables,
        source_channels=doc_channels,
        warnings=list(docling_result.warnings),
        metadata={
            "extractor": "docling",
            "headings_count": str(headings_count),
            "tables_count": str(len(all_tables)),
        },
    )
