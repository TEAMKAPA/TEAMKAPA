"""Syntetyczne dane testowe kanału wizualnego, generowane w kodzie (bez materiałów COI).

A.3: minimalne kontenery OLE2 (CFB) i ZIP (OOXML/ODF) do testów rozpoznawania formatu.
Generator PDF i obrazów dochodzi w A.4/A.5.
"""

import io
import struct
import zipfile

_ENDOFCHAIN = 0xFFFFFFFE
_FREESECT = 0xFFFFFFFF
_FATSECT = 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF
_SECTOR = 512


def _dir_entry(name: str, entry_type: int, child: int = _NOSTREAM) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    assert len(encoded) <= 64
    return struct.pack(
        "<64sHBBIII16sIQQIQ",
        encoded.ljust(64, b"\x00"),
        len(encoded),
        entry_type,
        1,  # kolor: czarny
        _NOSTREAM,
        _NOSTREAM,
        child,
        b"\x00" * 16,
        0,
        0,
        0,
        _ENDOFCHAIN,
        0,
    )


def make_ole2(stream_names: list[str], *, directory_loop: bool = False) -> bytes:
    """Minimalny plik CFB v3: sektor 0 = FAT, sektory 1..k = katalog (root + podane strumienie).

    ``directory_loop=True`` zapętla łańcuch katalogu (test odporności parsera).
    """
    entries = [_dir_entry("Root Entry", 5, child=1 if stream_names else _NOSTREAM)]
    entries += [_dir_entry(n, 2) for n in stream_names]
    while len(entries) % 4:
        entries.append(b"\x00" * 128)
    dir_sectors = len(entries) // 4

    fat = [_FATSECT] + [i + 2 for i in range(dir_sectors - 1)] + [_ENDOFCHAIN]
    if directory_loop:
        fat[-1] = 1
    fat += [_FREESECT] * (_SECTOR // 4 - len(fat))

    header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 16
    header += struct.pack("<HHHHH6sIIIIIIIII", 0x3E, 3, 0xFFFE, 9, 6, b"\x00" * 6, 0, 1, 1, 0,
                          4096, _ENDOFCHAIN, 0, _ENDOFCHAIN, 0)  # fmt: skip
    header += struct.pack("<109I", 0, *([_FREESECT] * 108))
    assert len(header) == _SECTOR
    return header + struct.pack(f"<{_SECTOR // 4}I", *fat) + b"".join(entries)


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_ooxml(main_content_type: str) -> bytes:
    """Minimalny pakiet OOXML z ``[Content_Types].xml`` wskazującym część główną."""
    ct = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/main.xml" ContentType="{main_content_type}"/></Types>'
    )
    return make_zip({"[Content_Types].xml": ct.encode(), "main.xml": b"<x/>"})


def make_odf(mimetype: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), mimetype.encode(), zipfile.ZIP_STORED)
        zf.writestr("content.xml", b"<x/>")
    return buf.getvalue()


DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
DOCM_TYPE = "application/vnd.ms-word.document.macroEnabled.main+xml"
XLSM_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
