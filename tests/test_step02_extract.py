"""Testy jednostkowe kroku 02: PDF, obrazy, DOCX/XLSX (Docling), ODS (odfdo), DOC (olefile)."""

import io
import json
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import odfdo
import pytest
from PIL import Image

from backend.shared.config import Settings
from backend.shared.schemas.pipeline import (
    DocumentFormat,
    ExtractedDocument,
    ExtractedPage,
    ExtractedTable,
    ExtractedTextBlock,
    OcrWord,
)
from backend.shared.schemas.quality import BoundingBox, SourceChannel
from backend.worker.pipeline.extractors import (
    DoclingExtractionResult,
    DoclingPageContent,
    ExtractionContext,
    OcrResult,
    normalize_bbox,
    parse_docling_document,
    preprocess_image_for_ocr,
    temporary_document_file,
)
from backend.worker.pipeline.extractors import image as image_extractor
from backend.worker.pipeline.extractors import ocr as ocr_module
from backend.worker.pipeline.extractors import ooxml as ooxml_extractor
from backend.worker.pipeline.extractors import pdf as pdf_extractor
from backend.worker.pipeline.step02_text_extract import (
    CorruptedFileError,
    EncryptedFileError,
    FileTooLargeError,
    UnsupportedFileFormatError,
    extract_document,
)

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _build_ole2_cfb(streams: dict[str, bytes]) -> bytes:
    """Buduje poprawny binarny kontener OLE2 (CFB v3, sektory 512 B) czytelny przez olefile."""
    sector_size = 512
    # Wszystkie strumienie powyżej 4096 B trafiają do normalnych sektorów (bez mini-FAT).
    padded_streams: list[tuple[str, bytes, int]] = []
    for name, raw in streams.items():
        # Dopełnij strumień zerami powyżej progu mini-streamu (4096 B), zachowując dokładną długość
        # lub używając rozmiaru >= 4096 B dla bezproblemowego odczytu zwykłego łańcucha FAT.
        storage = raw if len(raw) >= 4096 else raw + b"\x00" * (4096 - len(raw))
        sector_count = (len(storage) + sector_size - 1) // sector_size
        padded = storage.ljust(sector_count * sector_size, b"\x00")
        padded_streams.append((name, padded, len(storage)))

    # Układ sektorów:
    # 0: FAT
    # 1: Directory (mieści do 4 wpisów po 128 B: Root + do 3 strumieni)
    # 2..: dane strumieni
    fat = [0xFFFFFFFF] * 128  # FREESECT
    fat[0] = 0xFFFFFFFD  # FATSECT
    fat[1] = 0xFFFFFFFE  # ENDOFCHAIN (Directory)

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

    # Nagłówek CFB (512 B)
    header = bytearray(sector_size)
    header[0:8] = _OLE_MAGIC
    struct.pack_into("<H", header, 24, 0x003E)  # MinorVersion
    struct.pack_into("<H", header, 26, 0x0003)  # MajorVersion (v3)
    struct.pack_into("<H", header, 28, 0xFFFE)  # ByteOrder (little-endian)
    struct.pack_into("<H", header, 30, 9)  # SectorShift (512 B)
    struct.pack_into("<H", header, 32, 6)  # MiniSectorShift (64 B)
    struct.pack_into("<I", header, 44, 1)  # Number of FAT sectors
    struct.pack_into("<I", header, 48, 1)  # First Directory sector
    struct.pack_into("<I", header, 56, 4096)  # MiniStreamCutoffSize
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)  # First MiniFAT sector (none)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)  # First DIFAT sector (none)
    # DIFAT[0..108]
    struct.pack_into("<I", header, 76, 0)  # FAT sector 0
    for idx in range(1, 109):
        struct.pack_into("<I", header, 76 + idx * 4, 0xFFFFFFFF)

    fat_sector = struct.pack("<128I", *fat)

    def _dir_entry(
        name: str,
        obj_type: int,
        start_sector: int,
        size: int,
        *,
        left: int = 0xFFFFFFFF,
        right: int = 0xFFFFFFFF,
        child: int = 0xFFFFFFFF,
    ) -> bytes:
        entry = bytearray(128)
        encoded_name = (name + "\x00").encode("utf-16-le")
        entry[0 : len(encoded_name)] = encoded_name
        struct.pack_into("<H", entry, 64, len(encoded_name))
        entry[66] = obj_type  # 5=Root, 2=Stream
        entry[67] = 1  # black
        struct.pack_into("<III", entry, 68, left, right, child)
        struct.pack_into("<II", entry, 116, start_sector, size)
        return bytes(entry)

    dir_sector = bytearray()
    # Wpis 0: Root Entry (dziecko = 1 jeśli są strumienie)
    root_child = 1 if stream_meta else 0xFFFFFFFF
    dir_sector.extend(_dir_entry("Root Entry", 5, 0xFFFFFFFE, 0, child=root_child))

    # Budowa prostego BST po nazwach (1..N)
    for idx, (name, start_sec, logical_size) in enumerate(stream_meta, start=1):
        right = idx + 1 if idx < len(stream_meta) else 0xFFFFFFFF
        dir_sector.extend(_dir_entry(name, 2, start_sec, logical_size, right=right))

    dir_sector.extend(b"\x00" * (sector_size - len(dir_sector)))
    return bytes(header) + fat_sector + bytes(dir_sector) + bytes(data_blob)


