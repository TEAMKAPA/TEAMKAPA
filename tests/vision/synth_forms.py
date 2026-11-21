"""Syntetyczne strony formularzy z etykietami (#12, Etap B.5), bez materiałów COI i bez czcionek.

``make_form`` rysuje stronę A4 z elementami wszystkich 9 kategorii (kratki, checkboxy i grupa,
linie ciągłe i kropkowane, komórki tabeli, ramka sekcji, podpis, miejsce urzędu) oraz paskami
udającymi tekst, a etykiety (GT) zapisuje w formacie ``teamkapa.vision.labels/1``. Układ zależy od
ziarna, więc ten sam ``seed`` daje identyczny obraz i etykiety.

Degradacje (deterministyczne, każda zwraca nowy ``SynthForm``): ``rotate`` (z przeliczeniem GT),
``blur``, ``noise`` (stałe ziarno), ``jpeg``, ``contrast`` i ``downscale`` (niższe DPI, GT
w ułamkach strony bez zmian). Detektory z Etapu C korzystają z tego samego generatora.
"""

import hashlib
import io
import math
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage
from PIL import ImageFilter

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import ElementKind, ElementRole
from eval.visual.geometry import image_sha256
from eval.visual.match import EvalBox
from eval.visual.models import (
    DEFAULT_ROLES,
    LABELS_SCHEMA,
    LabelElement,
    LabelSource,
    LabelStatus,
    LabelTool,
    PageLabels,
    RoleOrigin,
)

Image = npt.NDArray[np.uint8]
Frac = tuple[float, float, float, float]

A4_MM = (210.0, 297.0)
INK = 0
TEXT_GRAY = 90
SYNTH_DOC = "syn01"


@dataclass(frozen=True)
class SynthElement:
    """Element GT: ramka w ułamkach strony (krawędzie pikseli, jak ``BoundingBox``)."""

    kind: ElementKind
    box: Frac
    role: ElementRole
    cells: int | None = None
    multi: bool | None = None
    members: tuple[int, ...] = ()  # indeksy checkboxów grupy w ``SynthForm.elements``


@dataclass(frozen=True)
class SynthForm:
    image: Image
    dpi: float
    elements: tuple[SynthElement, ...]
    text: tuple[Frac, ...]  # paski „tekstu” (nie są elementami)
    seed: int
    steps: tuple[str, ...] = ()  # zastosowane degradacje

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def order(self) -> list[int]:
        """Indeksy elementów w kolejności czytania (jak w konwerterze: góra, potem lewo)."""
        return sorted(
            range(len(self.elements)),
            key=lambda i: (self.elements[i].box[1], self.elements[i].box[0], i),
        )

    def ids(self) -> dict[int, str]:
        return {index: f"gt-p1-{n:03d}" for n, index in enumerate(self.order(), start=1)}

    def labels(self) -> PageLabels:
        ids = self.ids()
        elements = []
        for index in self.order():
            e = self.elements[index]
            elements.append(
                LabelElement(
                    id=ids[index],
                    kind=e.kind,
                    bbox=BoundingBox(x0=e.box[0], y0=e.box[1], x1=e.box[2], y1=e.box[3]),
                    role=e.role,
                    role_origin=RoleOrigin.DEFAULT
                    if e.role is DEFAULT_ROLES[e.kind]
                    else RoleOrigin.HUMAN,
                    cells=e.cells,
                    multi=e.multi,
                    options=tuple(ids[m] for m in sorted(e.members, key=self.order().index)),
                )
            )
        digest = hashlib.sha256(f"{self.seed}:{','.join(self.steps)}".encode()).hexdigest()
        return PageLabels(
            schema_version=LABELS_SCHEMA,
            label_id=f"{SYNTH_DOC}-p01",
            doc_id=SYNTH_DOC,
            source=LabelSource(
                file_id=f"{SYNTH_DOC}-png",
                sha256=digest,
                page=1,
                width_px=self.width,
                height_px=self.height,
                dpi=self.dpi,
                image_sha256=image_sha256(self.image),
            ),
            status=LabelStatus.COMPLETE,
            annotator="human-1",
            tool=LabelTool(name="synth_forms", version="1", config_sha256=digest),
            elements=tuple(elements),
        )

    def eval_boxes(self) -> tuple[EvalBox, ...]:
        """GT w postaci wspólnej dla metryk (np. jako predykcje wzorcowe w testach)."""
        ids = self.ids()
        return tuple(
            EvalBox(
                id=ids[i],
                kind=e.kind,
                box=e.box,
                role=e.role,
                cells=e.cells,
                multi=e.multi,
                options=tuple(ids[m] for m in e.members),
            )
            for i, e in enumerate(self.elements)
        )

    def pixel_box(self, box: Frac) -> tuple[int, int, int, int]:
        return (
            round(box[0] * self.width),
            round(box[1] * self.height),
            round(box[2] * self.width),
            round(box[3] * self.height),
        )


