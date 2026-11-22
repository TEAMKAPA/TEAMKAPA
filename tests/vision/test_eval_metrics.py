"""Dopasowanie i metryki detekcji (#12, B.4) na danych syntetycznych."""

import math

import pytest

from backend.worker.vision.models import ElementKind, ElementRole
from eval.visual.match import EvalBox, MatchParams, iou, match_any_kind, match_box, match_page
from eval.visual.metrics import (
    MISSED,
    SPURIOUS,
    Counts,
    FieldLocation,
    PageCase,
    ablation,
    evaluate_page,
    field_counts,
    field_targets,
    macro_over,
    summarize,
    wilson,
)

K = ElementKind
R = ElementRole
W, H = 1000, 1000  # strona kwadratowa: ułamki = piksele / 1000


def box(
    id_: str,
    kind: ElementKind,
    b: tuple[float, float, float, float],
    *,
    role: ElementRole | None = None,
    **kw: object,
) -> EvalBox:
    default = {K.CHECKBOX: R.CHOICE, K.CHECKBOX_GROUP: R.CHOICE, K.SECTION_FRAME: R.SECTION}
    return EvalBox(
        id=id_,
        kind=kind,
        box=tuple(v / 1000 for v in b),  # type: ignore[arg-type]
        role=role or default.get(kind, R.INPUT),
        **kw,  # type: ignore[arg-type]
    )


def case(gt: list[EvalBox], pred: list[EvalBox], **kw: object) -> PageCase:
    data: dict[str, object] = {
        "label_id": "f02-p01",
        "doc_id": "f02",
        "file_id": "f02-pdf",
        "page": 1,
        "format_group": "pdf",
        "width_px": W,
        "height_px": H,
        "gt": tuple(gt),
        "pred": tuple(pred),
    }
    data.update(kw)
    return PageCase(**data)  # type: ignore[arg-type]


PARAMS = MatchParams(page_aspect=1.0)


def test_iou() -> None:
    assert iou((0, 0, 1, 1), (0, 0, 1, 1)) == 1.0
    assert iou((0, 0, 1, 1), (1, 0, 2, 1)) == 0.0
    assert math.isclose(iou((0, 0, 2, 1), (1, 0, 3, 1)), 1 / 3)


def test_thin_lines_are_expanded() -> None:
    line = box("g1", K.DOTTED_LINE, (100, 500, 600, 502))
    assert match_box(line, PARAMS) == pytest.approx((0.1, 0.496, 0.6, 0.506))
    cell = box("g2", K.TABLE_CELL, (100, 500, 600, 502))
    assert match_box(cell, PARAMS) == cell.box  # tylko linie
    sig_line = box("g3", K.SIGNATURE, (100, 500, 600, 502))
    sig_frame = box("g4", K.SIGNATURE, (100, 500, 110, 504))  # nie jest linią
    assert match_box(sig_line, PARAMS) != sig_line.box
    assert match_box(sig_frame, PARAMS) == sig_frame.box
    # Linia przesunięta o 2 px: bez rozszerzenia IoU = 0, z rozszerzeniem wysoka.
    shifted = box("p1", K.DOTTED_LINE, (100, 503, 600, 505))
    assert iou(line.box, shifted.box) == 0.0
    assert match_page([line], [shifted], PARAMS).pairs


def test_greedy_matching_is_deterministic_and_one_to_one() -> None:
    gt = [box("g1", K.CHECKBOX, (0, 0, 20, 20)), box("g2", K.CHECKBOX, (10, 0, 30, 20))]
    pred = [box("p1", K.CHECKBOX, (5, 0, 25, 20))]
    first = match_page(gt, pred, PARAMS)
    assert [(p.gt.id, p.pred.id) for p in first.pairs] == [("g1", "p1")]  # remis: niższe ID
    assert [g.id for g in first.missed] == ["g2"]
    assert match_page(list(reversed(gt)), pred, PARAMS).pairs == first.pairs