def _build_word97_doc(
    *,
    pieces: list[tuple[str, str]],
    encrypted: bool = False,
) -> bytes:
    """Tworzy binarny plik .doc (OLE2 + FIB + 1Table PlcPcd) z kawałkami UTF-16LE i cp1250."""
    # FIB o rozmiarze 0x0200 (512 B), z csw=14, cslw=22, cbRgFcLcb=93 -> fcClx na 0x01A2.
    fib = bytearray(0x0200)
    struct.pack_into("<H", fib, 0x00, 0xA5EC)  # wIdent (Word 97+)
    flags = 0x0200 | (0x0100 if encrypted else 0x0000)  # fWhichTblStm=1Table
    struct.pack_into("<H", fib, 0x0A, flags)
    struct.pack_into("<H", fib, 0x20, 14)  # csw
    struct.pack_into("<H", fib, 0x3E, 22)  # cslw
    struct.pack_into("<H", fib, 0x98, 93)  # cbRgFcLcb

    word_body = bytearray(fib)
    cps = [0]
    pcds = bytearray()

    for text, encoding in pieces:
        cps.append(cps[-1] + len(text))
        offset = len(word_body)
        if encoding == "cp1250":
            raw = text.encode("cp1250")
            word_body.extend(raw)
            fc = (offset * 2) | 0x40000000
        else:
            raw = text.encode("utf-16-le")
            word_body.extend(raw)
            fc = offset
        pcds.extend(struct.pack("<HIH", 0, fc, 0))

    struct.pack_into("<I", word_body, 0x4C, cps[-1])  # ccpText

    pcdt_payload = struct.pack(f"<{len(cps)}I", *cps) + bytes(pcds)
    clx = b"\x02" + struct.pack("<I", len(pcdt_payload)) + pcdt_payload

    struct.pack_into("<II", word_body, 0x01A2, 0, len(clx))
    return _build_ole2_cfb({"WordDocument": bytes(word_body), "1Table": clx})


def _build_ods_with_odfdo() -> bytes:
    """Tworzy wieloarkuszowy plik .ods w pamięci przez odfdo (z polskimi znakami i scaleniem)."""
    doc = odfdo.Document("spreadsheet")
    doc.body.clear()

    sheet1 = odfdo.Table("Dane wnioskodawcy")
    sheet1.set_value("A1", "Pole")
    sheet1.set_value("B1", "Wartość")
    sheet1.set_value("A2", "Imię i nazwisko")
    sheet1.set_value("B2", "Jan Żółć")
    sheet1.set_value("D2", "Uwagi")
    sheet1.set_span("A3:B3", merge=True)
    sheet1.set_value("A3", "Podpis wnioskodawcy")
    # Dodaj wielokrotnie powtórzony pusty wiersz końcowy (jak w LibreOffice Calc).
    empty_tail = odfdo.Row()
    empty_tail.repeated = 1_000_000
    sheet1.append_row(empty_tail)
    doc.body.append(sheet1)

    sheet2 = odfdo.Table("Załączniki")
    sheet2.set_value("A1", "Lp.")
    sheet2.set_value("B1", "Dokument")
    sheet2.set_value("A2", 1)
    sheet2.set_value("B2", "Zaświadczenie o zameldowaniu")
    doc.body.append(sheet2)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _build_pdf(
    *,
    text: str | None = "Wniosek o wydanie dowodu osobistego",
    with_acroform: bool = False,
) -> bytes:
    """Tworzy poprawny binarny plik PDF 1.4 czytelny dla pypdfium2."""
    objs: list[bytes] = []
    if with_acroform:
        objs.append(
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R "
            b"/AcroForm << /Fields [5 0 R 6 0 R] /DR << /Font << /Helv 4 0 R >> >> >> >>\nendobj\n"
        )
    else:
        objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    annots = b" /Annots [5 0 R 6 0 R]" if with_acroform else b""
    content_ref = b"7 0 R" if with_acroform else b"5 0 R"
    objs.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] "
        b"/Resources << /Font << /Helv 4 0 R >> >> /Contents " + content_ref + annots + b" >>\n"
        b"endobj\n"
    )
    objs.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    if with_acroform:
        objs.append(
            b"5 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Tx /T (pesel) "
            b"/TU (Numer PESEL) /V (90010112345) /Ff 2 /Rect [60 640 300 680] /P 3 0 R >>\nendobj\n"
        )
        objs.append(
            b"6 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Ch /T (wojewodztwo) "
            b"/TU (Wojewodztwo) /Ff 131072 /Opt [(Mazowieckie) (Malopolskie)] "
            b"/Rect [60 560 300 600] /P 3 0 R >>\nendobj\n"
        )

    stream = (
        f"BT /Helv 14 Tf 60 740 Td ({text}) Tj ET".encode("latin-1")
        if text
        else b"q 1 0 0 1 0 0 cm Q"
    )
    content_obj_no = 7 if with_acroform else 5
    objs.append(
        f"{content_obj_no} 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode()
        + stream
        + b"\nendstream\nendobj\n"
    )

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


