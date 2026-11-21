"""Handler ekstrakcji dokumentów OOXML (.docx, .xlsx) — szkielet pod Docling."""

import io
import zipfile

from backend.shared.schemas.pipeline import DocumentFormat, ExtractedDocument
from backend.worker.pipeline.extractors.base import (
    ExtractionContext,
    build_skeleton_document,
    temporary_document_file,
)
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


def extract_ooxml(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje plik .docx/.xlsx i udostępnia go w izolowanym katalogu tymczasowym."""
    _validate_ooxml_bytes(ctx.content, ctx.document_format)

    with temporary_document_file(
        ctx.content,
        extension=ctx.extension,
        temp_dir=ctx.settings.temp_dir,
    ) as ooxml_path:
        # Szkielet: właściwa ekstrakcja (Docling) skorzysta z ``ooxml_path``.
        _ = ooxml_path
        return build_skeleton_document(
            ctx,
            warnings=[f"Ekstraktor {ctx.document_format.value} działa w trybie szkieletowym."],
        )
