"""Pewność pola z dowodów kanału wizualnego: sygnały, bramki poziomu, punkt podpięcia (E.6).

**Sygnały** (0–1, nazwane w ``FieldAssessment.signals``):

- ``source_page``: zgodność etykiety z tekstem swojej strony (``linking.check_source``),
- ``visual_match``: pewność elementu po fuzji połączonego z polem (0: brak elementu),
- ``dual_read``: podwójny odczyt (1: para zgodna, 0,3: brak pary, 0: inny rodzaj),
- ``ocr``: pewność OCR bloku etykiety (tylko skany).

**Bramki** (``codes``): poziom pola to najniższy poziom dopuszczony przez kody. HIGH tylko, gdy:
źródło znalezione, element ``both`` zgodnej kategorii z rolą od VLM, podwójny odczyt zgodny
(albo pominięty w ``balanced`` dla strony pewnej), kratki zgodne z etykietą i walidatorem,
OCR ≥ ``ocr_high``, strona bez kodów obniżających, kanał wizualny dostępny. Liczba
``confidence`` leży w paśmie poziomu, a położenie w paśmie to średnia sygnałów (``models.banded``).

**Brak kanału wizualnego** (format biurowy, inny ``RenderError``, awaria kanału albo strona
nieprzetworzona): ``NO_VISUAL_CHANNEL``, najwyżej MEDIUM; przyczyna (np.
``OFFICE_FORMAT_NOT_RENDERED``) jest w treści problemu.

**Punkt podpięcia** (``apply_evidence``): ocena zespołu (``pipeline.scoring.assess_fields``)
dostaje sygnały, źródła i problemy kanału wizualnego, a poziom wynika z bramek: kodów kanału
wizualnego oraz poziomu ważności problemów zespołu (ERROR → LOW, WARNING → najwyżej MEDIUM).
``scoring.py`` nie jest zmieniany; proponowaną zmianę opisuje ``docs/vision.md``.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import fmean
from typing import Literal
from uuid import UUID

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import FieldAssessment, FieldSource, Issue
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.labels import LabelReading, resolve_label
from backend.worker.vision.fusion.linking import Linking, element_source, visual_expected
from backend.worker.vision.fusion.models import FieldEvidence, FieldRef, banded
from backend.worker.vision.models import ElementKind
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.double_read import (
    GROUPS,
    Comparison,
    FormField,
    compare,
    expected_cells,
)
from backend.worker.vision.vlm.roles import TextBlock
from backend.worker.vision.vlm.schemas import FieldKind

FIELD_KINDS: Mapping[FieldType, FieldKind] = {
    FieldType.TEXT_FIELD: "text",
    FieldType.TEXTAREA: "text",
    FieldType.NUMBER: "number",
    FieldType.DATEPICKER: "date",
    FieldType.CHECKBOX: "choice",
    FieldType.RADIO: "choice",
    FieldType.SELECT: "choice",
    FieldType.CONSENT_SECTION: "choice",
}
"""Rodzaj pola w porównaniu z trybem 2 (jak ``double_read.REFERENCE_KINDS``)."""

VALIDATOR_CELLS: Mapping[str, frozenset[int]] = {
    "PESEL": frozenset({11}),
    "NIP": frozenset({10}),
    "REGON": frozenset({9, 14}),
    "ZIP_CODE": frozenset({2, 3, 5}),
}
"""Liczba kratek wynikająca z walidatora COI (kod pocztowy: grupy 2 i 3 albo całość)."""

DUAL_MATCH, DUAL_MISSING, DUAL_TYPE = 1.0, 0.3, 0.0


@dataclass(frozen=True)
class VisualContext:
    """Stan kanału wizualnego dokumentu (z kroku 3 i etapu VLM)."""

    pages: frozenset[int]  # strony przetworzone przez kanał wizualny (render + detekcja)
    no_visual_cause: str | None = None  # kod przyczyny braku kanału dla całego dokumentu
    mode: Literal["max", "balanced"] = "max"
    double_read: frozenset[int] = frozenset()  # strony z udanym trybem 2
    double_read_skipped: frozenset[int] = frozenset()  # balanced: strony pewne, bez trybu 2
    vlm_fields: tuple[FormField, ...] = ()  # pola odczytane trybem 2
    alt_reads: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # blok → inne OCR

    @classmethod
    def unavailable(cls, cause: str) -> VisualContext:
        return cls(pages=frozenset(), no_visual_cause=cause)


def _form_field(f: FieldRef, linking: Linking, blocks: Mapping[str, TextBlock]) -> FormField | None:
    kind = FIELD_KINDS.get(f.field_type)
    if kind is None or f.from_template:
        return None
    link = linking.links.get(f.field_id)
    element = link.element if link else None
    block = blocks.get(f.text_element_id or "")
    return FormField(
        id=str(f.field_id),
        label=f.label,
        kind=kind,
        page=f.page or (block.page if block else None),
        bbox=element.bbox if element else None,
        cells=element.cells if element and element.kind is ElementKind.CHAR_BOXES else None,
    )


def compare_double_read(
    fields: Sequence[FieldRef],
    linking: Linking,
    blocks: Sequence[TextBlock],
    context: VisualContext,
) -> Comparison | None:
    """Porównanie pól z tekstu z trybem 2 na stronach z udanym odczytem (``None``: brak odczytu)."""
    if not context.double_read:
        return None
    by_block = {b.id: b for b in blocks}
    text_fields = [
        ff
        for f in fields
        if (ff := _form_field(f, linking, by_block)) is not None and ff.page in context.double_read
    ]
    read = [v for v in context.vlm_fields if v.page in context.double_read]
    return compare(text_fields, read)


def _expected(f: FieldRef, label: str | None) -> frozenset[int] | None:
    expected: set[int] = set()
    for text in {f.label, label or ""}:
        found = expected_cells(text) if text else None
        expected |= set(found or ())
    for validator in f.validators:
        expected |= VALIDATOR_CELLS.get(validator, frozenset())
    return frozenset(expected) or None


def _issue(code: str, f: FieldRef, detail: str = "") -> Issue:
    s = codes.spec(code)
    message = s.description + (f" ({detail})" if detail else "")
    return Issue(code=code, severity=s.severity, message=message, field_id=f.field_id)


def field_evidence(
    f: FieldRef,
    linking: Linking,
    blocks: Sequence[TextBlock],
    context: VisualContext,
    comparison: Comparison | None,
    cfg: FusionConfig | None = None,
) -> FieldEvidence:
    """Dowody kanału wizualnego dla jednego pola (pole z szablonu COI: bez dowodów i bramek)."""
    cfg = cfg or FusionConfig()
    if f.from_template:
        return FieldEvidence(field_id=f.field_id, level=codes.level_for(()))
    by_block = {b.id: b for b in blocks}
    block = by_block.get(f.text_element_id or "")
    page = f.page or (block.page if block else None)
    found: list[tuple[str, str]] = []  # (kod, szczegóły)
    signals: dict[str, float] = {}
    sources: list[FieldSource] = []
    element_ids: list[str] = []

    source = linking.sources.get(f.field_id)
    if source is not None:
        signals["source_page"] = source.score
        if not source.found:
            found.append((codes.SOURCE_NOT_FOUND, f"zgodność {source.score:.2f}"))

    visual = context.no_visual_cause is None and page is not None and page in context.pages
    if not visual:
        cause = context.no_visual_cause or (
            "strona nieprzetworzona" if page is not None else "pole bez numeru strony"
        )
        found.append((codes.NO_VISUAL_CHANNEL, cause))
    elif visual_expected(f):
        link = linking.links.get(f.field_id)
        if link is None:
            signals["visual_match"] = 0.0
            found.append((codes.NO_VISUAL_ELEMENT, ""))
        else:
            element = link.element
            signals["visual_match"] = element.confidence
            sources.append(element_source(element))
            element_ids.append(element.id)
            for code in codes.capping(element.confidence_reasons):
                found.append((code, f"element {element.id}"))
            if not link.kind_ok:
                found.append(
                    (vlm.TYPE_MISMATCH, f"{f.field_type.value} a element {element.kind.value}")
                )
            label = block.text if block else None
            expected = _expected(f, label)
            if (
                element.kind is ElementKind.CHAR_BOXES
                and expected
                and element.cells is not None
                and element.cells not in expected
            ):
                found.append(
                    (
                        vlm.CELLS_LABEL_CONFLICT,
                        f"kratek {element.cells}, oczekiwano {sorted(expected)}",
                    )
                )

        vlm_label: str | None = None
        if FIELD_KINDS.get(f.field_type) is not None:
            if page in context.double_read and comparison is not None:
                match = next((m for m in comparison.matches if m.text.id == str(f.field_id)), None)
                if match is None:
                    signals["dual_read"] = DUAL_MISSING
                    found.append((vlm.MISSING_IN_VLM, ""))
                elif GROUPS[match.text.kind] != GROUPS[match.vlm.kind]:
                    signals["dual_read"] = DUAL_TYPE
                    found.append((vlm.TYPE_MISMATCH, f"obraz: {match.vlm.kind}"))
                else:
                    signals["dual_read"] = DUAL_MATCH
                    vlm_label = match.vlm.label
            elif page in context.double_read_skipped and context.mode == "balanced":
                found.append((codes.DUAL_READ_SKIPPED, ""))
            else:
                found.append((codes.DUAL_READ_UNAVAILABLE, ""))

        if block is not None and block.confidence is not None:  # skan: OCR i głosowanie etykiet
            signals["ocr"] = block.confidence
            if block.confidence < cfg.ocr_low:
                found.append((vlm.LOW_OCR_CONFIDENCE, f"OCR {block.confidence:.2f}"))
            elif block.confidence < cfg.ocr_high:
                found.append((codes.OCR_UNCERTAIN, f"OCR {block.confidence:.2f}"))
            readings = [LabelReading("ocr", block.text)]
            readings += [LabelReading("ocr2", t) for t in context.alt_reads.get(block.id, ())]
            if vlm_label:
                readings.append(LabelReading("vlm", vlm_label))
            decision = resolve_label(readings, native_text=False, cfg=cfg)
            found += [(c, f"odczyty {decision.agreeing}/{decision.reads}") for c in decision.codes]

    unique: dict[str, str] = {}
    for code, detail in found:
        unique.setdefault(code, detail)
    return FieldEvidence(
        field_id=f.field_id,
        signals={k: round(min(max(v, 0.0), 1.0), 4) for k, v in signals.items()},
        reasons=tuple(unique),
        level=codes.level_for(unique),
        sources=tuple(sources),
        issues=tuple(_issue(code, f, detail) for code, detail in unique.items()),
        element_ids=tuple(element_ids),
    )


def evidence_for_fields(
    fields: Sequence[FieldRef],
    linking: Linking,
    blocks: Sequence[TextBlock],
    context: VisualContext,
    cfg: FusionConfig | None = None,
) -> tuple[dict[UUID, FieldEvidence], Comparison | None]:
    comparison = compare_double_read(fields, linking, blocks, context)
    evidence = {
        f.field_id: field_evidence(f, linking, blocks, context, comparison, cfg) for f in fields
    }
    return evidence, comparison


def _merge_sources(team: Iterable[FieldSource], ours: Iterable[FieldSource]) -> list[FieldSource]:
    result: list[FieldSource] = []
    for source in [*team, *ours]:
        if source not in result:
            result.append(source)
    return result


def apply_evidence(
    assessment: FieldAssessment,
    evidence: FieldEvidence | None,
    *,
    high: float,
    low: float,
    demo_mode: bool = False,
) -> FieldAssessment:
    """Punkt podpięcia: ocena zespołu + dowody kanału wizualnego → ocena z poziomem z bramek.

    - sygnały: zespołu i kanału wizualnego (``visual_match`` kanału wizualnego zastępuje
      uproszczony sygnał nakładania się prostokątów),
    - problemy i źródła: suma, bez powtórzeń kodów i źródeł,
    - poziom: najniższy z bramki kanału wizualnego i z problemów zespołu według poziomu ważności
      (ERROR → LOW, WARNING → najwyżej MEDIUM); tryb demonstracyjny → LOW,
    - liczba: w paśmie poziomu, położenie = średnia sygnałów.

    Bez dowodów (``None``, np. pole spoza kontroli) ocena wraca bez zmian.
    """
    if evidence is None:
        return assessment
    if evidence.field_id != assessment.field_id:
        raise ValueError("dowody dotyczą innego pola")
    signals = {**assessment.signals, **evidence.signals}
    issues = list(assessment.issues)
    present = {i.code for i in issues}
    issues += [i for i in evidence.issues if i.code not in present]
    team_levels = [
        codes.effect_level(codes.spec(i.code, i.severity).effect) for i in assessment.issues
    ]
    levels = [evidence.level, *team_levels]
    if demo_mode:
        levels.append(codes.effect_level(codes.Effect.FORCE_LOW))
    level = codes.lowest(levels)
    position = fmean(signals.values()) if signals else 0.5
    return assessment.model_copy(
        update={
            "confidence": banded(level, position, high=high, low=low),
            "level": level,
            "signals": signals,
            "issues": issues,
            "sources": _merge_sources(assessment.sources, evidence.sources),
        }
    )


def severity_counts(evidence: Iterable[FieldEvidence]) -> dict[str, int]:
    """Liczności poziomów bramek (do raportu bez treści)."""
    counts: dict[str, int] = {}
    for item in evidence:
        counts[item.level.value] = counts.get(item.level.value, 0) + 1
    return dict(sorted(counts.items()))
