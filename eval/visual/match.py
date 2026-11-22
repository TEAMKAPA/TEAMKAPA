"""Dopasowanie elementów przewidzianych do etykiet jednej strony (#12, Etap B.4).

Zasady (README, „Metryki”):

- dopasowanie zachłanne po malejącym wyniku (IoU albo zgodność składu grupy), z deterministycznym
  rozstrzyganiem remisów (ID elementów), każdy element najwyżej w jednej parze,
- IoU cienkich linii (``text_line``, ``dotted_line`` oraz poziome ``signature`` i ``office_only``)
  liczone na ramkach rozszerzonych pionowo do ``min_line_height`` (ułamek wysokości strony), bo
  przy linii grubości 2 px przesunięcie o 1 px zeruje IoU,
- ``checkbox_group``: Jaccard członków (checkboxy przewidziane przeliczone na checkboxy etykiet
  przez wcześniejsze dopasowanie checkboxów); gdy grupa nie ma członków po którejś stronie, IoU,
- etykieta z flagą ``uncertain`` jest „trudna” (jak *difficult* w PASCAL VOC): para z nią nie jest
  ani trafieniem, ani fałszywym alarmem, a jej brak nie jest pominięciem.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from backend.worker.vision.models import ElementKind, ElementRole, VisualElement
from eval.visual.models import LabelElement

Box = tuple[float, float, float, float]

LINE_KINDS = frozenset({ElementKind.TEXT_LINE, ElementKind.DOTTED_LINE})
LINE_LIKE_KINDS = frozenset({ElementKind.SIGNATURE, ElementKind.OFFICE_ONLY})


@dataclass(frozen=True)
class MatchParams:
    iou: float = 0.5
    min_line_height: float = 0.01  # ułamek wysokości strony (~23 px przy 200 DPI, A4)
    line_aspect: float = 5.0  # signature/office_only z szerokością ≥ 5 × wysokość to linia
    group_jaccard: float = 0.5
    page_aspect: float = 2339 / 1654  # wysokość / szerokość strony (do proporcji ramek)


@dataclass(frozen=True)
class EvalBox:
    """Element w postaci wspólnej dla etykiet i predykcji."""

    id: str
    kind: ElementKind
    box: Box
    role: ElementRole
    cells: int | None = None
    multi: bool | None = None
    options: tuple[str, ...] = ()
    difficult: bool = False
    confidence: float = 1.0


def from_label(element: LabelElement) -> EvalBox:
    b = element.bbox
    return EvalBox(
        id=element.id,
        kind=element.kind,
        box=(b.x0, b.y0, b.x1, b.y1),
        role=element.role,
        cells=element.cells,
        multi=element.multi,
        options=element.options,
        difficult=element.uncertain,
    )


def from_prediction(element: VisualElement) -> EvalBox:
    b = element.bbox
    return EvalBox(
        id=element.id,
        kind=element.kind,
        box=(b.x0, b.y0, b.x1, b.y1),
        role=element.role,
        cells=element.cells,
        multi=element.multi,
        options=element.options,
        confidence=element.confidence,
    )


def _is_line(item: EvalBox, params: MatchParams) -> bool:
    if item.kind in LINE_KINDS:
        return True
    if item.kind not in LINE_LIKE_KINDS:
        return False
    width = item.box[2] - item.box[0]
    height = (item.box[3] - item.box[1]) * params.page_aspect
    return width >= params.line_aspect * max(height, 1e-9)


def match_box(item: EvalBox, params: MatchParams) -> Box:
    """Ramka używana do IoU: linia rozszerzona pionowo (symetrycznie) do minimalnej wysokości."""
    x0, y0, x1, y1 = item.box
    if not _is_line(item, params) or y1 - y0 >= params.min_line_height:
        return item.box
    center = (y0 + y1) / 2
    half = params.min_line_height / 2
    return x0, center - half, x1, center + half


def iou(a: Box, b: Box) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


@dataclass(frozen=True)
class Pair:
    gt: EvalBox
    pred: EvalBox
    score: float


@dataclass
class PageMatch:
    """Wynik dopasowania jednej strony (w obrębie kategorii)."""

    pairs: list[Pair] = field(default_factory=list)  # trafienia (etykieta nie jest trudna)
    ignored: list[Pair] = field(default_factory=list)  # pary z etykietą trudną
    missed: list[EvalBox] = field(default_factory=list)  # pominięcia (bez etykiet trudnych)
    spurious: list[EvalBox] = field(default_factory=list)  # fałszywe alarmy
    difficult_unmatched: list[EvalBox] = field(default_factory=list)


def greedy(
    gts: Sequence[EvalBox],
    preds: Sequence[EvalBox],
    score: Mapping[tuple[str, str], float],
    threshold: float,
) -> list[Pair]:
    """Pary o wyniku ≥ progu, zachłannie od najlepszego; remis: ID etykiety, potem predykcji."""
    by_gt = {g.id: g for g in gts}
    by_pred = {p.id: p for p in preds}
    candidates = sorted(
        ((value, g, p) for (g, p), value in score.items() if value >= threshold),
        # Zaokrąglenie: remis nie może zależeć od szumu zmiennoprzecinkowego.
        key=lambda item: (-round(item[0], 9), item[1], item[2]),
    )
    used_gt: set[str] = set()
    used_pred: set[str] = set()
    pairs = []
    for value, g, p in candidates:
        if g in used_gt or p in used_pred:
            continue
        used_gt.add(g)
        used_pred.add(p)
        pairs.append(Pair(gt=by_gt[g], pred=by_pred[p], score=value))
    return pairs


def _iou_scores(
    gts: Iterable[EvalBox], preds: Iterable[EvalBox], params: MatchParams, *, same_kind: bool
) -> dict[tuple[str, str], float]:
    gt_boxes = [(g, match_box(g, params)) for g in gts]
    scores = {}
    for p in preds:
        p_box = match_box(p, params)
        for g, g_box in gt_boxes:
            if same_kind and g.kind is not p.kind:
                continue
            value = iou(g_box, p_box)
            if value > 0:
                scores[(g.id, p.id)] = value
    return scores


def _group_scores(
    gts: Sequence[EvalBox],
    preds: Sequence[EvalBox],
    checkbox_map: Mapping[str, str],
    params: MatchParams,
) -> dict[tuple[str, str], float]:
    """Jaccard członków grup; grupa bez członków (po którejś stronie) porównywana przez IoU."""
    scores = {}
    for p in preds:
        mapped = {checkbox_map[o] for o in p.options if o in checkbox_map}
        for g in gts:
            if g.options and p.options:
                union = len(g.options) + len(p.options) - len(mapped & set(g.options))
                value = len(mapped & set(g.options)) / union
                threshold = params.group_jaccard
            else:
                value = iou(g.box, p.box)
                threshold = params.iou
            if value >= threshold:
                # Wspólna skala wyniku z progiem zależnym od sposobu porównania.
                scores[(g.id, p.id)] = value
    return scores


def match_page(
    gts: Sequence[EvalBox], preds: Sequence[EvalBox], params: MatchParams | None = None
) -> PageMatch:
    """Dopasowanie w obrębie kategorii: najpierw wszystkie poza grupami, potem grupy."""
    params = params or MatchParams()
    result = PageMatch()
    plain_gt = [g for g in gts if g.kind is not ElementKind.CHECKBOX_GROUP]
    plain_pred = [p for p in preds if p.kind is not ElementKind.CHECKBOX_GROUP]
    pairs = greedy(
        plain_gt, plain_pred, _iou_scores(plain_gt, plain_pred, params, same_kind=True), params.iou
    )
    checkbox_map = {
        pair.pred.id: pair.gt.id for pair in pairs if pair.gt.kind is ElementKind.CHECKBOX
    }
    group_gt = [g for g in gts if g.kind is ElementKind.CHECKBOX_GROUP]
    group_pred = [p for p in preds if p.kind is ElementKind.CHECKBOX_GROUP]
    group_scores = _group_scores(group_gt, group_pred, checkbox_map, params)
    pairs += greedy(group_gt, group_pred, group_scores, threshold=0.0)

    matched_gt = {pair.gt.id for pair in pairs}
    matched_pred = {pair.pred.id for pair in pairs}
    for pair in sorted(pairs, key=lambda pair: pair.gt.id):
        (result.ignored if pair.gt.difficult else result.pairs).append(pair)
    for g in gts:
        if g.id not in matched_gt:
            (result.difficult_unmatched if g.difficult else result.missed).append(g)
    result.spurious = [p for p in preds if p.id not in matched_pred]
    return result


def match_any_kind(
    gts: Sequence[EvalBox], preds: Sequence[EvalBox], params: MatchParams | None = None
) -> list[Pair]:
    """Dopasowanie bez względu na kategorię (macierz pomyłek kategorii); bez grup wyboru."""
    params = params or MatchParams()
    gts = [g for g in gts if g.kind is not ElementKind.CHECKBOX_GROUP]
    preds = [p for p in preds if p.kind is not ElementKind.CHECKBOX_GROUP]
    return greedy(gts, preds, _iou_scores(gts, preds, params, same_kind=False), params.iou)
