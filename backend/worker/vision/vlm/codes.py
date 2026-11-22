"""Kody powodów przeglądu kanału wizualnego (#11) i pozycja raportu przeglądu."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

MISSING_IN_VLM = "MISSING_IN_VLM"  # pole z tekstu nieodczytane przez VLM z obrazu
MISSING_IN_TEXT = "MISSING_IN_TEXT"  # pole odczytane przez VLM, którego nie ma w polach z tekstu
TYPE_MISMATCH = "TYPE_MISMATCH"  # ten sam opis pola, inny rodzaj (np. tekst a wybór)
CELLS_LABEL_CONFLICT = "CELLS_LABEL_CONFLICT"  # liczba kratek sprzeczna z etykietą (np. PESEL ≠ 11)
VLM_ONLY_ELEMENT = "VLM_ONLY_ELEMENT"  # element dodany przez VLM, niewykryty przez OpenCV
NO_ROLE = "NO_ROLE"  # element OpenCV bez roli od VLM
LOW_OCR_CONFIDENCE = "LOW_OCR_CONFIDENCE"  # etykieta z bloku tekstu o niskiej pewności OCR
VLM_INVALID_RESPONSE = "VLM_INVALID_RESPONSE"  # odpowiedź niezgodna ze schematem po ponowieniu
VLM_UNAVAILABLE = "VLM_UNAVAILABLE"  # błąd API, limit czasu, brak tokenu

REVIEW_CODES = (
    MISSING_IN_VLM,
    MISSING_IN_TEXT,
    TYPE_MISMATCH,
    CELLS_LABEL_CONFLICT,
    VLM_ONLY_ELEMENT,
    NO_ROLE,
    LOW_OCR_CONFIDENCE,
    VLM_INVALID_RESPONSE,
    VLM_UNAVAILABLE,
)


class ReviewItem(BaseModel):
    """Pozycja raportu przeglądu: rozbieżne albo niepotwierdzone pole lub element.

    Raport jest dla osoby przeglądającej wynik (zawiera etykiety pól); do logów trafiają tylko kody
    i liczby.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    page: Annotated[int, Field(ge=1)] | None = None
    element_id: str | None = None
    label: str | None = None
    detail: str = ""
