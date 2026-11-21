"""Obrazy JPEG/PNG: cięcie zszytych stron A4, EXIF, tryby, DPI, limity (dane syntetyczne)."""

import hashlib
import io

import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from backend.shared.schemas.quality import Severity
from backend.worker.vision.config import RenderConfig
from backend.worker.vision.coords import part_to_original, px_to_norm
from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.models import (
    DocumentKind,
    DpiEstimateSource,
    DpiSource,
    PageSource,
    RenderIssue,
)
from backend.worker.vision.render.image import (
    A4_SHORT_SIDE_IN,
    ImageRenderResult,
    plan_split,
    render_image,
)
from tests.vision.synth import (
    a4_height,
    encode,
    fill,
    png_claiming_size,
    stacked_pages,
    stored_for_orientation,
    white,
)

CFG = RenderConfig()
PNG, JPEG = DocumentKind.PNG, DocumentKind.JPEG


def _render(data: bytes, kind: DocumentKind = PNG, cfg: RenderConfig = CFG) -> ImageRenderResult:
    return render_image(data, kind, cfg, isolated=False)


def _codes(issues: tuple[RenderIssue, ...]) -> list[str]:
    return [i.code.value for i in issues]


def _bbox_of(image: npt.NDArray[np.uint8], color: tuple[int, int, int]) -> tuple[float, ...]:
    ys, xs = np.nonzero(np.all(image == np.array(color, dtype=np.uint8), axis=2))
    assert xs.size, f"brak koloru {color}"
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


# --- cięcie zszytych stron A4 -------------------------------------------------------------------


@pytest.mark.parametrize("parts", [2, 6, 16])
def test_stacked_pages_are_split(parts: int) -> None:
    width = 200
    image, _ = stacked_pages(width, parts)
    result = _render(encode(image))
    assert result.info.page_count == parts == len(result.pages)
    page_h = a4_height(width)
    for i, page in enumerate(result.pages):
        meta = page.meta
        assert (meta.page, meta.width_px, meta.height_px) == (i + 1, width, page_h)
        assert meta.origin is not None
        assert (meta.origin.offset_y_px, meta.origin.part, meta.origin.parts) == (
            i * page_h,
            i + 1,
            parts,
        )
        assert meta.source is PageSource.IMAGE and meta.geometry is None
        assert "IMAGE_SPLIT_A4" in _codes(meta.issues)
        # piksele strony to dokładnie odpowiedni fragment oryginału
        assert np.array_equal(page.image, image[i * page_h : (i + 1) * page_h])


def test_split_bbox_maps_back_to_original_image() -> None:
    image, rects = stacked_pages(200, 6)
    pages = _render(encode(image)).pages
    h, w = image.shape[:2]
    for page, rect in zip(pages, rects, strict=True):
        color = tuple(int(v) for v in image[rect[1], rect[0]])
        measured = _bbox_of(page.image, (color[0], color[1], color[2]))
        norm = px_to_norm(measured, page.meta.width_px, page.meta.height_px)
        assert page.meta.origin is not None
        in_original = part_to_original(norm, page.meta.origin)
        assert in_original == pytest.approx(px_to_norm(rect, w, h), abs=1e-12)


def test_ink_across_seam_prevents_split() -> None:
    image, _ = stacked_pages(200, 2)
    seam = a4_height(200)
    fill(image, (50, seam - 3, 60, seam + 3), (0, 0, 0))  # kreska przechodząca przez granicę
    result = _render(encode(image))
    [page] = result.pages
    assert page.meta.origin is None
    [issue] = [i for i in page.meta.issues if i.code is IssueCode.TALL_IMAGE_UNSPLIT]
    assert issue.severity is Severity.WARNING and issue.detail == f"ink at seam y={seam}"


def test_ink_just_outside_margin_still_splits() -> None:
    image, _ = stacked_pages(200, 2)
    seam = a4_height(200)
    margin = CFG.split_seam_margin_px
    fill(image, (50, seam - margin - 2, 60, seam - margin), (0, 0, 0))
    assert len(_render(encode(image)).pages) == 2


def test_height_not_multiple_of_a4_is_not_split() -> None:
    image, _ = stacked_pages(200, 2, extra_rows=10)
    [page] = _render(encode(image)).pages
    assert "TALL_IMAGE_UNSPLIT" in _codes(page.meta.issues)


def test_height_within_tolerance_is_split_evenly() -> None:
    image, _ = stacked_pages(200, 2, extra_rows=1)  # 2 * 283 + 1 = 567 (tolerancja 2 px)
    pages = _render(encode(image)).pages
    heights = [p.meta.height_px for p in pages]
    assert sum(heights) == image.shape[0] and max(heights) - min(heights) <= 1
    origins = [p.meta.origin for p in pages]
    assert origins[1] is not None and origins[1].offset_y_px == heights[0]


