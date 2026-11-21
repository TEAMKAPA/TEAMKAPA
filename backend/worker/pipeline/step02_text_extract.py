"""Krok 02 pipeline'u: router ekstrakcji dokumentów z pamięci do handlerów formatów.

Przyjmuje wyłącznie zawartość z pamięci (``bytes`` lub ``BytesIO``) oraz nazwę pliku
i/lub typ MIME. Pliki robocze — jeżeli dany ekstraktor wymaga ścieżki na dysku —
powstają wyłącznie wewnątrz ``tempfile.TemporaryDirectory()`` (w ``settings.temp_dir``)
i są gwarantowanie usuwane po zakończeniu lub błędzie.
"""

import hashlib
import io
from collections.abc import Mapping

from backend.shared.config import Settings, get_settings
from backend.shared.schemas.pipeline import DocumentFormat, ExtractedDocument
from backend.worker.pipeline.extractors import (
    CorruptedFileError,
    EncryptedFileError,
    ExtractionContext,
    ExtractionError,
    ExtractorHandler,
    FileTooLargeError,
    UnsupportedFileFormatError,
    extract_doc,
    extract_image,
    extract_ods,
    extract_ooxml,
    extract_pdf,
)

_MIME_PDF = "application/pdf"
_MIME_JPEG = "image/jpeg"
_MIME_PNG = "image/png"
_MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_ODS = "application/vnd.oasis.opendocument.spreadsheet"
_MIME_DOC = "application/msword"

_EXTENSION_TO_SPEC: dict[str, tuple[DocumentFormat, str]] = {
    "pdf": (DocumentFormat.PDF, _MIME_PDF),
    "jpg": (DocumentFormat.IMAGE, _MIME_JPEG),
    "jpeg": (DocumentFormat.IMAGE, _MIME_JPEG),
    "png": (DocumentFormat.IMAGE, _MIME_PNG),
    "docx": (DocumentFormat.DOCX, _MIME_DOCX),
    "xlsx": (DocumentFormat.XLSX, _MIME_XLSX),
    "ods": (DocumentFormat.ODS, _MIME_ODS),
    "doc": (DocumentFormat.DOC, _MIME_DOC),
}

_MIME_TO_SPEC: dict[str, tuple[DocumentFormat, str, str]] = {
    _MIME_PDF: (DocumentFormat.PDF, "pdf", _MIME_PDF),
    "application/x-pdf": (DocumentFormat.PDF, "pdf", _MIME_PDF),
    _MIME_JPEG: (DocumentFormat.IMAGE, "jpg", _MIME_JPEG),
    "image/jpg": (DocumentFormat.IMAGE, "jpg", _MIME_JPEG),
    _MIME_PNG: (DocumentFormat.IMAGE, "png", _MIME_PNG),
    _MIME_DOCX: (DocumentFormat.DOCX, "docx", _MIME_DOCX),
    _MIME_XLSX: (DocumentFormat.XLSX, "xlsx", _MIME_XLSX),
    _MIME_ODS: (DocumentFormat.ODS, "ods", _MIME_ODS),
    _MIME_DOC: (DocumentFormat.DOC, "doc", _MIME_DOC),
}

