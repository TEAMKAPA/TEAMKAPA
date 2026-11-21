"""Handler ekstrakcji dokumentów PDF (szkielet pod Docling / pypdfium2 / OCR)."""

from backend.shared.schemas.pipeline import ExtractedDocument
from backend.worker.pipeline.extractors.base import (
    ExtractionContext,
    build_skeleton_document,
    temporary_document_file,
)
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_PDF_HEADER = b"%PDF-"
_PDF_ENCRYPT_MARKER = b"/Encrypt"


def _validate_pdf_bytes(content: bytes) -> None:
    if not content.lstrip().startswith(_PDF_HEADER):
        raise CorruptedFileError("Plik PDF nie posiada prawidłowego nagłówka %PDF-.")
    if _PDF_ENCRYPT_MARKER in content:
        raise EncryptedFileError("Plik PDF jest zaszyfrowany lub chroniony hasłem.")


def extract_pdf(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje plik PDF i przygotowuje bezpieczny plik roboczy w katalogu tymczasowym."""
    _validate_pdf_bytes(ctx.content)

    with temporary_document_file(
        ctx.content,
        extension=ctx.extension,
        temp_dir=ctx.settings.temp_dir,
    ) as pdf_path:
        # Szkielet: właściwa ekstrakcja (Docling, AcroForm, pypdfium2 + Tesseract OCR)
        # zostanie dodana w kolejnych krokach i skorzysta z ``pdf_path``.
        _ = pdf_path
        return build_skeleton_document(
            ctx,
            warnings=["Ekstraktor PDF działa w trybie szkieletowym."],
            metadata={"render_dpi": str(ctx.settings.render_dpi)},
        )
