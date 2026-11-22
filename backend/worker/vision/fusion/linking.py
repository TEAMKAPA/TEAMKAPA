"""Pole ↔ element wizualny, kompletność i źródła w tekście strony (E.5, brief 7.6).

**Łączenie** (pole z kroku 7 ma ``text_element_id``: ID bloku tekstu etykiety z kroku 2):

1. dokładnie: element, którego ``label_block_id`` (wskazany przez VLM w trybie 1) jest blokiem
   etykiety pola; przy kilku kandydatach pierwszeństwo ma kategoria zgodna z typem pola (dla pól
   wyboru grupa przed pojedynczym checkboxem), potem wyższa pewność,
2. zapasowo (bez etykiety od VLM): najbliższy element zgodnej kategorii na prawo od ramki etykiety
   w tym samym wierszu albo poniżej niej, w odległości ≤ ``link_max_gap_mm``; elementy, których
   etykietą jest blok innego pola, nie są brane.

Kandydatami są elementy z rolą do wypełnienia (``input``, ``choice``, ``e_signature``) albo bez roli
(``unknown``); elementy ``skip`` i ramki sekcji nie. Kategoria niezgodna z typem pola przy łączeniu
dokładnym daje ``TYPE_MISMATCH``. Typy bez odpowiednika na papierze (załączniki, bloki tekstu,
sekcje) i pola z szablonu COI nie są łączone.

**Kompletność:** każdy element do wypełnienia ma pole albo jawny powód pominięcia
(``SkippedElement``): ``office_only`` („wypełnia urząd”), rola ``skip``, podpis (składany
elektronicznie). Element z rolą do wypełnienia bez pola: ``UNMAPPED_ELEMENT`` (problem do naprawy);
element bez roli bez pola: ``UNMAPPED_ELEMENT`` tylko do przeglądu. Tak samo pola AcroForm
(bloki ``acro-*`` bez pola).

**Źródła:** etykieta pola (``source_text``, inaczej tytuł) musi wystąpić w tekście swojej strony:
rapidfuzz ``partial_ratio`` po normalizacji (także bez znaków diakrytycznych) ≥ ``source_match``;
etykiety do ``source_exact_max_len`` znaków tylko jako całe słowa.
"""

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from rapidfuzz import fuzz

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import FieldSource, SkippedElement, SourceChannel
from backend.worker.vision.detect.scale import MM_PER_INCH
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.models import FieldRef, FusedElement
from backend.worker.vision.models import ElementKind, ElementRole
from backend.worker.vision.vlm.double_read import normalize
from backend.worker.vision.vlm.roles import TextBlock

ENTRY_KINDS = frozenset(
    {
        ElementKind.CHAR_BOXES,
        ElementKind.TEXT_LINE,
        ElementKind.DOTTED_LINE,
        ElementKind.TABLE_CELL,
    }
)
CHOICE_KINDS = frozenset({ElementKind.CHECKBOX_GROUP, ElementKind.CHECKBOX})

KINDS_FOR_TYPE: dict[FieldType, frozenset[ElementKind]] = {
    FieldType.TEXT_FIELD: ENTRY_KINDS,
    FieldType.NUMBER: ENTRY_KINDS,
    FieldType.DATEPICKER: ENTRY_KINDS,
    FieldType.TEXTAREA: ENTRY_KINDS,
    FieldType.CHECKBOX: CHOICE_KINDS,
    FieldType.RADIO: CHOICE_KINDS,
    FieldType.SELECT: CHOICE_KINDS,
    FieldType.CONSENT_SECTION: CHOICE_KINDS,
}
"""Kategorie zgodne z typem pola COI (typy spoza mapy nie mają odpowiednika na papierze)."""

LINKABLE_ROLES = frozenset(
    {ElementRole.INPUT, ElementRole.CHOICE, ElementRole.E_SIGNATURE, ElementRole.UNKNOWN}
)
FILL_ROLES = frozenset({ElementRole.INPUT, ElementRole.CHOICE})

SKIP_OFFICE = "OFFICE_ONLY: wypełnia urząd"
SKIP_ROLE = "SKIP_ROLE: nie jest polem wnioskodawcy (etykieta, nagłówek, kontener)"
SKIP_SIGNATURE = "E_SIGNATURE: podpis składany elektronicznie"

LinkMethod = Literal["label_block", "proximity"]


@dataclass(frozen=True)
class Link:
    element: FusedElement
    method: LinkMethod
    kind_ok: bool


@dataclass(frozen=True)
class SourceCheck:
    found: bool
    score: float  # 0–1
    block_found: bool  # blok etykiety (text_element_id) istnieje w tekście dokumentu


