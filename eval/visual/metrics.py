"""Metryki detekcji elementów wizualnych (#12, Etap B.4).

Jednostka oceny to strona pliku (wariant formatu) z etykietami swojego zestawu. Liczymy:

- P, R, F1 per kategoria (dopasowanie w obrębie kategorii, ``match.match_page``), mikro (suma
  par wszystkich kategorii) i makro (średnia F1 kategorii obecnych w etykietach lub predykcjach),
  z 95% przedziałem Wilsona dla P i R,
- macierz pomyłek kategorii z dopasowania bez względu na kategorię (``match.match_any_kind``),
- ``char_boxes``: zgodność liczby kratek wśród dopasowanych (dokładna i średni błąd bezwzględny),
- role: trafność i macierz pomyłek wśród dopasowanych; grupy: trafność ``multi``,
- pola (ablacja kanału wizualnego): elementy etykiet z rolą ``input``, ``choice`` albo
  ``e_signature``, grupa wyboru jako jedno pole (jej checkboxy nie są osobnymi polami).
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from backend.worker.vision.models import ElementKind, ElementRole
from eval.visual.match import (
    Box,
    EvalBox,
    MatchParams,
    PageMatch,
    Pair,
    greedy,
    iou,
    match_any_kind,
    match_page,
)

Z_95 = 1.959963984540054
FIELD_ROLES = frozenset({ElementRole.INPUT, ElementRole.CHOICE, ElementRole.E_SIGNATURE})
MISSED, SPURIOUS = "(brak)", "(nadmiar)"


def wilson(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """95% przedział Wilsona dla proporcji; ``None`` przy braku prób."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


@dataclass(frozen=True)
class PageCase:
    """Strona oceny: etykiety zestawu ``label_id`` i predykcje strony ``file_id``/``page``."""

    label_id: str
    doc_id: str
    file_id: str
    page: int
    format_group: str
    width_px: int
    height_px: int
    gt: tuple[EvalBox, ...]
    pred: tuple[EvalBox, ...]


@dataclass(frozen=True)
class PageEval:
    case: PageCase
    match: PageMatch
    any_kind: tuple[Pair, ...]


