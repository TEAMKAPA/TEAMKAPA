"""Bloki tekstu strony PDF z warstwy tekstowej (pypdfium2) dla trybu 1 VLM w ewaluacji (#11).

**Tymczasowy adapter do czasu integracji z kanałem tekstowym (#7, Docling/OCR):** w pipeline bloki
z ID ``p{strona}-b{n}`` i bbox dostarcza krok 02. Tutaj (tylko ewaluacja na PDF) fragmenty tekstu
z jednej linii są scalane, gdy przerwa nie przekracza wysokości linii, a bloki złożone wyłącznie
z kropek, podkreślników lub wielokropków są pomijane (to miejsca do wpisania, nie etykiety).
Detektory OpenCV z warstwy tekstowej nie korzystają (``docs/vision.md``).
"""

import re
from dataclasses import dataclass

import pypdfium2 as pdfium

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.vlm.roles import TextBlock

FILLER = re.compile(r"^[\s._…·_\-]*$")


@dataclass
class _Run:
    left: float
    bottom: float
    right: float
    top: float
    text: str


def _merge(runs: list[_Run]) -> list[_Run]:
    lines: list[list[_Run]] = []
    for run in sorted(runs, key=lambda r: (-r.top, r.left)):
        for line in lines:
            ref = line[0]
            overlap = min(ref.top, run.top) - max(ref.bottom, run.bottom)
            if overlap >= 0.5 * min(ref.top - ref.bottom, run.top - run.bottom):
                line.append(run)
                break
        else:
            lines.append([run])
    merged: list[_Run] = []
    for line in lines:
        current: _Run | None = None
        for run in sorted(line, key=lambda r: r.left):
            height = run.top - run.bottom
            if current is not None and run.left - current.right <= height:
                current = _Run(
                    current.left,
                    min(current.bottom, run.bottom),
                    max(current.right, run.right),
                    max(current.top, run.top),
                    f"{current.text} {run.text}".strip(),
                )
            else:
                if current is not None:
                    merged.append(current)
                current = run
        if current is not None:
            merged.append(current)
    return merged


def pdf_text_blocks(data: bytes, page_number: int) -> list[TextBlock]:
    """Bloki tekstu strony ``page_number`` (od 1), bbox w ułamkach strony (CropBox = MediaBox)."""
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[page_number - 1]
        if page.get_rotation() != 0:
            raise ValueError("strona z /Rotate: adapter obsługuje tylko strony bez obrotu")
        width, height = page.get_size()
        textpage = page.get_textpage()
        runs = []
        for index in range(textpage.count_rects()):
            left, bottom, right, top = textpage.get_rect(index)
            text = " ".join(textpage.get_text_bounded(left, bottom, right, top).split())
            if text:
                runs.append(_Run(left, bottom, right, top, text))
    finally:
        pdf.close()
    blocks: list[TextBlock] = []
    for run in sorted(_merge(runs), key=lambda r: (-r.top, r.left)):
        if FILLER.match(run.text):
            continue
        blocks.append(
            TextBlock(
                id=f"p{page_number}-b{len(blocks) + 1}",
                page=page_number,
                text=run.text,
                bbox=BoundingBox(
                    x0=max(0.0, run.left / width),
                    y0=max(0.0, 1 - run.top / height),
                    x1=min(1.0, run.right / width),
                    y1=min(1.0, 1 - run.bottom / height),
                ),
            )
        )
    return blocks
