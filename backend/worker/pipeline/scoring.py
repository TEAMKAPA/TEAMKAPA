"""Ocena pewności pól (wersja bazowa; kalibracja wag i progów: zadania #9/#12).

Sygnały pola (0..1, nazwane w raporcie — urzędnik i ewaluacja widzą, skąd ocena):
- ``source_found``   — tekst źródłowy pola występuje w treści dokumentu (ochrona przed
                       polami wymyślonymi przez model),
- ``source_element`` — pole wskazuje istniejący element dokumentu (strona, bbox),
- ``ocr``            — pewność odczytu OCR elementu źródłowego (tylko skany),
- ``rules``          — brak problemów reguł COI i normalizacji (ostrzeżenie: 0.6, błąd: 0),
- ``domain_rules``   — reguła dziedzinowa nadpisała decyzję modelu (0.4),
- ``visual_match``   — element źródłowy pokrywa się z elementem wykrytym na obrazie strony.

Pola ze standardowego szablonu COI (strony „Dane”, „Kontakt i zgody”) i pola informacyjne
bez źródła (np. tekst strony końcowej) nie pochodzą z dokumentu — nie mają sygnałów źródła ani
ostrzeżenia ``SOURCE_NOT_FOUND``; pola szablonu mają sygnał ``standard_template``.

Wynik = średnia dostępnych sygnałów, z ograniczeniami:
- brak kanału wizualnego → najwyżej MEDIUM (pole przeczytane tylko jednym kanałem),
- tryb demonstracyjny → LOW (wynik nie pochodzi z modelu).
"""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from statistics import fmean
from uuid import UUID

from backend.shared.schemas.coi_form import INFORMATIONAL_FIELD_TYPES
from backend.shared.schemas.quality import (
    BoundingBox,
    FieldAssessment,
    FieldSource,
    Issue,
    Severity,
    SourceChannel,
    confidence_level,
)
from backend.worker.pipeline.contracts import ExtractedDocument, TextElement, VisualAnalysis
from backend.worker.pipeline.normalize import NormalizedForm

_EPSILON = 0.001


def normalize_text(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", folded)).strip()


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


def _visual_signal(element: TextElement | None, visual: VisualAnalysis) -> float:
    if element is None or element.bbox is None:
        return 0.3
    for candidate in visual.elements:
        if candidate.page == element.page and _overlaps(candidate.bbox, element.bbox):
            return 1.0
    return 0.3


def _rules_signal(issues: Iterable[Issue]) -> float:
    severities = {i.severity for i in issues}
    if Severity.ERROR in severities:
        return 0.0
    if Severity.WARNING in severities:
        return 0.6
    return 1.0


def assess_fields(
    normalized: NormalizedForm,
    extracted: ExtractedDocument,
    visual: VisualAnalysis | None,
    rule_issues: list[Issue],
    *,
    high: float,
    low: float,
    demo_mode: bool,
) -> list[FieldAssessment]:
    elements = {e.element_id: e for e in extracted.elements}
    document_text = normalize_text(extracted.text)
    issues_by_field: dict[UUID, list[Issue]] = defaultdict(list)
    for issue in [*normalized.issues, *rule_issues]:
        if issue.field_id is not None:
            issues_by_field[issue.field_id].append(issue)

    assessments: list[FieldAssessment] = []
    for _, _, leaf in normalized.form.iter_fields():
        if leaf is None:
            continue
        source = normalized.sources.get(leaf.id)
        element = elements.get(source.element_id or "") if source else None
        field_issues = list(issues_by_field.get(leaf.id, []))

        signals: dict[str, float] = {"rules": _rules_signal(field_issues)}
        standard = source is not None and source.origin == "standard"
        template_text = standard or (source is None and leaf.type in INFORMATIONAL_FIELD_TYPES)
        found = bool(source and source.text and normalize_text(source.text) in document_text)
        if standard:
            signals["standard_template"] = 1.0
        if not template_text:
            signals["source_found"] = 1.0 if found else 0.0
            signals["source_element"] = 1.0 if element is not None else 0.0
        if element is not None and element.confidence is not None:
            signals["ocr"] = element.confidence
        for conflict in normalized.conflicts.get(leaf.id, []):
            signals["domain_rules"] = 0.4
            field_issues.append(
                Issue(
                    code="DOMAIN_RULE_OVERRIDE",
                    severity=Severity.WARNING,
                    message=f"Reguła dziedzinowa zmieniła {conflict.get('attribute')}: "
                    f"{conflict.get('llm_value')!r} → {conflict.get('rule_value')!r} "
                    f"({conflict.get('reason', '')})",
                    field_id=leaf.id,
                )
            )
        if visual is not None and not template_text:
            signals["visual_match"] = _visual_signal(element, visual)
        if not found and not template_text:
            field_issues.append(
                Issue(
                    code="SOURCE_NOT_FOUND",
                    severity=Severity.WARNING,
                    message="Nie znaleziono tekstu źródłowego pola w dokumencie — sprawdź, "
                    "czy pole rzeczywiście występuje na formularzu",
                    field_id=leaf.id,
                )
            )

        score = fmean(signals.values())
        if visual is None:
            score = min(score, max(high - _EPSILON, 0.0))
        if demo_mode:
            score = min(score, max(low - _EPSILON, 0.0))

        sources = []
        if source is not None and not standard:
            sources.append(
                FieldSource(
                    channel=element.channel if element else SourceChannel.TEXT,
                    page=source.page or (element.page if element else None),
                    bbox=element.bbox if element else None,
                    text=source.text,
                    element_id=source.element_id,
                )
            )
        assessments.append(
            FieldAssessment(
                field_id=leaf.id,
                tech_name=leaf.tech_name,
                confidence=round(score, 4),
                level=confidence_level(score, high=high, low=low),
                sources=sources,
                signals=signals,
                issues=field_issues,
            )
        )
    return assessments
