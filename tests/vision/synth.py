"""Syntetyczne dane testowe kanału wizualnego, generowane w kodzie (bez materiałów COI).

A.3: minimalne kontenery OLE2 (CFB) i ZIP (OOXML/ODF) do testów rozpoznawania formatu.
A.4: ręczny generator PDF (niezależny od testowanego renderera).
A.5: obrazy (zszyte strony A4, orientacja EXIF, tryby, nagłówki deklarujące rozmiar).
"""

import hashlib
import io
import struct
import zipfile
import zlib
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage

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


# --- obrazy (A.5) ------------------------------------------------------------------------------

PixelRect = tuple[int, int, int, int]
"""Prostokąt w pikselach: (x0, y0, x1, y1), krawędzie pikseli (x1, y1 wyłącznie)."""


def white(width: int, height: int) -> npt.NDArray[np.uint8]:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def fill(image: npt.NDArray[np.uint8], rect: PixelRect, color: Color) -> None:
    x0, y0, x1, y1 = rect
    image[y0:y1, x0:x1] = color


def a4_height(width: int) -> int:
    return round(width * 2**0.5)


def stacked_pages(
    width: int, parts: int, *, extra_rows: int = 0
) -> tuple[npt.NDArray[np.uint8], list[PixelRect]]:
    """Zszyte w pionie strony A4 (białe), na każdej jeden prostokąt w innym miejscu i kolorze.

    Zwraca obraz i prostokąty we współrzędnych całego obrazu. ``extra_rows`` dokleja białe wiersze.
    """
    page_h = a4_height(width)
    image = white(width, page_h * parts + extra_rows)
    rects = []
    for i in range(parts):
        top = i * page_h
        x0 = 10 + (i * 7) % (width // 2)
        rect = (x0, top + page_h // 4, x0 + width // 4, top + page_h // 4 + page_h // 8)
        fill(image, rect, ((40 * i) % 200, 255 - (30 * i) % 200, 60))
        rects.append(rect)
    return image, rects


def encode(
    image: npt.NDArray[np.uint8] | PILImage.Image,
    fmt: str = "PNG",
    *,
    dpi: tuple[float, float] | None = None,
    exif_orientation: int | None = None,
    **options: object,
) -> bytes:
    pil = image if isinstance(image, PILImage.Image) else PILImage.fromarray(image)
    kwargs: dict[str, object] = dict(options)
    if dpi is not None:
        kwargs["dpi"] = dpi
    if exif_orientation is not None:
        exif = PILImage.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    buf = io.BytesIO()
    pil.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


# Obraz zapisany tak, by po zastosowaniu orientacji EXIF N wyglądał jak ``upright``
# (EXIF 2.32, tabela Orientation: przekształcenie potrzebne do wyświetlenia, odwrócone).
_STORED_FOR_ORIENTATION: dict[int, PILImage.Transpose | None] = {
    1: None,
    2: PILImage.Transpose.FLIP_LEFT_RIGHT,
    3: PILImage.Transpose.ROTATE_180,
    4: PILImage.Transpose.FLIP_TOP_BOTTOM,
    5: PILImage.Transpose.TRANSPOSE,
    6: PILImage.Transpose.ROTATE_90,  # wyświetlenie wymaga obrotu o 90° w prawo
    7: PILImage.Transpose.TRANSVERSE,
    8: PILImage.Transpose.ROTATE_270,  # wyświetlenie wymaga obrotu o 90° w lewo
}


def stored_for_orientation(upright: npt.NDArray[np.uint8], orientation: int) -> PILImage.Image:
    pil = PILImage.fromarray(upright)
    op = _STORED_FOR_ORIENTATION[orientation]
    return pil if op is None else pil.transpose(op)


def png_claiming_size(width: int, height: int) -> bytes:
    """Mały PNG, którego nagłówek IHDR deklaruje podany rozmiar (test bomby dekompresyjnej)."""
    data = bytearray(encode(white(1, 1)))
    ihdr = data.index(b"IHDR")
    data[ihdr + 4 : ihdr + 12] = struct.pack(">II", width, height)
    crc = zlib.crc32(bytes(data[ihdr : ihdr + 17]))
    data[ihdr + 17 : ihdr + 21] = struct.pack(">I", crc)
    return bytes(data)


# --- dokumenty biurowe (A.6) -------------------------------------------------------------------


def make_minimal_docx(text: str = "Synthetic") -> bytes:
    """Najmniejszy poprawny DOCX (dla testu integracyjnego z LibreOffice)."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    return make_zip(
        {
            "[Content_Types].xml": (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" '
                'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                f'<Override PartName="/word/document.xml" ContentType="{DOCX_TYPE}"/></Types>'
            ).encode(),
            "_rels/.rels": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
                b'2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
            ),
            "word/document.xml": (
                f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}">'
                f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
            ).encode(),
        }
    )