@pytest.mark.parametrize(
    ("size", "reason"),
    [((300, 200), "not portrait"), ((200, 283), "ratio=1.415"), ((200, 400), "ratio=2.000")],
)
def test_plan_split_reasons(size: tuple[int, int], reason: str) -> None:
    bounds, why = plan_split(white(*size), CFG)
    assert bounds is None and why == reason


def test_single_a4_page_is_not_marked_tall() -> None:
    [page] = _render(encode(white(200, 283))).pages
    codes = _codes(page.meta.issues)
    assert "TALL_IMAGE_UNSPLIT" not in codes and "IMAGE_SPLIT_A4" not in codes


# --- DPI ---------------------------------------------------------------------------------------


def test_dpi_from_metadata() -> None:
    gray = Image.fromarray(white(130, 180)[:, :, 0], mode="L")
    [page] = _render(encode(gray, dpi=(130, 130))).pages
    assert (page.meta.dpi, page.meta.dpi_source) == (130.0, DpiSource.METADATA)
    assert page.meta.dpi_estimate is None and "DPI_UNKNOWN" not in _codes(page.meta.issues)


def test_dpi_estimate_for_a4_without_metadata() -> None:
    [page] = _render(encode(white(1654, 2339))).pages
    assert page.meta.dpi is None and page.meta.dpi_source is DpiSource.UNKNOWN
    assert page.meta.dpi_estimate == pytest.approx(1654 / A4_SHORT_SIDE_IN)  # ok. 200 DPI
    assert page.meta.dpi_estimate == pytest.approx(200.0, abs=0.1)
    assert page.meta.dpi_estimate_source is DpiEstimateSource.A4_WIDTH
    [issue] = [i for i in page.meta.issues if i.code is IssueCode.DPI_UNKNOWN]
    assert issue.severity is Severity.INFO and issue.detail == "no dpi metadata"


def test_no_estimate_for_non_a4_without_metadata() -> None:
    [page] = _render(encode(white(300, 300))).pages
    assert page.meta.dpi is None and page.meta.dpi_estimate is None
    [issue] = [i for i in page.meta.issues if i.code is IssueCode.DPI_UNKNOWN]
    assert issue.severity is Severity.WARNING


def test_inconsistent_dpi_metadata_is_rejected() -> None:
    [page] = _render(encode(white(300, 300), dpi=(300, 72))).pages
    assert page.meta.dpi is None
    assert [i.detail for i in page.meta.issues] == ["invalid dpi metadata"]


# --- orientacja EXIF ----------------------------------------------------------------------------

UPRIGHT = white(60, 90)
fill(UPRIGHT, (5, 5, 25, 15), (255, 0, 0))  # lewy górny róg
fill(UPRIGHT, (40, 70, 55, 85), (0, 0, 255))  # prawy dolny róg


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_orientation_gives_upright_page(orientation: int) -> None:
    data = encode(stored_for_orientation(UPRIGHT, orientation), exif_orientation=orientation)
    [page] = _render(data).pages
    assert np.array_equal(page.image, UPRIGHT)
    assert page.meta.exif_orientation == orientation
    assert (
        page.meta.rotation
        == {1: 0, 2: 0, 3: 180, 4: 180, 5: 90, 6: 90, 7: 270, 8: 270}[orientation]
    )


def test_exif_orientation_jpeg_bbox() -> None:
    data = encode(stored_for_orientation(UPRIGHT, 6), "JPEG", exif_orientation=6, quality=95)
    [page] = _render(data, JPEG).pages
    red = np.nonzero((page.image[:, :, 0] > 200) & (page.image[:, :, 2] < 80))
    ys, xs = red
    measured = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    assert max(abs(a - b) for a, b in zip(measured, (5, 5, 25, 15), strict=True)) <= 1


# --- tryby kolorów -----------------------------------------------------------------------------


def test_rgba_transparency_on_white() -> None:
    rgba = np.zeros((10, 10, 4), dtype=np.uint8)
    rgba[:5, :, :] = (0, 0, 0, 255)  # górna połowa czarna, dolna w pełni przezroczysta
    [page] = _render(encode(Image.fromarray(rgba, mode="RGBA"))).pages
    assert int(page.image[:5].max()) == 0 and int(page.image[5:].min()) == 255


def test_palette_with_transparency() -> None:
    p = Image.new("P", (8, 8), 0)
    p.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    p.paste(1, (0, 0, 4, 8))
    data = encode(p, transparency=0)
    [page] = _render(data).pages
    assert tuple(page.image[0, 0]) == (0, 255, 0) and tuple(page.image[0, 7]) == (255, 255, 255)