@dataclass(frozen=True)
class Unmapped:
    element_id: str
    page: int
    role_known: bool  # rola do wypełnienia od VLM (problem do naprawy), a nie ``unknown``
    label_block_id: str | None = None
    acroform: bool = False


@dataclass(frozen=True)
class Linking:
    links: dict[UUID, Link]
    sources: dict[UUID, SourceCheck]
    unmapped: tuple[Unmapped, ...]
    skipped: tuple[SkippedElement, ...]


def visual_expected(field: FieldRef) -> bool:
    """Czy pole powinno mieć element wizualny (typ z odpowiednikiem na papierze, nie szablon)."""
    return not field.from_template and field.field_type in KINDS_FOR_TYPE


def _page_of(field: FieldRef, blocks: dict[str, TextBlock]) -> int | None:
    if field.page is not None:
        return field.page
    block = blocks.get(field.text_element_id or "")
    return block.page if block else None


def _exact(
    field: FieldRef, elements: Sequence[FusedElement], allowed: frozenset[ElementKind]
) -> Link | None:
    candidates = [
        e
        for e in elements
        if e.label_block_id is not None
        and e.label_block_id == field.text_element_id
        and e.role in LINKABLE_ROLES
    ]
    if not candidates:
        return None
    order = {ElementKind.CHECKBOX_GROUP: 0, ElementKind.CHECKBOX: 1}
    best = min(
        candidates,
        key=lambda e: (e.kind not in allowed, order.get(e.kind, 2), -e.confidence, e.id),
    )
    return Link(best, "label_block", best.kind in allowed)


def _gap_mm(
    label: TextBlock, element: FusedElement, row_tol: float, page_mm: tuple[float, float]
) -> float | None:
    """Odstęp w mm elementu na prawo w wierszu etykiety albo poniżej niej; inaczej None."""
    a, b = label.bbox, element.bbox
    if a is None:
        return None
    center = (b.y0 + b.y1) / 2
    if a.y0 - row_tol <= center <= a.y1 + row_tol and b.x1 > a.x1:
        return max(0.0, b.x0 - a.x1) * page_mm[0]
    if b.y0 >= a.y1 - row_tol and min(a.x1, b.x1) - max(a.x0, b.x0) > 0:
        return max(0.0, b.y0 - a.y1) * page_mm[1]
    return None


def _proximity(
    field: FieldRef,
    label: TextBlock | None,
    elements: Sequence[FusedElement],
    allowed: frozenset[ElementKind],
    taken: set[str],
    foreign_blocks: set[str],
    cfg: FusionConfig,
    page_mm: tuple[float, float],
) -> Link | None:
    if label is None or label.bbox is None:
        return None
    row_tol = cfg.link_row_tolerance_mm / page_mm[1]
    best: tuple[float, str, FusedElement] | None = None
    for element in elements:
        if (
            element.id in taken
            or element.kind not in allowed
            or element.role not in LINKABLE_ROLES
            or element.label_block_id in foreign_blocks
        ):
            continue
        gap = _gap_mm(label, element, row_tol, page_mm)
        if gap is None or gap > cfg.link_max_gap_mm:
            continue
        key = (gap, element.id)
        if best is None or key < best[:2]:
            best = (gap, element.id, element)
    return Link(best[2], "proximity", True) if best else None


