"""Modele Etapu E: element po fuzji, pole wejściowe, dowody pola, problem do naprawy.

TODO(#17): modele tymczasowe, do uzgodnienia (opis kontraktu w ``docs/vision.md``).

**Pasma pewności.** Poziom wynika z bramek (``codes.level_for``), a liczba ``confidence`` leży
w paśmie tego poziomu: HIGH ``[high, 1]``, MEDIUM ``[low, high)``, LOW ``[0, low)``. Położenie
w paśmie (0–1) daje pewność detektora (element) albo średnia sygnałów (pole). Liczba i poziom
nie mogą sobie przeczyć, a nie ma wag dopasowywanych do zbioru #12.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import Field

from backend.shared.schemas.coi_form import INFORMATIONAL_FIELD_TYPES, FieldType
from backend.shared.schemas.quality import (
    BoundingBox,
    ConfidenceLevel,
    FieldSource,
    Fraction,
    Issue,
)
from backend.worker.vision.fusion.codes import RepairAction
from backend.worker.vision.models import ElementId, ReasonCode, VisionModel, VisualElement

EPSILON = 1e-4  # liczba w paśmie MEDIUM/LOW zawsze ostro poniżej górnej granicy pasma


def banded(level: ConfidenceLevel, position: float, *, high: float, low: float) -> float:
    """Liczba w paśmie poziomu; ``position`` 0–1 (przycinane) to położenie w paśmie."""
    p = min(max(position, 0.0), 1.0)
    if level is ConfidenceLevel.HIGH:
        value = high + (1.0 - high) * p
    elif level is ConfidenceLevel.MEDIUM:
        value = min(low + (high - low) * p, max(high - EPSILON, low))
    else:
        value = min(low * p, max(low - EPSILON, 0.0))
    return round(value, 4)


class Decision(StrEnum):
    """Decyzja fuzji o elemencie (brief 7.6)."""

    ACCEPTED = "accepted"  # OpenCV i VLM zgodne
    REVIEW = "review"  # zostaje, do przeglądu (np. bez roli, konflikt kratek)
    ADDED = "added"  # dodany przez VLM (zawsze LOW)


class FusedElement(VisualElement):
    """Element wizualny po fuzji (kontrakt 7.5 + decyzja i poziom).

    ``confidence`` to liczba w paśmie ``level``; ``confidence_reasons`` zawiera kody detekcji,
    VLM i fuzji (opis w ``codes``).
    """

    decision: Decision
    level: ConfidenceLevel


class RejectedElement(VisionModel):
    """Element dodany przez VLM i odrzucony przez kontrolę pikseli (trafia tylko do przeglądu)."""

    element: VisualElement
    code: ReasonCode
    reason: Annotated[str, Field(max_length=64)]  # np. TEXT_ROW
    detail: str = ""


class FieldRef(VisionModel):
    """Pole formularza z kanału tekstowego (kroki 06–09, po normalizacji), widziane przez kontrolę.

    ``text_element_id`` to ID bloku tekstu etykiety z kroku 02 (``StepSource.element_id``).
    """

    field_id: UUID
    tech_name: str
    title: str = ""
    field_type: FieldType
    validators: tuple[str, ...] = ()
    page: Annotated[int, Field(ge=1)] | None = None
    source_text: str | None = None
    text_element_id: str | None = None
    origin: str | None = None  # "standard": pole ze standardowego szablonu COI

    @property
    def from_template(self) -> bool:
        """Pole nie pochodzi z dokumentu (szablon COI albo pole informacyjne bez źródła)."""
        return self.origin == "standard" or (
            self.source_text is None
            and self.text_element_id is None
            and self.field_type in INFORMATIONAL_FIELD_TYPES
        )

    @property
    def label(self) -> str:
        return self.source_text or self.title or self.tech_name


class FieldEvidence(VisionModel):
    """Dowody kanału wizualnego dla jednego pola: sygnały, kody, bramka poziomu, źródła.

    Punkt podpięcia do ``pipeline.scoring.assess_fields`` (``confidence.apply_evidence``):
    sygnały i problemy są dołączane do oceny zespołu, a poziom to najniższy z bramki i z oceny.
    """

    field_id: UUID
    signals: dict[str, Fraction] = Field(default_factory=dict)
    reasons: tuple[ReasonCode, ...] = ()
    level: ConfidenceLevel  # najwyższy poziom dopuszczony przez bramki kanału wizualnego
    sources: tuple[FieldSource, ...] = ()
    issues: tuple[Issue, ...] = ()
    element_ids: tuple[ElementId, ...] = ()


class RepairProblem(VisionModel):
    """Problem do iteracji naprawczej (propozycja interfejsu dla #8; etykiety to dane dokumentu)."""

    code: ReasonCode
    action: RepairAction
    field_id: UUID | None = None
    tech_name: str | None = None
    page: Annotated[int, Field(ge=1)] | None = None
    element_ids: tuple[ElementId, ...] = ()
    label_block_id: str | None = None
    label: str | None = None
    bbox: BoundingBox | None = None
    evidence: dict[str, str | int] = Field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        """Tożsamość problemu między iteracjami (do wykrywania braku postępu)."""
        target = str(self.field_id) if self.field_id else ",".join(self.element_ids)
        return self.code, self.action.value, target or (self.label or "")