def test_kind_must_agree_and_any_kind_confusion() -> None:
    gt = [box("g1", K.SIGNATURE, (100, 100, 400, 104))]
    pred = [box("p1", K.DOTTED_LINE, (100, 100, 400, 104))]
    result = match_page(gt, pred, PARAMS)
    assert not result.pairs and result.missed and result.spurious
    pairs = match_any_kind(gt, pred, PARAMS)
    assert [(p.gt.kind, p.pred.kind) for p in pairs] == [(K.SIGNATURE, K.DOTTED_LINE)]
    summary = summarize([evaluate_page(case(gt, pred))])
    assert summary.kind_confusion[("signature", "dotted_line")] == 1
    assert (SPURIOUS, "dotted_line") not in summary.kind_confusion


def test_difficult_labels_are_ignored() -> None:
    gt = [
        box("g1", K.CHECKBOX, (0, 0, 20, 20), difficult=True),
        box("g2", K.CHECKBOX, (100, 0, 120, 20), difficult=True),
        box("g3", K.CHECKBOX, (200, 0, 220, 20)),
    ]
    pred = [box("p1", K.CHECKBOX, (0, 0, 20, 20)), box("p3", K.CHECKBOX, (200, 0, 220, 20))]
    result = match_page(gt, pred, PARAMS)
    assert [p.gt.id for p in result.ignored] == ["g1"]
    assert [g.id for g in result.difficult_unmatched] == ["g2"]
    counts = summarize([evaluate_page(case(gt, pred))]).per_kind[K.CHECKBOX]
    assert (counts.tp, counts.fp, counts.fn, counts.ignored) == (1, 0, 0, 1)


def _group_page(pred_options: tuple[str, ...], pred_box: tuple[float, ...]) -> list[EvalBox]:
    return [
        box("p1", K.CHECKBOX, (100, 100, 120, 120)),
        box("p2", K.CHECKBOX, (300, 100, 320, 120)),
        box("p3", K.CHECKBOX, (500, 100, 520, 120)),
        box("pg", K.CHECKBOX_GROUP, pred_box, options=pred_options, multi=False),  # type: ignore[arg-type]
    ]


GT_GROUP = [
    box("g1", K.CHECKBOX, (100, 100, 120, 120)),
    box("g2", K.CHECKBOX, (300, 100, 320, 120)),
    box("g3", K.CHECKBOX, (500, 100, 520, 120)),
    box("gg", K.CHECKBOX_GROUP, (95, 95, 600, 125), options=("g1", "g2", "g3"), multi=False),
]


@pytest.mark.parametrize(
    ("options", "pred_box", "matched"),
    [
        (("p1", "p2", "p3"), (0, 0, 1000, 1000), True),  # skład zgodny, ramka obojętna
        (("p1", "p2"), (95, 95, 330, 125), True),  # Jaccard 2/3
        (("p1",), (95, 95, 125, 125), False),  # Jaccard 1/3
        ((), (95, 95, 600, 125), True),  # bez członków: IoU
        ((), (95, 95, 200, 125), False),
    ],
)
def test_group_matching_by_members(
    options: tuple[str, ...], pred_box: tuple[float, ...], matched: bool
) -> None:
    result = match_page(GT_GROUP, _group_page(options, pred_box), PARAMS)
    groups = [p for p in result.pairs if p.gt.kind is K.CHECKBOX_GROUP]
    assert bool(groups) is matched


def test_summary_counts_cells_roles_multi() -> None:
    gt = [
        box("c1", K.CHAR_BOXES, (0, 0, 200, 20), cells=10),
        box("c2", K.CHAR_BOXES, (0, 100, 60, 120), cells=3),
        box("c3", K.TABLE_CELL, (0, 200, 500, 250), role=R.SKIP),
        *GT_GROUP,
    ]
    pred = [
        box("p8", K.CHAR_BOXES, (0, 0, 200, 20), cells=10),
        box("p9", K.CHAR_BOXES, (0, 100, 60, 120), cells=5),
        box("p10", K.TABLE_CELL, (0, 200, 500, 250), role=R.UNKNOWN),
        *_group_page(("p1", "p2", "p3"), (95, 95, 600, 125))[:3],
        box("pg", K.CHECKBOX_GROUP, (95, 95, 600, 125), options=("p1", "p2", "p3"), multi=True),
        box("p11", K.TEXT_LINE, (0, 900, 300, 902)),
    ]
    summary = summarize([evaluate_page(case(gt, pred))])
    report = summary.as_dict()
    assert report["micro"]["tp"] == 7 and report["micro"]["fp"] == 1 and report["micro"]["fn"] == 0
    assert report["cells"] == {"n": 2, "exact_rate": 0.5, "mae": 1.0}
    assert report["roles"]["confusion"]["skip"] == {"unknown": 1}
    assert report["roles"]["accuracy"] == round(6 / 7, 4)
    assert report["multi"] == {"n": 1, "accuracy": 0.0}
    assert report["per_kind"]["text_line"]["precision"] == 0.0
    assert report["per_kind"]["text_line"]["recall"] is None
    assert report["kind_confusion"][SPURIOUS] == {"text_line": 1}
    assert report["macro_f1"] is not None


