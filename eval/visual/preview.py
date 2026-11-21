"""Nakładki do kontroli etykiet (#12): ramki elementów narysowane na obrazie strony.

Nakładki zawierają obraz formularza, więc zapisujemy je wyłącznie w ``dane/robocze/``.
Kolor ramki to kolor kategorii z ``labelstudio/config.xml`` (jak w Label Studio), linia
przerywana oznacza rolę ``skip``, czerwona gruba ramka to region z błędem. Opisy są w ASCII,
bo wbudowana czcionka Pillow nie ma polskich znaków.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from backend.worker.vision.models import ElementKind, ImageArray
from eval.visual import ls

PixelBox = tuple[int, int, int, int]
"""Krawędzie pikseli (x0, y0, x1, y1), ``x1`` i ``y1`` wyłączne, jak ``BoundingBox`` × rozmiar."""

Rgb = tuple[int, int, int]

ABBREVIATIONS: dict[ElementKind, str] = {
    ElementKind.CHAR_BOXES: "KR",
    ElementKind.CHECKBOX: "CHK",
    ElementKind.CHECKBOX_GROUP: "GRP",
    ElementKind.TEXT_LINE: "LIN",
    ElementKind.DOTTED_LINE: "DOT",
    ElementKind.TABLE_CELL: "CEL",
    ElementKind.SECTION_FRAME: "SEC",
    ElementKind.SIGNATURE: "SIG",
    ElementKind.OFFICE_ONLY: "URZ",
}

ERROR_COLOR: Rgb = (255, 0, 0)
UNKNOWN_COLOR: Rgb = (128, 128, 128)
LEGEND_HEIGHT = 150
FONT_SIZE = 15
DASH, GAP = 8, 5
SECTION_OUTSET = 4  # px: ramka sekcji rysowana na zewnątrz, by nie znikała pod komórkami
MARK_RADIUS = 20  # px: okrąg wokół bardzo małego regionu z błędem


@dataclass(frozen=True)
class PreviewBox:
    box: PixelBox
    kind: ElementKind | None
    tag: str
    dashed: bool = False  # rola skip
    error: bool = False


def _rgb(color: str) -> Rgb:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def kind_colors() -> dict[ElementKind, Rgb]:
    colors = ls.label_colors()
    return {kind: _rgb(colors[kind.value]) for kind in ElementKind}


def _dashed_line(
    draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int], fill: Rgb, width: int
) -> None:
    (x0, y0), (x1, y1) = start, end
    length = max(abs(x1 - x0), abs(y1 - y0), 1)

    def point(offset: int) -> tuple[int, int]:
        return x0 + (x1 - x0) * offset // length, y0 + (y1 - y0) * offset // length

    for position in range(0, length, DASH + GAP):
        draw.line([point(position), point(min(position + DASH, length))], fill=fill, width=width)


def _rectangle(
    draw: ImageDraw.ImageDraw, box: PixelBox, fill: Rgb, width: int, *, dashed: bool
) -> None:
    x0, y0, x1, y1 = box[0], box[1], box[2] - 1, box[3] - 1  # krawędzie → ostatni piksel
    if not dashed:
        draw.rectangle([x0, y0, max(x0, x1), max(y0, y1)], outline=fill, width=width)
        return
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for index, start in enumerate(corners):
        _dashed_line(draw, start, corners[(index + 1) % 4], fill, width)


def _color(item: PreviewBox, colors: dict[ElementKind, Rgb]) -> Rgb:
    if item.error:
        return ERROR_COLOR
    return UNKNOWN_COLOR if item.kind is None else colors[item.kind]


def _text_color(background: Rgb) -> Rgb:
    luminance = 0.299 * background[0] + 0.587 * background[1] + 0.114 * background[2]
    return (0, 0, 0) if luminance > 140 else (255, 255, 255)


def _overlaps(a: PixelBox, b: PixelBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _tag(
    draw: ImageDraw.ImageDraw,
    box: PixelBox,
    text: str,
    color: Rgb,
    font: ImageFont.FreeTypeFont,
    placed: list[PixelBox],
) -> None:
    """Opis nad lewym górnym rogiem ramki; gdy zajęte, kolejne miejsca w prawo, potem wewnątrz."""
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    width, height = math.ceil(right - left) + 4, math.ceil(bottom - top) + 4
    rows = [box[1] - height, box[1] + 1] if box[1] - height >= 0 else [box[1] + 1]
    candidates = [
        (x, y) for y in rows for x in range(box[0], max(box[0], box[2] - width) + 1, width + 2)
    ]
    rects = [(x, y, x + width, y + height) for x, y in candidates]
    rect = next((r for r in rects if not any(_overlaps(r, p) for p in placed)), rects[0])
    placed.append(rect)
    draw.rectangle(list(rect), fill=color)
    draw.text((rect[0] + 2 - left, rect[1] + 2 - top), text, font=font, fill=_text_color(color))


def _legend(
    draw: ImageDraw.ImageDraw,
    top: int,
    width: int,
    colors: dict[ElementKind, Rgb],
    font: ImageFont.FreeTypeFont,
    note: str,
) -> None:
    draw.rectangle([0, top, width, top + LEGEND_HEIGHT], fill=(255, 255, 255))
    draw.line([0, top, width, top], fill=(0, 0, 0), width=2)
    column = max(width // 5, 1)
    for index, kind in enumerate(ElementKind):
        x, y = 10 + (index % 5) * column, top + 10 + (index // 5) * 26
        draw.rectangle([x, y, x + 30, y + 16], outline=colors[kind], width=3)
        draw.text((x + 38, y), f"{ABBREVIATIONS[kind]} {kind.value}", font=font, fill=(0, 0, 0))
    lines = (
        "linia ciagla: rola domyslna lub wybrana | przerywana: skip | skip* = rola wywnioskowana"
        f" przez konwerter | ? = uncertain | SEC rysowana {SECTION_OUTSET} px na zewnatrz",
        "KR x10 = liczba kratek | GRP single/multi/unknown | CHK g012 = checkbox w grupie 012"
        " | czerwona ramka ERR: blad (opis w convert_report.md)",
        note,
    )
    for row, line in enumerate(lines):
        draw.text((10, top + 66 + row * 24), line, font=font, fill=(0, 0, 0))


def draw_preview(image: ImageArray, boxes: Sequence[PreviewBox], note: str = "") -> PILImage.Image:
    """Obraz strony (rozjaśniony) z ramkami i opisami oraz legendą pod spodem."""
    page = PILImage.fromarray(image).convert("RGB")
    page = PILImage.blend(page, PILImage.new("RGB", page.size, (255, 255, 255)), 0.3)
    canvas = PILImage.new("RGB", (page.width, page.height + LEGEND_HEIGHT), (255, 255, 255))
    canvas.paste(page, (0, 0))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=FONT_SIZE)
    if not isinstance(font, ImageFont.FreeTypeFont):  # pragma: no cover - Pillow bez FreeType
        raise RuntimeError("nakładki wymagają Pillow z obsługą FreeType")
    colors = kind_colors()

    def area(item: PreviewBox) -> int:
        return (item.box[2] - item.box[0]) * (item.box[3] - item.box[1])

    ordered = sorted(boxes, key=lambda item: (item.error, -area(item)))  # duże pod spodem
    for item in ordered:
        color = _color(item, colors)
        if item.kind is ElementKind.SECTION_FRAME and not item.error:
            # Ramka sekcji zwykle pokrywa się z krawędziami komórek: rysowana na zewnątrz.
            x0, y0, x1, y1 = item.box
            outer = (
                x0 - SECTION_OUTSET,
                y0 - SECTION_OUTSET,
                x1 + SECTION_OUTSET,
                y1 + SECTION_OUTSET,
            )
            _rectangle(draw, outer, color, 3, dashed=item.dashed)
            continue
        _rectangle(
            draw, item.box, color, 4 if item.error else 2, dashed=item.dashed and not item.error
        )
        if item.error and max(item.box[2] - item.box[0], item.box[3] - item.box[1]) < MARK_RADIUS:
            cx, cy = (item.box[0] + item.box[2]) // 2, (item.box[1] + item.box[3]) // 2
            draw.ellipse(
                [cx - MARK_RADIUS, cy - MARK_RADIUS, cx + MARK_RADIUS, cy + MARK_RADIUS],
                outline=ERROR_COLOR,
                width=3,
            )
    placed: list[PixelBox] = []
    for item in sorted(ordered, key=lambda item: (not item.error, item.box[1], item.box[0])):
        _tag(draw, item.box, item.tag, _color(item, colors), font, placed)
    _legend(draw, page.height, page.width, colors, font, note)
    return canvas
