"""Etap E.7: lista problemów, pętla naprawcza z limitem, nierozstrzygnięte jako czerwone."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import BoundingBox, ConfidenceLevel
from backend.worker.pipeline.step10_control import (
    build_problems,
    control_fields,
    mark_unresolved,
    run_control_with_repair,
)
from backend.worker.vision.fusion import codes
from backend.worker.vision.fusion.confidence import VisualContext
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.control import ControlResult
from backend.worker.vision.fusion.elements import PageFusionInput, fuse_page
from backend.worker.vision.fusion.models import FieldRef, RepairProblem
from backend.worker.vision.fusion.repair import FakeRepairer, problems_payload
from backend.worker.vision.models import ElementKind, ElementRole, ElementSource, VisualElement
from backend.worker.vision.vlm import codes as vlm
from backend.worker.vision.vlm.double_read import FormField
from backend.worker.vision.vlm.roles import TextBlock

NS = UUID("00000000-0000-0000-0000-0000000000e7")


def box(x0: float, y0: float, x1: float, y1: float) -> BoundingBox:
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


BLOCKS = [
    TextBlock(id="t1", page=1, text="Numer PESEL", bbox=box(0.05, 0.10, 0.2, 0.12)),
    TextBlock(id="t2", page=1, text="Miejscowość", bbox=box(0.05, 0.20, 0.2, 0.22)),
]
ELEMENTS = fuse_page(
    PageFusionInput(
        1,
        (
            VisualElement(
                id="cv-p1-001",
                page=1,
                kind=ElementKind.CHAR_BOXES,
                bbox=box(0.3, 0.1, 0.6, 0.12),
                cells=10,
                role=ElementRole.INPUT,
                label_block_id="t1",
                source=ElementSource.BOTH,
                confidence=0.9,
            ),
            VisualElement(
                id="cv-p1-002",
                page=1,
                kind=ElementKind.DOTTED_LINE,
                bbox=box(0.3, 0.21, 0.7, 0.212),
                role=ElementRole.INPUT,
                label_block_id="t2",
                source=ElementSource.BOTH,
                confidence=0.8,
            ),
            VisualElement(
                id="cv-p1-003",
                page=1,
                kind=ElementKind.TEXT_LINE,
                bbox=box(0.3, 0.5, 0.7, 0.502),
                role=ElementRole.UNKNOWN,
                source=ElementSource.OPENCV,
                confidence=0.7,
            ),
        ),
        "ok",
    ),
    BLOCKS,
).elements
CONTEXT = VisualContext(pages=frozenset({1}), mode="balanced", double_read_skipped=frozenset({1}))


def pesel(validators: Sequence[str] = ("PESEL",), name: str = "pesel") -> dict[str, Any]:
    return {
        "name": name,
        "type": "TEXT_FIELD",
        "label": "Numer PESEL",
        "block": "t1",
        "validators": list(validators),
    }


def town() -> dict[str, Any]:
    return {
        "name": "miejscowosc",
        "type": "TEXT_FIELD",
        "label": "Miejscowość",
        "block": "t2",
        "validators": [],
    }


def refs(form: dict[str, Any]) -> list[FieldRef]:
    return [
        FieldRef(
            field_id=uuid5(NS, f["name"]),
            tech_name=f["name"],
            field_type=FieldType(f["type"]),
            validators=tuple(f["validators"]),
            page=1,
            source_text=f["label"],
            text_element_id=f["block"],
        )
        for f in form["fields"]
    ]


TOWN_ONLY = tuple(e for e in ELEMENTS if e.id == "cv-p1-002")


def control_for(context: VisualContext = CONTEXT, elements: Any = ELEMENTS) -> Any:
    async def control(form: Any) -> tuple[list[FieldRef], ControlResult]:
        fields = refs(form)
        return fields, control_fields(fields, elements, BLOCKS, context)

    return control


def run(
    form: dict[str, Any], repairer: Any, cfg: FusionConfig | None = None, elements: Any = ELEMENTS
) -> Any:
    control = control_for(elements=elements)
    return asyncio.run(run_control_with_repair(form, control, repairer, BLOCKS, cfg))


def problems_of(form: dict[str, Any], context: VisualContext = CONTEXT) -> list[RepairProblem]:
    fields = refs(form)
    return build_problems(fields, control_fields(fields, ELEMENTS, BLOCKS, context), BLOCKS)


def test_problems_from_codes_and_unmapped_elements() -> None:
    problems = problems_of({"fields": [pesel()]})
    assert [(p.code, p.action) for p in problems] == [
        (vlm.CELLS_LABEL_CONFLICT, codes.RepairAction.CHECK_LENGTH),
        (codes.UNMAPPED_ELEMENT, codes.RepairAction.ADD_FIELD),
    ]
    cells, unmapped = problems
    assert cells.evidence["cells"] == 10 and cells.element_ids == ("cv-p1-001",)
    assert (unmapped.element_ids, unmapped.label) == (("cv-p1-002",), "Miejscowość")
    # cv-p1-003 (bez roli) nie jest problemem do naprawy, a NO_ROLE nie trafia do naprawy wcale.
    assert all("cv-p1-003" not in p.element_ids for p in problems)
    payload = problems_payload(problems)
    assert json.loads(json.dumps(payload)) == payload


def test_no_problems_stops_immediately() -> None:
    fake = FakeRepairer(lambda f, p, i: f)
    outcome = run({"fields": [town()]}, fake, elements=TOWN_ONLY)
    assert (outcome.stop, outcome.iterations, fake.calls) == ("no_problems", 0, [])


def test_repair_resolves_problems_in_one_round() -> None:
    fake = FakeRepairer(lambda f, p, i: {"fields": [town()]})
    outcome = run({"fields": []}, fake, elements=TOWN_ONLY)
    assert (outcome.stop, outcome.iterations, outcome.unresolved) == ("no_problems", 1, ())
    assert outcome.rounds[0].added == ("miejscowosc",)
    (call,) = fake.calls
    assert call[0] == 1 and [p.code for p in call[1]] == [codes.UNMAPPED_ELEMENT]


def test_conflict_from_source_label_survives_repair() -> None:
    """Liczby kratek z obrazu nie zmieni kanał tekstowy: konflikt zostaje do decyzji urzędnika."""
    fix = FakeRepairer(lambda f, p, i: {"fields": [pesel(validators=()), town()]})
    outcome = run({"fields": [pesel()]}, fix)
    assert outcome.stop == "no_progress"
    assert [p.code for p in outcome.unresolved] == [vlm.CELLS_LABEL_CONFLICT]


def test_no_progress_marks_unresolved_red() -> None:
    outcome = run({"fields": [pesel()]}, FakeRepairer(lambda f, p, i: f))
    assert (outcome.stop, outcome.iterations) == ("no_progress", 1)
    evidence, form_issues = mark_unresolved(outcome.control.evidence, outcome.unresolved)
    field = evidence[uuid5(NS, "pesel")]
    assert field.level is ConfidenceLevel.LOW and codes.REPAIR_UNRESOLVED in field.reasons
    assert [i.code for i in form_issues] == [codes.REPAIR_UNRESOLVED]
    assert "cv-p1-002" in form_issues[0].message
    again, _ = mark_unresolved(evidence, outcome.unresolved)
    assert again[field.field_id].reasons.count(codes.REPAIR_UNRESOLVED) == 1


def test_limit_of_two_iterations() -> None:
    def alternate(form: Any, problems: Sequence[RepairProblem], iteration: int) -> Any:
        name = f"pesel{iteration}"  # każda iteracja daje nowe pole z tym samym konfliktem
        return {"fields": [pesel(name=name)]}

    fake = FakeRepairer(alternate)
    outcome = run({"fields": [pesel()]}, fake)
    assert (outcome.stop, outcome.iterations) == ("limit", 2)
    assert [i for i, _ in fake.calls] == [1, 2]
    assert outcome.unresolved
    assert outcome.rounds[0].removed == ("pesel",)
    assert [s.reason.split(":")[0] for s in outcome.skipped] == [
        codes.FIELD_REMOVED_BY_REPAIR,
        codes.FIELD_REMOVED_BY_REPAIR,
    ]


def test_repairer_failure_keeps_previous_result() -> None:
    def broken(form: Any, problems: Sequence[RepairProblem], iteration: int) -> Any:
        raise RuntimeError("model niedostępny")

    original = {"fields": [pesel()]}
    outcome = run(original, FakeRepairer(broken))
    assert (outcome.stop, outcome.form, outcome.iterations) == ("repairer_failed", original, 1)
    assert outcome.unresolved


def test_invalid_repaired_form_keeps_previous_result() -> None:
    bad = FakeRepairer(lambda f, p, i: {"fields": [{"name": "x", "type": "NIE_MA"}]})
    outcome = run({"fields": [pesel()]}, bad)
    assert outcome.stop == "repairer_failed" and outcome.fields[0].tech_name == "pesel"


def test_disabled_repair() -> None:
    assert run({"fields": [pesel()]}, None).stop == "disabled"
    off = run(
        {"fields": [pesel()]},
        FakeRepairer(lambda f, p, i: f),
        FusionConfig(repair_max_iterations=0),
    )
    assert (off.stop, off.iterations) == ("disabled", 0)


def test_missing_in_text_becomes_add_field_without_duplicates() -> None:
    reads = (
        FormField(
            id="v1", label="Miejscowość", kind="text", page=1, bbox=box(0.3, 0.205, 0.7, 0.215)
        ),
        FormField(
            id="v2", label="Data urodzenia", kind="date", page=1, bbox=box(0.3, 0.8, 0.5, 0.82)
        ),
    )
    context = VisualContext(pages=frozenset({1}), double_read=frozenset({1}), vlm_fields=reads)
    problems = problems_of({"fields": [pesel()]}, context)
    added = [p for p in problems if p.code == vlm.MISSING_IN_TEXT]
    assert [p.label for p in added] == ["Data urodzenia"]  # „Miejscowość” zgłasza UNMAPPED_ELEMENT
    assert any(p.code == codes.UNMAPPED_ELEMENT for p in problems)


def test_problem_order_is_deterministic() -> None:
    form = {"fields": [pesel(), town()]}
    assert problems_of(form) == problems_of(form)
