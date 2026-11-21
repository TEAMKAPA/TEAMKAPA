"""Kompletny zestaw testów ekstrakcji (krok 02) dla wszystkich 8 obsługiwanych rozszerzeń.

Pliki testowe (w tym .pdf tekstowy, .pdf skan, .pdf AcroForm, .docx, .doc, .xlsx,
.ods generowany przez odfdo, .jpg, .jpeg, .png oraz pliki uszkodzone/zaszyfrowane)
są tworzone w locie w ``tmp_path``, a katalog roboczy ``Settings(temp_dir=...)`` jest
weryfikowany pod kątem braku pozostałości po zakończeniu lub błędzie ekstrakcji.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import odfdo
import pytest
from PIL import Image, ImageDraw

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import (
    DocumentFormat,
    ExtractedDocument,
)
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.extractors import (
    DoclingExtractionResult,
    ExtractionError,
    parse_docling_document,
)
from backend.worker.pipeline.extractors import image as image_extractor
from backend.worker.pipeline.extractors import ocr as ocr_module
from backend.worker.pipeline.extractors import ooxml as ooxml_extractor
from backend.worker.pipeline.extractors import pdf as pdf_extractor
from backend.worker.pipeline.step02_text_extract import (
    CorruptedFileError,
    EncryptedFileError,
    UnsupportedFileFormatError,
    extract_document,
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


# ------------------------------------------------------------------ generatory plików testowych


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _build_pdf_multipage(pages_text: list[str | None]) -> bytes:
    """Generuje poprawny wielostronicowy PDF 1.4 czytelny dla pypdfium2."""
    # Obiekty:
    # 1: Catalog, 2: Pages, 3: Font Helvetica
    # Dla każdej strony i: (4 + 2*i) = Page, (5 + 2*i) = Content stream
    page_refs = [f"{4 + 2 * i} 0 R" for i in range(len(pages_text))]
    kids = " ".join(page_refs)
    objs: list[bytes] = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>\nendobj\n".encode(),
        b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    for i, text in enumerate(pages_text):
        page_id = 4 + 2 * i
        stream_id = 5 + 2 * i
        page_obj = (
            f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
            f"/Resources << /Font << /Helv 3 0 R >> >> /Contents {stream_id} 0 R >>\nendobj\n"
        )
        objs.append(page_obj.encode())
        if text:
            stream = f"BT /Helv 14 Tf 60 720 Td ({text}) Tj ET".encode("latin-1")
        else:
            # Skan bez warstwy tekstowej: prosty rysunek wektorowy (czarne paski jak linijki).
            stream = b"q 0 g 60 680 420 12 re f 60 640 360 12 re f Q"
        objs.append(
            f"{stream_id} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream\nendobj\n"
        )

    return _assemble_pdf(objs)


def _build_pdf_acroform() -> bytes:
    """Generuje poprawny PDF 1.4 z polami AcroForm: TEXTFIELD, COMBOBOX i CHECKBOX."""
    stream = b"BT /Helv 14 Tf 60 740 Td (Wniosek urzedowy AcroForm) Tj ET"
    objs: list[bytes] = [
        (
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R "
            b"/AcroForm << /Fields [5 0 R 6 0 R 7 0 R] "
            b"/DR << /Font << /Helv 4 0 R >> >> >> >>\nendobj\n"
        ),
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
            b"/Resources << /Font << /Helv 4 0 R >> >> "
            b"/Contents 8 0 R /Annots [5 0 R 6 0 R 7 0 R] >>\nendobj\n"
        ),
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
        (
            b"5 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Tx /T (pesel) "
            b"/TU (Numer PESEL) /V (90010112345) /Ff 2 /Rect [60 640 300 680] /P 3 0 R >>\nendobj\n"
        ),
        (
            b"6 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Ch /T (wojewodztwo) "
            b"/TU (Wojewodztwo) /Ff 131072 /Opt [(Mazowieckie) (Pomorskie)] "
            b"/Rect [60 560 300 600] /P 3 0 R >>\nendobj\n"
        ),
        (
            b"7 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Btn /T (zgoda_rodo) "
            b"/TU (Zgoda RODO) /V /Yes /AS /Yes /Rect [60 480 90 510] /P 3 0 R >>\nendobj\n"
        ),
        f"8 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
        + stream
        + b"\nendstream\nendobj\n",
    ]
    return _assemble_pdf(objs)


def _assemble_pdf(objs: list[bytes]) -> bytes:
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objs:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(offsets)}\n".encode())
    buf.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode())
    buf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(buf)


def _build_docx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                b'<Override PartName="/word/document.xml" '
                b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                b"</Types>"
            ),
            "word/document.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                b"<w:body><w:p><w:r><w:t>Wniosek o wydanie odpisu</w:t></w:r></w:p></w:body>"
                b"</w:document>"
            ),
        }
    )


def _build_xlsx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                b'<Override PartName="/xl/workbook.xml" '
                b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                b"</Types>"
            ),
            "xl/workbook.xml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                b'<sheets><sheet name="Dane" sheetId="1" r:id="rId1" '
                b'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets>'
                b"</workbook>"
            ),
        }
    )


def _build_ods() -> bytes:
    doc = odfdo.Document("spreadsheet")
    doc.body.clear()

    sheet1 = odfdo.Table("Dane wnioskodawcy")
    sheet1.set_value("A1", "Pole")
    sheet1.set_value("B1", "Wartość")
    sheet1.set_value("A2", "Imię i nazwisko")
    sheet1.set_value("B2", "Anna Gżegżółka")
    sheet1.set_value("D2", "Uwagi")
    sheet1.set_span("A3:B3", merge=True)
    sheet1.set_value("A3", "Podpis")
    tail = odfdo.Row()
    tail.repeated = 1_000_000
    sheet1.append_row(tail)
    doc.body.append(sheet1)

    sheet2 = odfdo.Table("Załączniki")
    sheet2.set_value("A1", "Lp.")
    sheet2.set_value("B1", "Nazwa")
    sheet2.set_value("A2", 1)
    sheet2.set_value("B2", "Pełnomocnictwo")
    doc.body.append(sheet2)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_ole2_cfb(streams: dict[str, bytes]) -> bytes:
    sector_size = 512
    padded_streams: list[tuple[str, bytes, int]] = []
    for name, raw in streams.items():
        storage = raw if len(raw) >= 4096 else raw + b"\x00" * (4096 - len(raw))
        sector_count = (len(storage) + sector_size - 1) // sector_size
        padded = storage.ljust(sector_count * sector_size, b"\x00")
        padded_streams.append((name, padded, len(storage)))

    fat = [0xFFFFFFFF] * 128
    fat[0] = 0xFFFFFFFD
    fat[1] = 0xFFFFFFFE

    next_sec = 2
    stream_meta: list[tuple[str, int, int]] = []
    data_blob = bytearray()
    for name, padded, logical_size in padded_streams:
        count = len(padded) // sector_size
        start_sec = next_sec
        for i in range(count):
            sec = next_sec + i
            fat[sec] = (sec + 1) if i + 1 < count else 0xFFFFFFFE
        next_sec += count
        data_blob.extend(padded)
        stream_meta.append((name, start_sec, logical_size))

    header = bytearray(sector_size)
    header[0:8] = _OLE_MAGIC
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 0x0003)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    struct.pack_into("<I", header, 76, 0)
    for idx in range(1, 109):
        struct.pack_into("<I", header, 76 + idx * 4, 0xFFFFFFFF)

    fat_sector = struct.pack("<128I", *fat)

    def _entry(
        name: str,
        obj_type: int,
        start_sec: int,
        size: int,
        *,
        right: int = 0xFFFFFFFF,
        child: int = 0xFFFFFFFF,
    ) -> bytes:
        raw_entry = bytearray(128)
        encoded_name = (name + "\x00").encode("utf-16-le")
        raw_entry[0 : len(encoded_name)] = encoded_name
        struct.pack_into("<H", raw_entry, 64, len(encoded_name))
        raw_entry[66] = obj_type
        raw_entry[67] = 1
        struct.pack_into("<III", raw_entry, 68, 0xFFFFFFFF, right, child)
        struct.pack_into("<II", raw_entry, 116, start_sec, size)
        return bytes(raw_entry)

    dir_sector = bytearray()
    dir_sector.extend(
        _entry("Root Entry", 5, 0xFFFFFFFE, 0, child=1 if stream_meta else 0xFFFFFFFF)
    )
    for idx, (name, start_sec, logical_size) in enumerate(stream_meta, start=1):
        right = idx + 1 if idx < len(stream_meta) else 0xFFFFFFFF
        dir_sector.extend(_entry(name, 2, start_sec, logical_size, right=right))
    dir_sector.extend(b"\x00" * (sector_size - len(dir_sector)))
    return bytes(header) + fat_sector + bytes(dir_sector) + bytes(data_blob)


def _build_doc(*, encrypted: bool = False) -> bytes:
    pieces = [
        ("Wniosek o udostępnienie informacji publicznej\r", "utf-16-le"),
        ("Wnioskodawca: Janina Łódzka-Żółć\r", "cp1250"),
        ("PESEL\x0790010112345\x07\x07\r", "utf-16-le"),
        ("Kod pocztowy\x0700-950\x07\x07\r", "cp1250"),
    ]
    fib = bytearray(0x0200)
    struct.pack_into("<H", fib, 0x00, 0xA5EC)
    struct.pack_into("<H", fib, 0x0A, 0x0200 | (0x0100 if encrypted else 0x0000))
    struct.pack_into("<H", fib, 0x20, 14)
    struct.pack_into("<H", fib, 0x3E, 22)
    struct.pack_into("<H", fib, 0x98, 93)

    word_body = bytearray(fib)
    cps = [0]
    pcds = bytearray()
    for text, enc in pieces:
        cps.append(cps[-1] + len(text))
        off = len(word_body)
        if enc == "cp1250":
            word_body.extend(text.encode("cp1250"))
            fc = (off * 2) | 0x40000000
        else:
            word_body.extend(text.encode("utf-16-le"))
            fc = off
        pcds.extend(struct.pack("<HIH", 0, fc, 0))

    struct.pack_into("<I", word_body, 0x4C, cps[-1])
    pcdt_payload = struct.pack(f"<{len(cps)}I", *cps) + bytes(pcds)
    clx = b"\x02" + struct.pack("<I", len(pcdt_payload)) + pcdt_payload
    struct.pack_into("<II", word_body, 0x01A2, 0, len(clx))
    return _build_ole2_cfb({"WordDocument": bytes(word_body), "1Table": clx})


def _build_image(
    fmt: str,
    *,
    size: tuple[int, int] = (320, 180),
    mode: str = "RGB",
    exif_orientation: int | None = None,
    dpi: tuple[int, int] | None = None,
) -> bytes:
    bg: Any = (255, 255, 255, 200) if mode == "RGBA" else (255, 255, 255)
    img = Image.new(mode, size, bg)
    draw = ImageDraw.Draw(img)
    fg: Any = (0, 0, 0, 255) if mode == "RGBA" else (0, 0, 0)
    # Rysujemy wyraźne paski symulujące wiersze tekstu dla preprocessingu OpenCV.
    draw.rectangle([30, 40, size[0] - 30, 52], fill=fg)
    draw.rectangle([30, 80, size[0] - 60, 92], fill=fg)

    buf = io.BytesIO()
    kwargs: dict[str, Any] = {}
    if dpi is not None:
        kwargs["dpi"] = dpi
    if exif_orientation is not None and fmt == "JPEG":
        exif = img.getexif()
        exif[274] = exif_orientation
        kwargs["exif"] = exif.tobytes()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


# --------------------------------------------------- deterministyczne atrapy Docling i Tesseract


@dataclass
class _FakeBBox:
    l: float  # noqa: E741
    t: float
    r: float
    b: float
    coord_origin: str = "BOTTOMLEFT"


@dataclass
class _FakeProv:
    page_no: int
    bbox: _FakeBBox


@dataclass
class _FakeSize:
    width: float
    height: float


@dataclass
class _FakePage:
    size: _FakeSize


@dataclass
class _FakeTextItem:
    text: str
    prov: list[_FakeProv] = field(default_factory=list)
    level: int | None = None
    label: str = "text"


@dataclass
class _FakeCell:
    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    text: str


@dataclass
class _FakeTableData:
    num_rows: int
    num_cols: int
    table_cells: list[_FakeCell]


@dataclass
class _FakeTableItem:
    data: _FakeTableData
    prov: list[_FakeProv] = field(default_factory=list)
    sheet_name: str | None = None


class _FakeDoclingDocument:
    def __init__(
        self,
        *,
        pages: dict[int, _FakePage],
        items: list[Any],
        markdown: str,
    ) -> None:
        self.pages = pages
        self._items = items
        self._markdown = markdown

    def iterate_items(self) -> list[tuple[Any, int]]:
        return [(item, 1) for item in self._items]

    def export_to_markdown(self) -> str:
        return self._markdown


def _install_deterministic_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Podmienia ciężkie modele ML (Docling, binarka Tesseract) przy zachowaniu pełnego kodu.

    - ``pypdfium2``, ``odfdo``, ``olefile``, ``Pillow`` oraz ``OpenCV`` działają w 100% realnie.
    - ``parse_docling_document`` i ``_parse_tesseract_data`` wykonują realną normalizację
      współrzędnych ``bbox`` do ``[0.0, 1.0]`` oraz mapowanie na ``ExtractedDocument``.
    """

    def fake_convert_with_docling(
        path: Path,
        *,
        settings: Settings,
        fallback_page_sizes: Any = None,
    ) -> DoclingExtractionResult:
        assert path.exists()
        _ = settings
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            sizes = fallback_page_sizes or {1: (600.0, 800.0)}
            # Dla skanu (brak tekstu) zwracamy brak bloków tekstowych, żeby uruchomić realny OCR.
            if path.read_bytes().find(b"Tj") == -1:
                return DoclingExtractionResult()

            pages = {no: _FakePage(_FakeSize(w, h)) for no, (w, h) in sizes.items()}
            items: list[Any] = []
            for page_no, (w, h) in sorted(sizes.items()):
                items.append(
                    _FakeTextItem(
                        text=f"Nagłówek formularza — strona {page_no}",
                        level=1,
                        label="section_header",
                        prov=[_FakeProv(page_no, _FakeBBox(60.0, h - 40.0, w - 60.0, h - 90.0))],
                    )
                )
                items.append(
                    _FakeTableItem(
                        data=_FakeTableData(
                            num_rows=2,
                            num_cols=2,
                            table_cells=[
                                _FakeCell(0, 1, 0, 1, "Pole"),
                                _FakeCell(0, 1, 1, 2, "Wartość"),
                                _FakeCell(1, 2, 0, 1, "PESEL"),
                                _FakeCell(1, 2, 1, 2, "90010112345"),
                            ],
                        ),
                        prov=[_FakeProv(page_no, _FakeBBox(60.0, h - 180.0, w - 60.0, h - 320.0))],
                    )
                )
            fake_doc = _FakeDoclingDocument(
                pages=pages,
                items=items,
                markdown="# Nagłówek formularza — strona 1",
            )
            return parse_docling_document(fake_doc, fallback_page_sizes=fallback_page_sizes)

        if suffix == ".png":
            w, h = (fallback_page_sizes or {1: (320.0, 180.0)})[1]
            fake_doc = _FakeDoclingDocument(
                pages={1: _FakePage(_FakeSize(w, h))},
                items=[
                    _FakeTableItem(
                        data=_FakeTableData(
                            num_rows=1,
                            num_cols=2,
                            table_cells=[
                                _FakeCell(0, 1, 0, 1, "Imię"),
                                _FakeCell(0, 1, 1, 2, "Jan"),
                            ],
                        ),
                        prov=[
                            _FakeProv(
                                1,
                                _FakeBBox(
                                    w * 0.1,
                                    h * 0.25,
                                    w * 0.9,
                                    h * 0.75,
                                    coord_origin="TOPLEFT",
                                ),
                            )
                        ],
                    )
                ],
                markdown="| Imię | Jan |",
            )
            return parse_docling_document(fake_doc, fallback_page_sizes=fallback_page_sizes)

        if suffix == ".docx":
            fake_doc = _FakeDoclingDocument(
                pages={1: _FakePage(_FakeSize(600.0, 800.0))},
                items=[
                    _FakeTextItem(
                        text="Wniosek o wydanie odpisu",
                        level=1,
                        label="title",
                        prov=[_FakeProv(1, _FakeBBox(60.0, 760.0, 540.0, 720.0))],
                    ),
                    _FakeTableItem(
                        data=_FakeTableData(
                            num_rows=2,
                            num_cols=2,
                            table_cells=[
                                _FakeCell(0, 1, 0, 1, "Dane"),
                                _FakeCell(0, 1, 1, 2, "Opis"),
                                _FakeCell(1, 2, 0, 1, "NIP"),
                                _FakeCell(1, 2, 1, 2, "1234567890"),
                            ],
                        ),
                        prov=[_FakeProv(1, _FakeBBox(60.0, 620.0, 540.0, 500.0))],
                    ),
                ],
                markdown="# Wniosek o wydanie odpisu",
            )
            return parse_docling_document(fake_doc)

        if suffix == ".xlsx":
            fake_doc = _FakeDoclingDocument(
                pages={},
                items=[
                    _FakeTableItem(
                        sheet_name="Dane",
                        data=_FakeTableData(
                            num_rows=2,
                            num_cols=2,
                            table_cells=[
                                _FakeCell(0, 1, 0, 1, "Lp."),
                                _FakeCell(0, 1, 1, 2, "Pole"),
                                _FakeCell(1, 2, 0, 1, "1"),
                                _FakeCell(1, 2, 1, 2, "PESEL"),
                            ],
                        ),
                        prov=[_FakeProv(1, _FakeBBox(0.0, 0.0, 100.0, 50.0, "TOPLEFT"))],
                    )
                ],
                markdown="",
            )
            return parse_docling_document(fake_doc, fallback_page_sizes={1: (100.0, 100.0)})

        return DoclingExtractionResult()

    def fake_tesseract_image_to_data(image: Any, **kwargs: Any) -> dict[str, list[Any]]:
        assert kwargs.get("lang") == "pol"
        img_h, img_w = image.shape[:2]
        box_h = max(1, int(img_h * 0.08))
        return {
            "text": ["", "Wniosek", "urzędowy", "PESEL", ""],
            "conf": ["-1", "93", "89", "96", "-1"],
            "left": [0, int(img_w * 0.1), int(img_w * 0.35), int(img_w * 0.1), 0],
            "top": [0, int(img_h * 0.1), int(img_h * 0.1), int(img_h * 0.3), 0],
            "width": [0, int(img_w * 0.2), int(img_w * 0.25), int(img_w * 0.2), 0],
            "height": [0, box_h, box_h, box_h, 0],
            "block_num": [0, 1, 1, 2, 0],
            "par_num": [0, 1, 1, 1, 0],
            "line_num": [0, 1, 1, 1, 0],
        }

    monkeypatch.setattr(pdf_extractor, "convert_with_docling", fake_convert_with_docling)
    monkeypatch.setattr(image_extractor, "convert_with_docling", fake_convert_with_docling)
    monkeypatch.setattr(ooxml_extractor, "convert_with_docling", fake_convert_with_docling)
    monkeypatch.setattr(ocr_module.pytesseract, "image_to_data", fake_tesseract_image_to_data)


