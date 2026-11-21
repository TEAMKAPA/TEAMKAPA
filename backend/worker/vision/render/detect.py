"""Rozpoznanie rodzaju dokumentu z zawartości (nie z rozszerzenia).

Rozszerzenie bywa niezgodne z zawartością (np. plik ``.DOC`` z zawartością DOCX), więc renderer
wybiera ścieżkę według zawartości, a niezgodność z rodzajem deklarowanym zgłasza jako ostrzeżenie
``DECLARED_KIND_MISMATCH``. Politykę odrzucania plików ustala krok 1 (API).

Rozpoznanie jest defensywne: czyta tylko nagłówki, katalog kontenera i małe fragmenty, z limitami.
Wynik ``kind=None`` oznacza format nieobsługiwany, a ``detail`` mówi, co rozpoznano (bez treści).
"""

import io
import struct
import zipfile
from pathlib import PurePath

from backend.shared.schemas.quality import Severity
from backend.worker.vision.errors import IssueCode
from backend.worker.vision.models import DocumentKind, RenderIssue, VisionModel

_PDF_HEADER_WINDOW = 1024  # ISO 32000: nagłówek %PDF- może wystąpić w pierwszych 1024 bajtach
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOI = b"\xff\xd8\xff"
_OLE2_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_SIGNATURE = b"PK\x03\x04"

_MAX_CONTENT_TYPES_BYTES = 1_000_000
_MAX_MIMETYPE_BYTES = 256
_MAX_OLE_DIR_SECTORS = 10_000
_MAX_OLE_FAT_SECTORS = 100_000

_OOXML_TYPES: tuple[tuple[bytes, str, DocumentKind | None], ...] = (
    # (fragment Content-Type części głównej, opis, rodzaj lub None = nieobsługiwany)
    (b"wordprocessingml.document.main+xml", "docx", DocumentKind.DOCX),
    (b"spreadsheetml.sheet.main+xml", "xlsx", DocumentKind.XLSX),
    (b"ms-word.document.macroEnabled.main+xml", "docm", None),
    (b"ms-excel.sheet.macroEnabled.main+xml", "xlsm", None),
    (b"wordprocessingml.template.main+xml", "dotx", None),
    (b"spreadsheetml.template.main+xml", "xltx", None),
    (b"presentationml.presentation.main+xml", "pptx", None),
)

_ODF_PREFIX = "application/vnd.oasis.opendocument."
_ODF_TYPES: dict[str, DocumentKind | None] = {
    "spreadsheet": DocumentKind.ODS,
    "text": None,  # ODT nie jest na liście formatów regulaminu
}

_EXTENSIONS: dict[str, DocumentKind] = {
    ".pdf": DocumentKind.PDF,
    ".doc": DocumentKind.DOC,
    ".docx": DocumentKind.DOCX,
    ".ods": DocumentKind.ODS,
    ".xlsx": DocumentKind.XLSX,
    ".jpg": DocumentKind.JPEG,
    ".jpeg": DocumentKind.JPEG,
    ".png": DocumentKind.PNG,
}


class Detection(VisionModel):
    """Wynik rozpoznania. ``container``: pdf/png/jpeg/ole2/zip/unknown; ``detail``: co dokładnie."""

    kind: DocumentKind | None
    container: str
    detail: str


def kind_from_extension(filename: str) -> DocumentKind | None:
    """Rodzaj deklarowany z rozszerzenia (bez rozróżniania wielkości liter; formaty regulaminu)."""
    return _EXTENSIONS.get(PurePath(filename).suffix.lower())


def detect_kind(data: bytes) -> Detection:
    # Najpierw sygnatury od bajtu 0; okno nagłówka PDF dopiero potem, żeby np. PNG lub ZIP
    # zawierający przypadkiem "%PDF-" w pierwszych bajtach nie został uznany za PDF.
    if data.startswith(_PNG_SIGNATURE):
        return Detection(kind=DocumentKind.PNG, container="png", detail="png")
    if data.startswith(_JPEG_SOI):
        return Detection(kind=DocumentKind.JPEG, container="jpeg", detail="jpeg")
    if data.startswith(_OLE2_SIGNATURE):
        return _detect_ole2(data)
    if data.startswith(_ZIP_SIGNATURE):
        return _detect_zip(data)
    if b"%PDF-" in data[:_PDF_HEADER_WINDOW]:
        return Detection(kind=DocumentKind.PDF, container="pdf", detail="pdf")
    return Detection(kind=None, container="unknown", detail="unknown")


def declared_kind_issues(declared: DocumentKind | None, detected: Detection) -> list[RenderIssue]:
    """Ostrzeżenie, gdy rodzaj deklarowany (np. z rozszerzenia) różni się od wykrytego."""
    if declared is None or detected.kind is None or declared is detected.kind:
        return []
    return [
        RenderIssue(
            code=IssueCode.DECLARED_KIND_MISMATCH,
            severity=Severity.WARNING,
            detail=f"declared={declared.value} detected={detected.kind.value}",
        )
    ]


