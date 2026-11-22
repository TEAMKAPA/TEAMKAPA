"""Iteracja naprawcza (E.7, brief 7.6): lista problemów, protokół ``FieldRepairer``, pętla.

**Podział odpowiedzialności (propozycja interfejsu, do uzgodnienia z #8 i #16):**

- kanał wizualny (ten moduł) tworzy ustrukturyzowaną listę problemów (``RepairProblem``) z kodów,
  które kanał tekstowy może naprawić: ``SOURCE_NOT_FOUND``, ``CELLS_LABEL_CONFLICT``,
  ``TYPE_MISMATCH``, ``LABEL_DISAGREEMENT``, ``UNMAPPED_ELEMENT`` (element z rolą do wypełnienia
  bez pola), ``MISSING_IN_TEXT`` (pole z odczytu obrazu bez pary),
- #8 implementuje ``FieldRepairer``: ponowne wywołanie LLM tekstowego dla wskazanych pól; problemy
  zawierają etykiety, czyli **dane dokumentu**, więc trafiają do promptu w bloku danych
  (``problems_payload``), z instrukcją ignorowania poleceń w nim,
- #16 wpina pętlę (``run_control_with_repair``) w orkiestrator: kontrola → problemy → naprawa →
  normalizacja → kontrola.

Pętla kończy się, gdy nie ma problemów do naprawy, gdy zbiór problemów się nie zmienia (brak
postępu), po ``repair_max_iterations`` (najwyżej 2) albo po błędzie naprawy (zostaje ostatni
poprawny wynik). Co zostaje nierozstrzygnięte, dostaje ``REPAIR_UNRESOLVED`` (LOW, czerwone).
Pole usunięte przez naprawę trafia do ``SkippedElement`` z powodem, a raport zapisuje różnice pól
między iteracjami. Problemy nienaprawialne przez tekst (np. ``VLM_ONLY_ELEMENT``,
``LOW_OCR_CONFIDENCE``, ``NO_VISUAL_CHANNEL``, ``MISSING_IN_VLM``) idą prosto do przeglądu.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from backend.shared.schemas.quality import (
    BoundingBox,
    FieldSource,
    Issue,
    SkippedElement,
    SourceChannel,
)
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.control import ControlResult
from backend.worker.vision.fusion.models import FieldEvidence, FieldRef, RepairProblem
from backend.worker.vision.models import ELEMENT_ID_PATTERN
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.roles import TextBlock

logger = logging.getLogger(__name__)

StopReason = Literal["no_problems", "no_progress", "limit", "disabled", "repairer_failed"]
RawForm = Mapping[str, Any]


class FieldRepairer(Protocol):
    """Naprawa pól przez kanał tekstowy (implementuje #8).

    Dostaje wynik kroków 06–09 (przed normalizacją) i problemy; zwraca poprawiony wynik w tym samym
    formacie. Powinien zmieniać tylko pola i miejsca wskazane w problemach.
    """

    async def repair(
        self, form: RawForm, problems: Sequence[RepairProblem], iteration: int
    ) -> RawForm: ...


ControlFn = Callable[[RawForm], Awaitable[tuple[list[FieldRef], ControlResult]]]
"""Normalizacja i kontrola wyniku kroków 06–09 (krok 10); błąd = wynik niepoprawny."""


@dataclass(frozen=True)
class RepairRound:
    iteration: int
    problems: tuple[RepairProblem, ...]  # problemy przekazane do naprawy
    added: tuple[str, ...] = ()  # nazwy techniczne pól dodanych przez naprawę
    removed: tuple[str, ...] = ()
    retyped: tuple[str, ...] = ()  # pola, którym naprawa zmieniła typ


@dataclass(frozen=True)
class RepairOutcome:
    form: RawForm
    fields: tuple[FieldRef, ...]
    control: ControlResult
    unresolved: tuple[RepairProblem, ...]
    rounds: tuple[RepairRound, ...]
    stop: StopReason
    skipped: tuple[SkippedElement, ...]  # pola usunięte przez naprawę

    @property
    def iterations(self) -> int:
        return len(self.rounds)


def _element_ids(ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(i for i in ids if re.match(ELEMENT_ID_PATTERN, i))


def build_problems(
    fields: Sequence[FieldRef], control: ControlResult, blocks: Sequence[TextBlock]
) -> list[RepairProblem]:
    """Problemy do naprawy przez kanał tekstowy, w stałej kolejności (strona, kod, cel)."""
    by_block = {b.id: b for b in blocks}
    elements = {link.element.id: link.element for link in control.linking.links.values()}
    problems: list[RepairProblem] = []
    for f in fields:
        evidence = control.evidence.get(f.field_id)
        if evidence is None:
            continue
        details = {i.code: i.message for i in evidence.issues}
        element = elements.get(evidence.element_ids[0]) if evidence.element_ids else None
        for code in evidence.reasons:
            action = codes.spec(code).repair
            if action is None:
                continue
            extra: dict[str, str | int] = {"detail": details.get(code, "")}
            if element is not None:
                extra["element_kind"] = element.kind.value
                if element.cells is not None:
                    extra["cells"] = element.cells
            extra["field_type"] = f.field_type.value
            problems.append(
                RepairProblem(
                    code=code,
                    action=action,
                    field_id=f.field_id,
                    tech_name=f.tech_name,
                    page=f.page or (element.page if element else None),
                    element_ids=evidence.element_ids,
                    label_block_id=f.text_element_id,
                    label=f.label,
                    bbox=element.bbox if element else None,
                    evidence=extra,
                )
            )
    all_elements = {e.id: e for e in control.elements}
    for item in control.linking.unmapped:
        if not item.role_known:
            continue  # element bez roli: tylko przegląd, nie wiadomo, czy to pole
        element = all_elements.get(item.element_id)
        block = by_block.get(item.label_block_id or "")
        problems.append(
            RepairProblem(
                code=codes.UNMAPPED_ELEMENT,
                action=codes.RepairAction.ADD_FIELD,
                page=item.page,
                element_ids=_element_ids([item.element_id]),
                label_block_id=item.label_block_id,
                label=block.text if block else None,
                bbox=element.bbox if element else (block.bbox if block else None),
                evidence={
                    "element_kind": element.kind.value if element else "acroform",
                    **({"cells": element.cells} if element and element.cells else {}),
                },
            )
        )
    if control.comparison is not None:
        unmapped_boxes = [p.bbox for p in problems if p.code == codes.UNMAPPED_ELEMENT and p.bbox]
        for review in control.comparison.review:
            if review.code != vlm.MISSING_IN_TEXT:
                continue
            vlm_field = next(
                (
                    v
                    for v in control.vlm_fields
                    if v.label == review.label and v.page == review.page
                ),
                None,
            )
            box = vlm_field.bbox if vlm_field else None
            if box is not None and any(_overlap(box, other) for other in unmapped_boxes):
                continue  # to samo miejsce zgłasza już UNMAPPED_ELEMENT
            problems.append(
                RepairProblem(
                    code=vlm.MISSING_IN_TEXT,
                    action=codes.RepairAction.ADD_FIELD,
                    page=review.page,
                    label=review.label,
                    bbox=box,
                    evidence={"vlm_kind": vlm_field.kind if vlm_field else ""},
                )
            )
    return sorted(problems, key=lambda p: (p.page or 0, p.code, p.key[2]))


def _overlap(a: BoundingBox, b: BoundingBox) -> bool:
    return bool(a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1)


def problems_payload(problems: Sequence[RepairProblem]) -> list[dict[str, Any]]:
    """Problemy jako dane JSON do bloku danych promptu #8 (etykiety to treść dokumentu)."""
    return [p.model_dump(mode="json", exclude_none=True, exclude_defaults=True) for p in problems]


def _diff(
    before: Sequence[FieldRef], after: Sequence[FieldRef]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], list[FieldRef]]:
    old = {f.field_id: f for f in before}
    new = {f.field_id: f for f in after}
    old_names = {f.tech_name for f in before}
    new_names = {f.tech_name for f in after}
    added = tuple(
        sorted(f.tech_name for k, f in new.items() if k not in old and f.tech_name not in old_names)
    )
    removed_refs = [f for k, f in old.items() if k not in new and f.tech_name not in new_names]
    retyped = tuple(
        sorted(
            f.tech_name for k, f in new.items() if k in old and old[k].field_type != f.field_type
        )
    )
    return added, tuple(sorted(f.tech_name for f in removed_refs)), retyped, removed_refs


def _removed_source(f: FieldRef) -> SkippedElement:
    reason = f"{codes.FIELD_REMOVED_BY_REPAIR}: pole „{f.tech_name}” usunięte w iteracji naprawczej"
    return SkippedElement(
        reason=reason,
        source=FieldSource(
            channel=SourceChannel.TEXT,
            page=f.page,
            text=f.source_text,
            element_id=None,
        ),
    )


async def run_control_with_repair(
    form: RawForm,
    control: ControlFn,
    repairer: FieldRepairer | None,
    blocks: Sequence[TextBlock],
    cfg: FusionConfig | None = None,
) -> RepairOutcome:
    """Kontrola z najwyżej ``repair_max_iterations`` iteracjami naprawczymi."""
    cfg = cfg or FusionConfig()
    fields, result = await control(form)
    problems = build_problems(fields, result, blocks)
    rounds: list[RepairRound] = []
    skipped: list[SkippedElement] = []
    stop: StopReason
    if repairer is None or cfg.repair_max_iterations == 0:
        stop = "disabled" if problems else "no_problems"
    else:
        stop = "limit"
        previous = frozenset(p.key for p in problems)
        for iteration in range(1, cfg.repair_max_iterations + 1):
            if not problems:
                stop = "no_problems"
                break
            try:
                repaired = await repairer.repair(form, problems, iteration)
                new_fields, new_result = await control(repaired)
            except Exception as exc:  # błąd naprawy nie przerywa analizy; zostaje poprzedni wynik
                logger.warning("Iteracja naprawcza %d: błąd %s", iteration, type(exc).__name__)
                rounds.append(RepairRound(iteration, tuple(problems)))
                stop = "repairer_failed"
                break
            added, removed, retyped, removed_refs = _diff(fields, new_fields)
            rounds.append(RepairRound(iteration, tuple(problems), added, removed, retyped))
            skipped += [_removed_source(f) for f in removed_refs]
            form, fields, result = repaired, new_fields, new_result
            problems = build_problems(fields, result, blocks)
            current = frozenset(p.key for p in problems)
            if not problems:
                stop = "no_problems"
                break
            if current == previous:
                stop = "no_progress"
                break
            previous = current
    return RepairOutcome(
        form=form,
        fields=tuple(fields),
        control=result,
        unresolved=tuple(problems),
        rounds=tuple(rounds),
        stop=stop,
        skipped=tuple(skipped),
    )


