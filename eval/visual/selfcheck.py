"""Kontrola miernika na oznaczonych stronach ``dev`` (#12): etykiety jako predykcje z zaburzeniami.

Oczekiwania (README, „Metryki”): etykiety bez zmian dają P = R = 1, przesunięcie o 1–2 px nie
zmienia wyniku, przesunięcie o pół szerokości elementu go zeruje, a usunięcie co 10. elementu
daje R ≈ 0,9 przy P = 1. Działa wyłącznie na części ``dev`` (nie ma opcji ``holdout``)::

    python -m eval.visual.selfcheck
"""

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from eval.visual.match import EvalBox, MatchParams, from_label
from eval.visual.metrics import PageCase, evaluate_page, summarize
from eval.visual.models import Manifest, Split, read_json, write_text
from eval.visual.run import (
    DEFAULT_LABELS,
    DEFAULT_MANIFEST,
    DEFAULT_OUT,
    format_group,
    load_dataset,
)

Perturb = Callable[[tuple[EvalBox, ...], int, int], tuple[EvalBox, ...]]


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def shift(dx_px: float, dy_px: float) -> Perturb:
    def apply(boxes: tuple[EvalBox, ...], width: int, height: int) -> tuple[EvalBox, ...]:
        dx, dy = dx_px / width, dy_px / height
        return tuple(
            replace(
                b,
                box=(
                    _clamp(b.box[0] + dx),
                    _clamp(b.box[1] + dy),
                    _clamp(b.box[2] + dx),
                    _clamp(b.box[3] + dy),
                ),
            )
            for b in boxes
        )

    return apply


def shift_half_width(boxes: tuple[EvalBox, ...], width: int, height: int) -> tuple[EvalBox, ...]:
    del width, height
    shifted = []
    for b in boxes:
        half = (b.box[2] - b.box[0]) / 2
        x0 = b.box[0] + half if b.box[2] + half <= 1 else b.box[0] - half
        shifted.append(replace(b, box=(x0, b.box[1], x0 + 2 * half, b.box[3])))
    return tuple(shifted)


def drop_every_tenth(boxes: tuple[EvalBox, ...], width: int, height: int) -> tuple[EvalBox, ...]:
    del width, height
    ordered = sorted(boxes, key=lambda b: b.id)
    return tuple(b for index, b in enumerate(ordered, start=1) if index % 10 != 0)


SCENARIOS: dict[str, Perturb] = {
    "etykiety bez zmian": lambda boxes, width, height: boxes,
    "przesunięcie 1 px (x i y)": shift(1, 1),
    "przesunięcie 2 px (x i y)": shift(2, 2),
    "przesunięcie o pół szerokości": shift_half_width,
    "bez co 10. elementu": drop_every_tenth,
}


def selfcheck(
    manifest: Manifest, labels: Path, params: MatchParams | None = None
) -> dict[str, Any]:
    dataset = load_dataset(manifest, labels, Split.DEV)
    results: dict[str, Any] = {}
    for name, perturb in SCENARIOS.items():
        cases = []
        for item in dataset.labeled:
            files = {f.file_id: f for f in item.document.files}
            gt = tuple(from_label(e) for e in item.labels.elements)
            for variant in item.page.variants:
                if not variant.included:
                    continue
                cases.append(
                    PageCase(
                        label_id=item.page.label_id,
                        doc_id=item.document.doc_id,
                        file_id=variant.file_id,
                        page=variant.page,
                        format_group=format_group(files[variant.file_id], variant),
                        width_px=item.page.width_px,
                        height_px=item.page.height_px,
                        gt=gt,
                        pred=perturb(gt, item.page.width_px, item.page.height_px),
                    )
                )
        summary = summarize(evaluate_page(case, params) for case in cases).as_dict()
        results[name] = {
            "pages": summary["pages"],
            **{k: summary["micro"][k] for k in ("tp", "fp", "fn", "precision", "recall", "f1")},
        }
    return {
        "split": Split.DEV.value,
        "labeled": [item.page.label_id for item in dataset.labeled],
        "label_sets": dataset.label_sets,
        "scenarios": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kontrola miernika na oznaczonych stronach dev")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        result = selfcheck(read_json(args.manifest, Manifest), args.labels)
    except (ValueError, OSError) as error:
        print(f"BŁĄD: {error}", file=sys.stderr)
        return 2
    write_text(
        args.out / "selfcheck-dev.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"dev: {len(result['labeled'])} z {result['label_sets']} zestawów etykiet")
    for name, row in result["scenarios"].items():
        scores = f"P={row['precision']}  R={row['recall']}  F1={row['f1']}"
        print(f"{name:32} stron {row['pages']:3}  {scores}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