def _encrypted_pdf_stub() -> bytes:
    return (
        b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n"
        b"2 0 obj << /Encrypt 3 0 R >> endobj\n%%EOF\n"
    )


def _make_image_bytes(
    fmt: str,
    size: tuple[int, int] = (200, 100),
    *,
    mode: str = "RGB",
    exif_orientation: int | None = None,
    dpi: tuple[int, int] | None = None,
) -> bytes:
    color: Any = (255, 255, 255, 128) if mode == "RGBA" else (255, 255, 255)
    img = Image.new(mode, size, color)
    buf = io.BytesIO()
    save_kwargs: dict[str, Any] = {}
    if dpi is not None:
        save_kwargs["dpi"] = dpi
    if exif_orientation is not None and fmt == "JPEG":
        exif = img.getexif()
        exif[274] = exif_orientation
        save_kwargs["exif"] = exif.tobytes()
    img.save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


def _minimal_docx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": b"<w:document/>",
        }
    )


def _minimal_xlsx() -> bytes:
    return _make_zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
        }
    )


def _encrypted_ods_stub() -> bytes:
    return _make_zip(
        {
            "mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
            "content.xml": b"<office:document-content/>",
            "META-INF/manifest.xml": b"<manifest><encryption-data/></manifest>",
        }
    )


def _stub_docling_empty(
    path: Path,
    *,
    settings: Settings,
    fallback_page_sizes: Any = None,
) -> DoclingExtractionResult:
    assert path.exists()
    _ = settings, fallback_page_sizes
    return DoclingExtractionResult()


def _stub_ocr_result(
    image_bytes: bytes,
    *,
    page_number: int,
    lang: str = "pol",
) -> OcrResult:
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert lang == "pol"
    word = OcrWord(
        text="Wniosek",
        confidence=0.94,
        bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.3, y1=0.15),
    )
    block = ExtractedTextBlock(
        text="Wniosek",
        page=page_number,
        bbox=word.bbox,
        channel=SourceChannel.OCR,
        confidence=0.94,
    )
    return OcrResult(text="Wniosek", words=[word], blocks=[block], mean_confidence=0.94)


def test_extracted_page_excludes_png_image_and_dimensions_from_json() -> None:
    page = ExtractedPage(
        page_number=1,
        width=600.0,
        height=800.0,
        text="Treść strony",
        image_png=b"\x89PNG\r\n\x1a\nfake",
        image_width=1667,
        image_height=2223,
        image_dpi=200,
    )
    assert page.image_png == b"\x89PNG\r\n\x1a\nfake"
    assert page.image_width == 1667
    assert page.image_height == 2223
    assert page.image_dpi == 200

    dumped = page.model_dump(mode="json")
    for excluded_key in ("image_png", "image_width", "image_height", "image_dpi"):
        assert excluded_key not in dumped

    parsed_json = json.loads(page.model_dump_json())
    assert "image_png" not in parsed_json
    assert "image_dpi" not in parsed_json


def test_extract_pdf_digital_with_acroform_docling_and_png(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docling_block = ExtractedTextBlock(
        text="Wniosek o wydanie dowodu osobistego",
        page=1,
        bbox=BoundingBox(x0=0.1, y0=0.05, x1=0.8, y1=0.1),
        channel=SourceChannel.TEXT,
        heading_level=1,
    )
    docling_table = ExtractedTable(
        page=1,
        rows=[["Pole", "Wartość"], ["PESEL", "90010112345"]],
        bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.9, y1=0.4),
    )

    def fake_docling(
        path: Path,
        *,
        settings: Settings,
        fallback_page_sizes: Any = None,
    ) -> DoclingExtractionResult:
        assert path.exists()
        assert fallback_page_sizes == {1: (600.0, 800.0)}
        return DoclingExtractionResult(
            pages={
                1: DoclingPageContent(
                    blocks=[docling_block],
                    tables=[docling_table],
                    width=600.0,
                    height=800.0,
                )
            },
            markdown="# Wniosek o wydanie dowodu osobistego",
        )

    monkeypatch.setattr(pdf_extractor, "convert_with_docling", fake_docling)
    settings = Settings(temp_dir=tmp_path, render_dpi=150)

    pdf_bytes = _build_pdf(text="Wniosek o wydanie dowodu osobistego", with_acroform=True)
    result = extract_document(
        pdf_bytes, filename="wniosek.pdf", mime_type="application/pdf", settings=settings
    )

    assert result.document_format is DocumentFormat.PDF
    assert result.page_count == 1
    assert result.markdown == "# Wniosek o wydanie dowodu osobistego"
    assert SourceChannel.TEXT in result.source_channels
    assert SourceChannel.ACROFORM in result.source_channels
    assert SourceChannel.OCR not in result.source_channels
    assert len(result.tables) == 1
    assert len(result.acroform_fields) == 2

    pesel_field = next(f for f in result.acroform_fields if f.name == "pesel")
    assert pesel_field.field_type == "TEXTFIELD"
    assert pesel_field.label == "Numer PESEL"
    assert pesel_field.value == "90010112345"
    assert pesel_field.required is True
    assert pesel_field.bbox is not None
    assert pesel_field.bbox.x0 == pytest.approx(0.1)
    assert pesel_field.bbox.x1 == pytest.approx(0.5)
    assert pesel_field.bbox.y0 == pytest.approx(0.15)
    assert pesel_field.bbox.y1 == pytest.approx(0.2)

    woj_field = next(f for f in result.acroform_fields if f.name == "wojewodztwo")
    assert woj_field.field_type == "COMBOBOX"
    assert woj_field.options == ["Mazowieckie", "Malopolskie"]

    page = result.pages[0]
    assert page.needs_ocr is False
    assert page.ocr_applied is False
    assert page.image_png is not None
    assert page.image_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert page.image_dpi == 150
    assert page.image_width == round(600 * 150 / 72)
    assert page.image_height == round(800 * 150 / 72)
    assert "image_png" not in page.model_dump(mode="json")
    assert list(tmp_path.iterdir()) == []


