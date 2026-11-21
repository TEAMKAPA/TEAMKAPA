"""Handler ekstrakcji starszych dokumentów Word (.doc / OLE2) — przetwarzanie w pamięci."""

import io

from backend.shared.schemas.pipeline import ExtractedDocument
from backend.worker.pipeline.extractors.base import ExtractionContext, build_skeleton_document
from backend.worker.pipeline.extractors.exceptions import CorruptedFileError, EncryptedFileError

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_WORD_DOCUMENT_STREAM_UTF16 = "WordDocument".encode("utf-16-le")
_ENCRYPTED_PACKAGE_UTF16 = "EncryptedPackage".encode("utf-16-le")
_ENCRYPTION_INFO_UTF16 = "EncryptionInfo".encode("utf-16-le")

# W formacie MS-DOC (FIB — File Information Block, offset 0x0A, 16-bit flags) bit 0x0100 oznacza
# fEncrypted. Weryfikacja sygnatury FIB (wIdent == 0xA5EC) i flagi szyfrowania bez rozpakowywania
# całego pliku daje szybkie wykrycie dokumentów chronionych hasłem.
_FIB_IDENT = b"\xec\xa5"
_FIB_FLAG_ENCRYPTED = 0x0100


def _has_encrypted_fib(content: bytes) -> bool:
    idx = content.find(_FIB_IDENT)
    while idx != -1:
        if idx + 12 <= len(content):
            flags = int.from_bytes(content[idx + 10 : idx + 12], "little")
            if flags & _FIB_FLAG_ENCRYPTED:
                return True
        idx = content.find(_FIB_IDENT, idx + 2)
    return False


def _validate_doc_bytes(content: bytes) -> None:
    if not content.startswith(_OLE_MAGIC):
        raise CorruptedFileError("Plik DOC nie ma prawidłowego nagłówka kontenera OLE2.")
    if _ENCRYPTED_PACKAGE_UTF16 in content or _ENCRYPTION_INFO_UTF16 in content:
        raise EncryptedFileError("Plik DOC jest zaszyfrowany lub chroniony hasłem.")
    if _WORD_DOCUMENT_STREAM_UTF16 not in content:
        raise CorruptedFileError("Plik DOC nie zawiera strumienia WordDocument.")
    if _has_encrypted_fib(content):
        raise EncryptedFileError("Plik DOC jest chroniony hasłem (flaga FIB fEncrypted).")


def extract_doc(ctx: ExtractionContext) -> ExtractedDocument:
    """Waliduje plik .doc (OLE2) i przygotowuje strumień w pamięci pod ekstrakcję (olefile)."""
    _validate_doc_bytes(ctx.content)

    with io.BytesIO(ctx.content) as doc_stream:
        # Szkielet: właściwa ekstrakcja tekstu ze strumieni OLE2 (olefile)
        # będzie operować bezpośrednio na ``doc_stream``.
        _ = doc_stream
        return build_skeleton_document(
            ctx,
            warnings=["Ekstraktor DOC działa w trybie szkieletowym."],
        )
