"""Tryb 2 (#11): podwójny odczyt pól. VLM wypisuje pola strony wyłącznie z obrazu, a wynik jest
porównywany z polami z kanału tekstowego.

- Pola z tekstu dostarcza ``TextFieldSource``: docelowo LLM tekstowy (krok 7, #8); do tego
  czasu ``ReferenceJsonFieldSource`` czyta oczekiwany JSON COI (materiał referencyjny) albo fake.
- Porównanie: etykiety znormalizowane (małe litery, bez interpunkcji i dwukropka) i porównane
  średnią ``token_set_ratio`` i ``token_sort_ratio`` (rapidfuzz). Para jest dopasowana przy
  podobieństwie ≥ ``label_match`` albo ≥ ``label_match_low``, gdy oba pola mają bbox na tej samej
  stronie i IoU ≥ 0,3. Dopasowanie zachłanne po malejącym wyniku (podobieństwo + IoU).
- Kody: ``MISSING_IN_VLM`` (pole z tekstu bez pary), ``MISSING_IN_TEXT`` (pole z obrazu bez pary),
  ``TYPE_MISMATCH`` (para o rodzajach z różnych grup: wpis / wybór / podpis),
  ``CELLS_LABEL_CONFLICT`` (liczba kratek sprzeczna z etykietą, np. PESEL ≠ 11).
"""

import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from rapidfuzz import fuzz

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import ImageArray
from backend.worker.vision.vlm.client import VlmClient, VlmError, VlmUsage
from backend.worker.vision.vlm.codes import (
    CELLS_LABEL_CONFLICT,
    MISSING_IN_TEXT,
    MISSING_IN_VLM,
    TYPE_MISMATCH,
    ReviewItem,
)
from backend.worker.vision.vlm.config import VlmSettings
from backend.worker.vision.vlm.image import page_image
from backend.worker.vision.vlm.prompts import load_prompt
from backend.worker.vision.vlm.schemas import FieldKind, FieldsResponse
from backend.worker.vision.vlm.structured import ask

TASK = "fields"
PROMPT = "fields_v1"
LABEL_MATCH = 80.0
LABEL_MATCH_LOW = 50.0
BBOX_MATCH = 0.3

GROUPS: Mapping[str, str] = {
    "text": "entry",
    "number": "entry",
    "date": "entry",
    "char_boxes": "entry",
    "choice": "choice",
    "signature": "signature",
}
"""Rodzaje zgodne, gdy należą do jednej grupy (np. PESEL jako tekst lub kratki)."""

REFERENCE_KINDS: Mapping[str, FieldKind | None] = {
    "TEXT_FIELD": "text",
    "TEXTAREA": "text",
    "EMAIL": "text",
    "PHONE": "text",
    "NUMBER": "number",
    "DATE": "date",
    "DATE_PICKER": "date",
    "RADIO": "choice",
    "SELECT": "choice",
    "MULTISELECT": "choice",
    "CHECKBOX": "choice",
    "CONSENT_SECTION": "choice",
    "SIGNATURE": "signature",
    # Nie są polami wypełnianymi na papierowym formularzu:
    "SECTION": None,
    "TEXT_BLOCK": None,
    "FILE_UPLOADER": None,
}

EXPECTED_CELLS: tuple[tuple[re.Pattern[str], frozenset[int]], ...] = (
    (re.compile(r"\bpesel\b"), frozenset({11})),
    (re.compile(r"\bnip\b"), frozenset({10})),
    (re.compile(r"\bregon\b"), frozenset({9, 14})),
    (re.compile(r"\bkrs\b"), frozenset({10})),
    (re.compile(r"\bkod\w* pocztow"), frozenset({2, 3, 5})),
)
"""Liczba kratek wynikająca z etykiety (kod pocztowy: grupy 2 i 3 albo całość 5)."""


@dataclass(frozen=True)
class FormField:
    """Pole formularza z jednego kanału (tekst albo obraz)."""

    id: str
    label: str
    kind: FieldKind
    page: int | None = None
    bbox: BoundingBox | None = None
    cells: int | None = None


class TextFieldSource(Protocol):
    def fields(self) -> list[FormField]: ...


@dataclass
class ReferenceJsonFieldSource:
    """Pola z oczekiwanego JSON-a COI (schemat ``docs/coi``): zagnieżdżone obiekty ``field``.

    Typy spoza ``REFERENCE_KINDS`` są liczone w ``skipped_types`` (nie są zgadywane).
    """

    document: Mapping[str, Any]
    skipped_types: dict[str, int] = field(default_factory=dict)

    def _walk(self, node: object) -> Iterator[Mapping[str, Any]]:
        if isinstance(node, Mapping):
            if isinstance(node.get("type"), str) and isinstance(node.get("title"), str):
                yield node
            for value in node.values():
                yield from self._walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from self._walk(item)

    def fields(self) -> list[FormField]:
        result: list[FormField] = []
        for index, node in enumerate(self._walk(self.document), start=1):
            node_type = str(node["type"])
            if node_type not in REFERENCE_KINDS:
                self.skipped_types[node_type] = self.skipped_types.get(node_type, 0) + 1
                continue
            kind = REFERENCE_KINDS[node_type]
            label = str(node["title"]).strip()
            if kind is None or not label:
                continue
            identifier = str(node.get("techName") or node.get("id") or f"ref-{index}")
            result.append(FormField(id=identifier, label=label, kind=kind))
        return result