# --------------------------------------------------------------- wspólne asercje kontraktowe


def _collect_all_bboxes(doc: ExtractedDocument) -> list[BoundingBox]:
    bboxes: list[BoundingBox] = []
    for table in doc.tables:
        if table.bbox is not None:
            bboxes.append(table.bbox)
    for field_item in doc.acroform_fields:
        if field_item.bbox is not None:
            bboxes.append(field_item.bbox)
    for page in doc.pages:
        for block in page.blocks:
            if block.bbox is not None:
                bboxes.append(block.bbox)
        for table in page.tables:
            if table.bbox is not None:
                bboxes.append(table.bbox)
        for field_item in page.acroform_fields:
            if field_item.bbox is not None:
                bboxes.append(field_item.bbox)
        for word in page.ocr_words:
            if word.bbox is not None:
                bboxes.append(word.bbox)
    return bboxes


def _assert_valid_extracted_document(
    doc: ExtractedDocument,
    *,
    expected_format: DocumentFormat,
    expected_pages: int | None = None,
    expect_bboxes: bool = False,
) -> None:
    assert isinstance(doc, ExtractedDocument)
    assert doc.document_format is expected_format
    assert doc.page_count == len(doc.pages)
    assert doc.page_count >= 1
    if expected_pages is not None:
        assert doc.page_count == expected_pages

    # Spójna numeracja stron (1..N) i niepusta zawartość.
    assert [p.page_number for p in doc.pages] == list(range(1, doc.page_count + 1))
    assert doc.full_text.strip() != ""
    for page in doc.pages:
        assert page.text.strip() != "" or len(page.tables) > 0
        for block in page.blocks:
            assert block.page == page.page_number
        for table in page.tables:
            assert table.page == page.page_number
        for field_item in page.acroform_fields:
            assert field_item.page == page.page_number

    # Wszystkie współrzędne bbox ściśle w przedziale [0.0, 1.0].
    bboxes = _collect_all_bboxes(doc)
    if expect_bboxes:
        assert len(bboxes) > 0, f"Oczekiwano co najmniej jednego bbox dla {doc.filename}"
    for bbox in bboxes:
        assert 0.0 <= bbox.x0 <= bbox.x1 <= 1.0
        assert 0.0 <= bbox.y0 <= bbox.y1 <= 1.0

    # Serializacja JSON działa poprawnie i pomija surowe obrazy PNG stron.
    dumped = json.loads(doc.model_dump_json())
    assert dumped["document_format"] == expected_format.value
    for page_dict in dumped["pages"]:
        for excluded in ("image_png", "image_width", "image_height", "image_dpi"):
            assert excluded not in page_dict
    reparsed = ExtractedDocument.model_validate(dumped)
    assert reparsed.document_sha256 == doc.document_sha256