def test_extract_pdf_scanned_page_triggers_ocr_with_word_confidences(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pdf_extractor, "convert_with_docling", _stub_docling_empty)
    monkeypatch.setattr(pdf_extractor, "run_tesseract_ocr", _stub_ocr_result)

    scanned_pdf = _build_pdf(text=None, with_acroform=False)
    settings = Settings(temp_dir=tmp_path, render_dpi=200)
    result = extract_document(scanned_pdf, filename="skan.pdf", settings=settings)

    assert result.page_count == 1
    page = result.pages[0]
    assert page.needs_ocr is True
    assert page.ocr_applied is True
    assert page.ocr_confidence == pytest.approx(0.94)
    assert len(page.ocr_words) == 1
    assert page.ocr_words[0].text == "Wniosek"
    assert page.ocr_words[0].confidence == pytest.approx(0.94)
    assert page.source_channels == [SourceChannel.OCR]
    assert page.image_dpi == 200
    assert list(tmp_path.iterdir()) == []


def test_extract_image_normalizes_exif_orientation_and_merges_ocr_and_docling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    docling_table = ExtractedTable(
        page=1,
        rows=[["Imię", "Jan"]],
        bbox=BoundingBox(x0=0.05, y0=0.25, x1=0.95, y1=0.55),
    )

    def fake_docling(
        path: Path,
        *,
        settings: Settings,
        fallback_page_sizes: Any = None,
    ) -> DoclingExtractionResult:
        assert path.exists()
        assert fallback_page_sizes == {1: (120.0, 240.0)}
        return DoclingExtractionResult(
            pages={
                1: DoclingPageContent(
                    tables=[docling_table],
                    width=120.0,
                    height=240.0,
                )
            },
            markdown="| Imię | Jan |",
        )

    monkeypatch.setattr(image_extractor, "convert_with_docling", fake_docling)
    monkeypatch.setattr(image_extractor, "run_tesseract_ocr", _stub_ocr_result)

    rotated_jpg = _make_image_bytes("JPEG", size=(240, 120), exif_orientation=6, dpi=(300, 300))
    settings = Settings(temp_dir=tmp_path)
    result = extract_document(
        io.BytesIO(rotated_jpg),
        filename="zdjecie.jpg",
        mime_type="image/jpeg",
        settings=settings,
    )

    assert result.document_format is DocumentFormat.IMAGE
    assert result.page_count == 1
    page = result.pages[0]
    assert page.width == 120.0
    assert page.height == 240.0
    assert page.image_width == 120
    assert page.image_height == 240
    assert page.image_dpi == 300
    assert page.image_png is not None
    assert page.image_png.startswith(b"\x89PNG\r\n\x1a\n")
    assert page.ocr_applied is True
    assert page.ocr_confidence == pytest.approx(0.94)
    assert len(page.ocr_words) == 1
    assert len(page.tables) == 1
    assert SourceChannel.OCR in page.source_channels
    assert SourceChannel.TEXT in page.source_channels
    assert list(tmp_path.iterdir()) == []