def test_sixteen_bit_grayscale() -> None:
    arr = np.full((4, 4), 0x8000, dtype=np.uint16)
    arr[0, 0] = 0xFFFF
    data = encode(Image.fromarray(arr))
    assert Image.open(io.BytesIO(data)).mode.startswith("I")
    [page] = _render(data).pages
    assert tuple(page.image[1, 1]) == (128, 128, 128) and tuple(page.image[0, 0]) == (255,) * 3


def test_bilevel_and_cmyk() -> None:
    bilevel = Image.new("1", (8, 8), 1)
    bilevel.putpixel((0, 0), 0)
    [page] = _render(encode(bilevel)).pages
    assert tuple(page.image[0, 0]) == (0, 0, 0) and tuple(page.image[1, 1]) == (255, 255, 255)
    cmyk = Image.new("CMYK", (8, 8), (0, 0, 0, 0))
    [page] = _render(encode(cmyk, "JPEG", quality=100), JPEG).pages
    assert page.image.shape == (8, 8, 3) and int(page.image.min()) >= 250


def test_multi_frame_png_uses_first_frame() -> None:
    first = Image.fromarray(white(10, 10))
    second = Image.fromarray(np.zeros((10, 10, 3), dtype=np.uint8))
    data = encode(first, save_all=True, append_images=[second])
    result = _render(data)
    assert result.info.frames == 2 and _codes(result.info.issues) == ["MULTI_FRAME_IMAGE"]
    assert int(result.pages[0].image.min()) == 255


# --- limity i błędy ----------------------------------------------------------------------------


def test_header_claiming_huge_size_is_rejected_before_decoding() -> None:
    with pytest.raises(RenderError) as info:
        _render(png_claiming_size(20_000, 20_000))  # 400 Mpx > 100 Mpx
    assert info.value.code is IssueCode.IMAGE_TOO_LARGE


def test_image_over_configured_pixel_limit() -> None:
    data = encode(white(2000, 1000))
    with pytest.raises(RenderError) as info:
        _render(data, cfg=RenderConfig(max_image_pixels=1_000_000))
    assert info.value.code is IssueCode.IMAGE_TOO_LARGE
    assert len(_render(data, cfg=RenderConfig(max_image_pixels=2_000_000)).pages) == 1


def test_page_over_pixel_limit_is_downscaled_with_dpi_reduced() -> None:
    image = white(800, 2000)  # 1,6 Mpx, wysoki i niedający się pociąć
    [page] = _render(
        encode(image, dpi=(200, 200)), cfg=RenderConfig(max_page_pixels=1_000_000)
    ).pages
    codes = _codes(page.meta.issues)
    assert "DPI_REDUCED" in codes and "TALL_IMAGE_UNSPLIT" in codes
    assert page.meta.width_px * page.meta.height_px <= 1_000_000
    assert page.meta.dpi == pytest.approx(200 * page.meta.width_px / 800)


def test_too_many_pages_after_split() -> None:
    image, _ = stacked_pages(100, 6)
    with pytest.raises(RenderError) as info:
        _render(encode(image), cfg=RenderConfig(max_pages=5))
    assert info.value.code is IssueCode.TOO_MANY_PAGES


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (encode(white(10, 10))[:40], PNG),  # obcięty PNG
        (encode(white(10, 10)), JPEG),  # PNG podany jako JPEG: dekoder ograniczony do formatu
        (b"\xff\xd8\xff\xe0" + b"\x00" * 50, JPEG),
    ],
)
def test_unreadable_images(data: bytes, kind: DocumentKind) -> None:
    with pytest.raises(RenderError) as info:
        _render(data, kind)
    assert info.value.code is IssueCode.IMAGE_UNREADABLE


def test_pillow_global_limit_is_restored() -> None:
    before = Image.MAX_IMAGE_PIXELS
    _render(encode(white(10, 10)), cfg=RenderConfig(max_image_pixels=5_000_000))
    with pytest.raises(RenderError):
        _render(png_claiming_size(20_000, 20_000))
    assert before == Image.MAX_IMAGE_PIXELS


# --- determinizm i izolacja ---------------------------------------------------------------------


def test_deterministic_and_isolation_is_transparent() -> None:
    image, _ = stacked_pages(300, 2)
    data = encode(image, "JPEG", quality=80)
    a = _render(data, JPEG)
    b = render_image(data, JPEG, CFG)  # proces potomny
    digest = [[hashlib.sha256(p.image.tobytes()).hexdigest() for p in r.pages] for r in (a, b)]
    assert digest[0] == digest[1]
    assert [p.meta for p in a.pages] == [p.meta for p in b.pages]


def test_isolated_errors_keep_codes() -> None:
    with pytest.raises(RenderError) as info:
        render_image(png_claiming_size(20_000, 20_000), PNG, CFG)
    assert info.value.code is IssueCode.IMAGE_TOO_LARGE
