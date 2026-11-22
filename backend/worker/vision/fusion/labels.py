"""Etykieta pola z kilku odczytów: warstwa tekstowa (Docling), OCR, drugi OCR, VLM (E.4, brief 7.6).

- **PDF z warstwą tekstu:** wygrywa warstwa tekstowa. Inne odczyty różne od niej to tylko
  informacja (``LABEL_VARIANT``), bo warstwa tekstowa jest dokładnym źródłem.
- **Skan:** głosowanie. Odczyty są zgodne, gdy podobieństwo etykiet (``label_similarity``, to samo
  co w porównaniu pól Etapu D) ≥ ``label_agree``. Wygrywa grupa zgodnych odczytów, która ma
  większość (2 z 3, 2 z 2). Tekst etykiety pochodzi zawsze z odczytu kanału tekstowego
  (OCR), a VLM tylko głosuje (etykiety pochodzą ze źródła, nie z VLM).
  - jeden odczyt: ``LABEL_SINGLE_READ`` (najwyżej MEDIUM): brak głosowania,
  - brak większości albo większość bez odczytu tekstowego: ``LABEL_DISAGREEMENT`` (LOW).

Drugi odczyt OCR skanu musi dostarczyć #7 (propozycja w ``docs/vision.md``); do tego czasu
skan ma zwykle dwa odczyty: OCR i VLM (tryb 2), a przy braku pary w trybie 2 tylko jeden.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.vlm.double_read import label_similarity

ReadingSource = Literal["text_layer", "ocr", "ocr2", "vlm"]
TEXT_SOURCES: tuple[ReadingSource, ...] = ("text_layer", "ocr", "ocr2")  # kolejność pierwszeństwa


@dataclass(frozen=True)
class LabelReading:
    source: ReadingSource
    text: str


@dataclass(frozen=True)
class LabelDecision:
    text: str | None  # etykieta przyjęta (z kanału tekstowego) albo None
    source: ReadingSource | None
    codes: tuple[str, ...]
    agreeing: int  # liczba odczytów zgodnych z przyjętą etykietą (z nią samą)
    reads: int  # liczba odczytów niepustych


def _agree(a: str, b: str, cfg: FusionConfig) -> bool:
    return label_similarity(a, b) >= cfg.label_agree


def resolve_label(
    readings: Sequence[LabelReading], *, native_text: bool, cfg: FusionConfig | None = None
) -> LabelDecision:
    cfg = cfg or FusionConfig()
    reads = [r for r in readings if r.text.strip()]
    if not reads:
        return LabelDecision(None, None, (), 0, 0)
    rank = {source: i for i, source in enumerate(TEXT_SOURCES)}

    layer = next((r for r in reads if r.source == "text_layer"), None)
    if native_text and layer is not None:
        others = [r for r in reads if r is not layer]
        agreeing = 1 + sum(_agree(layer.text, r.text, cfg) for r in others)
        variant = (codes.LABEL_VARIANT,) if agreeing < len(reads) else ()
        return LabelDecision(layer.text, layer.source, variant, agreeing, len(reads))

    if len(reads) == 1:
        only = reads[0]
        if only.source == "vlm":  # etykieta wyłącznie z VLM nie jest źródłem
            return LabelDecision(None, None, (codes.LABEL_DISAGREEMENT,), 1, 1)
        return LabelDecision(only.text, only.source, (codes.LABEL_SINGLE_READ,), 1, 1)

    groups = [[r for r in reads if r is c or _agree(c.text, r.text, cfg)] for c in reads]
    best = max(groups, key=len)  # max zwraca pierwszą największą: kolejność odczytów
    text_reads = sorted((r for r in best if r.source != "vlm"), key=lambda r: rank[r.source])
    if 2 * len(best) <= len(reads) or not text_reads:
        return LabelDecision(None, None, (codes.LABEL_DISAGREEMENT,), len(best), len(reads))
    winner = text_reads[0]
    return LabelDecision(winner.text, winner.source, (), len(best), len(reads))