def test_extract_image_rgba_png_converts_cleanly_to_rgb_png(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(image_extractor, "convert_with_docling", _stub_docling_empty)
    monkeypatch.setattr(image_extractor, "run_tesseract_ocr", _stub_ocr_result)

    rgba_png = _make_image_bytes("PNG", size=(160, 90), mode="RGBA")
    result = extract_document(
        rgba_png,
        filename="formularz.png",
        mime_type="image/png",
        settings=Settings(temp_dir=tmp_path),
    )
    page = result.pages[0]
    assert page.image_png is not None
    with Image.open(io.BytesIO(page.image_png)) as normalized:
        assert normalized.mode == "RGB"
        assert normalized.size == (160, 90)


def test_extract_docx_and_xlsx_map_docling_headings_text_and_tables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_docling_docx(path: Path, *, settings: Settings) -> DoclingExtractionResult:
        assert path.suffix == ".docx"
        assert path.exists()
        _ = settings
        return DoclingExtractionResult(
            pages={
                1: DoclingPageContent(
                    blocks=[
                        ExtractedTextBlock(
                            text="Wniosek o zaświadczenie",
                            page=1,
                            channel=SourceChannel.TEXT,
                            heading_level=1,
                        ),
                        ExtractedTextBlock(
                            text="Proszę o wydanie zaświadczenia o niezaleganiu.",
                            page=1,
                            channel=SourceChannel.TEXT,
                        ),
                    ],
                    tables=[
                        ExtractedTable(
                            page=1,
                            rows=[["Pole", "Opis"], ["NIP", "10 cyfr"]],
                        )
                    ],
                )
            },
            markdown="# Wniosek o zaświadczenie\n\nProszę o wydanie zaświadczenia.",
        )

    monkeypatch.setattr(ooxml_extractor, "convert_with_docling", fake_docling_docx)
    docx_res = extract_document(
        _minimal_docx(),
        filename="wniosek.docx",
        settings=Settings(temp_dir=tmp_path),
    )
    assert docx_res.document_format is DocumentFormat.DOCX
    assert docx_res.page_count == 1
    assert docx_res.pages[0].blocks[0].heading_level == 1
    assert "Wniosek o zaświadczenie" in docx_res.full_text
    assert len(docx_res.tables) == 1
    assert docx_res.tables[0].rows == [["Pole", "Opis"], ["NIP", "10 cyfr"]]
    assert docx_res.source_channels == [SourceChannel.TEXT]
    assert docx_res.metadata["headings_count"] == "1"
    assert list(tmp_path.iterdir()) == []

    def fake_docling_xlsx(path: Path, *, settings: Settings) -> DoclingExtractionResult:
        assert path.suffix == ".xlsx"
        assert path.exists()
        _ = settings
        return DoclingExtractionResult(
            pages={
                1: DoclingPageContent(
                    tables=[
                        ExtractedTable(
                            page=1,
                            sheet_name="Dane",
                            rows=[["Lp.", "Pole"], ["1", "PESEL"]],
                        )
                    ]
                ),
                2: DoclingPageContent(
                    tables=[
                        ExtractedTable(
                            page=2,
                            sheet_name="Załączniki",
                            rows=[["Kod", "Nazwa"], ["Z1", "Pełnomocnictwo"]],
                        )
                    ]
                ),
            },
            markdown="",
        )

    monkeypatch.setattr(ooxml_extractor, "convert_with_docling", fake_docling_xlsx)
    xlsx_res = extract_document(
        _minimal_xlsx(),
        filename="formularz.xlsx",
        settings=Settings(temp_dir=tmp_path),
    )
    assert xlsx_res.document_format is DocumentFormat.XLSX
    assert xlsx_res.page_count == 2
    assert len(xlsx_res.tables) == 2
    assert xlsx_res.tables[0].sheet_name == "Dane"
    assert xlsx_res.tables[1].sheet_name == "Załączniki"
    assert "Pełnomocnictwo" in xlsx_res.full_text
    assert "## Załączniki" in xlsx_res.markdown
    assert list(tmp_path.iterdir()) == []


def test_extract_ods_parses_all_sheets_grid_and_merged_spans_with_odfdo(tmp_path: Path) -> None:
    ods_bytes = _build_ods_with_odfdo()
    result = extract_document(
        io.BytesIO(ods_bytes),
        filename="budzet.ods",
        mime_type="application/vnd.oasis.opendocument.spreadsheet",
        settings=Settings(temp_dir=tmp_path),
    )

    assert result.document_format is DocumentFormat.ODS
    assert result.page_count == 2
    assert result.source_channels == [SourceChannel.TEXT]
    assert len(result.tables) == 2
    assert result.metadata["extractor"] == "odfdo"
    assert result.metadata["sheet_count"] == "2"

    t1 = result.tables[0]
    assert t1.sheet_name == "Dane wnioskodawcy"
    # Pełna prostokątna siatka 3x4 (bez materializacji 1_000_000 pustych wierszy końcowych).
    assert t1.rows == [
        ["Pole", "Wartość", "", ""],
        ["Imię i nazwisko", "Jan Żółć", "", "Uwagi"],
        ["Podpis wnioskodawcy", "", "", ""],
    ]
    merged_cell = next(c for c in t1.cells if c.text == "Podpis wnioskodawcy")
    assert merged_cell.row == 2
    assert merged_cell.col == 0
    assert merged_cell.col_span == 2

    t2 = result.tables[1]
    assert t2.sheet_name == "Załączniki"
    assert t2.rows == [
        ["Lp.", "Dokument"],
        ["1", "Zaświadczenie o zameldowaniu"],
    ]
    assert "Jan Żółć" in result.full_text
    assert "## Dane wnioskodawcy" in result.markdown
    assert "## Załączniki" in result.markdown
    assert list(tmp_path.iterdir()) == []


def test_extract_doc_reads_utf16le_cp1250_and_tables_via_olefile(tmp_path: Path) -> None:
    doc_bytes = _build_word97_doc(
        pieces=[
            ("Wniosek o wydanie zaświadczenia\r", "utf-16-le"),
            ("Imię i nazwisko: Łódź / Gżegżółka\r", "cp1250"),
            ("PESEL\x0790010112345\x07\x07\r", "utf-16-le"),
            ("Kod pocztowy\x0700-950\x07\x07\r", "cp1250"),
        ]
    )

    result = extract_document(
        io.BytesIO(doc_bytes),
        filename="stary_formularz.doc",
        mime_type="application/msword",
        settings=Settings(temp_dir=tmp_path),
    )

    assert result.document_format is DocumentFormat.DOC
    assert result.page_count == 1
    assert result.source_channels == [SourceChannel.TEXT]
    assert result.metadata["extractor"] == "olefile"
    assert result.metadata["decode_strategy"] == "piece_table"
    assert "Wniosek o wydanie zaświadczenia" in result.full_text
    assert "Imię i nazwisko: Łódź / Gżegżółka" in result.full_text
    assert len(result.tables) == 1
    assert result.tables[0].rows == [
        ["PESEL", "90010112345"],
        ["Kod pocztowy", "00-950"],
    ]
    assert list(tmp_path.iterdir()) == []


def test_extract_doc_fallback_stream_decoding_without_table_stream() -> None:
    raw_utf16 = "Formularz urzędowy bez strumienia 1Table".encode("utf-16-le")
    ole_doc = _build_ole2_cfb({"WordDocument": b"\x00" * 16 + raw_utf16})

    result = extract_document(ole_doc, filename="legacy.doc")
    assert result.document_format is DocumentFormat.DOC
    assert "Formularz urzędowy bez strumienia 1Table" in result.full_text
    assert result.metadata["decode_strategy"] == "utf-16-le"
    assert result.warnings


def test_ocr_preprocessing_deskews_tilted_scan_and_parses_word_confidences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canvas = np.full((300, 500), 255, dtype=np.uint8)
    for y in (70, 120, 170, 220):
        cv2.rectangle(canvas, (60, y), (440, y + 10), 0, -1)
    rot_m = cv2.getRotationMatrix2D((250.0, 150.0), 6.0, 1.0)
    tilted = cv2.warpAffine(canvas, rot_m, (500, 300), borderValue=255)
    ok, encoded = cv2.imencode(".png", tilted)
    assert ok

    processed, width, height = preprocess_image_for_ocr(encoded.tobytes())
    assert (width, height) == (500, 300)
    assert processed.ndim == 2
    assert set(np.unique(processed)).issubset({0, 255})
    assert abs(ocr_module.estimate_skew_angle(processed)) < 1.0

    def fake_image_to_data(*_args: Any, **kwargs: Any) -> dict[str, list[Any]]:
        assert kwargs["lang"] == "pol"
        return {
            "text": ["", "Imię", "i", "nazwisko", ""],
            "conf": ["-1", "92", "88", "95", "-1"],
            "left": [0, 50, 120, 150, 0],
            "top": [0, 30, 30, 30, 0],
            "width": [0, 60, 20, 100, 0],
            "height": [0, 30, 30, 30, 0],
            "block_num": [0, 1, 1, 1, 0],
            "par_num": [0, 1, 1, 1, 0],
            "line_num": [0, 1, 1, 1, 0],
        }

    monkeypatch.setattr(ocr_module.pytesseract, "image_to_data", fake_image_to_data)
    ocr_res = ocr_module.run_tesseract_ocr(encoded.tobytes(), page_number=2, lang="pol")
    assert ocr_res.text == "Imię i nazwisko"
    assert len(ocr_res.words) == 3
    assert [w.text for w in ocr_res.words] == ["Imię", "i", "nazwisko"]
    assert ocr_res.mean_confidence == pytest.approx((0.92 + 0.88 + 0.95) / 3)
    assert len(ocr_res.blocks) == 1
    blk = ocr_res.blocks[0]
    assert blk.page == 2
    assert blk.channel is SourceChannel.OCR
    assert blk.bbox is not None
    assert blk.bbox.x0 == pytest.approx(50 / 500)
    assert blk.bbox.x1 == pytest.approx(250 / 500)
    assert blk.bbox.y0 == pytest.approx(30 / 300)
    assert blk.bbox.y1 == pytest.approx(60 / 300)


def test_docling_adapter_normalizes_bottom_left_and_top_left_bboxes() -> None:
    bl_box = normalize_bbox(
        60.0,
        800.0,
        540.0,
        720.0,
        page_width=600.0,
        page_height=800.0,
        coord_origin="BOTTOMLEFT",
    )
    assert bl_box is not None
    assert bl_box.x0 == pytest.approx(0.1)
    assert bl_box.x1 == pytest.approx(0.9)
    assert bl_box.y0 == pytest.approx(0.0)
    assert bl_box.y1 == pytest.approx(0.1)

    tl_box = normalize_bbox(
        100.0,
        200.0,
        300.0,
        400.0,
        page_width=1000.0,
        page_height=2000.0,
        coord_origin="TOPLEFT",
    )
    assert tl_box is not None
    assert tl_box.x0 == pytest.approx(0.1)
    assert tl_box.y0 == pytest.approx(0.1)
    assert tl_box.x1 == pytest.approx(0.3)
    assert tl_box.y1 == pytest.approx(0.2)


def test_parse_docling_document_extracts_blocks_and_tables() -> None:
    @dataclass
    class _BBox:
        l: float  # noqa: E741
        t: float
        r: float
        b: float
        coord_origin: str = "BOTTOMLEFT"

    @dataclass
    class _Prov:
        page_no: int
        bbox: _BBox

    @dataclass
    class _Size:
        width: float
        height: float

    @dataclass
    class _Page:
        size: _Size

    @dataclass
    class _Header:
        text: str
        level: int
        label: str
        prov: list[_Prov]

    @dataclass
    class _Cell:
        start_row_offset_idx: int
        end_row_offset_idx: int
        start_col_offset_idx: int
        end_col_offset_idx: int
        text: str

    @dataclass
    class _TableData:
        num_rows: int
        num_cols: int
        table_cells: list[_Cell] = field(default_factory=list)

    @dataclass
    class _TableItem:
        data: _TableData
        prov: list[_Prov]

    class _FakeDoc:
        def __init__(self) -> None:
            self.pages = {1: _Page(size=_Size(width=500.0, height=1000.0))}

        def iterate_items(self) -> list[tuple[Any, int]]:
            return [
                (
                    _Header(
                        text="Dane wnioskodawcy",
                        level=2,
                        label="section_header",
                        prov=[_Prov(1, _BBox(50.0, 950.0, 450.0, 900.0))],
                    ),
                    1,
                ),
                (
                    _TableItem(
                        data=_TableData(
                            num_rows=1,
                            num_cols=2,
                            table_cells=[
                                _Cell(0, 1, 0, 1, "PESEL"),
                                _Cell(0, 1, 1, 2, "12345678901"),
                            ],
                        ),
                        prov=[_Prov(1, _BBox(50.0, 800.0, 450.0, 700.0))],
                    ),
                    1,
                ),
            ]

        def export_to_markdown(self) -> str:
            return "## Dane wnioskodawcy"

    parsed = parse_docling_document(_FakeDoc())
    assert parsed.markdown == "## Dane wnioskodawcy"
    p1 = parsed.pages[1]
    assert len(p1.blocks) == 1
    assert p1.blocks[0].heading_level == 2
    assert p1.blocks[0].bbox == BoundingBox(x0=0.1, y0=0.05, x1=0.9, y1=0.1)
    assert len(p1.tables) == 1
    assert p1.tables[0].rows == [["PESEL", "12345678901"]]
    assert p1.tables[0].bbox == BoundingBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3)