# Ogólne typy kontenerów (np. wykrywane przez libmagic), dopuszczalne gdy rozszerzenie
# jednoznacznie wskazuje konkretny format kontenerowy.
_GENERIC_CONTAINER_MIMES: dict[DocumentFormat, frozenset[str]] = {
    DocumentFormat.DOCX: frozenset(
        {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
    ),
    DocumentFormat.XLSX: frozenset(
        {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
    ),
    DocumentFormat.ODS: frozenset(
        {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
    ),
    DocumentFormat.DOC: frozenset(
        {
            "application/x-ole-storage",
            "application/cdfv2",
            "application/vnd.ms-office",
            "application/octet-stream",
        }
    ),
}

DEFAULT_HANDLERS: dict[DocumentFormat, ExtractorHandler] = {
    DocumentFormat.PDF: extract_pdf,
    DocumentFormat.IMAGE: extract_image,
    DocumentFormat.DOCX: extract_ooxml,
    DocumentFormat.XLSX: extract_ooxml,
    DocumentFormat.ODS: extract_ods,
    DocumentFormat.DOC: extract_doc,
}


def _normalize_mime(mime_type: str | None) -> str | None:
    if mime_type is None:
        return None
    cleaned = mime_type.split(";", maxsplit=1)[0].strip().lower()
    return cleaned or None


def _extract_extension(filename: str | None) -> str | None:
    if filename is None or not filename.strip():
        return None
    name = filename.strip().rsplit("/", maxsplit=1)[-1].rsplit("\\", maxsplit=1)[-1]
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem.strip():
        return None
    return ext.lower()


def _resolve_format(
    filename: str | None,
    mime_type: str | None,
) -> tuple[str, str, str, DocumentFormat]:
    """Rozpoznaje (nazwa, rozszerzenie, kanoniczny MIME, format) albo rzuca wyjątek domenowy."""
    ext = _extract_extension(filename)
    norm_mime = _normalize_mime(mime_type)

    if ext is None and norm_mime is None:
        raise UnsupportedFileFormatError(
            "Nie podano ani rozszerzenia pliku, ani typu MIME — nie można określić formatu."
        )

    ext_spec = _EXTENSION_TO_SPEC.get(ext) if ext is not None else None
    mime_spec = _MIME_TO_SPEC.get(norm_mime) if norm_mime is not None else None

    if ext is not None and ext_spec is None:
        allowed = ", ".join(sorted(_EXTENSION_TO_SPEC))
        raise UnsupportedFileFormatError(
            f"Nieobsługiwane rozszerzenie '.{ext}'. Obsługiwane formaty: {allowed}.",
            details={"extension": ext},
        )

    if ext_spec is not None:
        doc_format, canonical_mime = ext_spec
        if norm_mime is not None:
            generic_allowed = _GENERIC_CONTAINER_MIMES.get(doc_format, frozenset())
            mime_matches = (
                mime_spec is not None
                and mime_spec[0] is doc_format
                and mime_spec[2] == canonical_mime
            ) or norm_mime in generic_allowed
            if not mime_matches:
                raise UnsupportedFileFormatError(
                    f"Typ MIME '{norm_mime}' jest niespójny z rozszerzeniem '.{ext}'.",
                    details={"extension": ext or "", "mime_type": norm_mime},
                )
        resolved_name = filename.strip() if filename is not None else f"document.{ext}"
        assert ext is not None  # noqa: S101
        return resolved_name, ext, canonical_mime, doc_format

    if mime_spec is None:
        raise UnsupportedFileFormatError(
            f"Nieobsługiwany typ MIME: '{norm_mime}'.",
            details={"mime_type": norm_mime or ""},
        )

    doc_format, default_ext, canonical_mime = mime_spec
    resolved_name = filename.strip() if filename and filename.strip() else f"document.{default_ext}"
    return resolved_name, default_ext, canonical_mime, doc_format


def _read_payload(data: bytes | io.BytesIO) -> bytes:
    if isinstance(data, io.BytesIO):
        return data.getvalue()
    if isinstance(data, bytes):
        return data
    raise TypeError(f"Oczekiwano bytes lub BytesIO, otrzymano: {type(data).__name__}")


def extract_document(
    data: bytes | io.BytesIO,
    *,
    filename: str | None = None,
    mime_type: str | None = None,
    settings: Settings | None = None,
    handlers: Mapping[DocumentFormat, ExtractorHandler] | None = None,
) -> ExtractedDocument:
    """Główny punkt wejścia kroku 02 — kieruje plik z pamięci do właściwego ekstraktora."""
    active_settings = settings or get_settings()
    content = _read_payload(data)

    if not content:
        raise CorruptedFileError("Przekazany plik jest pusty (0 bajtów).")
    if len(content) > active_settings.max_file_size_bytes:
        raise FileTooLargeError(
            f"Plik przekracza limit {active_settings.max_file_size_mb} MB.",
            details={"size_bytes": str(len(content))},
        )

    resolved_name, extension, canonical_mime, doc_format = _resolve_format(filename, mime_type)

    handler_map = handlers if handlers is not None else DEFAULT_HANDLERS
    handler = handler_map.get(doc_format)
    if handler is None:
        raise UnsupportedFileFormatError(
            f"Brak zarejestrowanego handlera dla formatu {doc_format.value}.",
            details={"format": doc_format.value},
        )

    ctx = ExtractionContext(
        content=content,
        filename=resolved_name,
        extension=extension,
        mime_type=canonical_mime,
        document_format=doc_format,
        sha256=hashlib.sha256(content).hexdigest(),
        settings=active_settings,
    )
    return handler(ctx)


__all__ = [
    "DEFAULT_HANDLERS",
    "CorruptedFileError",
    "EncryptedFileError",
    "ExtractionContext",
    "ExtractionError",
    "ExtractorHandler",
    "FileTooLargeError",
    "UnsupportedFileFormatError",
    "extract_document",
]
