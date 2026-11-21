"""Handler ekstrakcji obrazów (.jpg, .jpeg, .png) — przetwarzanie w pamięci."""

import io

from backend.shared.schemas.pipeline import ExtractedDocument
from backend.worker.pipeline.extractors.base import ExtractionContext, build_skeleton_document
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _validate_image_bytes(content: bytes, extension: str) -> None:
    if extension in {"jpg", "jpeg"}:
        if not content.startswith(_JPEG_MAGIC):
            raise CorruptedFileError("Plik JPEG ma uszkodzony lub nieprawidłowy nagłówek.")
        return
    if extension == "png":
        if not content.startswith(_PNG_MAGIC):
            raise CorruptedFileError("Plik PNG ma uszkodzony lub nieprawidłowy nagłówek.")
        return
    raise CorruptedFileError(f"Nieprawidłowe rozszerzenie obrazu: .{extension}")


def extract_image(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje obraz w pamięci (``BytesIO``) pod dalszy preprocessing OpenCV i OCR."""
    _validate_image_bytes(ctx.content, ctx.extension)

    with io.BytesIO(ctx.content) as image_stream:
        # Szkielet: właściwy preprocessing (OpenCV / Pillow) i OCR (Tesseract)
        # będą operować bezpośrednio na strumieniu w pamięci.
        _ = image_stream
        return build_skeleton_document(
            ctx,
            warnings=["Ekstraktor obrazów działa w trybie szkieletowym."],
        )