def mark_unresolved(
    evidence: Mapping[UUID, FieldEvidence], unresolved: Sequence[RepairProblem]
) -> tuple[dict[UUID, FieldEvidence], list[Issue]]:
    """Nierozstrzygnięte problemy: pole dostaje ``REPAIR_UNRESOLVED`` (LOW), a problemy bez pola
    (np. element bez pola) trafiają do problemów formularza jako ``REPAIR_UNRESOLVED``."""
    updated = dict(evidence)
    form_issues: list[Issue] = []
    spec = codes.spec(codes.REPAIR_UNRESOLVED)
    for problem in unresolved:
        target = updated.get(problem.field_id) if problem.field_id else None
        message = f"{spec.description}: {problem.code}"
        if target is None:
            where = ",".join(problem.element_ids) or (problem.label_block_id or "")
            form_issues.append(
                Issue(
                    code=codes.REPAIR_UNRESOLVED,
                    severity=spec.severity,
                    message=f"{message} ({where}, strona {problem.page})",
                )
            )
            continue
        if codes.REPAIR_UNRESOLVED in target.reasons:
            continue
        reasons = (*target.reasons, codes.REPAIR_UNRESOLVED)
        issue = Issue(
            code=codes.REPAIR_UNRESOLVED,
            severity=spec.severity,
            message=message,
            field_id=target.field_id,
        )
        updated[target.field_id] = target.model_copy(
            update={
                "reasons": reasons,
                "level": codes.level_for(reasons),
                "issues": (*target.issues, issue),
            }
        )
    return updated, form_issues


class FakeRepairer:
    """Naprawa testowa: funkcja (formularz, problemy, iteracja) → formularz; zapisuje wywołania."""

    def __init__(self, fix: Callable[[RawForm, Sequence[RepairProblem], int], RawForm]) -> None:
        self._fix = fix
        self.calls: list[tuple[int, tuple[RepairProblem, ...]]] = []

    async def repair(
        self, form: RawForm, problems: Sequence[RepairProblem], iteration: int
    ) -> RawForm:
        self.calls.append((iteration, tuple(problems)))
        return self._fix(form, problems, iteration)
