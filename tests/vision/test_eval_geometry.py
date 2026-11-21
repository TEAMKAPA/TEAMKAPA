"""Tożsamość obrazów stron i zgodność geometrii wariantów (eval/visual/geometry.py)."""

import io

import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage

from eval.visual.geometry import (
    GeometryParams,
    compare_pages,
    dilate,
    image_sha256,
    shift_mask,
)
from tests.vision.synth import fill, white

Image = npt.NDArray[np.uint8]


def _page() -> Image:
    """Syntetyczna strona: ramki, kreski grubości 1–2 px i wypełnione prostokąty."""
    image = white(300, 424)
    fill(image, (20, 30, 280, 32), (0, 0, 0))  # linia pozioma 2 px
    fill(image, (20, 30, 21, 120), (0, 0, 0))  # linia pionowa 1 px
    fill(image, (60, 60, 120, 90), (30, 30, 30))
    for i in range(8):  # rząd kratek
        x0 = 40 + i * 22
        fill(image, (x0, 200, x0 + 20, 201), (0, 0, 0))
        fill(image, (x0, 200, x0 + 1, 222), (0, 0, 0))
    fill(image, (200, 300, 260, 380), (90, 90, 90))
    return image


def _shifted(image: Image, dx: int, dy: int) -> Image:
    out = white(image.shape[1], image.shape[0])
    h, w = image.shape[:2]
    out[max(dy, 0) : h + min(dy, 0), max(dx, 0) : w + min(dx, 0)] = image[
        max(-dy, 0) : h - max(dy, 0), max(-dx, 0) : w - max(dx, 0)
    ]
    return out


def _jpeg(image: Image, quality: int) -> Image:
    buf = io.BytesIO()
    PILImage.fromarray(image).save(buf, format="JPEG", quality=quality)
    with PILImage.open(io.BytesIO(buf.getvalue())) as decoded:
        return np.asarray(decoded.convert("RGB"), dtype=np.uint8)


def test_image_sha256_identifies_pixels_and_shape() -> None:
    page = _page()
    assert image_sha256(page) == image_sha256(page.copy())
    changed = page.copy()
    changed[0, 0, 0] = 254
    assert image_sha256(changed) != image_sha256(page)
    flat = np.zeros((2, 6, 3), dtype=np.uint8)
    assert image_sha256(flat) != image_sha256(flat.reshape(3, 4, 3))


def test_shift_mask_without_wrapping() -> None:
    mask = np.zeros((4, 5), dtype=np.bool_)
    mask[0, 0] = mask[3, 4] = True
    right_down = shift_mask(mask, 1, 1)
    assert right_down[1, 1] and right_down.sum() == 1  # piksel z rogu nie wraca z drugiej strony
    left_up = shift_mask(mask, -1, -1)
    assert left_up[2, 3] and left_up.sum() == 1
    assert not shift_mask(mask, 5, 0).any()


def test_dilate_square_neighbourhood() -> None:
    mask = np.zeros((5, 5), dtype=np.bool_)
    mask[2, 2] = True
    assert dilate(mask, 1).sum() == 9
    assert dilate(mask, 2).sum() == 25


def test_identical_and_one_pixel_shift_pass() -> None:
    page = _page()
    same = compare_pages(page, page.copy())
    assert same.passed and same.shift_px == (0, 0)
    assert same.variant_ink_near_base == same.base_ink_near_variant == 1.0
    shifted = compare_pages(page, _shifted(page, 0, 1))
    assert shifted.passed and shifted.shift_px == (0, 1)
    diagonal = compare_pages(page, _shifted(page, -1, 1))
    assert diagonal.passed and diagonal.shift_px == (-1, 1)


def test_jpeg_variant_passes() -> None:
    page = _page()
    check = compare_pages(page, _jpeg(page, 90))
    assert check.passed, check


def test_rejects_larger_shift_other_size_and_missing_content() -> None:
    page = _page()
    two_px = compare_pages(page, _shifted(page, 2, 0))
    assert not two_px.passed and two_px.shift_px == (2, 0)
    other_size = compare_pages(page, white(300, 420))
    assert not other_size.passed and not other_size.size_equal
    missing = page.copy()
    missing[300:380, 200:260] = 255  # brak jednego prostokąta
    check = compare_pages(page, missing)
    assert check.passed is False
    assert check.variant_ink_near_base == 1.0 and check.base_ink_near_variant < 0.97


def test_blank_pages() -> None:
    blank = white(100, 141)
    assert compare_pages(blank, blank.copy()).passed
    assert not compare_pages(blank, _page()[:141, :100]).passed


def test_params_are_respected() -> None:
    page = _page()
    loose = GeometryParams(max_shift_px=2)
    assert compare_pages(page, _shifted(page, 2, 0), loose).passed
    extra = page.copy()
    extra[400, 10] = 0  # jeden piksel tuszu daleko od tuszu strony
    assert compare_pages(page, extra).passed
    assert not compare_pages(page, extra, GeometryParams(min_ink_fraction=1.0)).passed
