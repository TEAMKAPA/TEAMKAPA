"""Układ współrzędnych: wartości oczekiwane wyliczone ręcznie (niezależnie od testowanego kodu)."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from backend.shared.schemas.quality import BoundingBox, Severity
from backend.worker.vision.coords import (
    EPS,
    bbox_as_list,
    check_rotation,
    clip_to_page,
    norm_to_px,
    norm_to_px_int,
    part_to_original,
    pdf_rect_to_norm,
    px_to_norm,
    rotate_norm,
    to_bbox,
)
from backend.worker.vision.errors import CoordinateError, IssueCode
from backend.worker.vision.models import (
    DocumentKind,
    DocumentMeta,
    DpiEstimateSource,
    DpiSource,
    PageGeometry,
    PageMeta,
    PageOrigin,
    PageSource,
    RenderedDocument,
    RenderedPage,
    RenderIssue,
)

A4 = (0.0, 0.0, 595.0, 842.0)


def approx(t: tuple[float, ...]) -> object:
    return pytest.approx(t, abs=1e-12)


# --- piksele ---------------------------------------------------------------------------------


def test_px_to_norm_uses_pixel_edges() -> None:
    # kolumny 10..109 i wiersze 20..219 na obrazie 200 x 400
    assert px_to_norm((10, 20, 110, 220), 200, 400) == approx((0.05, 0.05, 0.55, 0.55))


def test_px_to_norm_orders_corners() -> None:
    assert px_to_norm((110, 220, 10, 20), 200, 400) == approx((0.05, 0.05, 0.55, 0.55))


def test_px_norm_round_trip_is_resolution_independent() -> None:
    bbox = to_bbox(px_to_norm((10, 20, 110, 220), 200, 400))
    # ten sam prostokąt w 2x większej rozdzielczości
    assert norm_to_px(bbox, 400, 800) == approx((20.0, 40.0, 220.0, 440.0))


def test_norm_to_px_int_encloses_box() -> None:
    bbox = BoundingBox(x0=0.101, y0=0.2, x1=0.349, y1=0.5)
    # 100 px: 10.1 -> 10, 34.9 -> 35; 0.2*50 = 10, 0.5*50 = 25 (bez rozszerzania przez szum)
    assert norm_to_px_int(bbox, 100, 50) == (10, 10, 35, 25)


@pytest.mark.parametrize("size", [(0, 10), (10, 0), (-1, 10), (math.inf, 10)])
def test_invalid_page_size_raises(size: tuple[float, float]) -> None:
    with pytest.raises(CoordinateError):
        px_to_norm((0, 0, 1, 1), *size)


def test_nonfinite_coordinates_raise() -> None:
    with pytest.raises(CoordinateError):
        px_to_norm((0, 0, math.nan, 1), 10, 10)


# --- BoundingBox: ściśle i z przycięciem --------------------------------------------------------


def test_to_bbox_strict_rejects_outside_page() -> None:
    with pytest.raises(CoordinateError):
        to_bbox((-0.01, 0.0, 0.5, 0.5))
    with pytest.raises(CoordinateError):
        to_bbox((0.0, 0.0, 0.5, 1.01))


def test_to_bbox_tolerates_float_noise() -> None:
    bbox = to_bbox((-EPS / 2, 0.0, 1.0 + EPS / 2, 1.0))
    assert bbox_as_list(bbox) == [0.0, 0.0, 1.0, 1.0]


def test_clip_to_page_reports_clipping() -> None:
    bbox, clipped = clip_to_page((-0.2, 0.1, 0.5, 1.3))
    assert clipped is True
    assert bbox_as_list(bbox) == [0.0, 0.1, 0.5, 1.0]


def test_clip_to_page_without_clipping() -> None:
    bbox, clipped = clip_to_page((0.1, 0.2, 0.3, 0.4))
    assert clipped is False
    assert bbox_as_list(bbox) == [0.1, 0.2, 0.3, 0.4]


def test_clip_to_page_rejects_box_fully_outside() -> None:
    with pytest.raises(CoordinateError):
        clip_to_page((1.1, 0.0, 1.5, 0.5))


def test_bbox_as_list_order() -> None:
    assert bbox_as_list(BoundingBox(x0=0.1, y0=0.2, x1=0.3, y1=0.4)) == [0.1, 0.2, 0.3, 0.4]


# --- obroty ------------------------------------------------------------------------------------

BOX = (0.1, 0.2, 0.3, 0.5)


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (0.1, 0.2, 0.3, 0.5)),
        (90, (0.5, 0.1, 0.8, 0.3)),  # (u, v) -> (1 - v, u)
        (180, (0.7, 0.5, 0.9, 0.8)),  # (u, v) -> (1 - u, 1 - v)
        (270, (0.2, 0.7, 0.5, 0.9)),  # (u, v) -> (v, 1 - u)
    ],
)
def test_rotate_norm_clockwise(rotation: int, expected: tuple[float, ...]) -> None:
    assert rotate_norm(BOX, rotation) == pytest.approx(expected, abs=1e-12)


def test_rotations_compose() -> None:
    four = BOX
    for _ in range(4):
        four = rotate_norm(four, 90)
    assert four == pytest.approx(BOX, abs=1e-12)
    assert rotate_norm(rotate_norm(BOX, 90), 270) == pytest.approx(BOX, abs=1e-12)


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (-90, 270), (450, 90), (720, 0)])
def test_check_rotation_normalizes(value: int, expected: int) -> None:
    assert check_rotation(value) == expected


def test_check_rotation_rejects_non_right_angles() -> None:
    with pytest.raises(CoordinateError):
        check_rotation(45)


# --- PDF -> ułamki ------------------------------------------------------------------------------


def test_pdf_rect_top_left_origin() -> None:
    g = PageGeometry(mediabox=A4, cropbox=A4)
    # prostokąt przy górnej krawędzi: y od 742 do 842 pt
    assert pdf_rect_to_norm((59.5, 742.0, 119.0, 842.0), g) == approx(
        (0.1, 0.0, 0.2, 100.0 / 842.0)
    )


def test_pdf_rect_with_rotate_90() -> None:
    g = PageGeometry(mediabox=A4, cropbox=A4, rotation=90)
    # lewy górny róg strony nieobróconej trafia po obrocie w prawo do prawego górnego rogu
    assert pdf_rect_to_norm((59.5, 742.0, 119.0, 842.0), g) == approx(
        (1.0 - 100.0 / 842.0, 0.1, 1.0, 0.2)
    )
    assert g.displayed_size_pt == (842.0, 595.0)


def test_pdf_rect_with_offset_mediabox() -> None:
    g = PageGeometry(mediabox=(100.0, 200.0, 700.0, 1000.0), cropbox=(100.0, 200.0, 700.0, 1000.0))
    assert pdf_rect_to_norm((160.0, 920.0, 220.0, 1000.0), g) == approx((0.1, 0.0, 0.2, 0.1))


def test_pdf_rect_with_smaller_cropbox() -> None:
    g = PageGeometry(mediabox=(0.0, 0.0, 600.0, 800.0), cropbox=(100.0, 100.0, 500.0, 700.0))
    assert pdf_rect_to_norm((100.0, 600.0, 200.0, 700.0), g) == approx((0.0, 0.0, 0.25, 1 / 6))


def test_visible_box_is_intersection_of_crop_and_media() -> None:
    g = PageGeometry(mediabox=(0.0, 0.0, 600.0, 800.0), cropbox=(-50.0, 100.0, 650.0, 900.0))
    assert g.visible_box == (0.0, 100.0, 600.0, 800.0)


@pytest.mark.parametrize(
    "boxes",
    [
        ((0.0, 0.0, 0.0, 842.0), A4),  # pusta MediaBox
        (A4, (600.0, 0.0, 700.0, 842.0)),  # CropBox rozłączna z MediaBox
    ],
)
def test_invalid_geometry_raises(boxes: tuple[tuple[float, ...], tuple[float, ...]]) -> None:
    with pytest.raises(ValidationError):
        PageGeometry(mediabox=boxes[0], cropbox=boxes[1])


# --- strony wycięte ze zszytego obrazu ----------------------------------------------------------


def test_part_to_original() -> None:
    origin = PageOrigin(
        image_width_px=1654,
        image_height_px=4678,
        offset_y_px=2339,
        part_height_px=2339,
        part=2,
        parts=2,
    )
    assert part_to_original((0.1, 0.0, 0.2, 0.5), origin) == approx((0.1, 0.5, 0.2, 0.75))


def test_part_to_original_uneven_parts() -> None:
    # część 2 z 2 w obrazie o wysokości 201: wiersze 100..200 (101 px)
    origin = PageOrigin(
        image_width_px=100,
        image_height_px=201,
        offset_y_px=100,
        part_height_px=101,
        part=2,
        parts=2,
    )
    assert part_to_original((0.0, 0.0, 1.0, 1.0), origin) == approx((0.0, 100 / 201, 1.0, 1.0))


def test_page_origin_validation() -> None:
    def make(offset: int, height: int, part: int, parts: int) -> PageOrigin:
        return PageOrigin(
            image_width_px=10,
            image_height_px=10,
            offset_y_px=offset,
            part_height_px=height,
            part=part,
            parts=parts,
        )

    make(5, 5, 2, 2)
    with pytest.raises(ValidationError):
        make(0, 5, 3, 2)  # part > parts
    with pytest.raises(ValidationError):
        make(6, 5, 2, 2)  # część poza obrazem


# --- modele stron i dokumentu -------------------------------------------------------------------


def _meta(page: int = 1, w: int = 4, h: int = 3) -> PageMeta:
    return PageMeta(
        page=page, width_px=w, height_px=h, dpi=200, dpi_source=DpiSource.RENDER,
        source=PageSource.NATIVE_PDF, geometry=PageGeometry(mediabox=A4, cropbox=A4),
    )  # fmt: skip


def test_page_meta_dpi_consistency() -> None:
    with pytest.raises(ValidationError):
        PageMeta(page=1, width_px=1, height_px=1, dpi=None, dpi_source=DpiSource.RENDER,
                 source=PageSource.IMAGE)  # fmt: skip
    with pytest.raises(ValidationError):
        PageMeta(page=1, width_px=1, height_px=1, dpi=None, dpi_source=DpiSource.UNKNOWN,
                 dpi_estimate=200.0, source=PageSource.IMAGE)  # fmt: skip
    meta = PageMeta(page=1, width_px=1, height_px=1, dpi=None, dpi_source=DpiSource.UNKNOWN,
                    dpi_estimate=200.0, dpi_estimate_source=DpiEstimateSource.A4_WIDTH,
                    source=PageSource.IMAGE)  # fmt: skip
    assert meta.dpi is None


def test_page_meta_source_consistency() -> None:
    with pytest.raises(ValidationError):
        PageMeta(
            page=1,
            width_px=1,
            height_px=1,
            dpi=200,
            dpi_source=DpiSource.RENDER,
            source=PageSource.IMAGE,
            geometry=PageGeometry(mediabox=A4, cropbox=A4),
        )
    with pytest.raises(ValidationError):
        PageMeta(page=0, width_px=1, height_px=1, dpi=200, dpi_source=DpiSource.RENDER,
                 source=PageSource.NATIVE_PDF)  # fmt: skip


def test_page_meta_serializes_without_image() -> None:
    issue = RenderIssue(code=IssueCode.DPI_REDUCED, severity=Severity.WARNING, detail="200->150")
    meta = _meta().model_copy(update={"issues": (issue,)})
    dumped = meta.model_dump(mode="json")
    assert dumped["issues"][0]["code"] == "DPI_REDUCED"
    assert "image" not in dumped


def test_rendered_page_checks_image_shape() -> None:
    RenderedPage(meta=_meta(), image=np.zeros((3, 4, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="rozmiar"):
        RenderedPage(meta=_meta(), image=np.zeros((4, 3, 3), dtype=np.uint8))
    with pytest.raises(ValueError, match="RGB"):
        RenderedPage(meta=_meta(), image=np.zeros((3, 4), dtype=np.uint8))
    with pytest.raises(ValueError, match="RGB"):
        RenderedPage(meta=_meta(), image=np.zeros((3, 4, 3), dtype=np.float32))


def test_rendered_document_requires_consecutive_pages() -> None:
    doc_meta = DocumentMeta(
        sha256="0" * 64, declared_kind=None, detected_kind=DocumentKind.PDF, source_page_count=2
    )
    img = np.zeros((3, 4, 3), dtype=np.uint8)
    RenderedDocument(
        meta=doc_meta, pages=(RenderedPage(_meta(1), img), RenderedPage(_meta(2), img))
    )
    with pytest.raises(ValueError, match="kolejno"):
        RenderedDocument(meta=doc_meta, pages=(RenderedPage(_meta(2), img),))


def test_document_kind_groups() -> None:
    assert {k for k in DocumentKind if k.is_office} == {
        DocumentKind.DOC, DocumentKind.DOCX, DocumentKind.ODS, DocumentKind.XLSX,
    }  # fmt: skip
    assert {k for k in DocumentKind if k.is_image} == {DocumentKind.JPEG, DocumentKind.PNG}
