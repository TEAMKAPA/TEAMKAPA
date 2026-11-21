"""Ekstraktor dokumentów PDF: pypdfium2 (AcroForm + render PNG) + Docling + OCR skanów."""

import ctypes
import io
from dataclasses import dataclass
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from backend.shared.schemas.pipeline import (
    AcroFormField,
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.extractors.base import ExtractionContext, temporary_document_file
from backend.worker.pipeline.extractors.docling_adapter import (
    DoclingPageContent,
    convert_with_docling,
)
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError
from backend.worker.pipeline.extractors.ocr import run_tesseract_ocr

_PDF_HEADER = b"%PDF-"
_PDF_ENCRYPT_MARKER = b"/Encrypt"
_PDF_POINTS_PER_INCH = 72.0
_MIN_NATIVE_TEXT_CHARS = 10

_FORM_FIELD_TYPES: dict[int, str] = {
    pdfium_c.FPDF_FORMFIELD_PUSHBUTTON: "PUSHBUTTON",
    pdfium_c.FPDF_FORMFIELD_CHECKBOX: "CHECKBOX",
    pdfium_c.FPDF_FORMFIELD_RADIOBUTTON: "RADIOBUTTON",
    pdfium_c.FPDF_FORMFIELD_COMBOBOX: "COMBOBOX",
    pdfium_c.FPDF_FORMFIELD_LISTBOX: "LISTBOX",
    pdfium_c.FPDF_FORMFIELD_TEXTFIELD: "TEXTFIELD",
    pdfium_c.FPDF_FORMFIELD_SIGNATURE: "SIGNATURE",
}


@dataclass(frozen=True, slots=True)
class _RenderedPdfPage:
    page_number: int
    width_pt: float
    height_pt: float
    native_text: str
    acroform_fields: list[AcroFormField]
    image_png: bytes
    image_width: int
    image_height: int
    image_dpi: int


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _read_utf16_field(fn: Any, *args: Any) -> str:
    length = int(fn(*args, None, 0))
    if length <= 2:
        return ""
    buf = (ctypes.c_ushort * (length // 2))()
    fn(*args, buf, length)
    return bytes(buf).decode("utf-16-le", errors="replace").rstrip("\x00")


def _normalize_pdf_rect(
    rect: Any,
    *,
    page_bbox: tuple[float, float, float, float],
    rotation: int,
) -> BoundingBox | None:
    bl_x, bl_y, tr_x, tr_y = page_bbox
    page_w = tr_x - bl_x
    page_h = tr_y - bl_y
    if page_w <= 0.0 or page_h <= 0.0:
        return None

    x0 = _clamp01((min(float(rect.left), float(rect.right)) - bl_x) / page_w)
    x1 = _clamp01((max(float(rect.left), float(rect.right)) - bl_x) / page_w)
    y0 = _clamp01((tr_y - max(float(rect.top), float(rect.bottom))) / page_h)
    y1 = _clamp01((tr_y - min(float(rect.top), float(rect.bottom))) / page_h)

    rot = rotation % 360
    if rot == 90:
        x0, y0, x1, y1 = 1.0 - y1, x0, 1.0 - y0, x1
    elif rot == 180:
        x0, y0, x1, y1 = 1.0 - x1, 1.0 - y1, 1.0 - x0, 1.0 - y0
    elif rot == 270:
        x0, y0, x1, y1 = y0, 1.0 - x1, y1, 1.0 - x0

    x0, x1 = _clamp01(min(x0, x1)), _clamp01(max(x0, x1))
    y0, y1 = _clamp01(min(y0, y1)), _clamp01(max(y0, y1))
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _extract_page_acroform_fields(
    page: Any,
    form_handle: Any,
    *,
    page_number: int,
    page_bbox: tuple[float, float, float, float],
    rotation: int,
) -> list[AcroFormField]:
    if form_handle is None:
        return []

    fields: list[AcroFormField] = []
    annot_count = int(pdfium_c.FPDFPage_GetAnnotCount(page.raw))
    for annot_idx in range(max(0, annot_count)):
        annot = pdfium_c.FPDFPage_GetAnnot(page.raw, annot_idx)
        if not annot:
            continue
        try:
            if int(pdfium_c.FPDFAnnot_GetSubtype(annot)) != int(pdfium_c.FPDF_ANNOT_WIDGET):
                continue

            raw_type = int(pdfium_c.FPDFAnnot_GetFormFieldType(form_handle, annot))
            field_type = _FORM_FIELD_TYPES.get(raw_type, "UNKNOWN")
            name = _read_utf16_field(pdfium_c.FPDFAnnot_GetFormFieldName, form_handle, annot)
            label = (
                _read_utf16_field(pdfium_c.FPDFAnnot_GetFormFieldAlternateName, form_handle, annot)
                or None
            )
            value = (
                _read_utf16_field(pdfium_c.FPDFAnnot_GetFormFieldValue, form_handle, annot) or None
            )
            if (
                value is None
                and field_type in {"CHECKBOX", "RADIOBUTTON"}
                and bool(pdfium_c.FPDFAnnot_IsChecked(form_handle, annot))
            ):
                export_val = _read_utf16_field(
                    pdfium_c.FPDFAnnot_GetFormFieldExportValue, form_handle, annot
                )
                value = export_val or "true"

            flags = int(pdfium_c.FPDFAnnot_GetFormFieldFlags(form_handle, annot))
            read_only = bool(flags & int(pdfium_c.FPDF_FORMFLAG_READONLY))
            required = bool(flags & int(pdfium_c.FPDF_FORMFLAG_REQUIRED))

            options: list[str] = []
            if field_type in {"COMBOBOX", "LISTBOX"}:
                opt_count = int(pdfium_c.FPDFAnnot_GetOptionCount(form_handle, annot))
                for opt_idx in range(max(0, opt_count)):
                    opt_label = _read_utf16_field(
                        pdfium_c.FPDFAnnot_GetOptionLabel, form_handle, annot, opt_idx
                    )
                    if opt_label:
                        options.append(opt_label)

            rect = pdfium_c.FS_RECTF()
            bbox: BoundingBox | None = None
            if pdfium_c.FPDFAnnot_GetRect(annot, ctypes.byref(rect)):
                bbox = _normalize_pdf_rect(rect, page_bbox=page_bbox, rotation=rotation)

            fields.append(
                AcroFormField(
                    name=name or f"field_{page_number}_{annot_idx}",
                    field_type=field_type,
                    page=page_number,
                    label=label,
                    value=value,
                    options=options,
                    required=required,
                    read_only=read_only,
                    bbox=bbox,
                )
            )
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)

    return fields


def _open_pdf_document(content: bytes) -> Any:
    if not content.lstrip().startswith(_PDF_HEADER):
        raise CorruptedFileError("Plik PDF nie posiada prawidłowego nagłówka %PDF-.")
    try:
        return pdfium.PdfDocument(content)
    except pdfium.PdfiumError as exc:
        err_code = int(pdfium_c.FPDF_GetLastError())
        msg_lower = str(exc).lower()
        if (
            err_code in {int(pdfium_c.FPDF_ERR_PASSWORD), int(pdfium_c.FPDF_ERR_SECURITY)}
            or "password" in msg_lower
            or "security" in msg_lower
            or _PDF_ENCRYPT_MARKER in content
        ):
            raise EncryptedFileError("Plik PDF jest zaszyfrowany lub chroniony hasłem.") from exc
        raise CorruptedFileError(f"Nie udało się otworzyć pliku PDF: {exc}") from exc


def _inspect_and_render_pdf(content: bytes, *, dpi: int) -> list[_RenderedPdfPage]:
    doc = _open_pdf_document(content)
    try:
        page_count = len(doc)
        if page_count == 0:
            raise CorruptedFileError("Plik PDF nie zawiera żadnych stron.")

        form_handle: Any = None
        try:
            doc.init_forms()
            if doc.formenv is not None:
                form_handle = doc.formenv.raw
        except pdfium.PdfiumError:
            form_handle = None

        scale = dpi / _PDF_POINTS_PER_INCH
        rendered_pages: list[_RenderedPdfPage] = []

        for page_idx in range(page_count):
            page_number = page_idx + 1
            page = doc[page_idx]
            try:
                width_pt, height_pt = page.get_size()
                page_bbox = tuple(float(v) for v in page.get_bbox())
                rotation = int(page.get_rotation())

                textpage = page.get_textpage()
                try:
                    native_text = str(textpage.get_text_bounded() or "").strip()
                finally:
                    textpage.close()

                acroform_fields = _extract_page_acroform_fields(
                    page,
                    form_handle,
                    page_number=page_number,
                    page_bbox=(page_bbox[0], page_bbox[1], page_bbox[2], page_bbox[3]),
                    rotation=rotation,
                )

                bitmap = page.render(scale=scale)
                try:
                    pil_image = bitmap.to_pil()
                    if pil_image.mode != "RGB":
                        pil_image = pil_image.convert("RGB")
                    img_w, img_h = pil_image.size
                    png_buf = io.BytesIO()
                    pil_image.save(png_buf, format="PNG", dpi=(dpi, dpi))
                    png_bytes = png_buf.getvalue()
                finally:
                    bitmap.close()

                rendered_pages.append(
                    _RenderedPdfPage(
                        page_number=page_number,
                        width_pt=float(width_pt),
                        height_pt=float(height_pt),
                        native_text=native_text,
                        acroform_fields=acroform_fields,
                        image_png=png_bytes,
                        image_width=img_w,
                        image_height=img_h,
                        image_dpi=dpi,
                    )
                )
            finally:
                page.close()

        return rendered_pages
    finally:
        doc.close()


def _has_text_layer(native_text: str, docling_blocks: list[ExtractedTextBlock]) -> bool:
    native_chars = len("".join(native_text.split()))
    if native_chars >= _MIN_NATIVE_TEXT_CHARS:
        return True
    docling_chars = sum(len("".join(b.text.split())) for b in docling_blocks)
    return docling_chars >= _MIN_NATIVE_TEXT_CHARS


def _dedupe_channels(channels: list[SourceChannel]) -> list[SourceChannel]:
    seen: set[SourceChannel] = set()
    ordered: list[SourceChannel] = []
    for ch in channels:
        if ch not in seen:
            seen.add(ch)
            ordered.append(ch)
    return ordered


def extract_pdf(ctx: ExtractionContext) -> ExtractedDocument:
    """Pełna ekstrakcja PDF: pypdfium2 (AcroForm + PNG) + Docling + OCR dla stron skanowanych."""
    dpi = ctx.settings.render_dpi
    rendered_pages = _inspect_and_render_pdf(ctx.content, dpi=dpi)
    fallback_sizes = {p.page_number: (p.width_pt, p.height_pt) for p in rendered_pages}

    with temporary_document_file(
        ctx.content,
        extension=ctx.extension,
        temp_dir=ctx.settings.temp_dir,
    ) as pdf_path:
        docling_result = convert_with_docling(
            pdf_path,
            settings=ctx.settings,
            fallback_page_sizes=fallback_sizes,
        )

    pages: list[ExtractedPage] = []
    all_tables: list[ExtractedTable] = []
    all_acroform: list[AcroFormField] = []
    all_channels: list[SourceChannel] = []
    page_texts: list[str] = []
    warnings = list(docling_result.warnings)

    for rendered in rendered_pages:
        docling_page = docling_result.pages.get(rendered.page_number, DoclingPageContent())
        page_channels: list[SourceChannel] = []
        blocks = list(docling_page.blocks)
        tables = list(docling_page.tables)
        ocr_words = []
        ocr_applied = False
        ocr_confidence: float | None = None

        has_text = _has_text_layer(rendered.native_text, blocks)
        needs_ocr = not has_text

        if needs_ocr:
            ocr_result = run_tesseract_ocr(
                rendered.image_png,
                page_number=rendered.page_number,
                lang="pol",
            )
            ocr_applied = True
            ocr_words = ocr_result.words
            ocr_confidence = ocr_result.mean_confidence
            blocks = ocr_result.blocks
            page_text = ocr_result.text
            page_channels.append(SourceChannel.OCR)
        else:
            if blocks:
                page_text = "\n".join(b.text for b in blocks)
            else:
                page_text = rendered.native_text
                if page_text:
                    blocks = [
                        ExtractedTextBlock(
                            text=page_text,
                            page=rendered.page_number,
                            channel=SourceChannel.TEXT,
                        )
                    ]
            page_channels.append(SourceChannel.TEXT)

        if rendered.acroform_fields:
            page_channels.append(SourceChannel.ACROFORM)

        deduped_page_channels = _dedupe_channels(page_channels)
        pages.append(
            ExtractedPage(
                page_number=rendered.page_number,
                width=rendered.width_pt,
                height=rendered.height_pt,
                text=page_text,
                blocks=blocks,
                tables=tables,
                acroform_fields=rendered.acroform_fields,
                needs_ocr=needs_ocr,
                ocr_applied=ocr_applied,
                ocr_confidence=ocr_confidence,
                ocr_words=ocr_words,
                source_channels=deduped_page_channels,
                image_png=rendered.image_png,
                image_width=rendered.image_width,
                image_height=rendered.image_height,
                image_dpi=rendered.image_dpi,
            )
        )
        all_tables.extend(tables)
        all_acroform.extend(rendered.acroform_fields)
        all_channels.extend(deduped_page_channels)
        if page_text:
            page_texts.append(page_text)

    full_text = "\n\n".join(page_texts)
    markdown = docling_result.markdown or full_text

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
        acroform_fields=all_acroform,
        source_channels=_dedupe_channels(all_channels),
        warnings=warnings,
        metadata={"render_dpi": str(dpi)},
    )
