"""Ekstraktor obrazów (.jpg, .jpeg, .png): Pillow (EXIF) + OpenCV/Tesseract OCR + Docling."""

import io
from collections.abc import Mapping
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from backend.shared.schemas.pipeline import (
    ExtractedDocument,
    ExtractedPage,
    ExtractedTextBlock,
)
from backend.shared.schemas.quality import SourceChannel
from backend.worker.pipeline.extractors.base import ExtractionContext, temporary_document_file
from backend.worker.pipeline.extractors.docling_adapter import (
    DoclingPageContent,
    convert_with_docling,
)
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError
from backend.worker.pipeline.extractors.ocr import run_tesseract_ocr

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _validate_image_magic(content: bytes, extension: str) -> None:
    if extension in {"jpg", "jpeg"}:
        if not content.startswith(_JPEG_MAGIC):
            raise CorruptedFileError("Plik JPEG ma uszkodzony lub nieprawidłowy nagłówek.")
        return
    if extension == "png":
        if not content.startswith(_PNG_MAGIC):
            raise CorruptedFileError("Plik PNG ma uszkodzony lub nieprawidłowy nagłówek.")
        return
    raise CorruptedFileError(f"Nieprawidłowe rozszerzenie obrazu: .{extension}")


def _extract_dpi(info: Mapping[Any, Any]) -> int | None:
    raw_dpi = info.get("dpi")
    if isinstance(raw_dpi, tuple) and raw_dpi:
        first = raw_dpi[0]
        if isinstance(first, (int, float)) and first > 0:
            return round(first)
    elif isinstance(raw_dpi, (int, float)) and raw_dpi > 0:
        return round(raw_dpi)
    return None


def _normalize_image_to_png(content: bytes) -> tuple[bytes, int, int, int | None]:
    """Normalizuje orientację EXIF i tryb koloru przez Pillow, zwracając PNG RGB."""
    try:
        with Image.open(io.BytesIO(content)) as raw_img:
            raw_img.load()
            dpi = _extract_dpi(raw_img.info)
            oriented = ImageOps.exif_transpose(raw_img)
            if oriented is None:
                oriented = raw_img

            if oriented.mode in {"RGBA", "LA"} or (
                oriented.mode == "P" and "transparency" in oriented.info
            ):
                rgba = oriented.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgb_image = background
            elif oriented.mode != "RGB":
                rgb_image = oriented.convert("RGB")
            else:
                rgb_image = oriented

            width, height = rgb_image.size
            if width <= 0 or height <= 0:
                raise CorruptedFileError("Obraz ma zerowe wymiary.")

            out = io.BytesIO()
            if dpi is not None:
                rgb_image.save(out, format="PNG", dpi=(dpi, dpi))
            else:
                rgb_image.save(out, format="PNG")
            return out.getvalue(), width, height, dpi
    except CorruptedFileError:
        raise
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise CorruptedFileError(f"Nie udało się wczytać obrazu: {exc}") from exc


def extract_image(ctx: ExtractionContext) -> ExtractedDocument:
    """Pełna ekstrakcja obrazu: Pillow (EXIF + PNG) -> OpenCV/Tesseract OCR + Docling."""
    _validate_image_magic(ctx.content, ctx.extension)
    png_bytes, width, height, dpi = _normalize_image_to_png(ctx.content)

    ocr_result = run_tesseract_ocr(png_bytes, page_number=1, lang="pol")

    with temporary_document_file(
        png_bytes,
        extension="png",
        temp_dir=ctx.settings.temp_dir,
    ) as png_path:
        docling_result = convert_with_docling(
            png_path,
            settings=ctx.settings,
            fallback_page_sizes={1: (float(width), float(height))},
        )

    docling_page = docling_result.pages.get(1, DoclingPageContent())
    blocks: list[ExtractedTextBlock] = list(ocr_result.blocks)
    channels: list[SourceChannel] = [SourceChannel.OCR]

    if docling_page.blocks:
        blocks.extend(docling_page.blocks)
        channels.append(SourceChannel.TEXT)
    elif docling_page.tables:
        channels.append(SourceChannel.TEXT)

    page_text = ocr_result.text or "\n".join(b.text for b in docling_page.blocks)
    markdown = docling_result.markdown or page_text

    page = ExtractedPage(
        page_number=1,
        width=float(width),
        height=float(height),
        text=page_text,
        blocks=blocks,
        tables=docling_page.tables,
        needs_ocr=True,
        ocr_applied=True,
        ocr_confidence=ocr_result.mean_confidence,
        ocr_words=ocr_result.words,
        source_channels=channels,
        image_png=png_bytes,
        image_width=width,
        image_height=height,
        image_dpi=dpi,
    )

    metadata = {"normalized_format": "PNG"}
    if dpi is not None:
        metadata["image_dpi"] = str(dpi)

    return ExtractedDocument(
        document_sha256=ctx.sha256,
        filename=ctx.filename,
        extension=ctx.extension,
        mime_type=ctx.mime_type,
        document_format=ctx.document_format,
        page_count=1,
        full_text=page_text,
        markdown=markdown,
        pages=[page],
        tables=docling_page.tables,
        source_channels=channels,
        warnings=list(docling_result.warnings),
        metadata=metadata,
    )
