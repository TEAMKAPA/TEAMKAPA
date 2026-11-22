"""Preprocessing detektorów (#10, C.2) na stronach syntetycznych."""

import numpy as np
import pytest

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.preprocess import (
    LOW_CONTRAST,
    SKEW_CORRECTED,
    prepare,
)
from backend.worker.vision.detect.scale import Scale
from tests.vision.synth_forms import SynthForm, contrast, make_form, noise, rotate

CFG = DetectConfig()


@pytest.fixture(scope="module")
def form() -> SynthForm:
    return make_form(seed=5)


def _explained(form: SynthForm, margin: int) -> np.ndarray:
    mask = np.zeros((form.height, form.width), dtype=bool)
    for box in [e.box for e in form.elements] + list(form.text):
        x0, y0, x1, y1 = form.pixel_box(box)
        mask[max(y0 - margin, 0) : y1 + margin, max(x0 - margin, 0) : x1 + margin] = True
    return mask


def test_clean_page_ink(form: SynthForm) -> None:
    prepared = prepare(form.image, CFG, Scale(form.dpi))
    drawn = form.image.min(axis=2) < 128
    assert prepared.angle == 0.0 and prepared.reasons == ()
    assert (prepared.ink & drawn).sum() >= 0.98 * drawn.sum()
    assert not (prepared.ink & ~_explained(form, 1)).any()


def test_noise_does_not_create_ink(form: SynthForm) -> None:
    noisy = noise(form, 25.0, seed=3)
    prepared = prepare(noisy.image, CFG, Scale(form.dpi))
    outside = prepared.ink & ~_explained(form, 1)
    assert outside.sum() <= 0.001 * prepared.ink.sum()


def test_low_contrast_is_reported_and_binarized(form: SynthForm) -> None:
    faded = contrast(form, 0.4)
    prepared = prepare(faded.image, CFG, Scale(form.dpi))
    drawn = form.image.min(axis=2) < 128
    assert LOW_CONTRAST in prepared.reasons
    assert (prepared.ink & drawn).sum() >= 0.95 * drawn.sum()


@pytest.mark.parametrize("angle", [1.5, -2.0, 3.3])
def test_skew_is_estimated_and_boxes_map_back(form: SynthForm, angle: float) -> None:
    rotated = rotate(form, angle)
    prepared = prepare(rotated.image, CFG, Scale(form.dpi))
    assert prepared.angle == pytest.approx(angle, abs=0.05)
    assert SKEW_CORRECTED in prepared.reasons
    # Element w obrazie wyprostowanym leży tam, gdzie na stronie bez obrotu;
    # to_page daje ramkę obejmującą w układzie strony obróconej (jak etykieta).
    for original, expected in zip(form.elements, rotated.elements, strict=True):
        mapped = prepared.to_page(form.pixel_box(original.box))
        target = rotated.pixel_box(expected.box)
        assert all(abs(a - b) <= 3 for a, b in zip(mapped, target, strict=True))


def test_small_angles_are_ignored(form: SynthForm) -> None:
    prepared = prepare(rotate(form, 0.05).image, CFG, Scale(form.dpi))
    assert prepared.angle == 0.0
    assert prepared.to_page((10, 20, 30, 40)) == (10, 20, 30, 40)


def test_blank_page_and_determinism(form: SynthForm) -> None:
    blank = np.full((500, 400, 3), 255, dtype=np.uint8)
    prepared = prepare(blank, CFG, Scale(100.0))
    assert not prepared.ink.any() and prepared.angle == 0.0 and prepared.contrast == 255
    first = prepare(rotate(form, 1.0).image, CFG, Scale(form.dpi))
    second = prepare(rotate(form, 1.0).image, CFG, Scale(form.dpi))
    assert first.angle == second.angle and np.array_equal(first.ink, second.ink)