@pytest.mark.parametrize(
    ("filename", "mime_type", "payload", "expected_format"),
    [
        ("wniosek.pdf", "application/pdf", _build_pdf(), DocumentFormat.PDF),
        ("skan.jpg", "image/jpeg", _make_image_bytes("JPEG"), DocumentFormat.IMAGE),
        ("skan.jpeg", "image/jpeg", _make_image_bytes("JPEG"), DocumentFormat.IMAGE),
        ("skan.png", "image/png", _make_image_bytes("PNG"), DocumentFormat.IMAGE),
        (
            "form.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _minimal_docx(),
            DocumentFormat.DOCX,
        ),
        ("form.docx", "application/zip", _minimal_docx(), DocumentFormat.DOCX),
        (
            "tabela.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _minimal_xlsx(),
            DocumentFormat.XLSX,
        ),
        (
            "arkusz.ods",
            "application/vnd.oasis.opendocument.spreadsheet",
            _build_ods_with_odfdo(),
            DocumentFormat.ODS,
        ),
        (
            "stary.doc",
            "application/msword",
            _build_word97_doc(pieces=[("Tekst testowy", "cp1250")]),
            DocumentFormat.DOC,
        ),
    ],
)
def test_extract_document_routes_supported_formats(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    filename: str,
    mime_type: str,
    payload: bytes,
    expected_format: DocumentFormat,
) -> None:
    monkeypatch.setattr(pdf_extractor, "convert_with_docling", _stub_docling_empty)
    monkeypatch.setattr(image_extractor, "convert_with_docling", _stub_docling_empty)
    monkeypatch.setattr(ooxml_extractor, "convert_with_docling", _stub_docling_empty)
    monkeypatch.setattr(image_extractor, "run_tesseract_ocr", _stub_ocr_result)
    settings = Settings(temp_dir=tmp_path)

    result_bytes = extract_document(
        payload, filename=filename, mime_type=mime_type, settings=settings
    )
    result_stream = extract_document(
        io.BytesIO(payload), filename=filename, mime_type=mime_type, settings=settings
    )

    assert isinstance(result_bytes, ExtractedDocument)
    assert result_bytes.document_format is expected_format
    assert result_bytes.document_sha256 == result_stream.document_sha256
    assert list(tmp_path.iterdir()) == []


