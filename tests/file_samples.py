"""Minimalne, strukturalnie poprawne próbki obsługiwanych formatów (bez binariów w repo)."""

import io
import struct
import zipfile
import zlib

OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_DOCX_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
_XLSX_TYPES = _DOCX_TYPES.replace("/word/document.xml", "/xl/workbook.xml").replace(
    "wordprocessingml.document.main", "spreadsheetml.sheet.main"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
)


def pdf() -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
        b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )


def jpeg() -> bytes:
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0 + b"\xff\xd9"


def _zip(entries: list[tuple[str, str | bytes]], *, stored_first: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, (name, data) in enumerate(entries):
            compress = zipfile.ZIP_STORED if stored_first and index == 0 else zipfile.ZIP_DEFLATED
            archive.writestr(name, data, compress_type=compress)
    return buffer.getvalue()


def docx() -> bytes:
    return _zip(
        [
            ("[Content_Types].xml", _DOCX_TYPES),
            ("_rels/.rels", _RELS),
            ("word/document.xml", "<w:document/>"),
        ]
    )


def xlsx() -> bytes:
    return _zip(
        [
            ("[Content_Types].xml", _XLSX_TYPES),
            ("_rels/.rels", _RELS),
            ("xl/workbook.xml", "<workbook/>"),
        ]
    )


def ods() -> bytes:
    return _zip(
        [
            ("mimetype", b"application/vnd.oasis.opendocument.spreadsheet"),
            ("content.xml", "<office:document-content/>"),
            ("META-INF/manifest.xml", "<manifest:manifest/>"),
        ],
        stored_first=True,
    )


def doc() -> bytes:
    """Nagłówek kontenera OLE z wpisem katalogu „WordDocument” (struktura uproszczona)."""
    header = OLE_SIGNATURE + b"\x00" * (512 - len(OLE_SIGNATURE))
    entry = "WordDocument".encode("utf-16-le").ljust(64, b"\x00")
    return header + entry + b"\x00" * 448


SAMPLES = {
    "pdf": pdf,
    "png": png,
    "jpg": jpeg,
    "jpeg": jpeg,
    "docx": docx,
    "xlsx": xlsx,
    "ods": ods,
    "doc": doc,
}


def sniff_mime(content: bytes) -> str:
    """Uproszczony detektor do testów API (prawdziwą libmagic testuje test_file_validators)."""
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\x89PNG"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"PK\x03\x04"):
        return "application/zip"
    if content.startswith(OLE_SIGNATURE):
        return "application/x-ole-storage"
    return "application/octet-stream"