def test_missed_in_kind_confusion() -> None:
    gt = [box("g1", K.CHECKBOX, (0, 0, 20, 20))]
    summary = summarize([evaluate_page(case(gt, []))])
    assert summary.kind_confusion[("checkbox", MISSED)] == 1
    assert summary.micro().as_dict()["recall"] == 0.0


def test_wilson_interval() -> None:
    assert wilson(0, 0) is None
    low, high = wilson(8, 10) or (0, 0)
    assert low == pytest.approx(0.4902, abs=1e-4) and high == pytest.approx(0.9433, abs=1e-4)
    assert wilson(10, 10) == (pytest.approx(0.7225, abs=1e-4), pytest.approx(1.0))


def test_counts_f1_edge_cases() -> None:
    assert Counts().as_dict()["f1"] is None
    assert Counts(fp=2, fn=1).as_dict()["f1"] == 0.0
    assert Counts(tp=1).as_dict()["f1"] == 1.0


def test_macro_over_documents() -> None:
    perfect = summarize(
        [
            evaluate_page(
                case([box("g", K.CHECKBOX, (0, 0, 20, 20))], [box("p", K.CHECKBOX, (0, 0, 20, 20))])
            )
        ]
    )
    zero = summarize([evaluate_page(case([box("g", K.CHECKBOX, (0, 0, 20, 20))], []))])
    assert macro_over({"f02": perfect, "f07": zero}) == 0.5
    assert macro_over({}) is None


def test_field_targets_and_ablation() -> None:
    gt = [
        *GT_GROUP,
        box("g4", K.DOTTED_LINE, (100, 500, 600, 502)),
        box("g5", K.TABLE_CELL, (0, 600, 500, 650), role=R.SKIP),
        box("g6", K.SECTION_FRAME, (0, 0, 1000, 700)),
        box("g7", K.SIGNATURE, (600, 800, 900, 802)),
        box("g8", K.CHAR_BOXES, (0, 900, 100, 920), cells=5, difficult=True),
    ]
    assert [t.id for t in field_targets(gt)] == ["gg", "g4", "g7", "g8"]
    with_visual = [
        FieldLocation("f1", (0.095, 0.095, 0.6, 0.125)),
        FieldLocation("f2", (0.1, 0.502, 0.6, 0.504)),  # linia przesunięta o 2 px
        FieldLocation("f3", (0.6, 0.8, 0.9, 0.802)),
        FieldLocation("f4", (0.0, 0.9, 0.1, 0.92)),  # pole trudne: ignorowane
    ]
    without_visual = [
        FieldLocation("f1", (0.095, 0.095, 0.6, 0.125)),
        FieldLocation("x", (0.5, 0.5, 0.52, 0.52)),
    ]
    counts = field_counts(gt, with_visual, PARAMS)
    assert (counts.tp, counts.fp, counts.fn, counts.ignored) == (3, 0, 0, 1)
    result = ablation([(case(gt, []), with_visual, without_visual)], PARAMS)
    assert result["with_visual"]["recall"] == 1.0
    assert result["without_visual"]["recall"] == round(1 / 3, 4)
    assert result["delta_recall"] == round(1 - 1 / 3, 4)
    assert result["delta_precision"] == 0.5
