"""Dane testowe VLM (#11): syntetyczna strona, bloki tekstu i nagrane odpowiedzi fake.

Nagrania (``tests/vision/recordings/vlm/*.json``) są **syntetyczne**: wygenerowane z etykiet strony
syntetycznej (``synth_forms.make_form``), a nie z prawdziwego modelu, i nie zawierają treści COI.
Prawdziwe odpowiedzi z materiałów COI zostają w ``dane/robocze/vlm_cache/``.

Ponowne wygenerowanie (po zmianie detektorów lub generatora)::

    python -m tests.vision.vlm_synth
"""

import json
from functools import cache
from pathlib import Path

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.detect.page import detect_image
from backend.worker.vision.models import ElementKind, ElementRole, VisualElement
from backend.worker.vision.vlm.client import VlmRequest
from backend.worker.vision.vlm.roles import TextBlock
from tests.vision.synth_forms import SynthForm, make_form

RECORDINGS = Path(__file__).resolve().parent / "recordings" / "vlm"
SEED = 0
LABELS = {  # syntetyczne podpisy pól (nie pochodzą z formularzy COI)
    ElementKind.CHAR_BOXES: "Numer identyfikacyjny",
    ElementKind.CHECKBOX: "Zgoda",
    ElementKind.CHECKBOX_GROUP: "Wybór wariantu",
    ElementKind.TEXT_LINE: "Uwagi",
    ElementKind.DOTTED_LINE: "Opis",
    ElementKind.TABLE_CELL: "Pole tekstowe",
    ElementKind.SIGNATURE: "Podpis",
}


@cache
def page() -> SynthForm:
    return make_form(seed=SEED)


@cache
def detected() -> tuple[VisualElement, ...]:
    form = page()
    return detect_image(form.image, page=1, dpi=form.dpi).elements


def blocks() -> list[TextBlock]:
    """Bloki tekstu: paski „tekstu” strony syntetycznej z syntetycznymi podpisami."""
    form = page()
    result = []
    for index, box in enumerate(form.text, start=1):
        result.append(
            TextBlock(
                id=f"p1-b{index}",
                page=1,
                text=f"Podpis pola {index}",
                bbox=BoundingBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
            )
        )
    return result


def _iou(a: BoundingBox, b: tuple[float, float, float, float]) -> float:
    w = min(a.x1, b[2]) - max(a.x0, b[0])
    h = min(a.y1, b[3]) - max(a.y0, b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a.x1 - a.x0) * (a.y1 - a.y0) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def _nearest_block(element: VisualElement, candidates: list[TextBlock]) -> str | None:
    """Blok na lewo lub powyżej elementu (najbliższy) jako etykieta."""
    best: tuple[float, str] | None = None
    for block in candidates:
        if block.bbox is None:
            continue
        dx = element.bbox.x0 - block.bbox.x1
        dy = element.bbox.y0 - block.bbox.y1
        if block.bbox.y1 < element.bbox.y0 - 0.05 or block.bbox.y0 > element.bbox.y1:
            continue
        distance = abs(dx) + abs(dy)
        if best is None or distance < best[0]:
            best = (distance, block.id)
    return best[1] if best else None


def roles_response() -> dict[str, object]:
    """Odpowiedź trybu 1 zgodna z etykietami strony syntetycznej."""
    form = page()
    gt = form.elements
    answers = []
    for element in detected():
        match = max(gt, key=lambda g: _iou(element.bbox, g.box))
        role = match.role if _iou(element.bbox, match.box) >= 0.5 else ElementRole.UNKNOWN
        if element.kind is ElementKind.SECTION_FRAME:
            role = ElementRole.SECTION
        answers.append(
            {
                "id": element.id,
                "role": role.value,
                "label_block_id": _nearest_block(element, blocks()),
            }
        )
    return {
        "elements": answers,
        "added": [
            {
                "kind": "text_line",
                "bbox": [0.1, 0.92, 0.5, 0.925],
                "role": "input",
                "label_block_id": None,
                "cells": None,
            },
        ],
    }


def fields_response() -> dict[str, object]:
    """Odpowiedź trybu 2: pola strony syntetycznej odczytane „z obrazu”."""
    form = page()
    fields = []
    members = {m for g in form.elements for m in g.members}
    for index, element in enumerate(form.elements):
        if element.role not in {ElementRole.INPUT, ElementRole.CHOICE, ElementRole.E_SIGNATURE}:
            continue
        if index in members:
            continue  # checkbox grupy: grupa to jedno pole
        kind = {
            ElementKind.CHAR_BOXES: "char_boxes",
            ElementKind.CHECKBOX: "choice",
            ElementKind.CHECKBOX_GROUP: "choice",
            ElementKind.SIGNATURE: "signature",
        }.get(element.kind, "text")
        fields.append(
            {
                "label": LABELS.get(element.kind, "Pole"),
                "kind": kind,
                "bbox": [round(v, 4) for v in element.box],
                "cells": element.cells,
            }
        )
    return {"fields": fields}


ROLE_BY_KIND = {
    "char_boxes": "input",
    "text_line": "input",
    "dotted_line": "input",
    "table_cell": "input",
    "checkbox": "choice",
    "checkbox_group": "choice",
    "section_frame": "section",
}


def echo_responder(request: VlmRequest) -> str:
    """Fake dla dowolnej strony: role według kategorii elementów z bloku danych, pola z nagrania."""
    if request.task == "fields":
        return recording("synth_fields")
    section = request.prompt.split("ELEMENTS (", 1)[1].split("TEXT BLOCKS (", 1)[0]
    answers = []
    for line in section.splitlines()[1:]:
        if line.startswith("{"):
            row = json.loads(line)
            answers.append(
                {"id": row["id"], "role": ROLE_BY_KIND[row["kind"]], "label_block_id": None}
            )
    return json.dumps({"elements": answers, "added": []})


def recording(name: str) -> str:
    return (RECORDINGS / f"{name}.json").read_text(encoding="utf-8")


def write() -> None:
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    for name, data in (("synth_roles", roles_response()), ("synth_fields", fields_response())):
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        (RECORDINGS / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write()
