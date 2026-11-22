"""Jakość detekcji na stronach syntetycznych o różnej jakości (#10, C.10; wymaganie #10).

Kategorie zależne od tekstu porównujemy z ich postacią wizualną: podpis (kropki) jako
``dotted_line``, miejsce urzędu (ramka) jako ``table_cell``. OpenCV z założenia ich nie
rozróżnia (rozstrzyga VLM i fuzja), a test mierzy geometrię.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from backend.worker.vision.detect.page import detect_image
from backend.worker.vision.models import ElementKind
from eval.visual.match import from_prediction
from eval.visual.metrics import PageCase, evaluate_page, summarize
from tests.vision.synth_forms import (
    DEGRADATIONS,
    HARD_DEGRADATIONS,
    LIMIT_DEGRADATIONS,
    SynthForm,
    make_form,
)

VISUAL_KIND = {
    ElementKind.SIGNATURE: ElementKind.DOTTED_LINE,
    ElementKind.OFFICE_ONLY: ElementKind.TABLE_CELL,
}
Degrade = Callable[[SynthForm], SynthForm]


def _score(degrade: Degrade, seeds: tuple[int, ...]) -> dict[str, Any]:
    pages = []
    for seed in seeds:
        form = degrade(make_form(seed=seed))
        detection = detect_image(form.image, page=1, dpi=form.dpi)
        gt = tuple(replace(g, kind=VISUAL_KIND.get(g.kind, g.kind)) for g in form.eval_boxes())
        case = PageCase(
            label_id="syn01-p01",
            doc_id="syn01",
            file_id="syn01-png",
            page=1,
            format_group="image_own",
            width_px=form.width,
            height_px=form.height,
            gt=gt,
            pred=tuple(from_prediction(e) for e in detection.elements),
        )
        pages.append(evaluate_page(case))
    return summarize(pages).as_dict()


def _identity(form: SynthForm) -> SynthForm:
    return form


CASES: dict[str, tuple[Degrade, tuple[int, ...]]] = {
    "clean": (_identity, (0, 1, 2)),
    **{name: (fn, (0,)) for name, fn in DEGRADATIONS.items()},
    **{name: (fn, (1,)) for name, fn in HARD_DEGRADATIONS.items()},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_quality_is_perfect_on_supported_degradations(name: str) -> None:
    degrade, seeds = CASES[name]
    report = _score(degrade, seeds)
    failing = {k: v["f1"] for k, v in report["per_kind"].items() if v["f1"] != 1.0}
    assert report["micro"]["f1"] == 1.0, failing
    assert report["cells"]["exact_rate"] == 1.0


@pytest.mark.parametrize(
    ("name", "micro_min", "exempt"),
    [
        ("faded_blurred", 0.85, {"dotted_line"}),  # kropki wyblakłe i rozmyte znikają
        ("dpi_75", 0.9, {"section_frame", "text_line"}),  # linie 1 px łączą się z tekstem
        ("noise_40", 0.9, {"dotted_line"}),  # szum przerywa łańcuchy kropek
    ],
)
def test_known_limits_have_lower_bounds(name: str, micro_min: float, exempt: set[str]) -> None:
    report = _score(LIMIT_DEGRADATIONS[name], (0,))
    assert report["micro"]["f1"] >= micro_min
    for kind, values in report["per_kind"].items():
        if kind not in exempt:
            assert values["f1"] == 1.0, (kind, values)
