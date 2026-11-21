"""Walidacja przesyłanych plików (wymagania regulaminu).

- dozwolone formaty: PDF, DOC, DOCX, ODS, XLSX, JPG, JPEG, PNG,
- nazwa pliku ≤ 250 znaków, bez znaków ``; : > < / \\ | ' * % $`` (i znaków sterujących),
- rozmiar ≤ ``MAX_FILE_SIZE_MB``, plik niepusty,
- typ MIME wykryty z treści (libmagic) musi odpowiadać rozszerzeniu.

libmagic dla kontenerów ZIP (DOCX/XLSX/ODS) i OLE (DOC) czasem zwraca typ ogólny
(``application/zip``, ``application/x-ole-storage``). Wtedy rozstrzyga sprawdzenie struktury
kontenera — wyłącznie odczyt spisu treści, bez rozpakowywania (odporne na zip-bomby).
"""

import hashlib
import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

MAX_FILENAME_LENGTH = 250
FORBIDDEN_FILENAME_CHARS = frozenset(";:><\\/|'*%$")

MimeDetector = Callable[[bytes], str]


class FileErrorCode(StrEnum):
    FILENAME_MISSING = "FILENAME_MISSING"
    FILENAME_TOO_LONG = "FILENAME_TOO_LONG"
    FILENAME_FORBIDDEN_CHARS = "FILENAME_FORBIDDEN_CHARS"
    EXTENSION_NOT_ALLOWED = "EXTENSION_NOT_ALLOWED"
    FILE_EMPTY = "FILE_EMPTY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    MIME_MISMATCH = "MIME_MISMATCH"


class FileValidationError(Exception):
    def __init__(self, code: FileErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    filename: str
    extension: str
    mime_type: str
    content: bytes
    sha256: str

    @property
    def size_bytes(self) -> int:
        return len(self.content)


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ODS = "application/vnd.oasis.opendocument.spreadsheet"
_DOC = "application/msword"

#: Typ MIME wynikowy (kanoniczny) dla rozszerzenia.
CANONICAL_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "doc": _DOC,
    "docx": _DOCX,
    "ods": _ODS,
    "xlsx": _XLSX,
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}
ALLOWED_EXTENSIONS = frozenset(CANONICAL_MIME)

# Typy ogólne kontenerów, przy których decyduje sprawdzenie struktury.
_ZIP_GENERIC = frozenset(
    {"application/zip", "application/x-zip-compressed", "application/octet-stream"}
)
_OLE_GENERIC = frozenset(
    {"application/x-ole-storage", "application/CDFV2", "application/vnd.ms-office",
     "application/octet-stream"}
)  # fmt: skip


def validate_filename(filename: str | None) -> tuple[str, str]:
    """Zwraca (nazwa, rozszerzenie małymi literami) albo rzuca ``FileValidationError``."""
    if filename is None or not filename.strip():
        raise FileValidationError(FileErrorCode.FILENAME_MISSING, "Brak nazwy pliku")
    if len(filename) > MAX_FILENAME_LENGTH:
        raise FileValidationError(
            FileErrorCode.FILENAME_TOO_LONG,
            f"Nazwa pliku ma {len(filename)} znaków (maksymalnie {MAX_FILENAME_LENGTH})",
        )
    bad = sorted(
        {c for c in filename if c in FORBIDDEN_FILENAME_CHARS or ord(c) < 32 or c == "\x7f"}
    )
    if bad:
        shown = " ".join(repr(c) for c in bad)
        raise FileValidationError(
            FileErrorCode.FILENAME_FORBIDDEN_CHARS,
            f"Nazwa pliku zawiera niedozwolone znaki: {shown}",
        )
    stem, dot, extension = filename.rpartition(".")
    extension = extension.lower()
    if not dot or not stem.strip() or extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise FileValidationError(
            FileErrorCode.EXTENSION_NOT_ALLOWED,
            f"Niedozwolony typ pliku. Dozwolone rozszerzenia: {allowed}",
        )
    return filename, extension


def validate_size(size: int, max_bytes: int) -> None:
    if size == 0:
        raise FileValidationError(FileErrorCode.FILE_EMPTY, "Plik jest pusty")
    if size > max_bytes:
        raise FileValidationError(
            FileErrorCode.FILE_TOO_LARGE,
            f"Plik jest za duży (maksymalnie {max_bytes // (1024 * 1024)} MB)",
        )


def _zip_names(content: bytes) -> set[str] | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return set(archive.namelist())
    except zipfile.BadZipFile, ValueError, OSError:
        return None


def _is_ooxml(content: bytes, main_part: str) -> bool:
    names = _zip_names(content)
    return names is not None and "[Content_Types].xml" in names and main_part in names


def _is_ods(content: bytes) -> bool:
    # ODF: pierwszy wpis to nieskompresowany plik "mimetype" z typem dokumentu.
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("mimetype")
            if info.file_size > 100:
                return False
            return archive.read(info).strip() == _ODS.encode()
    except zipfile.BadZipFile, KeyError, ValueError, OSError:
        return False


def _is_word_ole(content: bytes) -> bool:
    # Kontener OLE (CFB) z dokumentem Worda ma strumień "WordDocument" (nazwa w UTF-16LE).
    return content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") and (
        "WordDocument".encode("utf-16-le") in content
    )


def mime_matches_extension(extension: str, detected: str, content: bytes) -> bool:
    expected = CANONICAL_MIME[extension]
    if detected == expected:
        return True
    match extension:
        case "docx":
            return detected in _ZIP_GENERIC and _is_ooxml(content, "word/document.xml")
        case "xlsx":
            return detected in _ZIP_GENERIC and _is_ooxml(content, "xl/workbook.xml")
        case "ods":
            return detected in _ZIP_GENERIC and _is_ods(content)
        case "doc":
            return detected in _OLE_GENERIC and _is_word_ole(content)
        case _:
            return False


def libmagic_detector(content: bytes) -> str:
    """Domyślny detektor MIME (libmagic). Import leniwy — biblioteka natywna tylko w runtime."""
    import magic

    detected: str = magic.from_buffer(content, mime=True)
    return detected


def validate_upload(
    filename: str | None,
    content: bytes,
    *,
    max_bytes: int,
    detect_mime: MimeDetector = libmagic_detector,
) -> ValidatedFile:
    name, extension = validate_filename(filename)
    validate_size(len(content), max_bytes)
    detected = detect_mime(content)
    if not mime_matches_extension(extension, detected, content):
        raise FileValidationError(
            FileErrorCode.MIME_MISMATCH,
            f"Zawartość pliku ({detected}) nie odpowiada rozszerzeniu .{extension}",
        )
    return ValidatedFile(
        filename=name,
        extension=extension,
        mime_type=CANONICAL_MIME[extension],
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )
