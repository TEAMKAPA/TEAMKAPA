"""Kody powodów Etapu E: poziom ważności, skutek dla poziomu pewności i akcja naprawcza.

Zasada bramek (decyzja z 2026-11-22): **poziom wynika z kodów, a nie ze średniej sygnałów.**
Brak dowodu (np. etykiety w tekście strony) też jest kodem (``SOURCE_NOT_FOUND``), więc pole jest
zielone (HIGH) wtedy i tylko wtedy, gdy nie ma żadnego kodu o skutku ``CAP_MEDIUM`` ani
``FORCE_LOW``. Średnia sygnałów ustala tylko położenie liczby w paśmie poziomu (``models.banded``).

Kody z Etapów A, C i D są tu powtórzone z ich skutkiem; nowe kody Etapu E są opisane przy
definicji. Kod nieznany (np. z kanału tekstowego) dostaje skutek z poziomu ważności problemu
(``Issue.severity``): ERROR → LOW, WARNING → najwyżej MEDIUM, INFO → bez wpływu.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from backend.shared.schemas.quality import ConfidenceLevel, Severity
from backend.worker.vision.errors import IssueCode
from backend.worker.vision.vlm import codes as vlm


class Effect(StrEnum):
    NONE = "none"  # informacja, bez wpływu na poziom
    CAP_MEDIUM = "cap_medium"  # najwyżej MEDIUM (żółty: do przejrzenia)
    FORCE_LOW = "force_low"  # LOW (czerwony: wymaga decyzji urzędnika)


class RepairAction(StrEnum):
    """Co kanał tekstowy (#8) ma sprawdzić w iteracji naprawczej."""

    ADD_FIELD = "ADD_FIELD"  # miejsce do wypełnienia bez pola w formularzu
    CHECK_TYPE = "CHECK_TYPE"  # rodzaj pola sprzeczny z elementem lub drugim odczytem
    CHECK_LENGTH = "CHECK_LENGTH"  # liczba kratek sprzeczna z etykietą (np. PESEL)
    VERIFY_SOURCE = "VERIFY_SOURCE"  # etykieta pola nie występuje w tekście strony


@dataclass(frozen=True)
class CodeSpec:
    code: str
    severity: Severity
    effect: Effect
    description: str
    repair: RepairAction | None = None


def _spec(
    code: str,
    severity: Severity,
    effect: Effect,
    description: str,
    repair: RepairAction | None = None,
) -> CodeSpec:
    return CodeSpec(code, severity, effect, description, repair)


INFO, W = Severity.INFO, Severity.WARNING
NONE, CAP, LOW = Effect.NONE, Effect.CAP_MEDIUM, Effect.FORCE_LOW

# --- Nowe kody Etapu E ---
CELLS_LABEL_MATCH = "CELLS_LABEL_MATCH"
GROUP_MULTI_UNKNOWN = "GROUP_MULTI_UNKNOWN"
GROUP_CANDIDATE = "GROUP_CANDIDATE"
CELLS_IRREGULAR = "CELLS_IRREGULAR"
WEAK_VISUAL_EVIDENCE = "WEAK_VISUAL_EVIDENCE"
NO_VISUAL_EVIDENCE = "NO_VISUAL_EVIDENCE"
VLM_ONLY_REJECTED = "VLM_ONLY_REJECTED"
LABEL_VARIANT = "LABEL_VARIANT"
LABEL_DISAGREEMENT = "LABEL_DISAGREEMENT"
LABEL_SINGLE_READ = "LABEL_SINGLE_READ"
NO_VISUAL_CHANNEL = "NO_VISUAL_CHANNEL"
NO_VISUAL_ELEMENT = "NO_VISUAL_ELEMENT"
SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
UNMAPPED_ELEMENT = "UNMAPPED_ELEMENT"
DUAL_READ_SKIPPED = "DUAL_READ_SKIPPED"
DUAL_READ_UNAVAILABLE = "DUAL_READ_UNAVAILABLE"
OCR_UNCERTAIN = "OCR_UNCERTAIN"
REPAIR_UNRESOLVED = "REPAIR_UNRESOLVED"
FIELD_REMOVED_BY_REPAIR = "FIELD_REMOVED_BY_REPAIR"

_SPECS: tuple[CodeSpec, ...] = (
    # Etap D (#11): raport przeglądu VLM
    _spec(vlm.MISSING_IN_VLM, W, CAP, "pole z tekstu bez pary w odczycie z obrazu"),
    _spec(
        vlm.MISSING_IN_TEXT,
        W,
        CAP,
        "pole odczytane z obrazu bez pary w polach z tekstu",
        RepairAction.ADD_FIELD,
    ),
    _spec(
        vlm.TYPE_MISMATCH,
        W,
        LOW,
        "rodzaj pola sprzeczny z elementem wizualnym lub z drugim odczytem",
        RepairAction.CHECK_TYPE,
    ),
    _spec(
        vlm.CELLS_LABEL_CONFLICT,
        W,
        LOW,
        "liczba kratek sprzeczna z etykietą; typ z etykiety",
        RepairAction.CHECK_LENGTH,
    ),
    _spec(vlm.VLM_ONLY_ELEMENT, W, LOW, "element dodany przez VLM, niewykryty przez OpenCV"),
    _spec(vlm.NO_ROLE, W, CAP, "element OpenCV bez roli od VLM"),
    _spec(vlm.LOW_OCR_CONFIDENCE, W, LOW, "etykieta z bloku tekstu o niskiej pewności OCR"),
    _spec(vlm.VLM_INVALID_RESPONSE, W, CAP, "odpowiedź VLM niezgodna ze schematem"),
    _spec(vlm.VLM_UNAVAILABLE, W, CAP, "VLM niedostępny (błąd API, limit czasu, brak tokenu)"),
    # Etap A (#10): problemy strony obniżające wiarygodność detekcji
    _spec(IssueCode.DPI_REDUCED.value, W, CAP, "strona wyrenderowana w obniżonej rozdzielczości"),
    _spec(IssueCode.TALL_IMAGE_UNSPLIT.value, W, CAP, "wysoki obraz niepocięty na strony"),
    _spec(IssueCode.DPI_UNKNOWN.value, W, CAP, "obraz bez DPI i bez estymaty"),
    # Etap C (#10): kody detektorów
    _spec("LOW_CONTRAST", INFO, NONE, "niski kontrast strony (pewność detektora już obniżona)"),
    _spec("SKEW_CORRECTED", INFO, NONE, "strona prostowana (pewność detektora już obniżona)"),
    _spec(CELLS_IRREGULAR, W, CAP, "kratki o nieregularnej szerokości: liczba może być błędna"),
    _spec(GROUP_CANDIDATE, W, CAP, "grupa wyboru zaproponowana przez detektor (kandydat)"),
    # Etap E: fuzja elementów
    _spec(CELLS_LABEL_MATCH, INFO, NONE, "liczba kratek zgodna z etykietą: typ potwierdzony"),
    _spec(
        GROUP_MULTI_UNKNOWN,
        W,
        CAP,
        "nieznane, czy grupa jest jednokrotnego wyboru (RADIO lub CHECKBOX)",
    ),
    _spec(WEAK_VISUAL_EVIDENCE, INFO, NONE, "element dodany przez VLM ma ślad w pikselach"),
    _spec(NO_VISUAL_EVIDENCE, INFO, NONE, "element dodany przez VLM w pustym obszarze strony"),
    _spec(
        VLM_ONLY_REJECTED,
        INFO,
        NONE,
        "element dodany przez VLM odrzucony: obraz mu przeczy (tekst)",
    ),
    # Etap E: etykiety z kilku odczytów
    _spec(LABEL_VARIANT, INFO, NONE, "inne odczyty etykiety różnią się; wygrywa warstwa tekstowa"),
    _spec(
        LABEL_DISAGREEMENT,
        W,
        LOW,
        "odczyty etykiety skanu bez większości 2 z 3",
        RepairAction.VERIFY_SOURCE,
    ),
    _spec(LABEL_SINGLE_READ, INFO, CAP, "etykieta skanu z jednego odczytu: brak głosowania"),
    # Etap E: pola
    _spec(NO_VISUAL_CHANNEL, W, CAP, "kanał wizualny nie przetworzył strony ani dokumentu pola"),
    _spec(NO_VISUAL_ELEMENT, W, CAP, "brak elementu wizualnego dla pola"),
    _spec(
        SOURCE_NOT_FOUND,
        W,
        LOW,
        "etykieta pola nie występuje w tekście strony",
        RepairAction.VERIFY_SOURCE,
    ),
    _spec(
        UNMAPPED_ELEMENT,
        W,
        CAP,
        "miejsce do wypełnienia bez pola i bez powodu pominięcia",
        RepairAction.ADD_FIELD,
    ),
    _spec(DUAL_READ_SKIPPED, INFO, NONE, "tryb balanced: strona pewna, bez podwójnego odczytu"),
    _spec(DUAL_READ_UNAVAILABLE, W, CAP, "podwójny odczyt strony nie powiódł się"),
    _spec(OCR_UNCERTAIN, INFO, CAP, "pewność OCR etykiety poniżej progu pewnego odczytu"),
    _spec(REPAIR_UNRESOLVED, W, LOW, "problem nierozstrzygnięty po iteracjach naprawczych"),
    _spec(FIELD_REMOVED_BY_REPAIR, INFO, NONE, "pole usunięte w iteracji naprawczej"),
)

CODES: Mapping[str, CodeSpec] = {s.code: s for s in _SPECS}

_SEVERITY_EFFECT = {Severity.ERROR: LOW, Severity.WARNING: CAP, Severity.INFO: NONE}
_ORDER = {ConfidenceLevel.LOW: 0, ConfidenceLevel.MEDIUM: 1, ConfidenceLevel.HIGH: 2}


def spec(code: str, severity: Severity | None = None) -> CodeSpec:
    """Opis kodu; nieznany kod dostaje skutek z poziomu ważności (domyślnie WARNING)."""
    known = CODES.get(code)
    if known is not None:
        return known
    level = severity or Severity.WARNING
    return CodeSpec(code, level, _SEVERITY_EFFECT[level], "kod spoza kanału wizualnego")


def effect_level(effect: Effect) -> ConfidenceLevel:
    if effect is Effect.FORCE_LOW:
        return ConfidenceLevel.LOW
    if effect is Effect.CAP_MEDIUM:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH


def lowest(levels: Iterable[ConfidenceLevel]) -> ConfidenceLevel:
    """Najniższy poziom (HIGH dla pustej listy)."""
    return min(levels, key=_ORDER.__getitem__, default=ConfidenceLevel.HIGH)


def level_for(codes: Iterable[str]) -> ConfidenceLevel:
    """Bramka poziomu: najniższy poziom dopuszczony przez kody."""
    return lowest(effect_level(spec(c).effect) for c in codes)


def capping(codes: Iterable[str]) -> tuple[str, ...]:
    """Kody obniżające poziom (bez informacyjnych), bez powtórzeń, w kolejności wystąpienia."""
    return tuple(dict.fromkeys(c for c in codes if spec(c).effect is not Effect.NONE))