# --- Rysowanie ---


class _Canvas:
    def __init__(self, dpi: float, seed: int) -> None:
        self.dpi = dpi
        self.width = round(A4_MM[0] / 25.4 * dpi)
        self.height = round(A4_MM[1] / 25.4 * dpi)
        self.image: Image = np.full((self.height, self.width, 3), 255, dtype=np.uint8)
        self.rng = np.random.default_rng(seed)
        self.elements: list[SynthElement] = []
        self.text: list[Frac] = []

    def px(self, mm: float) -> int:
        return round(mm / 25.4 * self.dpi)

    def line_px(self) -> int:
        return max(1, round(self.dpi / 100))  # ok. 0,25 mm

    def frac(self, x0: int, y0: int, x1: int, y1: int) -> Frac:
        return x0 / self.width, y0 / self.height, x1 / self.width, y1 / self.height

    def fill(self, x0: int, y0: int, x1: int, y1: int, value: int = INK) -> None:
        self.image[y0:y1, x0:x1] = value

    def frame(self, x0: int, y0: int, x1: int, y1: int, width: int | None = None) -> None:
        w = width or self.line_px()
        self.fill(x0, y0, x1, y0 + w)
        self.fill(x0, y1 - w, x1, y1)
        self.fill(x0, y0, x0 + w, y1)
        self.fill(x1 - w, y0, x1, y1)

    def add(self, kind: ElementKind, box: tuple[int, int, int, int], **attrs: object) -> int:
        role = attrs.pop("role", DEFAULT_ROLES[kind])
        self.elements.append(SynthElement(kind=kind, box=self.frac(*box), role=role, **attrs))  # type: ignore[arg-type]
        return len(self.elements) - 1

    def text_bar(self, x0: int, y_mid: int, length_mm: float) -> tuple[int, int, int, int]:
        h = self.px(2.2)
        box = (x0, y_mid - h // 2, x0 + self.px(length_mm), y_mid - h // 2 + h)
        self.fill(*box, value=TEXT_GRAY)
        self.text.append(self.frac(*box))
        return box

    # Elementy

    def cell(
        self, x0: int, y0: int, x1: int, y1: int, role: ElementRole = ElementRole.INPUT
    ) -> int:
        self.frame(x0, y0, x1, y1)
        return self.add(ElementKind.TABLE_CELL, (x0, y0, x1, y1), role=role)

    def char_boxes(self, x0: int, y0: int, cells: int) -> tuple[int, int, int, int]:
        size, w = self.px(5), self.line_px()
        x1, y1 = x0 + cells * size + w, y0 + size + w
        for i in range(cells + 1):
            self.fill(x0 + i * size, y0, x0 + i * size + w, y1)
        self.fill(x0, y0, x1, y0 + w)
        self.fill(x0, y1 - w, x1, y1)
        self.add(ElementKind.CHAR_BOXES, (x0, y0, x1, y1), cells=cells)
        return x0, y0, x1, y1

    def checkbox(self, x0: int, y0: int) -> tuple[int, tuple[int, int, int, int]]:
        size = self.px(3.5)
        box = (x0, y0, x0 + size, y0 + size)
        self.frame(*box)
        return self.add(ElementKind.CHECKBOX, box), box

    def text_line(
        self, x0: int, y0: int, x1: int, kind: ElementKind = ElementKind.TEXT_LINE
    ) -> int:
        w = self.line_px()
        self.fill(x0, y0, x1, y0 + w)
        return self.add(kind, (x0, y0, x1, y0 + w))

    def dotted_line(
        self, x0: int, y0: int, x1: int, kind: ElementKind = ElementKind.DOTTED_LINE
    ) -> int:
        dot, step = max(1, self.line_px()), self.px(1.0)
        last = x0
        for x in range(x0, x1 - dot + 1, step):
            self.fill(x, y0, x + dot, y0 + dot)
            last = x + dot
        return self.add(kind, (x0, y0, last, y0 + dot))


def make_form(seed: int = 0, dpi: float = 200.0) -> SynthForm:
    """Strona formularza z elementami wszystkich kategorii; układ zależy od ``seed``."""
    c = _Canvas(dpi, seed)
    rng = c.rng

    def jitter(mm: float) -> int:
        return c.px(float(rng.uniform(0, mm)))

    left, right = c.px(20) + jitter(5), c.width - c.px(20) - jitter(5)

    # Nagłówek: miejsce urzędu (ramka) i data (kropki).
    top = c.px(15) + jitter(5)
    stamp = (left, top, left + c.px(60), top + c.px(30))
    c.frame(*stamp)
    c.text_bar(stamp[0] + c.px(3), stamp[1] + c.px(5), 30)
    c.add(ElementKind.OFFICE_ONLY, stamp)
    date_y = top + c.px(10)
    c.text_bar(right - c.px(80), date_y, 15)
    c.dotted_line(right - c.px(60), date_y + c.px(1), right)

    # Sekcja z tabelą: komórka-nagłówek (skip), wiersze etykieta | wartość.
    y = top + c.px(45) + jitter(5)
    rows = 3 + int(rng.integers(0, 2))
    row_h, split_x = c.px(9), left + c.px(55) + jitter(10)
    section_top = y
    c.cell(left, y, right, y + row_h, role=ElementRole.SKIP)
    c.text_bar(left + c.px(3), y + row_h // 2, 50)
    for r in range(rows):
        y0 = y + (r + 1) * row_h
        c.cell(left, y0, split_x, y0 + row_h, role=ElementRole.SKIP)
        c.text_bar(left + c.px(3), y0 + row_h // 2, float(rng.uniform(15, 35)))
        c.cell(split_x, y0, right, y0 + row_h)
    section_bottom = y + (rows + 1) * row_h
    outer = c.line_px() * 2
    section = (
        left - outer - c.px(1),
        section_top - outer - c.px(1),
        right + outer + c.px(1),
        section_bottom + outer + c.px(1),
    )
    c.frame(*section, width=outer)
    c.add(ElementKind.SECTION_FRAME, section)

    # Kratki: PESEL (11), kod pocztowy (2 + 3).
    y = section[3] + c.px(12)
    c.text_bar(left, y + c.px(2.5), 20)
    c.char_boxes(left + c.px(30), y, 11)
    y += c.px(12)
    c.text_bar(left, y + c.px(2.5), 25)
    first = c.char_boxes(left + c.px(30), y, 2)
    dash_x = first[2] + c.px(2)
    c.fill(dash_x, y + c.px(2.5), dash_x + c.px(2), y + c.px(2.5) + c.line_px(), value=TEXT_GRAY)
    c.text.append(c.frac(dash_x, y + c.px(2.5), dash_x + c.px(2), y + c.px(2.5) + c.line_px()))
    c.char_boxes(dash_x + c.px(4), y, 3)

    # Grupa wyboru (2–3 opcje, jedna odpowiedź) i pojedynczy checkbox (zgoda).
    y += c.px(16)
    options = 2 + int(rng.integers(0, 2))
    members, x = [], left
    group_x0, group_y0, group_x1, group_y1 = x, y, x, y
    for _ in range(options):
        index, box = c.checkbox(x, y)
        bar = c.text_bar(box[2] + c.px(2), (box[1] + box[3]) // 2, 18)
        members.append(index)
        group_x1, group_y1 = max(group_x1, bar[2]), max(group_y1, box[3], bar[3])
        group_y0 = min(group_y0, bar[1])
        x = bar[2] + c.px(10)
    c.add(
        ElementKind.CHECKBOX_GROUP,
        (group_x0, group_y0, group_x1, group_y1),
        multi=False,
        members=tuple(members),
    )
    y += c.px(12)
    _, consent = c.checkbox(left, y)
    c.text_bar(consent[2] + c.px(2), (consent[1] + consent[3]) // 2, 90)

    # Linie do wpisywania: ciągła i kropkowane (kilka wierszy).
    y += c.px(16)
    c.text_bar(left, y - c.px(3), 40)
    c.text_line(left, y, right)
    for _ in range(2 + int(rng.integers(0, 2))):
        y += c.px(9)
        c.dotted_line(left, y, right)

    # Podpis: kropki z opisem pod spodem.
    y += c.px(30)
    sig_x0 = right - c.px(70)
    c.dotted_line(sig_x0, y, right, kind=ElementKind.SIGNATURE)
    c.text_bar(sig_x0 + c.px(20), y + c.px(4), 30)

    return SynthForm(
        image=c.image, dpi=dpi, elements=tuple(c.elements), text=tuple(c.text), seed=seed
    )


# --- Degradacje ---


def _with(form: SynthForm, image: Image, step: str, **changes: object) -> SynthForm:
    return replace(form, image=image, steps=(*form.steps, step), **changes)  # type: ignore[arg-type]


def _rotate_points(
    points: npt.NDArray[np.float64], angle: float, w: int, h: int
) -> npt.NDArray[np.float64]:
    """Obrót punktów (współrzędne krawędzi pikseli) wokół środka strony; kąt dodatni: w lewo."""
    theta = math.radians(angle)
    cx, cy = w / 2, h / 2
    cos, sin = math.cos(theta), math.sin(theta)
    x, y = points[:, 0] - cx, points[:, 1] - cy
    # Oś y w dół: obrót „w lewo” na ekranie.
    return np.stack([cx + cos * x + sin * y, cy - sin * x + cos * y], axis=1)


def rotate(form: SynthForm, angle: float) -> SynthForm:
    """Obrót o ``angle`` stopni wokół środka (białe tło, interpolacja dwuliniowa).

    Etykiety: prostokąt osiowy obejmujący obrócone narożniki ramki, przycięty do strony.
    """
    h, w = form.height, form.width
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    centers = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], axis=1)
    source = _rotate_points(centers, -angle, w, h) - 0.5  # odwrotne odwzorowanie środków pikseli
    sx, sy = source[:, 0], source[:, 1]
    x0, y0 = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    fx, fy = (sx - x0)[:, None], (sy - y0)[:, None]
    # Strony syntetyczne są szare (kanały równe): wtedy liczymy jeden kanał.
    gray = bool((form.image == form.image[:, :, :1]).all())
    channels = form.image[:, :, :1] if gray else form.image
    padded = np.pad(channels.astype(np.float32), ((1, 2), (1, 2), (0, 0)), constant_values=255.0)
    fx32, fy32 = fx.astype(np.float32), fy.astype(np.float32)

    def sample(dx: int, dy: int) -> npt.NDArray[np.float32]:
        xi = np.clip(x0 + dx + 1, 0, w + 2)
        yi = np.clip(y0 + dy + 1, 0, h + 2)
        return padded[yi, xi]

    value = (
        sample(0, 0) * (1 - fx32) * (1 - fy32)
        + sample(1, 0) * fx32 * (1 - fy32)
        + sample(0, 1) * (1 - fx32) * fy32
        + sample(1, 1) * fx32 * fy32
    )
    result = np.clip(np.rint(value), 0, 255).astype(np.uint8).reshape(h, w, channels.shape[2])
    image = np.repeat(result, 3, axis=2) if gray else result

    def turn(box: Frac) -> Frac:
        corners = np.array(
            [
                [box[0] * w, box[1] * h],
                [box[2] * w, box[1] * h],
                [box[2] * w, box[3] * h],
                [box[0] * w, box[3] * h],
            ]
        )
        moved = _rotate_points(corners, angle, w, h)
        return (
            max(0.0, float(moved[:, 0].min()) / w),
            max(0.0, float(moved[:, 1].min()) / h),
            min(1.0, float(moved[:, 0].max()) / w),
            min(1.0, float(moved[:, 1].max()) / h),
        )

    elements = tuple(replace(e, box=turn(e.box)) for e in form.elements)
    return _with(
        form, image, f"rotate({angle:g})", elements=elements, text=tuple(turn(t) for t in form.text)
    )


def blur(form: SynthForm, radius: float) -> SynthForm:
    image = np.asarray(PILImage.fromarray(form.image).filter(ImageFilter.GaussianBlur(radius)))
    return _with(form, image, f"blur({radius:g})")


def noise(form: SynthForm, sigma: float, seed: int = 0) -> SynthForm:
    rng = np.random.default_rng(seed)
    noisy = form.image.astype(np.float64) + rng.normal(0.0, sigma, form.image.shape)
    return _with(form, np.clip(np.rint(noisy), 0, 255).astype(np.uint8), f"noise({sigma:g},{seed})")


def jpeg(form: SynthForm, quality: int) -> SynthForm:
    buffer = io.BytesIO()
    PILImage.fromarray(form.image).save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    with PILImage.open(buffer) as decoded:
        image = np.asarray(decoded.convert("RGB"), dtype=np.uint8)
    return _with(form, image, f"jpeg({quality})")


def contrast(form: SynthForm, factor: float) -> SynthForm:
    """Słabszy kontrast: odległość od bieli mnożona przez ``factor`` (np. 0,4: wyblakły druk)."""
    faded = 255.0 - (255.0 - form.image.astype(np.float64)) * factor
    return _with(form, np.clip(np.rint(faded), 0, 255).astype(np.uint8), f"contrast({factor:g})")


def downscale(form: SynthForm, dpi: float) -> SynthForm:
    """Niższe DPI (np. 150 lub 100); etykiety w ułamkach strony się nie zmieniają."""
    width = round(form.width * dpi / form.dpi)
    height = round(form.height * dpi / form.dpi)
    resized = PILImage.fromarray(form.image).resize((width, height), PILImage.Resampling.LANCZOS)
    return _with(form, np.asarray(resized, dtype=np.uint8), f"downscale({dpi:g})", dpi=dpi)


DEGRADATIONS = {
    "rotate_1": lambda f: rotate(f, 1.0),
    "rotate_-2": lambda f: rotate(f, -2.0),
    "blur_1": lambda f: blur(f, 1.0),
    "noise_20": lambda f: noise(f, 20.0, seed=7),
    "jpeg_30": lambda f: jpeg(f, 30),
    "contrast_0.4": lambda f: contrast(f, 0.4),
    "dpi_100": lambda f: downscale(f, 100.0),
}
"""Zestaw degradacji do testów odporności detektorów (Etap C)."""
