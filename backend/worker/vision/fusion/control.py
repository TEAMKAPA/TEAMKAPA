"""Kontrola pól dokumentu względem kanału wizualnego (E.5–E.6): łączenie, źródła, dowody pól.

Wynik (``ControlResult``) niesie też elementy po fuzji i pola z trybu 2, żeby lista problemów do
naprawy (``repair.build_problems``) i raport miały komplet informacji. Kontrola jest
deterministyczna i nie wywołuje modeli.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from backend.worker.vision.fusion.confidence import VisualContext, evidence_for_fields
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.linking import Linking, link_fields
from backend.worker.vision.fusion.models import FieldEvidence, FieldRef, FusedElement
from backend.worker.vision.vlm.double_read import Comparison, FormField
from backend.worker.vision.vlm.roles import TextBlock


@dataclass(frozen=True)
class ControlResult:
    linking: Linking
    evidence: dict[UUID, FieldEvidence]
    comparison: Comparison | None
    elements: tuple[FusedElement, ...] = ()
    vlm_fields: tuple[FormField, ...] = ()


def control_fields(
    fields: Sequence[FieldRef],
    elements: Iterable[FusedElement],
    blocks: Sequence[TextBlock],
    context: VisualContext,
    cfg: FusionConfig | None = None,
    *,
    acroform: Sequence[TextBlock] = (),
    page_mm: tuple[float, float] = (210.0, 297.0),
) -> ControlResult:
    all_elements = tuple(elements)
    linking = link_fields(fields, all_elements, blocks, cfg, acroform=acroform, page_mm=page_mm)
    evidence, comparison = evidence_for_fields(fields, linking, blocks, context, cfg)
    return ControlResult(linking, evidence, comparison, all_elements, context.vlm_fields)