def evaluate_page(case: PageCase, params: MatchParams | None = None) -> PageEval:
    params = replace(params or MatchParams(), page_aspect=case.height_px / case.width_px)
    return PageEval(
        case=case,
        match=match_page(case.gt, case.pred, params),
        any_kind=tuple(match_any_kind(case.gt, case.pred, params)),
    )


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    ignored: int = 0  # predykcje dopasowane do etykiet trudnych (uncertain)

    def add(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.ignored += other.ignored

    def as_dict(self) -> dict[str, Any]:
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        recall = self.tp / (self.tp + self.fn) if self.tp + self.fn else None
        # 2TP / (2TP + FP + FN): także 0 przy braku predykcji, gdy są etykiety (i odwrotnie).
        total = 2 * self.tp + self.fp + self.fn
        f1 = 2 * self.tp / total if total else None
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "ignored": self.ignored,
            "precision": _round(precision),
            "recall": _round(recall),
            "f1": _round(f1),
            "precision_ci95": _round_pair(wilson(self.tp, self.tp + self.fp)),
            "recall_ci95": _round_pair(wilson(self.tp, self.tp + self.fn)),
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _round_pair(pair: tuple[float, float] | None) -> list[float] | None:
    return None if pair is None else [round(pair[0], 4), round(pair[1], 4)]


def kind_counts(page: PageEval) -> dict[ElementKind, Counts]:
    counts: dict[ElementKind, Counts] = defaultdict(Counts)
    for pair in page.match.pairs:
        counts[pair.gt.kind].tp += 1
    for pair in page.match.ignored:
        counts[pair.gt.kind].ignored += 1
    for gt in page.match.missed:
        counts[gt.kind].fn += 1
    for pred in page.match.spurious:
        counts[pred.kind].fp += 1
    return counts


@dataclass
class Summary:
    """Zbiorcze metryki zbioru stron (jeden przekrój: całość, format albo dokument)."""

    pages: int = 0
    per_kind: dict[ElementKind, Counts] = field(default_factory=lambda: defaultdict(Counts))
    kind_confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    role_confusion: Counter[tuple[str, str]] = field(default_factory=Counter)
    cells_n: int = 0
    cells_exact: int = 0
    cells_abs_error: int = 0
    multi_n: int = 0
    multi_correct: int = 0
    difficult_gt: int = 0

    def add(self, page: PageEval) -> None:
        self.pages += 1
        for kind, counts in kind_counts(page).items():
            self.per_kind[kind].add(counts)
        self.difficult_gt += sum(g.difficult for g in page.case.gt)
        any_matched_gt = {pair.gt.id for pair in page.any_kind}
        any_matched_pred = {pair.pred.id for pair in page.any_kind}
        for pair in page.any_kind:
            if not pair.gt.difficult:
                self.kind_confusion[(pair.gt.kind.value, pair.pred.kind.value)] += 1
        for gt in page.case.gt:
            grouped = gt.kind is ElementKind.CHECKBOX_GROUP
            if not grouped and not gt.difficult and gt.id not in any_matched_gt:
                self.kind_confusion[(gt.kind.value, MISSED)] += 1
        for pred in page.case.pred:
            if pred.kind is not ElementKind.CHECKBOX_GROUP and pred.id not in any_matched_pred:
                self.kind_confusion[(SPURIOUS, pred.kind.value)] += 1
        for pair in page.match.pairs:
            self.role_confusion[(pair.gt.role.value, pair.pred.role.value)] += 1
            if pair.gt.kind is ElementKind.CHAR_BOXES and pair.gt.cells and pair.pred.cells:
                self.cells_n += 1
                self.cells_exact += pair.gt.cells == pair.pred.cells
                self.cells_abs_error += abs(pair.gt.cells - pair.pred.cells)
            if pair.gt.kind is ElementKind.CHECKBOX_GROUP:
                self.multi_n += 1
                self.multi_correct += pair.gt.multi == pair.pred.multi

    def micro(self) -> Counts:
        total = Counts()
        for counts in self.per_kind.values():
            total.add(counts)
        return total

    def macro_f1(self) -> float | None:
        values = [c.as_dict()["f1"] for c in self.per_kind.values()]
        present = [v for v in values if v is not None]
        return _round(sum(present) / len(present)) if present else None

    def as_dict(self) -> dict[str, Any]:
        role_n = sum(self.role_confusion.values())
        role_ok = sum(n for (g, p), n in self.role_confusion.items() if g == p)
        return {
            "pages": self.pages,
            "micro": self.micro().as_dict(),
            "macro_f1": self.macro_f1(),
            "per_kind": {
                k.value: self.per_kind[k].as_dict() for k in ElementKind if k in self.per_kind
            },
            "difficult_gt": self.difficult_gt,
            "cells": {
                "n": self.cells_n,
                "exact_rate": _round(self.cells_exact / self.cells_n) if self.cells_n else None,
                "mae": _round(self.cells_abs_error / self.cells_n) if self.cells_n else None,
            },
            "roles": {
                "n": role_n,
                "accuracy": _round(role_ok / role_n) if role_n else None,
                "confusion": _nested(self.role_confusion),
            },
            "multi": {
                "n": self.multi_n,
                "accuracy": _round(self.multi_correct / self.multi_n) if self.multi_n else None,
            },
            "kind_confusion": _nested(self.kind_confusion),
        }


def _nested(counter: Counter[tuple[str, str]]) -> dict[str, dict[str, int]]:
    nested: dict[str, dict[str, int]] = {}
    for (row, column), count in sorted(counter.items()):
        nested.setdefault(row, {})[column] = count
    return nested


def summarize(pages: Iterable[PageEval]) -> Summary:
    summary = Summary()
    for page in pages:
        summary.add(page)
    return summary


def macro_over(groups: dict[str, Summary]) -> float | None:
    """Średnia mikro-F1 przekrojów (np. dokumentów), bez przekrojów bez par i błędów."""
    values = [s.micro().as_dict()["f1"] for s in groups.values()]
    present = [v for v in values if v is not None]
    return _round(sum(present) / len(present)) if present else None


# --- Pola (ablacja kanału wizualnego) ---


def field_targets(gt: Sequence[EvalBox]) -> list[EvalBox]:
    """Pola formularza według etykiet: elementy do wypełnienia, grupa wyboru jako jedno pole."""
    members = {o for g in gt if g.kind is ElementKind.CHECKBOX_GROUP for o in g.options}
    return [g for g in gt if g.role in FIELD_ROLES and g.id not in members]


@dataclass(frozen=True)
class FieldLocation:
    """Miejsce pola w wyniku pipeline'u (``FieldSource.bbox`` w #17) na ocenianej stronie."""

    id: str
    box: Box


def _field_box(box: Box, params: MatchParams) -> Box:
    x0, y0, x1, y1 = box
    thin = y1 - y0 < params.min_line_height
    wide = (x1 - x0) >= params.line_aspect * max((y1 - y0) * params.page_aspect, 1e-9)
    if not (thin and wide):
        return box
    center, half = (y0 + y1) / 2, params.min_line_height / 2
    return x0, center - half, x1, center + half


def field_counts(
    gt: Sequence[EvalBox], fields: Sequence[FieldLocation], params: MatchParams | None = None
) -> Counts:
    """P/R pól jednej strony: dopasowanie IoU miejsc pól do pól z etykiet (trudne ignorowane)."""
    params = params or MatchParams()
    targets = field_targets(gt)
    scores = {}
    for f in fields:
        f_box = _field_box(f.box, params)
        for t in targets:
            value = iou(_field_box(t.box, params), f_box)
            if value > 0:
                scores[(t.id, f.id)] = value
    pred_boxes = [
        EvalBox(id=f.id, kind=ElementKind.TABLE_CELL, box=f.box, role=ElementRole.INPUT)
        for f in fields
    ]
    pairs = greedy(targets, pred_boxes, scores, params.iou)
    counts = Counts()
    matched_targets = {p.gt.id for p in pairs}
    for pair in pairs:
        if pair.gt.difficult:
            counts.ignored += 1
        else:
            counts.tp += 1
    counts.fn = sum(1 for t in targets if t.id not in matched_targets and not t.difficult)
    counts.fp = len(fields) - len(pairs)
    return counts


def ablation(
    pages: Sequence[tuple[PageCase, Sequence[FieldLocation], Sequence[FieldLocation]]],
    params: MatchParams | None = None,
) -> dict[str, Any]:
    """Pola wyniku pipeline'u z kanałem wizualnym i bez niego, na tych samych stronach."""
    with_visual, without_visual = Counts(), Counts()
    for case, fields_with, fields_without in pages:
        page_params = replace(params or MatchParams(), page_aspect=case.height_px / case.width_px)
        with_visual.add(field_counts(case.gt, fields_with, page_params))
        without_visual.add(field_counts(case.gt, fields_without, page_params))
    a, b = with_visual.as_dict(), without_visual.as_dict()
    return {
        "with_visual": a,
        "without_visual": b,
        "delta_precision": _delta(a["precision"], b["precision"]),
        "delta_recall": _delta(a["recall"], b["recall"]),
    }


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 4)
