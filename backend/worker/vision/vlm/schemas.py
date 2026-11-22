"""Schematy odpowiedzi VLM (#11): wymuszane jako ``json_schema`` i walidowane Pydantic.

Wszystkie pola są wymagane (wartość pusta to ``null``), a dodatkowe pola są zabronione, żeby schemat
nadawał się do trybu ``strict``. ``bbox`` to ``[x0, y0, x1, y1]`` w ułamkach strony (początek
w lewym górnym rogu), jak ``BoundingBox`` z #17.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["input", "choice", "skip", "e_signature", "section", "unknown"]
Kind = Literal[
    "char_boxes",
    "checkbox",
    "checkbox_group",
    "text_line",
    "dotted_line",
    "table_cell",
    "section_frame",
    "signature",
    "office_only",
]
FieldKind = Literal["text", "number", "date", "choice", "signature", "char_boxes"]
Box = Annotated[list[float], Field(min_length=4, max_length=4)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ElementAnswer(StrictModel):
    id: str
    role: Role
    label_block_id: str | None


class AddedElement(StrictModel):
    kind: Kind
    bbox: Box
    role: Role
    label_block_id: str | None
    cells: int | None


class RolesResponse(StrictModel):
    """Tryb 1: role i bloki etykiet dla elementów ``cv-*`` oraz elementy pominięte przez OpenCV."""

    elements: list[ElementAnswer]
    added: list[AddedElement]


class FieldAnswer(StrictModel):
    label: str
    kind: FieldKind
    bbox: Box | None
    cells: int | None


class FieldsResponse(StrictModel):
    """Tryb 2: pola strony odczytane wyłącznie z obrazu."""

    fields: list[FieldAnswer]


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Schemat JSON modelu bez tytułów i opisów (docstringi nie są instrukcją dla modelu)."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in {"title", "description"}}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    result: dict[str, Any] = strip(model.model_json_schema())
    return result