def test_extract_document_resolves_from_mime_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(pdf_extractor, "convert_with_docling", _stub_docling_empty)
    settings = Settings(temp_dir=tmp_path)
    result = extract_document(_build_pdf(), mime_type="application/pdf", settings=settings)
    assert result.document_format is DocumentFormat.PDF
    assert result.extension == "pdf"


def test_extract_document_rejects_unsupported_and_mismatched_formats() -> None:
    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello", filename="notes.txt")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello", mime_type="text/plain")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(b"hello")

    with pytest.raises(UnsupportedFileFormatError):
        extract_document(_build_pdf(), filename="form.pdf", mime_type="image/png")


def test_extract_document_detects_corrupted_files() -> None:
    with pytest.raises(CorruptedFileError):
        extract_document(b"", filename="empty.pdf")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a pdf", filename="broken.pdf")

    with pytest.raises(CorruptedFileError):
        extract_document(b"%PDF-1.7\ngarbage", filename="corrupt.pdf")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a png", filename="broken.png")

    with pytest.raises(CorruptedFileError):
        extract_document(b"\x89PNG\r\n\x1a\ncorrupt", filename="truncated.png")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a zip", filename="broken.docx")

    with pytest.raises(CorruptedFileError):
        extract_document(_make_zip({"other.txt": b"x"}), filename="broken.xlsx")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not an ods", filename="broken.ods")

    with pytest.raises(CorruptedFileError):
        extract_document(b"not a doc", filename="broken.doc")

    ole_without_word = _build_ole2_cfb({"OtherStream": b"hello"})
    with pytest.raises(CorruptedFileError):
        extract_document(ole_without_word, filename="missing_word.doc")