def normalize(label: str) -> str:
    text = unicodedata.normalize("NFC", label).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def expected_cells(label: str) -> frozenset[int] | None:
    text = normalize(label)
    for pattern, cells in EXPECTED_CELLS:
        if pattern.search(text):
            return cells
    return None


def cells_conflict(label: str, cells: int | None) -> bool:
    expected = expected_cells(label)
    return expected is not None and cells is not None and cells not in expected


def label_similarity(a: str, b: str) -> float:
    """Średnia ``token_set_ratio`` i ``token_sort_ratio`` (0–100) etykiet znormalizowanych.

    Sam ``token_set_ratio`` daje 100 dla etykiety będącej podzbiorem drugiej („Adres” i „Adres
    zamieszkania”); średnia z ``token_sort_ratio`` obniża taki wynik, a skróty z jednym słowem
    dodatkowym („PESEL” i „Numer PESEL”) nadal przechodzą próg.
    """
    x, y = normalize(a), normalize(b)
    return (fuzz.token_set_ratio(x, y) + fuzz.token_sort_ratio(x, y)) / 2


def _iou(a: BoundingBox | None, b: BoundingBox | None) -> float:
    if a is None or b is None:
        return 0.0
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - inter
    return inter / union if union > 0 else 0.0


@dataclass(frozen=True)
class FieldMatch:
    text: FormField
    vlm: FormField
    similarity: float
    iou: float


@dataclass(frozen=True)
class Comparison:
    matches: tuple[FieldMatch, ...]
    review: tuple[ReviewItem, ...]


def compare(
    text_fields: Sequence[FormField],
    vlm_fields: Sequence[FormField],
    *,
    label_match: float = LABEL_MATCH,
    label_match_low: float = LABEL_MATCH_LOW,
) -> Comparison:
    candidates: list[tuple[float, int, int, float, float]] = []
    for i, text in enumerate(text_fields):
        for j, image in enumerate(vlm_fields):
            if text.page is not None and image.page is not None and text.page != image.page:
                continue
            similarity = label_similarity(text.label, image.label)
            overlap = _iou(text.bbox, image.bbox)
            if similarity >= label_match or (
                similarity >= label_match_low and overlap >= BBOX_MATCH
            ):
                candidates.append((similarity / 100 + overlap, i, j, similarity, overlap))
    candidates.sort(key=lambda c: (-round(c[0], 9), c[1], c[2]))
    used_text: set[int] = set()
    used_vlm: set[int] = set()
    matches: list[FieldMatch] = []
    for _, i, j, similarity, overlap in candidates:
        if i in used_text or j in used_vlm:
            continue
        used_text.add(i)
        used_vlm.add(j)
        matches.append(
            FieldMatch(text_fields[i], vlm_fields[j], round(similarity, 1), round(overlap, 3))
        )

    review: list[ReviewItem] = []
    for match in matches:
        if GROUPS[match.text.kind] != GROUPS[match.vlm.kind]:
            review.append(
                ReviewItem(
                    code=TYPE_MISMATCH,
                    page=match.vlm.page,
                    label=match.text.label,
                    detail=f"tekst: {match.text.kind}, obraz: {match.vlm.kind}",
                )
            )
    for i, text in enumerate(text_fields):
        if i not in used_text:
            review.append(ReviewItem(code=MISSING_IN_VLM, page=text.page, label=text.label))
    for j, image in enumerate(vlm_fields):
        if j not in used_vlm:
            review.append(ReviewItem(code=MISSING_IN_TEXT, page=image.page, label=image.label))
        expected = expected_cells(image.label)
        if cells_conflict(image.label, image.cells):
            review.append(
                ReviewItem(
                    code=CELLS_LABEL_CONFLICT,
                    page=image.page,
                    label=image.label,
                    detail=f"kratek {image.cells}, oczekiwano {sorted(expected or ())}",
                )
            )
    return Comparison(matches=tuple(matches), review=tuple(review))


Status = Literal["ok", "invalid", "unavailable"]


@dataclass(frozen=True)
class FieldsRead:
    page: int
    fields: tuple[FormField, ...]
    status: Status
    review: tuple[ReviewItem, ...] = ()
    attempts: int = 0
    usage: VlmUsage = field(default_factory=VlmUsage)
    cached: bool = False


def read_fields(
    page: int, image: ImageArray, client: VlmClient, settings: VlmSettings
) -> FieldsRead:
    """Tryb 2 dla jednej strony: pola z samego obrazu (bez tekstu dokumentu w prompcie)."""
    picture = page_image(image, settings.image_max_side_px)
    try:
        answer = ask(client, TASK, load_prompt(PROMPT), {}, picture, FieldsResponse)
    except VlmError as error:
        failure = ReviewItem(code=error.code, page=page, detail=f"tryb 2: {error.detail}")
        status: Status = "unavailable" if error.code == "VLM_UNAVAILABLE" else "invalid"
        return FieldsRead(page=page, fields=(), status=status, review=(failure,))
    fields: list[FormField] = []
    for index, item in enumerate(answer.value.fields, start=1):
        box = None
        if item.bbox is not None:
            x0, y0, x1, y1 = item.bbox
            if 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1:
                box = BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)
        label = item.label.strip()
        if not label:
            continue
        fields.append(
            FormField(
                id=f"vlmf-p{page}-{index:03d}",
                label=label,
                kind=item.kind,
                page=page,
                bbox=box,
                cells=item.cells if item.kind == "char_boxes" else None,
            )
        )
    return FieldsRead(
        page=page,
        fields=tuple(fields),
        status="ok",
        attempts=answer.attempts,
        usage=answer.usage,
        cached=answer.cached,
    )
