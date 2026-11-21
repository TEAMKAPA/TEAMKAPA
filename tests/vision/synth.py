"""Syntetyczne dane testowe kanału wizualnego, generowane w kodzie (bez materiałów COI).

A.3: minimalne kontenery OLE2 (CFB) i ZIP (OOXML/ODF) do testów rozpoznawania formatu.
A.4: ręczny generator PDF (niezależny od testowanego renderera). Obrazy: A.5.
"""

import hashlib
import io
import struct
import zipfile
from dataclasses import dataclass

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


# --- PDF (A.4) ---------------------------------------------------------------------------------

Color = tuple[int, int, int]
PdfRect = tuple[float, float, float, float, Color]
"""Wypełniony prostokąt w punktach PDF: (x0, y0, x1, y1, (r, g, b)), lewy dolny róg."""


@dataclass(frozen=True)
class SynthPage:
    mediabox: tuple[float, float, float, float] = (0.0, 0.0, 595.0, 842.0)
    cropbox: tuple[float, float, float, float] | None = None
    rotate: int = 0
    rects: tuple[PdfRect, ...] = ()


def _content(rects: tuple[PdfRect, ...]) -> bytes:
    ops = []
    for x0, y0, x1, y1, (r, g, b) in rects:
        ops.append(f"{r / 255:.4f} {g / 255:.4f} {b / 255:.4f} rg")
        ops.append(f"{x0:.4f} {y0:.4f} {x1 - x0:.4f} {y1 - y0:.4f} re f")
    return "\n".join(ops).encode("ascii")


def _num(box: tuple[float, ...]) -> str:
    return "[" + " ".join(f"{v:g}" for v in box) + "]"


def make_pdf(pages: list[SynthPage], *, user_password: str | None = None) -> bytes:
    """Minimalny, poprawny PDF 1.4 (nieskompresowane strumienie, tablica xref).

    ``user_password`` dodaje słownik /Encrypt (Standard, R2, RC4 40 bit), więc otwarcie bez hasła
    musi się nie udać. Treść strumieni nie jest szyfrowana: test sprawdza tylko odmowę otwarcia.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # uzupełniany niżej
    pages_obj = add(b"")
    page_ids = []
    for p in pages:
        stream = _content(p.rects)
        content_id = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        extra = f" /CropBox {_num(p.cropbox)}" if p.cropbox is not None else ""
        extra += f" /Rotate {p.rotate}" if p.rotate else ""
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_obj} 0 R /MediaBox {_num(p.mediabox)}{extra}"
                f" /Resources << >> /Contents {content_id} 0 R >>".encode("ascii")
            )
        )
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_obj} 0 R >>".encode("ascii")
    objects[pages_obj - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "ascii"
    )

    file_id = hashlib.md5(b"synthetic-pdf").digest()  # noqa: S324 - identyfikator, nie kryptografia
    trailer_extra = ""
    if user_password is not None:
        enc_id = add(_rc4_encrypt_dict(user_password, file_id))
        trailer_extra = f" /Encrypt {enc_id} 0 R"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii")
    out += b"".join(f"{o:010d} 00000 n \n".encode("ascii") for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R{trailer_extra}"
        f" /ID [<{file_id.hex()}> <{file_id.hex()}>] >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(byte ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def _rc4_encrypt_dict(user_password: str, file_id: bytes) -> bytes:
    """Słownik /Encrypt (ISO 32000-1, alg. 3.2-3.4, R2); hasło właściciela = hasło użytkownika."""
    pw = (user_password.encode("latin-1") + _PAD)[:32]
    owner = _rc4(hashlib.md5(pw).digest()[:5], pw)  # noqa: S324 - wymagane przez format PDF
    perms = -4
    key = hashlib.md5(pw + owner + struct.pack("<i", perms) + file_id).digest()[:5]  # noqa: S324
    user = _rc4(key, _PAD)
    return (
        f"<< /Filter /Standard /V 1 /R 2 /Length 40 /P {perms}"
        f" /O <{owner.hex()}> /U <{user.hex()}> >>"
    ).encode("ascii")
