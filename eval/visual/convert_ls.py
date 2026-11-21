"""Konwersja eksportu Label Studio na etykiety stron (#12), raport i nakładki do kontroli.

Użycie (w Label Studio: Export → JSON, plik do ``dane/robocze/labelstudio/exports/``)::

    python -m eval.visual.convert_ls dane/robocze/labelstudio/exports/<plik>.json

Wynik w ``dane/robocze/labels/``:

- ``<label_id>.json``: etykiety strony (``PageLabels``), tylko dla stron bez błędów,
- ``preview/<label_id>.png``: nakładki do kontroli, także dla stron z błędami,
- ``convert_report.md`` i ``convert_report.json``: błędy, ostrzeżenia, role wywnioskowane, czas.

Nic nie dzieje się po cichu: każda niezgodność z ``LABELING.md`` jest błędem strony (strona nie
dostaje pliku etykiet, poprzedni plik zostaje bez zmian, kod wyjścia 1) albo wpisem w raporcie.
Jedyne automatyczne poprawki to przycięcie ramki wystającej poza stronę o ≤ 0,5 px oraz rola
``skip`` dla komórki zawierającej inny element do wypełnienia; obie są w raporcie.
"""

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from PIL import Image as PILImage

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import ElementKind, ElementRole, ImageArray
from eval.visual import ls
from eval.visual.geometry import image_sha256
from eval.visual.models import (
    ALLOWED_ROLES,
    DEFAULT_ROLES,
    LABELS_SCHEMA,
    LabelElement,
    LabelSource,
    LabelStatus,
    LabelTool,
    Manifest,
    ManifestDocument,
    ManifestPage,
    PageLabels,
    RoleOrigin,
    dump_json,
    read_json,
    write_text,
)
from eval.visual.preview import ABBREVIATIONS, PixelBox, PreviewBox, draw_preview

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = REPO_ROOT / "dane" / "robocze" / "b" / "manifest.full.json"
DEFAULT_PAGES = REPO_ROOT / "dane" / "robocze" / "b" / "pages"
DEFAULT_OUT = REPO_ROOT / "dane" / "robocze" / "labels"

REGION_CONTROLS = frozenset(
    {ls.KIND_CONTROL, ls.CELLS_CONTROL, ls.ROLE_CONTROL, ls.MULTI_CONTROL, ls.FLAGS_CONTROL}
)
FRAMED_KINDS = frozenset(
    {
        ElementKind.TABLE_CELL,
        ElementKind.CHAR_BOXES,
        ElementKind.SECTION_FRAME,
        ElementKind.CHECKBOX,
    }
)
"""Kategorie rysowane obramowaniem: krawędź ciasnej ramki leży na linii (LABELING.md, zasada 4)."""
EDGE_NAMES = ("lewa", "górna", "prawa", "dolna")
FILLABLE_ROLES = frozenset({ElementRole.INPUT, ElementRole.CHOICE, ElementRole.E_SIGNATURE})
MULTI_MAP: Mapping[str, bool | None] = {"single": False, "multi": True, "unknown": None}


class ExportError(ValueError):
    """Plik eksportu ma niepoprawną strukturę (nie da się przypisać błędu do strony)."""


@dataclass(frozen=True)
class ConvertParams:
    min_long_side_px: int = 8  # 1 mm przy 200 DPI: mniejszy region to przypadkowe kliknięcie
    clip_tolerance_px: float = 0.5  # wystawanie poza stronę przycinane z wpisem w raporcie
    cell_aspect: tuple[float, float] = (0.25, 4.0)  # szerokość jednej kratki / wysokość grupy
    contain_fraction: float = 0.9  # część pola elementu wewnątrz komórki, by uznać go za zawarty
    ink_threshold: int = 160  # piksel ciemniejszy (min. kanałów) to tusz linii
    edge_search_px: int = 8  # szukanie linii wokół krawędzi ramki
    edge_ink_fraction: float = 0.5  # część długości krawędzi pokryta tuszem, by uznać ją za linię
    edge_tolerance_px: int = 2  # większa odległość krawędzi od linii to ostrzeżenie


class PageState(StrEnum):
    CONVERTED = "converted"
    ERROR = "error"
    PENDING = "pending"  # zadanie bez zapisanej anotacji