# --- ZIP: OOXML i ODF -----------------------------------------------------------------------


def _detect_zip(data: bytes) -> Detection:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" in names:
                head = _read_limited(zf, "[Content_Types].xml", _MAX_CONTENT_TYPES_BYTES)
                for marker, label, kind in _OOXML_TYPES:
                    if marker in head:
                        return Detection(kind=kind, container="zip", detail=f"ooxml:{label}")
                return Detection(kind=None, container="zip", detail="ooxml:unknown")
            if "mimetype" in names:
                raw = _read_limited(zf, "mimetype", _MAX_MIMETYPE_BYTES)
                mime = raw.decode("ascii", errors="replace").strip()
                if mime.startswith(_ODF_PREFIX):
                    sub = mime.removeprefix(_ODF_PREFIX)
                    kind = _ODF_TYPES.get(sub)
                    return Detection(kind=kind, container="zip", detail=f"odf:{sub[:40]}")
                return Detection(kind=None, container="zip", detail="zip:mimetype")
    except zipfile.BadZipFile, OSError, KeyError, RuntimeError, ValueError:
        return Detection(kind=None, container="zip", detail="zip:unreadable")
    return Detection(kind=None, container="zip", detail="zip:other")


def _read_limited(zf: zipfile.ZipFile, name: str, limit: int) -> bytes:
    with zf.open(name) as fh:
        return fh.read(limit)


# --- OLE2 (Compound File Binary): rozpoznanie po nazwach strumieni w katalogu -----------------

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_MAX_REGULAR_SECTOR = 0xFFFFFFFA


class _OleFormatError(Exception):
    pass


def _detect_ole2(data: bytes) -> Detection:
    try:
        names = _ole2_entry_names(data)
    except _OleFormatError, struct.error:
        return Detection(kind=None, container="ole2", detail="ole2:unreadable")
    if "WordDocument" in names:
        return Detection(kind=DocumentKind.DOC, container="ole2", detail="ole2:doc")
    if "EncryptedPackage" in names:
        return Detection(kind=None, container="ole2", detail="ole2:encrypted_ooxml")
    if "Workbook" in names or "Book" in names:
        return Detection(kind=None, container="ole2", detail="ole2:xls")
    return Detection(kind=None, container="ole2", detail="ole2:other")


def _ole2_entry_names(data: bytes) -> set[str]:
    """Nazwy wpisów katalogu pliku CFB (MS-CFB). Ograniczona liczba sektorów i wykrywanie pętli."""
    if len(data) < 512:
        raise _OleFormatError("za krótki nagłówek")
    shift = struct.unpack_from("<H", data, 0x1E)[0]
    if shift not in (9, 12):
        raise _OleFormatError("rozmiar sektora")
    sector_size = 1 << shift
    num_fat = struct.unpack_from("<I", data, 0x2C)[0]
    first_dir = struct.unpack_from("<I", data, 0x30)[0]
    first_difat = struct.unpack_from("<I", data, 0x44)[0]
    num_difat = struct.unpack_from("<I", data, 0x48)[0]
    if num_fat > _MAX_OLE_FAT_SECTORS:
        raise _OleFormatError("za dużo sektorów FAT")

    def sector(index: int) -> bytes:
        if index > _MAX_REGULAR_SECTOR:
            raise _OleFormatError("niepoprawny numer sektora")
        start = (index + 1) * sector_size
        if start + sector_size > len(data):
            raise _OleFormatError("sektor poza plikiem")
        return data[start : start + sector_size]

    fat_sectors = [v for v in struct.unpack_from("<109I", data, 0x4C) if v != _FREESECT]
    next_difat, seen_difat = first_difat, 0
    per_difat = sector_size // 4 - 1
    while next_difat not in (_ENDOFCHAIN, _FREESECT) and seen_difat < num_difat:
        entries = struct.unpack(f"<{per_difat + 1}I", sector(next_difat))
        fat_sectors.extend(v for v in entries[:per_difat] if v != _FREESECT)
        next_difat = entries[per_difat]
        seen_difat += 1
        if seen_difat > _MAX_OLE_FAT_SECTORS:
            raise _OleFormatError("pętla DIFAT")
    fat_sectors = fat_sectors[:num_fat]

    fat: list[int] = []
    for s in fat_sectors:
        fat.extend(struct.unpack(f"<{sector_size // 4}I", sector(s)))

    names: set[str] = set()
    current, visited = first_dir, set[int]()
    while current != _ENDOFCHAIN:
        if current in visited or len(visited) >= _MAX_OLE_DIR_SECTORS or current >= len(fat):
            raise _OleFormatError("łańcuch katalogu")
        visited.add(current)
        block = sector(current)
        for off in range(0, sector_size, 128):
            name_len = struct.unpack_from("<H", block, off + 64)[0]
            entry_type = block[off + 66]
            if entry_type in (1, 2) and 2 <= name_len <= 64:
                names.add(block[off : off + name_len - 2].decode("utf-16-le", errors="replace"))
        current = fat[current]
    return names