def fold(text: str) -> str:
    """Normalizacja do porównania źródeł: jak w Etapie D, a do tego bez znaków diakrytycznych
    (OCR i modele często gubią polskie znaki: „Imie” zamiast „Imię”)."""
    decomposed = unicodedata.normalize("NFKD", normalize(text).replace("ł", "l"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def check_source(
    field: FieldRef, page_text: str, block: TextBlock | None, cfg: FusionConfig
) -> SourceCheck:
    label = fold(field.label)
    text = fold(page_text)
    if not label:
        return SourceCheck(False, 0.0, block is not None)
    if len(label) <= cfg.source_exact_max_len:
        words = set(text.split())
        found = all(word in words for word in label.split())
        return SourceCheck(found, 1.0 if found else 0.0, block is not None)
    score = float(fuzz.partial_ratio(label, text)) if text else 0.0
    return SourceCheck(score >= cfg.source_match, round(score / 100, 4), block is not None)


def _skipped_reason(element: FusedElement) -> str | None:
    if element.kind is ElementKind.OFFICE_ONLY:
        return SKIP_OFFICE
    if element.role is ElementRole.SKIP:
        return SKIP_ROLE
    if element.role is ElementRole.E_SIGNATURE:
        return SKIP_SIGNATURE
    return None


def element_source(element: FusedElement) -> FieldSource:
    return FieldSource(
        channel=SourceChannel.VISUAL,
        page=element.page,
        bbox=element.bbox,
        element_id=element.id,
    )


def link_fields(
    fields: Sequence[FieldRef],
    elements: Iterable[FusedElement],
    blocks: Sequence[TextBlock],
    cfg: FusionConfig | None = None,
    *,
    acroform: Sequence[TextBlock] = (),
    page_mm: tuple[float, float] = (210.0, 297.0),
) -> Linking:
    """Łączy pola z elementami, sprawdza źródła i kompletność (deterministycznie)."""
    cfg = cfg or FusionConfig()
    all_elements = sorted(elements, key=lambda e: (e.page, e.id))
    by_block = {b.id: b for b in [*blocks, *acroform]}
    by_page: dict[int, list[FusedElement]] = {}
    for element in all_elements:
        by_page.setdefault(element.page, []).append(element)
    page_text: dict[int, str] = {}
    for block in blocks:
        page_text[block.page] = f"{page_text.get(block.page, '')} {block.text}"
    field_blocks = {f.text_element_id for f in fields if f.text_element_id}

    links: dict[UUID, Link] = {}
    taken: set[str] = set()
    pending: list[FieldRef] = []
    for field in fields:  # najpierw łączenie dokładne (etykieta od VLM)
        if not visual_expected(field):
            continue
        page = _page_of(field, by_block)
        candidates = by_page.get(page, []) if page else all_elements
        link = _exact(field, candidates, KINDS_FOR_TYPE[field.field_type])
        if link is None:
            pending.append(field)
            continue
        links[field.field_id] = link
        taken.add(link.element.id)
    for field in pending:  # potem bliskość, bez elementów już połączonych
        page = _page_of(field, by_block)
        label = by_block.get(field.text_element_id or "")
        foreign = field_blocks - {field.text_element_id}
        link = _proximity(
            field,
            label,
            by_page.get(page, []) if page else [],
            KINDS_FOR_TYPE[field.field_type],
            taken,
            {b for b in foreign if b},
            cfg,
            page_mm,
        )
        if link is not None:
            links[field.field_id] = link
            taken.add(link.element.id)

    sources: dict[UUID, SourceCheck] = {}
    for field in fields:
        if field.from_template:
            continue
        page = _page_of(field, by_block)
        text = page_text.get(page, "") if page else " ".join(page_text.values())
        sources[field.field_id] = check_source(
            field, text, by_block.get(field.text_element_id or ""), cfg
        )

    mapped = set(taken)
    by_id = {e.id: e for e in all_elements}
    for element_id in list(taken):  # grupa połączona z polem obejmuje swoje checkboxy
        mapped.update(by_id[element_id].options)
    for element in all_elements:  # checkbox w połączonej grupie albo grupa z połączonym członkiem
        if element.kind is ElementKind.CHECKBOX_GROUP and mapped & set(element.options):
            mapped.add(element.id)
            mapped.update(element.options)

    unmapped: list[Unmapped] = []
    skipped: list[SkippedElement] = []
    for element in all_elements:
        if element.id in mapped or element.role is ElementRole.SECTION:
            continue
        reason = _skipped_reason(element)
        if reason is not None:
            skipped.append(SkippedElement(reason=reason, source=element_source(element)))
            continue
        if element.kind in (ElementKind.SECTION_FRAME, ElementKind.CHECKBOX_GROUP) and (
            element.role is ElementRole.UNKNOWN
        ):
            continue  # ramka bez roli i kandydat na grupę: ich członkowie są oceniani osobno
        if element.role in FILL_ROLES or element.role is ElementRole.UNKNOWN:
            unmapped.append(
                Unmapped(
                    element_id=element.id,
                    page=element.page,
                    role_known=element.role in FILL_ROLES,
                    label_block_id=element.label_block_id,
                )
            )
    referenced = {f.text_element_id for f in fields if f.text_element_id}
    for acro in acroform:
        if acro.id not in referenced:
            unmapped.append(
                Unmapped(
                    element_id=acro.id,
                    page=acro.page,
                    role_known=True,
                    label_block_id=acro.id,
                    acroform=True,
                )
            )
    return Linking(links, sources, tuple(unmapped), tuple(skipped))


def page_size_mm(width_px: int, height_px: int, dpi: float | None) -> tuple[float, float]:
    """Rozmiar strony w mm (do odległości łączenia); bez DPI: A4 w orientacji strony."""
    if dpi:
        return width_px / dpi * MM_PER_INCH, height_px / dpi * MM_PER_INCH
    return (210.0, 297.0) if height_px >= width_px else (297.0, 210.0)