@dataclass
class Region:
    ls_id: str
    box: PixelBox | None = None
    kind: ElementKind | None = None
    cells: int | None = None
    role: ElementRole | None = None  # wybrana przez oznaczającego
    multi: str | None = None
    uncertain: bool = False
    controls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    number: int = 0  # pozycja w kolejności czytania (od 1); po konwersji = numer w gt-p{n}-{nnn}
    final_role: ElementRole | None = None
    role_origin: RoleOrigin | None = None
    group: str | None = None  # ls_id grupy, do której należy checkbox
    members: list[str] = field(default_factory=list)

    def describe(self) -> str:
        kind = self.kind.value if self.kind else "?"
        where = f", x={self.box[0]} y={self.box[1]} px" if self.box else ""
        return f"region {self.ls_id} ({kind}{where})"


@dataclass
class PageResult:
    label_id: str
    state: PageState
    task_id: int | None = None
    annotation_id: int | None = None
    lead_time_s: float | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # przycięcia i role wywnioskowane
    regions: list[Region] = field(default_factory=list)
    edge_offsets: list[int | None] = field(default_factory=list)  # krawędzie ramek od linii (px)
    labels: PageLabels | None = None
    image: ImageArray | None = None


# --- Odczyt struktury eksportu ---


def _dict(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExportError(f"{what}: oczekiwano obiektu JSON")
    return value


def _list(value: object, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise ExportError(f"{what}: oczekiwano listy JSON")
    return value


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _annotator(completed_by: object) -> str | None:
    """``human-<id>`` z identyfikatora użytkownika LS; adres e-mail nigdy nie trafia do etykiet."""
    user_id = _int_or_none(completed_by)
    if user_id is None and isinstance(completed_by, dict):
        user_id = _int_or_none(completed_by.get("id"))
    return f"human-{user_id}" if user_id is not None and user_id >= 1 else None


def _single_choice(
    value: dict[str, Any], allowed: Sequence[str], region: Region, what: str
) -> str | None:
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or choices[0] not in allowed:
        region.errors.append(f"{what}: oczekiwano jednej wartości z {', '.join(allowed)}")
        return None
    return str(choices[0])


# --- Geometria ---


def _edges(
    value: dict[str, Any], width: int, height: int, region: Region, params: ConvertParams
) -> tuple[PixelBox | None, list[str]]:
    """Procenty LS → krawędzie pikseli; przycięcie ≤ tolerancji, większe wystawanie to błąd."""
    try:
        x, y = float(value["x"]), float(value["y"])
        w, h = float(value["width"]), float(value["height"])
        rotation = float(value.get("rotation", 0.0))
    except KeyError, TypeError, ValueError:
        region.errors.append("brak lub niepoprawne współrzędne ramki")
        return None, []
    if not all(math.isfinite(v) for v in (x, y, w, h, rotation)):
        region.errors.append("niepoprawne współrzędne ramki")
        return None, []
    if abs(rotation) > 1e-6:
        region.errors.append(f"ramka obrócona o {rotation:g}°: obrót jest niedozwolony")
        return None, []
    raw = (x * width / 100, y * height / 100, (x + w) * width / 100, (y + h) * height / 100)
    limits = (width, height, width, height)
    notes: list[str] = []
    clipped: list[float] = []
    for name, value_px, limit in zip(("x0", "y0", "x1", "y1"), raw, limits, strict=True):
        excess = max(-value_px, value_px - limit, 0.0)
        if excess > params.clip_tolerance_px:
            region.errors.append(f"ramka wychodzi poza stronę o {excess:.1f} px ({name})")
            return None, []
        if excess > 1e-6:
            notes.append(f"przycięto {name} o {excess:.2f} px do krawędzi strony")
        clipped.append(min(max(value_px, 0.0), float(limit)))
    x0, y0, x1, y1 = (round(v) for v in clipped)
    return (x0, y0, x1, y1), notes


def _area(box: PixelBox) -> int:
    return max(box[2] - box[0], 0) * max(box[3] - box[1], 0)


def _intersection(a: PixelBox, b: PixelBox) -> int:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _center_inside(inner: PixelBox, outer: PixelBox) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


# --- Regiony jednej anotacji ---


def _parse_results(
    results: list[Any], page: ManifestPage, params: ConvertParams, result: PageResult
) -> tuple[list[Region], list[str]]:
    regions: dict[str, Region] = {}
    statuses: list[str] = []
    for index, item in enumerate(results):
        entry = _dict(item, f"{page.label_id}: wynik {index}")
        control = entry.get("from_name")
        value = _dict(entry.get("value", {}), f"{page.label_id}: wartość wyniku {index}")
        if control == ls.PAGE_STATUS_CONTROL:
            statuses.extend(str(c) for c in _list(value.get("choices", []), "page_status"))
            continue
        if control not in REGION_CONTROLS:
            result.errors.append(
                f"nieznana kontrolka {control!r} (konfiguracja LS inna niż config.xml?)"
            )
            continue
        ls_id = entry.get("id")
        if not isinstance(ls_id, str) or not ls_id:
            result.errors.append(f"wynik {index} ({control}) bez id regionu")
            continue
        region = regions.setdefault(ls_id, Region(ls_id=ls_id))
        if control in region.controls:
            region.errors.append(f"kontrolka {control} powtórzona w regionie")
            continue
        region.controls.append(str(control))
        if entry.get("origin", "manual") != "manual":
            region.errors.append(f"region pochodzi z pre-anotacji (origin={entry.get('origin')})")
        if (entry.get("original_width"), entry.get("original_height")) != (
            page.width_px,
            page.height_px,
        ):
            region.errors.append(
                f"rozmiar obrazu w LS {entry.get('original_width')}×{entry.get('original_height')}"
                f" różni się od manifestu {page.width_px}×{page.height_px}"
            )
        if control == ls.KIND_CONTROL:
            labels = value.get("rectanglelabels")
            if not isinstance(labels, list) or len(labels) != 1:
                region.errors.append("region musi mieć dokładnie jedną kategorię")
            elif labels[0] not in {k.value for k in ElementKind}:
                region.errors.append(f"nieznana kategoria {labels[0]!r}")
            else:
                region.kind = ElementKind(labels[0])
            region.box, notes = _edges(value, page.width_px, page.height_px, region, params)
            result.notes.extend(f"{region.describe()}: {note}" for note in notes)
        elif control == ls.CELLS_CONTROL:
            number = value.get("number")
            if (
                isinstance(number, (int, float))
                and not isinstance(number, bool)
                and float(number).is_integer()
                and number >= 1
            ):
                region.cells = int(number)
            else:
                region.errors.append(f"cells musi być liczbą całkowitą ≥ 1 (jest {number!r})")
        elif control == ls.ROLE_CONTROL:
            roles = [r.value for r in ElementRole if r is not ElementRole.UNKNOWN]
            chosen = _single_choice(value, roles, region, "role")
            region.role = ElementRole(chosen) if chosen else None
        elif control == ls.MULTI_CONTROL:
            region.multi = _single_choice(value, ls.MULTI_VALUES, region, "multi")
        else:  # flags
            flags = value.get("choices")
            if not isinstance(flags, list) or any(f != ls.UNCERTAIN_FLAG for f in flags):
                region.errors.append(f"flags: dozwolona tylko wartość {ls.UNCERTAIN_FLAG}")
            else:
                region.uncertain = bool(flags)
    for region in regions.values():
        if ls.KIND_CONTROL not in region.controls:
            region.errors.append("atrybuty bez ramki (region bez kategorii)")
    return list(regions.values()), statuses


def _check_region(region: Region, params: ConvertParams, warnings: list[str]) -> None:
    kind, box = region.kind, region.box
    if kind is None or box is None:
        return
    width, height = box[2] - box[0], box[3] - box[1]
    if min(width, height) < 1 or max(width, height) < params.min_long_side_px:
        region.errors.append(
            f"region za mały ({width}×{height} px), prawdopodobnie przypadkowe kliknięcie: usuń go"
        )
        return
    if kind is ElementKind.CHAR_BOXES:
        if region.cells is None:
            region.errors.append("char_boxes wymaga liczby kratek (cells)")
        else:
            aspect = width / region.cells / height
            low, high = params.cell_aspect
            if not low <= aspect <= high:
                message = (
                    f"cells={region.cells} nieprawdopodobne: kratka miałaby"
                    f" {width / region.cells:.1f}"
                    f" px szerokości przy wysokości {height} px (proporcja {aspect:.2f},"
                    f" oczekiwana {low}–{high}); popraw liczbę kratek"
                )
                if region.uncertain:
                    warnings.append(f"{region.describe()}: {message}")
                else:
                    region.errors.append(message)
    elif region.cells is not None:
        region.errors.append(
            f"cells ustawione dla {kind.value} (zostało po zmianie kategorii?): usuń wartość"
        )
    if (
        kind is ElementKind.CHECKBOX_GROUP
        and region.multi is None
        and ls.MULTI_CONTROL not in region.controls
    ):
        region.errors.append("checkbox_group wymaga wyboru multi (single, multi albo unknown)")
    if kind is not ElementKind.CHECKBOX_GROUP and ls.MULTI_CONTROL in region.controls:
        region.errors.append(f"multi ustawione dla {kind.value} (zostało po zmianie kategorii?)")
    if region.role is not None and region.role not in ALLOWED_ROLES[kind]:
        allowed = ", ".join(sorted(r.value for r in ALLOWED_ROLES[kind]))
        region.errors.append(
            f"rola {region.role.value} niedozwolona dla {kind.value} (dozwolone: {allowed})"
        )


def _assign_groups(regions: list[Region]) -> None:
    groups = [r for r in regions if r.kind is ElementKind.CHECKBOX_GROUP and r.box is not None]
    for checkbox in regions:
        if checkbox.kind is not ElementKind.CHECKBOX or checkbox.box is None:
            continue
        box = checkbox.box
        owners = [g for g in groups if g.box is not None and _center_inside(box, g.box)]
        if len(owners) > 1:
            checkbox.errors.append(
                "środek checkboxa leży w kilku grupach: "
                + ", ".join(g.ls_id for g in owners)
                + " (popraw ramki grup)"
            )
        elif owners:
            checkbox.group = owners[0].ls_id
            owners[0].members.append(checkbox.ls_id)
    for group in groups:
        if len(group.members) < 2:
            group.errors.append(
                f"grupa obejmuje {len(group.members)} checkbox(y); wymagane ≥ 2 (środki checkboxów"
                " muszą leżeć w ramce grupy; pojedynczy checkbox oznaczamy bez grupy)"
            )


def _reading_key(region: Region) -> tuple[int, int, int, int, str, str]:
    box = region.box or (0, 0, 0, 0)
    return box[1], box[0], box[3], box[2], region.kind.value if region.kind else "", region.ls_id


def _required[T](value: T | None) -> T:
    """Wartość ustalona dla strony bez błędów (wywoływane tylko po walidacji regionów)."""
    if value is None:
        raise RuntimeError("niekompletny region na stronie bez błędów")
    return value


def _infer_roles(regions: list[Region], params: ConvertParams, notes: list[str]) -> None:
    for region in regions:
        region.final_role = region.role or DEFAULT_ROLES[_required(region.kind)]
        region.role_origin = RoleOrigin.HUMAN if region.role else RoleOrigin.DEFAULT
    base_roles = {r.ls_id: r.final_role for r in regions}
    for cell in regions:
        if cell.kind is not ElementKind.TABLE_CELL or cell.role is not None or cell.box is None:
            continue
        inside = [
            other
            for other in regions
            if other is not cell
            and other.box is not None
            and other.kind is not ElementKind.SECTION_FRAME
            and base_roles[other.ls_id] in FILLABLE_ROLES
            and _area(other.box) < _area(cell.box)
            and _intersection(other.box, cell.box) >= params.contain_fraction * _area(other.box)
        ]
        if inside:
            cell.final_role, cell.role_origin = ElementRole.SKIP, RoleOrigin.INFERRED
            names = ", ".join(f"{o.number:03d} {o.kind.value if o.kind else '?'}" for o in inside)
            notes.append(f"komórka {cell.number:03d}: rola skip (zawiera {names})")


def _build_labels(
    regions: list[Region],
    status: LabelStatus,
    annotator: str,
    document: ManifestDocument,
    page: ManifestPage,
    config_sha256: str,
) -> PageLabels:
    by_ls = {r.ls_id: r for r in regions}
    width, height = page.width_px, page.height_px

    def gt_id(region: Region) -> str:
        return f"gt-p{page.page}-{region.number:03d}"

    elements = []
    for region in regions:
        x0, y0, x1, y1 = _required(region.box)
        elements.append(
            LabelElement(
                id=gt_id(region),
                kind=_required(region.kind),
                bbox=BoundingBox(x0=x0 / width, y0=y0 / height, x1=x1 / width, y1=y1 / height),
                role=_required(region.final_role),
                role_origin=_required(region.role_origin),
                cells=region.cells,
                multi=MULTI_MAP[region.multi] if region.multi is not None else None,
                options=tuple(
                    gt_id(by_ls[m]) for m in sorted(region.members, key=lambda m: by_ls[m].number)
                ),
                uncertain=region.uncertain,
                ls_region_id=region.ls_id,
            )
        )
    base = next(f for f in document.files if f.file_id == page.base_file_id)
    return PageLabels(
        schema_version=LABELS_SCHEMA,
        label_id=page.label_id,
        doc_id=document.doc_id,
        source=LabelSource(
            file_id=page.base_file_id,
            sha256=base.sha256,
            page=page.page,
            width_px=width,
            height_px=height,
            dpi=page.dpi,
            image_sha256=page.image_sha256,
        ),
        status=status,
        annotator=annotator,
        tool=LabelTool(name=ls.LS_NAME, version=ls.LS_VERSION, config_sha256=config_sha256),
        elements=tuple(elements),
    )


def edge_offsets(
    ink: npt.NDArray[np.bool_], box: PixelBox, params: ConvertParams
) -> tuple[int | None, ...]:
    """Odległość (px) krawędzi lewej, górnej, prawej i dolnej od najbliższej linii tuszu.

    Linia to kolumna (wiersz) pikseli, w której tusz pokrywa co najmniej ``edge_ink_fraction``
    długości krawędzi (bez narożników, żeby nie liczyć linii prostopadłych). ``None``: w promieniu
    ``edge_search_px`` nie ma linii. Krawędź prawa i dolna to ostatni piksel ramki (``x1 - 1``).
    """
    height, width = ink.shape
    x0, y0, x1, y1 = box
    offsets: list[int | None] = []
    for index, edge in enumerate((x0, y0, x1 - 1, y1 - 1)):
        vertical = index % 2 == 0  # krawędź lewa/prawa: profil kolumn
        start, stop = (y0, y1) if vertical else (x0, x1)
        margin = max(2, (stop - start) // 10)
        start, stop = start + margin, stop - margin
        limit = width if vertical else height
        best: int | None = None
        if stop > start:
            for position in range(
                max(edge - params.edge_search_px, 0),
                min(edge + params.edge_search_px, limit - 1) + 1,
            ):
                line = ink[start:stop, position] if vertical else ink[position, start:stop]
                distance = abs(position - edge)
                if line.mean() >= params.edge_ink_fraction and (best is None or distance < best):
                    best = distance
        offsets.append(best)
    return tuple(offsets)


def _check_edges(
    regions: list[Region], image: ImageArray, params: ConvertParams, result: PageResult
) -> None:
    ink = image.min(axis=2) < params.ink_threshold
    for region in sorted(regions, key=_reading_key):
        if region.box is None or region.kind not in FRAMED_KINDS or region.errors:
            continue
        offsets = edge_offsets(ink, region.box, params)
        result.edge_offsets.extend(offsets)
        problems = [
            f"{name} {'bez linii w pobliżu' if offset is None else f'{offset} px od linii'}"
            for name, offset in zip(EDGE_NAMES, offsets, strict=True)
            if offset is None or offset > params.edge_tolerance_px
        ]
        if problems:
            result.warnings.append(
                f"{region.number:03d} {region.describe()}: krawędź {', '.join(problems)}"
            )


def _load_image(path: Path) -> ImageArray | None:
    if not path.is_file():
        return None
    with PILImage.open(path) as image:
        array: ImageArray = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return array


def convert_task(
    task: dict[str, Any],
    manifest: Manifest,
    pages_dir: Path,
    params: ConvertParams,
    config_sha256: str,
) -> PageResult:
    """Jedno zadanie LS → etykiety strony albo lista błędów (z regionami do nakładki)."""
    data = _dict(task.get("data"), "zadanie: data")
    label_id = data.get("label_id")
    if not isinstance(label_id, str):
        raise ExportError("zadanie bez data.label_id (zadania muszą pochodzić z tasks.json)")
    result = PageResult(
        label_id=label_id, state=PageState.ERROR, task_id=_int_or_none(task.get("id"))
    )
    try:
        document, page = manifest.find_page(label_id)
    except KeyError:
        result.errors.append("label_id nie istnieje w manifeście (uruchom prepare ponownie?)")
        return result
    expected = {
        ls.IMAGE_TAG: ls.image_url(label_id),
        "doc_id": document.doc_id,
        "split": document.split.value,
        "page": page.page,
    }
    for key, value in expected.items():
        if data.get(key) != value:
            result.errors.append(f"data.{key} = {data.get(key)!r}, oczekiwano {value!r}")

    if _list(task.get("predictions", []), "predictions"):
        result.errors.append("zadanie ma pre-anotacje (predictions); oznaczamy bez nich")
    if _list(task.get("drafts", []), "drafts"):
        result.warnings.append(
            "zadanie ma niezapisany szkic: zmiany po ostatnim Submit nie są w eksporcie"
        )
    annotations = [
        _dict(a, f"{label_id}: anotacja") for a in _list(task.get("annotations", []), "annotations")
    ]
    active = [a for a in annotations if not a.get("was_cancelled")]
    if not active:
        result.state = PageState.PENDING
        return result
    if len(active) > 1:
        ids = ", ".join(str(a.get("id")) for a in active)
        result.errors.append(f"zadanie ma {len(active)} anotacje ({ids}); zostaw jedną")
        return result
    annotation = active[0]
    result.annotation_id = _int_or_none(annotation.get("id"))
    lead_time = annotation.get("lead_time")
    result.lead_time_s = float(lead_time) if isinstance(lead_time, (int, float)) else None
    if annotation.get("prediction") or annotation.get("parent_prediction") is not None:
        result.errors.append("anotacja powstała z pre-anotacji (prediction)")
    annotator = _annotator(annotation.get("completed_by"))
    if annotator is None:
        result.errors.append("brak identyfikatora oznaczającego (completed_by)")

    result.image = _load_image(pages_dir / ls.page_file_name(label_id))
    if result.image is None:
        result.errors.append(f"brak obrazu strony {ls.page_file_name(label_id)} w {pages_dir}")
    elif image_sha256(result.image) != page.image_sha256:
        result.errors.append("obraz strony różni się od manifestu: etykiety nie pasują do pikseli")

    regions, statuses = _parse_results(
        _list(annotation.get("result", []), f"{label_id}: result"), page, params, result
    )
    for region in regions:
        _check_region(region, params, result.warnings)
    _assign_groups(regions)
    boxed = sorted((r for r in regions if r.box is not None), key=_reading_key)
    for number, region in enumerate(boxed, start=1):
        region.number = number
    result.regions = regions

    if result.image is not None and image_sha256(result.image) == page.image_sha256:
        _check_edges(boxed, result.image, params, result)

    status: LabelStatus | None = None
    if len(statuses) != 1 or statuses[0] not in {s.value for s in LabelStatus}:
        result.errors.append(
            f"status strony musi mieć jedną wartość complete/empty (jest {statuses})"
        )
    else:
        status = LabelStatus(statuses[0])
        if status is LabelStatus.EMPTY and regions:
            result.errors.append(
                f"status strony empty, a strona ma {len(regions)} regionów: wybierz complete"
            )
        if status is LabelStatus.COMPLETE and not regions:
            result.errors.append("status complete bez regionów: wybierz empty albo oznacz elementy")

    region_errors = [r for r in regions if r.errors]
    for region in sorted(region_errors, key=_reading_key):
        result.errors.extend(f"{region.describe()}: {message}" for message in region.errors)
    if result.errors or status is None or annotator is None:
        return result

    _infer_roles(boxed, params, result.notes)
    result.labels = _build_labels(boxed, status, annotator, document, page, config_sha256)
    result.state = PageState.CONVERTED
    return result


# --- Nakładki i raport ---


def _preview_boxes(result: PageResult) -> list[PreviewBox]:
    by_ls = {r.ls_id: r for r in result.regions}
    boxes = []
    for region in result.regions:
        if region.box is None:
            continue
        parts = [f"{region.number:03d}", ABBREVIATIONS[region.kind] if region.kind else "?"]
        if region.cells is not None:
            parts.append(f"x{region.cells}")
        if region.multi is not None:
            parts.append(region.multi)
        if region.group is not None:
            parts.append(f"g{by_ls[region.group].number:03d}")
        role = region.final_role or region.role
        if role is not None and region.kind is not None and role is not DEFAULT_ROLES[region.kind]:
            parts.append(role.value + ("*" if region.role_origin is RoleOrigin.INFERRED else ""))
        if region.uncertain:
            parts.append("?")
        if region.errors:
            parts.insert(0, "ERR")
        boxes.append(
            PreviewBox(
                box=region.box,
                kind=region.kind,
                tag=" ".join(parts),
                dashed=role is ElementRole.SKIP,
                error=bool(region.errors),
            )
        )
    return boxes


def _preview_note(result: PageResult) -> str:
    if result.labels is None:
        return f"{result.label_id}: STRONA Z BLEDAMI, numery tymczasowe; etykiety nie zapisane"
    elements, page = len(result.labels.elements), result.labels.source.page
    return f"{result.label_id}: {elements} elementow, numer NNN = id gt-p{page}-NNN"


def _edge_histogram(offsets: Sequence[int | None]) -> dict[str, int]:
    """Liczba krawędzi ramek wg odległości od linii: 0, 1, 2, >2 px i bez linii."""
    keys = ("0", "1", "2", ">2", "brak")
    counts = Counter("brak" if o is None else ">2" if o > 2 else str(o) for o in offsets)
    return {key: counts.get(key, 0) for key in keys}


def _page_summary(result: PageResult) -> dict[str, Any]:
    elements = result.labels.elements if result.labels else ()
    count = len(elements) if result.labels else len(result.regions)
    per_element = result.lead_time_s / count if result.lead_time_s and count else None
    return {
        "label_id": result.label_id,
        "state": result.state.value,
        "task_id": result.task_id,
        "annotation_id": result.annotation_id,
        "elements": count,
        "by_kind": dict(sorted(Counter(e.kind.value for e in elements).items())),
        "uncertain": sum(e.uncertain for e in elements),
        "role_origin": dict(sorted(Counter(e.role_origin.value for e in elements).items())),
        "lead_time_s": result.lead_time_s,
        "s_per_element": round(per_element, 1) if per_element else None,
        "errors": result.errors,
        "warnings": result.warnings,
        "edge_offsets": _edge_histogram(result.edge_offsets),
        "notes": result.notes,
    }


def _markdown(export: Path, export_sha: str, results: list[PageResult]) -> str:
    done = [r for r in results if r.state is not PageState.PENDING]
    lines = [
        "# Konwersja eksportu Label Studio",
        "",
        f"Eksport: `{export.name}` (sha256 `{export_sha[:12]}…`).",
        f"Strony: {sum(r.state is PageState.CONVERTED for r in results)} skonwertowane,"
        f" {sum(r.state is PageState.ERROR for r in results)} z błędami,"
        f" {sum(r.state is PageState.PENDING for r in results)} bez anotacji.",
        "",
        "| Strona | Stan | Elementy | uncertain | Role human/default/inferred | Czas | s/el. |",
        "|---|---|---|---|---|---|---|",
    ]
    total_time = total_elements = 0.0
    for result in done:
        summary = _page_summary(result)
        origin = summary["role_origin"]
        roles = "/".join(str(origin.get(k.value, 0)) for k in RoleOrigin) if result.labels else "–"
        lead = summary["lead_time_s"]
        if lead and result.labels:
            total_time += lead
            total_elements += summary["elements"]
        lines.append(
            f"| {result.label_id} | {result.state.value} | {summary['elements']} |"
            f" {summary['uncertain'] if result.labels else '–'} | {roles} |"
            f" {f'{lead / 60:.1f} min' if lead else '–'} | {summary['s_per_element'] or '–'} |"
        )
    if total_elements:
        lines += [
            "",
            f"Średnio {total_time / total_elements:.0f} s na element (strony skonwertowane).",
        ]
    edges = _edge_histogram([o for r in done for o in r.edge_offsets])
    if sum(edges.values()):
        lines += [
            "",
            "Krawędzie ramek (komórki, kratki, sekcje, checkboxy) od linii w obrazie: "
            + ", ".join(
                f"{key} px: {count}" if key != "brak" else f"brak linii: {count}"
                for key, count in edges.items()
            )
            + ".",
        ]
    for result in done:
        if not (result.errors or result.warnings or result.notes):
            continue
        lines += ["", f"## {result.label_id}", ""]
        lines += [f"- **BŁĄD:** {e}" for e in result.errors]
        lines += [f"- Ostrzeżenie: {w}" for w in result.warnings]
        lines += [f"- {n}" for n in result.notes]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ConvertRun:
    results: list[PageResult]
    written: list[str]  # label_id stron z zapisanymi etykietami

    @property
    def ok(self) -> bool:
        return all(r.state is not PageState.ERROR for r in self.results)


def convert(
    export: Path,
    manifest_path: Path = DEFAULT_MANIFEST,
    pages_dir: Path = DEFAULT_PAGES,
    out: Path = DEFAULT_OUT,
    params: ConvertParams | None = None,
    *,
    preview: bool = True,
) -> ConvertRun:
    params = params or ConvertParams()
    raw = export.read_bytes()
    try:
        tasks = _list(json.loads(raw.decode("utf-8")), "eksport")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExportError(f"eksport nie jest poprawnym JSON-em: {error}") from error
    manifest = read_json(manifest_path, Manifest)
    config_sha = ls.config_sha256()
    results = [
        convert_task(_dict(t, "zadanie"), manifest, pages_dir, params, config_sha) for t in tasks
    ]
    seen = Counter(r.label_id for r in results)
    for result in results:
        if seen[result.label_id] > 1:
            result.state = PageState.ERROR
            result.labels = None
            result.errors.insert(0, "strona występuje w eksporcie więcej niż raz")

    written = []
    for result in results:
        if result.labels is not None:
            write_text(out / f"{result.label_id}.json", dump_json(result.labels))
            written.append(result.label_id)
        if preview and result.image is not None and result.state is not PageState.PENDING:
            note = _preview_note(result)
            path = out / "preview" / ls.page_file_name(result.label_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            draw_preview(result.image, _preview_boxes(result), note).save(path, format="PNG")

    export_sha = hashlib.sha256(raw).hexdigest()
    report = {
        "export": export.name,
        "export_sha256": export_sha,
        "params": {
            "min_long_side_px": params.min_long_side_px,
            "clip_tolerance_px": params.clip_tolerance_px,
            "cell_aspect": list(params.cell_aspect),
            "contain_fraction": params.contain_fraction,
            "ink_threshold": params.ink_threshold,
            "edge_search_px": params.edge_search_px,
            "edge_ink_fraction": params.edge_ink_fraction,
            "edge_tolerance_px": params.edge_tolerance_px,
        },
        "pages": [_page_summary(r) for r in results],
    }
    write_text(out / "convert_report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(out / "convert_report.md", _markdown(export, export_sha, results))
    return ConvertRun(results=results, written=written)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("export", type=Path, help="plik JSON wyeksportowany z Label Studio")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pages", type=Path, default=DEFAULT_PAGES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-preview", action="store_true", help="bez nakładek")
    args = parser.parse_args(argv)
    try:
        run = convert(args.export, args.manifest, args.pages, args.out, preview=not args.no_preview)
    except (ExportError, ValueError, OSError) as error:
        print(f"BŁĄD: {error}", file=sys.stderr)
        return 2
    for result in run.results:
        count = len(result.labels.elements) if result.labels else len(result.regions)
        state, errors = result.state.value, len(result.errors)
        print(f"{result.label_id}: {state}, regiony/elementy: {count}, błędy: {errors}")
    print(f"Raport: {args.out / 'convert_report.md'}")
    return 0 if run.ok else 1


if __name__ == "__main__":
    sys.exit(main())
