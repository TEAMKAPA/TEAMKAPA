"""Generator syntetycznych stron formularzy (#12, B.5): determinizm, zgodność GT z pikselami."""

from collections.abc import Callable

import numpy as np
import pytest

from backend.worker.vision.models import ElementKind
from eval.visual.convert_ls import ConvertParams, edge_offsets
from eval.visual.geometry import image_sha256
from eval.visual.match import from_label
from eval.visual.metrics import PageCase, evaluate_page, summarize
from eval.visual.models import dump_json
from tests.vision.synth_forms import (
    DEGRADATIONS,
    SynthForm,
    contrast,
    downscale,
    make_form,
    noise,
    rotate,
)

FRAMED = {
    ElementKind.TABLE_CELL,
    ElementKind.CHAR_BOXES,
    ElementKind.CHECKBOX,
    ElementKind.SECTION_FRAME,
    ElementKind.OFFICE_ONLY,
}


@pytest.fixture(scope="module")
def form() -> SynthForm:
    return make_form(seed=1)


def _ink(form: SynthForm, threshold: int = 128) -> np.ndarray:
    return form.image.min(axis=2) < threshold


def _union_mask(form: SynthForm, boxes: list[tuple[float, ...]], margin: int = 0) -> np.ndarray:
    mask = np.zeros((form.height, form.width), dtype=bool)
    for box in boxes:
        x0, y0, x1, y1 = form.pixel_box(box)  # type: ignore[arg-type]
        mask[max(y0 - margin, 0) : y1 + margin, max(x0 - margin, 0) : x1 + margin] = True
    return mask


def test_deterministic_and_seed_dependent() -> None:
    a, b, c = make_form(seed=3), make_form(seed=3), make_form(seed=4)
    assert image_sha256(a.image) == image_sha256(b.image)
    assert dump_json(a.labels()) == dump_json(b.labels())
    assert image_sha256(a.image) != image_sha256(c.image)


def test_labels_cover_all_kinds(form: SynthForm) -> None:
    labels = form.labels()
    assert {e.kind for e in labels.elements} == set(ElementKind)
    assert (labels.source.width_px, labels.source.height_px) == (1654, 2339)
    assert labels.source.image_sha256 == image_sha256(form.image)
    ids = [e.id for e in labels.elements]
    assert ids == [f"gt-p1-{i:03d}" for i in range(1, len(ids) + 1)]
    group = next(e for e in labels.elements if e.kind is ElementKind.CHECKBOX_GROUP)
    assert 2 <= len(group.options) <= 3 and group.multi is False
    cells = sorted(e.cells for e in labels.elements if e.cells is not None)
    assert cells == [2, 3, 11]


@pytest.mark.parametrize("dpi", [200.0, 150.0])
def test_framed_labels_are_tight(dpi: float) -> None:
    form = make_form(seed=2, dpi=dpi)
    ink = form.image.min(axis=2) < 160
    for element in form.elements:
        if element.kind in FRAMED:
            box = form.pixel_box(element.box)
            assert edge_offsets(ink, box, ConvertParams()) == (0, 0, 0, 0), element


def test_every_ink_pixel_is_explained(form: SynthForm) -> None:
    ink = _ink(form, 250)
    explained = _union_mask(form, [e.box for e in form.elements] + list(form.text))
    assert not (ink & ~explained).any()
    for element in form.elements:
        x0, y0, x1, y1 = form.pixel_box(element.box)
        assert ink[y0:y1, x0:x1].any(), element


def test_rotation_zero_is_identity(form: SynthForm) -> None:
    rotated = rotate(form, 0.0)
    assert np.array_equal(rotated.image, form.image)
    assert rotated.elements == form.elements


@pytest.mark.parametrize("angle", [1.5, -2.0])
def test_rotated_labels_follow_ink(form: SynthForm, angle: float) -> None:
    rotated = rotate(form, angle)
    ink = _ink(rotated)
    for element in rotated.elements:
        if element.kind is not ElementKind.CHECKBOX:
            continue
        x0, y0, x1, y1 = rotated.pixel_box(element.box)
        window = ink[y0 - 3 : y1 + 3, x0 - 3 : x1 + 3]
        rows, cols = np.nonzero(window)
        measured = (
            cols.min() + x0 - 3,
            rows.min() + y0 - 3,
            cols.max() + x0 - 2,
            rows.max() + y0 - 2,
        )
        assert all(abs(m - e) <= 1.5 for m, e in zip(measured, (x0, y0, x1, y1), strict=True))
    outside = ink & ~_union_mask(rotated, [e.box for e in rotated.elements] + list(rotated.text), 2)
    assert outside.sum() <= 0.001 * ink.sum()


@pytest.mark.parametrize("name", sorted(DEGRADATIONS))
def test_degradations_are_deterministic(form: SynthForm, name: str) -> None:
    degrade: Callable[[SynthForm], SynthForm] = DEGRADATIONS[name]
    first, second = degrade(form), degrade(form)
    assert image_sha256(first.image) == image_sha256(second.image)
    assert first.steps == (*form.steps, first.steps[-1]) and first.steps[-1].split("(")[
        0
    ] in name.replace("dpi", "downscale")
    assert not np.array_equal(first.image, form.image)
    labels = first.labels()  # etykiety po degradacji nadal spełniają kontrakt
    assert len(labels.elements) == len(form.elements)
    case = PageCase(
        label_id=labels.label_id,
        doc_id=labels.doc_id,
        file_id=labels.source.file_id,
        page=1,
        format_group="image_own",
        width_px=first.width,
        height_px=first.height,
        gt=tuple(from_label(e) for e in labels.elements),
        pred=first.eval_boxes(),
    )
    assert summarize([evaluate_page(case)]).micro().as_dict()["f1"] == 1.0


def test_noise_seed_and_contrast_and_downscale(form: SynthForm) -> None:
    assert not np.array_equal(noise(form, 20, seed=1).image, noise(form, 20, seed=2).image)
    faded = contrast(form, 0.4)
    assert int(faded.image.min()) == 153 and faded.elements == form.elements
    small = downscale(form, 100.0)
    assert (small.width, small.height, small.dpi) == (827, 1170, 100.0)
    assert small.elements == form.elements  # ułamki strony bez zmian
    ink = small.image.min(axis=2) < 160
    for element in small.elements:
        if element.kind in FRAMED:
            offsets = edge_offsets(ink, small.pixel_box(element.box), ConvertParams())
            assert all(o is not None and o <= 1 for o in offsets), element
