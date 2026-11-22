"""Krok 10 pipeline'u: kontrola kompletności, źródeł i zgodności (Etap E, #9), cienkie wejście.

Kontrola (``backend.worker.vision.fusion``) łączy pola z elementami wizualnymi, sprawdza
kompletność i źródła, porównuje podwójny odczyt i daje dowody pola (``FieldEvidence``) oraz listę
problemów do iteracji naprawczej. **Oceny pól nie przejmuje:** pewność liczy
``pipeline.scoring.assess_fields`` (zespół), a dowody dołącza do niej
``vision.fusion.confidence.apply_evidence`` (punkt podpięcia; propozycja zmiany w
``docs/vision.md``). Podpięcie w orkiestratorze należy do #16.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from backend.worker.vision.fusion.confidence import VisualContext, evidence_for_fields
from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.linking import Linking, link_fields
from backend.worker.vision.fusion.models import FieldEvidence, FieldRef, FusedElement
from backend.worker.vision.vlm.double_read import Comparison
from backend.worker.vision.vlm.roles import TextBlock


@dataclass(frozen=True)
class ControlResult:
    linking: Linking
    evidence: dict[UUID, FieldEvidence]
    comparison: Comparison | None


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
    """Kontrola pól dokumentu względem kanału wizualnego (deterministyczna, bez wywołań modeli)."""
    linking = link_fields(fields, elements, blocks, cfg, acroform=acroform, page_mm=page_mm)
    evidence, comparison = evidence_for_fields(fields, linking, blocks, context, cfg)
    return ControlResult(linking, evidence, comparison)