# --------------------------------------------------------------- zestaw plików w tmp_path


@pytest.fixture
def sample_files(tmp_path: Path) -> dict[str, tuple[Path, str, DocumentFormat, bool]]:
    """Generuje małe pliki testowe w locie w ``tmp_path/fixtures`` dla wszystkich 8 rozszerzeń."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    specs: dict[str, tuple[str, bytes, str, DocumentFormat, bool]] = {
        "pdf_text": (
            "tekstowy.pdf",
            _build_pdf_multipage(
                [
                    "Wniosek o wydanie dowodu osobistego - strona 1",
                    "Zalaczniki i oswiadczenia wnioskodawcy - strona 2",
                ]
            ),
            "application/pdf",
            DocumentFormat.PDF,
            True,
        ),
        "pdf_scan": (
            "skan.pdf",
            _build_pdf_multipage([None]),
            "application/pdf",
            DocumentFormat.PDF,
            True,
        ),
        "pdf_acroform": (
            "acroform.pdf",
            _build_pdf_acroform(),
            "application/pdf",
            DocumentFormat.PDF,
            True,
        ),
        "docx": (
            "formularz.docx",
            _build_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            DocumentFormat.DOCX,
            True,
        ),
        "doc": (
            "stary.doc",
            _build_doc(),
            "application/msword",
            DocumentFormat.DOC,
            False,
        ),
        "xlsx": (
            "arkusz.xlsx",
            _build_xlsx(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            DocumentFormat.XLSX,
            True,
        ),
        "ods": (
            "budzet.ods",
            _build_ods(),
            "application/vnd.oasis.opendocument.spreadsheet",
            DocumentFormat.ODS,
            False,
        ),
        "jpg": (
            "skan.jpg",
            _build_image("JPEG", size=(320, 180), exif_orientation=6, dpi=(200, 200)),
            "image/jpeg",
            DocumentFormat.IMAGE,
            True,
        ),
        "jpeg": (
            "skan.jpeg",
            _build_image("JPEG", size=(240, 160), dpi=(150, 150)),
            "image/jpeg",
            DocumentFormat.IMAGE,
            True,
        ),
        "png": (
            "formularz.png",
            _build_image("PNG", size=(280, 160), mode="RGBA"),
            "image/png",
            DocumentFormat.IMAGE,
            True,
        ),
    }

    created: dict[str, tuple[Path, str, DocumentFormat, bool]] = {}
    for key, (fname, content, mime, fmt, expect_bboxes) in specs.items():
        path = fixtures_dir / fname
        path.write_bytes(content)
        created[key] = (path, mime, fmt, expect_bboxes)
    return created


# --------------------------------------------------------------------------- testy główne


@pytest.mark.parametrize(
    "case_key",
    [
        "pdf_text",
        "pdf_scan",
        "pdf_acroform",
        "docx",
        "doc",
        "xlsx",
        "ods",
        "jpg",
        "jpeg",
        "png",
    ],
)
def test_extraction_end_to_end_all_formats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
    case_key: str,
) -> None:
    """Weryfikuje ExtractedDocument, niepustą treść, strony, bbox [0..1] i brak plików temp."""
    _install_deterministic_backends(monkeypatch)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    settings = Settings(temp_dir=work_tmp, render_dpi=200)

    file_path, mime_type, expected_format, expect_bboxes = sample_files[case_key]
    raw_bytes = file_path.read_bytes()

    doc_from_bytes = extract_document(
        raw_bytes,
        filename=file_path.name,
        mime_type=mime_type,
        settings=settings,
    )
    _assert_valid_extracted_document(
        doc_from_bytes,
        expected_format=expected_format,
        expect_bboxes=expect_bboxes,
    )
    assert list(work_tmp.iterdir()) == []

    doc_from_stream = extract_document(
        io.BytesIO(raw_bytes),
        filename=file_path.name,
        mime_type=mime_type,
        settings=settings,
    )
    assert doc_from_stream.document_sha256 == doc_from_bytes.document_sha256
    assert list(work_tmp.iterdir()) == []


def test_pdf_text_multipage_renders_png_and_preserves_page_numbers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    _install_deterministic_backends(monkeypatch)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    file_path, mime, fmt, _ = sample_files["pdf_text"]

    doc = extract_document(
        file_path.read_bytes(),
        filename=file_path.name,
        mime_type=mime,
        settings=Settings(temp_dir=work_tmp, render_dpi=150),
    )

    _assert_valid_extracted_document(doc, expected_format=fmt, expected_pages=2, expect_bboxes=True)
    assert SourceChannel.TEXT in doc.source_channels
    assert SourceChannel.OCR not in doc.source_channels
    for page in doc.pages:
        assert page.needs_ocr is False
        assert page.ocr_applied is False
        assert page.image_png is not None
        assert page.image_png.startswith(b"\x89PNG\r\n\x1a\n")
        assert page.image_dpi == 150
    assert list(work_tmp.iterdir()) == []


def test_pdf_scan_triggers_opencv_and_tesseract_ocr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    _install_deterministic_backends(monkeypatch)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    file_path, mime, fmt, _ = sample_files["pdf_scan"]

    doc = extract_document(
        file_path.read_bytes(),
        filename=file_path.name,
        mime_type=mime,
        settings=Settings(temp_dir=work_tmp, render_dpi=200),
    )

    _assert_valid_extracted_document(doc, expected_format=fmt, expected_pages=1, expect_bboxes=True)
    page = doc.pages[0]
    assert page.needs_ocr is True
    assert page.ocr_applied is True
    assert page.ocr_confidence is not None
    assert 0.0 <= page.ocr_confidence <= 1.0
    assert len(page.ocr_words) == 3
    assert "Wniosek urzędowy" in page.text
    assert page.source_channels == [SourceChannel.OCR]
    assert list(work_tmp.iterdir()) == []


def test_pdf_acroform_extracts_all_widget_types_and_bboxes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    _install_deterministic_backends(monkeypatch)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    file_path, mime, fmt, _ = sample_files["pdf_acroform"]

    doc = extract_document(
        file_path.read_bytes(),
        filename=file_path.name,
        mime_type=mime,
        settings=Settings(temp_dir=work_tmp),
    )

    _assert_valid_extracted_document(doc, expected_format=fmt, expected_pages=1, expect_bboxes=True)
    assert SourceChannel.ACROFORM in doc.source_channels
    fields_by_name = {f.name: f for f in doc.acroform_fields}
    assert set(fields_by_name) == {"pesel", "wojewodztwo", "zgoda_rodo"}
    assert fields_by_name["pesel"].field_type == "TEXTFIELD"
    assert fields_by_name["pesel"].required is True
    assert fields_by_name["wojewodztwo"].options == ["Mazowieckie", "Pomorskie"]
    assert fields_by_name["zgoda_rodo"].field_type == "CHECKBOX"
    for f in fields_by_name.values():
        assert f.bbox is not None
        assert 0.0 <= f.bbox.x0 <= f.bbox.x1 <= 1.0
        assert 0.0 <= f.bbox.y0 <= f.bbox.y1 <= 1.0
    assert list(work_tmp.iterdir()) == []


def test_jpg_exif_orientation_and_png_rgba_normalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    _install_deterministic_backends(monkeypatch)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    settings = Settings(temp_dir=work_tmp)

    # JPG 320x180 z EXIF Orientation=6 -> po normalizacji 180x320.
    jpg_path, jpg_mime, jpg_fmt, _ = sample_files["jpg"]
    jpg_doc = extract_document(
        jpg_path.read_bytes(), filename=jpg_path.name, mime_type=jpg_mime, settings=settings
    )
    _assert_valid_extracted_document(jpg_doc, expected_format=jpg_fmt, expect_bboxes=True)
    assert jpg_doc.pages[0].image_width == 180
    assert jpg_doc.pages[0].image_height == 320
    assert jpg_doc.pages[0].image_dpi == 200
    assert len(jpg_doc.tables) == 1

    # PNG RGBA -> znormalizowany do RGB PNG bez kanału alfa.
    png_path, png_mime, png_fmt, _ = sample_files["png"]
    png_doc = extract_document(
        png_path.read_bytes(), filename=png_path.name, mime_type=png_mime, settings=settings
    )
    _assert_valid_extracted_document(png_doc, expected_format=png_fmt, expect_bboxes=True)
    assert png_doc.pages[0].image_png is not None
    with Image.open(io.BytesIO(png_doc.pages[0].image_png)) as normalized:
        assert normalized.mode == "RGB"
        assert normalized.size == (280, 160)
    assert list(work_tmp.iterdir()) == []


def test_ods_and_doc_preserve_polish_text_and_tables(
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    settings = Settings(temp_dir=work_tmp)

    ods_path, ods_mime, ods_fmt, _ = sample_files["ods"]
    ods_doc = extract_document(
        ods_path.read_bytes(), filename=ods_path.name, mime_type=ods_mime, settings=settings
    )
    _assert_valid_extracted_document(ods_doc, expected_format=ods_fmt, expected_pages=2)
    assert "Anna Gżegżółka" in ods_doc.full_text
    assert ods_doc.tables[0].rows[1] == ["Imię i nazwisko", "Anna Gżegżółka", "", "Uwagi"]
    assert ods_doc.tables[1].sheet_name == "Załączniki"

    doc_path, doc_mime, doc_fmt, _ = sample_files["doc"]
    legacy_doc = extract_document(
        doc_path.read_bytes(), filename=doc_path.name, mime_type=doc_mime, settings=settings
    )
    _assert_valid_extracted_document(legacy_doc, expected_format=doc_fmt, expected_pages=1)
    assert "Wniosek o udostępnienie informacji publicznej" in legacy_doc.full_text
    assert "Janina Łódzka-Żółć" in legacy_doc.full_text
    assert legacy_doc.tables[0].rows == [["PESEL", "90010112345"], ["Kod pocztowy", "00-950"]]
    assert list(work_tmp.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "payload"),
    [
        ("pusty.pdf", b""),
        ("uszkodzony.pdf", b"%PDF-1.7\n<<<uszkodzone-dane>>>"),
        ("uszkodzony.docx", b"PK\x03\x04uszkodzony-zip"),
        ("uszkodzony.xlsx", _make_zip({"tylko_smietnik.txt": b"x"})),
        ("uszkodzony.ods", b"niepoprawny-kontener-ods"),
        ("uszkodzony.doc", b"niepoprawny-naglowek-ole2"),
        ("uszkodzony.jpg", b"\xff\xd8\xff\x00uszkodzony-jpeg"),
        ("uszkodzony.jpeg", b"brak-naglowka-jpeg"),
        ("uszkodzony.png", b"\x89PNG\r\n\x1a\nuszkodzony-png"),
    ],
)
def test_corrupted_files_raise_domain_error_and_clean_temp_dir(
    tmp_path: Path,
    filename: str,
    payload: bytes,
) -> None:
    """Uszkodzone pliki rzucają CorruptedFileError i nie zostawiają plików tymczasowych."""
    broken_file = tmp_path / filename
    broken_file.write_bytes(payload)
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()

    with pytest.raises(CorruptedFileError):
        extract_document(
            broken_file.read_bytes(),
            filename=broken_file.name,
            settings=Settings(temp_dir=work_tmp),
        )

    assert list(work_tmp.iterdir()) == []


def test_encrypted_and_unsupported_files_raise_domain_errors_without_temp_leaks(
    tmp_path: Path,
) -> None:
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    settings = Settings(temp_dir=work_tmp)

    encrypted_cases = [
        ("haslo.pdf", b"%PDF-1.7\n1 0 obj << /Encrypt 2 0 R >> endobj\n%%EOF"),
        ("haslo.docx", _OLE_MAGIC + "EncryptedPackage".encode("utf-16-le")),
        ("haslo.doc", _build_doc(encrypted=True)),
        (
            "haslo.ods",
            _make_zip(
                {
                    "mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
                    "content.xml": b"<office:document-content/>",
                    "META-INF/manifest.xml": b"<manifest><encryption-data/></manifest>",
                }
            ),
        ),
    ]
    for fname, data in encrypted_cases:
        file_path = tmp_path / fname
        file_path.write_bytes(data)
        with pytest.raises(EncryptedFileError):
            extract_document(file_path.read_bytes(), filename=fname, settings=settings)
        assert list(work_tmp.iterdir()) == []

    unsupported_path = tmp_path / "skrypt.exe"
    unsupported_path.write_bytes(b"MZ\x90\x00")
    with pytest.raises(UnsupportedFileFormatError):
        extract_document(
            unsupported_path.read_bytes(), filename=unsupported_path.name, settings=settings
        )
    assert list(work_tmp.iterdir()) == []


def test_temp_dir_is_cleaned_when_docling_fails_mid_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_files: dict[str, tuple[Path, str, DocumentFormat, bool]],
) -> None:
    """Nawet przy wyjątku w trakcie ekstrakcji plik tymczasowy w TemporaryDirectory znika."""
    work_tmp = tmp_path / "work_tmp"
    work_tmp.mkdir()
    observed_temp_path: list[Path] = []

    def exploding_docling(
        path: Path, *, settings: Settings, fallback_page_sizes: Any = None
    ) -> Any:
        _ = settings, fallback_page_sizes
        observed_temp_path.append(path)
        assert path.exists()
        assert path.parent.parent == work_tmp
        raise ExtractionError("Symulowany błąd silnika Docling.", code="DOCLING_EXTRACTION_ERROR")

    monkeypatch.setattr(pdf_extractor, "convert_with_docling", exploding_docling)
    pdf_path, mime, _, _ = sample_files["pdf_text"]

    with pytest.raises(ExtractionError, match="Symulowany błąd silnika Docling"):
        extract_document(
            pdf_path.read_bytes(),
            filename=pdf_path.name,
            mime_type=mime,
            settings=Settings(temp_dir=work_tmp),
        )

    assert len(observed_temp_path) == 1
    assert not observed_temp_path[0].exists()
    assert list(work_tmp.iterdir()) == []