def test_extract_document_detects_encrypted_files() -> None:
    with pytest.raises(EncryptedFileError):
        extract_document(_encrypted_pdf_stub(), filename="locked.pdf")

    encrypted_ooxml = _OLE_MAGIC + "EncryptedPackage".encode("utf-16-le")
    with pytest.raises(EncryptedFileError):
        extract_document(encrypted_ooxml, filename="locked.docx")

    with pytest.raises(EncryptedFileError):
        extract_document(_encrypted_ods_stub(), filename="locked.ods")

    encrypted_doc_fib = _build_word97_doc(pieces=[("Tajny tekst", "cp1250")], encrypted=True)
    with pytest.raises(EncryptedFileError):
        extract_document(encrypted_doc_fib, filename="locked_fib.doc")

    encrypted_doc_pkg = _build_ole2_cfb({"EncryptedPackage": b"cipher", "EncryptionInfo": b"info"})
    with pytest.raises(EncryptedFileError):
        extract_document(encrypted_doc_pkg, filename="locked_pkg.doc")


def test_extract_document_enforces_size_limit() -> None:
    settings = Settings(max_file_size_mb=1)
    oversized = _build_pdf() + (b"A" * (1024 * 1024 + 1))
    with pytest.raises(FileTooLargeError):
        extract_document(oversized, filename="huge.pdf", settings=settings)


def test_temporary_document_file_cleans_up_on_success_and_error(tmp_path: Path) -> None:
    created_path: Path | None = None
    with temporary_document_file(b"abc", extension="pdf", temp_dir=tmp_path) as path:
        created_path = path
        assert path.exists()
        assert path.read_bytes() == b"abc"
        assert path.parent.parent == tmp_path
    assert created_path is not None
    assert not created_path.exists()
    assert list(tmp_path.iterdir()) == []

    failed_path: Path | None = None
    with (
        pytest.raises(RuntimeError, match="boom"),
        temporary_document_file(b"xyz", extension="docx", temp_dir=tmp_path) as path,
    ):
        failed_path = path
        assert path.exists()
        raise RuntimeError("boom")
    assert failed_path is not None
    assert not failed_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_custom_handler_error_leaves_no_temp_files(tmp_path: Path) -> None:
    settings = Settings(temp_dir=tmp_path)

    def failing_handler(ctx: ExtractionContext) -> ExtractedDocument:
        with temporary_document_file(
            ctx.content, extension=ctx.extension, temp_dir=ctx.settings.temp_dir
        ) as doc_path:
            assert doc_path.exists()
            raise CorruptedFileError("Uszkodzony plik podczas parsowania.")

    with pytest.raises(CorruptedFileError):
        extract_document(
            _build_pdf(),
            filename="fail.pdf",
            settings=settings,
            handlers={DocumentFormat.PDF: failing_handler},
        )
    assert list(tmp_path.iterdir()) == []
