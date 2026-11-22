"""Tryb 2 VLM (#11, D.5): podwójny odczyt i porównanie z polami z tekstu (dane syntetyczne)."""

import json
from pathlib import Path
from typing import Any

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.vlm.client import FakeVlmClient, VlmUnavailable
from backend.worker.vision.vlm.codes import (
    CELLS_LABEL_CONFLICT,
    MISSING_IN_TEXT,
    MISSING_IN_VLM,
    TYPE_MISMATCH,
    VLM_INVALID_RESPONSE,
    VLM_UNAVAILABLE,
)
from backend.worker.vision.vlm.config import VlmBackend, VlmSettings
from backend.worker.vision.vlm.double_read import (
    FormField,
    ReferenceJsonFieldSource,
    cells_conflict,
    compare,
    expected_cells,
    normalize,
    read_fields,
)
from tests.vision import vlm_synth


def settings(tmp_path: Path) -> VlmSettings:
    env = tmp_path / "t.env"
    env.touch()
    return VlmSettings(_env_file=env, backend=VlmBackend.FAKE)  # type: ignore[call-arg]


def _field(title: str, kind: str, tech: str) -> dict[str, Any]:
    return {"id": tech, "type": kind, "techName": tech, "title": title, "options": []}


REFERENCE = {  # struktura jak w schemacie COI, treść syntetyczna
    "title": "Wniosek syntetyczny",
    "tabs": [
        {
            "id": "t1",
            "title": "Dane",
            "questions": [
                {
                    "field": {
                        **_field("Sekcja", "SECTION", "s1"),
                        "repeatingSectionGrid": [
                            {"field": _field("Numer identyfikacyjny", "TEXT_FIELD", "nr")},
                            {"field": _field("Wybór wariantu", "RADIO", "wariant")},
                            {"field": _field("Opis", "TEXTAREA", "opis")},
                            {"field": _field("Informacja", "TEXT_BLOCK", "info")},
                            {"field": _field("Załącznik", "FILE_UPLOADER", "zal")},
                            {"field": _field("Coś nowego", "NOWY_TYP", "x")},
                        ],
                    }
                }
            ],
        }
    ],
}


def test_reference_json_source() -> None:
    source = ReferenceJsonFieldSource(REFERENCE)
    fields = source.fields()
    assert [(f.id, f.label, f.kind) for f in fields] == [
        ("nr", "Numer identyfikacyjny", "text"),
        ("wariant", "Wybór wariantu", "choice"),
        ("opis", "Opis", "text"),
    ]
    assert source.skipped_types == {"NOWY_TYP": 1}


def test_read_fields_from_image_only(tmp_path: Path) -> None:
    client = FakeVlmClient({"fields": vlm_synth.recording("synth_fields")})
    result = read_fields(1, vlm_synth.page().image, client, settings(tmp_path))
    recorded = json.loads(vlm_synth.recording("synth_fields"))["fields"]
    assert result.status == "ok" and len(result.fields) == len(recorded)
    assert result.fields[0].id == "vlmf-p1-001" and all(f.page == 1 for f in result.fields)
    prompt = client.requests[0].prompt
    assert "DATA_BEGIN" not in prompt and "Podpis pola" not in prompt  # bez tekstu dokumentu
    boxes = [f for f in result.fields if f.kind == "char_boxes"]
    assert boxes and all(f.cells for f in boxes)


def test_read_fields_failures(tmp_path: Path) -> None:
    down = read_fields(
        1,
        vlm_synth.page().image,
        FakeVlmClient({"fields": "{}"}, failures=[VlmUnavailable("x")]),
        settings(tmp_path),
    )
    assert down.status == "unavailable" and down.review[0].code == VLM_UNAVAILABLE
    bad = read_fields(
        1, vlm_synth.page().image, FakeVlmClient({"fields": "[]"}), settings(tmp_path)
    )
    assert bad.status == "invalid" and bad.review[0].code == VLM_INVALID_RESPONSE


def ff(label: str, kind: Any, **kw: Any) -> FormField:
    return FormField(id=kw.pop("id", label), label=label, kind=kind, **kw)


def test_compare_codes() -> None:
    text = [
        ff("Imię i nazwisko:", "text"),
        ff("Numer PESEL", "text"),
        ff("Płeć", "choice"),
        ff("Data urodzenia", "date"),
    ]
    image = [
        ff("imię i nazwisko", "text", page=1),
        ff("PESEL", "char_boxes", page=1, cells=10),  # zgodna grupa, zła liczba kratek
        ff("Płeć", "text", page=1),  # inny rodzaj
        ff("Telefon", "text", page=1),
    ]
    result = compare(text, image)
    pairs = {(m.text.label, m.vlm.label) for m in result.matches}
    assert pairs == {
        ("Imię i nazwisko:", "imię i nazwisko"),
        ("Numer PESEL", "PESEL"),
        ("Płeć", "Płeć"),
    }
    codes = sorted((r.code, r.label) for r in result.review)
    assert codes == sorted(
        [
            (TYPE_MISMATCH, "Płeć"),
            (MISSING_IN_VLM, "Data urodzenia"),
            (MISSING_IN_TEXT, "Telefon"),
            (CELLS_LABEL_CONFLICT, "PESEL"),
        ]
    )


def test_compare_uses_bbox_for_weak_labels() -> None:
    box = BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.15)
    text = [ff("Adres zamieszkania", "text", page=1, bbox=box)]
    weak = [ff("Adres", "text", page=1, bbox=box)]
    far = [ff("Adres", "text", page=1, bbox=BoundingBox(x0=0.6, y0=0.6, x1=0.9, y1=0.65))]
    other_page = [ff("Adres zamieszkania", "text", page=2, bbox=box)]
    assert compare(text, weak, label_match=95).matches
    assert not compare(text, far, label_match=95).matches
    assert not compare(text, other_page).matches


def test_synthetic_double_read_against_reference(tmp_path: Path) -> None:
    read = read_fields(
        1,
        vlm_synth.page().image,
        FakeVlmClient({"fields": vlm_synth.recording("synth_fields")}),
        settings(tmp_path),
    )
    text_fields = ReferenceJsonFieldSource(REFERENCE).fields()
    result = compare(text_fields, list(read.fields))
    matched = {m.text.id for m in result.matches}
    assert {"nr", "wariant", "opis"} <= matched
    assert any(r.code == MISSING_IN_TEXT for r in result.review)


def test_label_helpers() -> None:
    assert normalize("  Kod  pocztowy: ") == "kod pocztowy"
    assert expected_cells("Numer PESEL") == {11} and expected_cells("Ulica") is None
    assert (
        cells_conflict("NIP", 9)
        and not cells_conflict("NIP", 10)
        and not cells_conflict("NIP", None)
    )
    assert not cells_conflict("Kod pocztowy", 2) and not cells_conflict("Kod pocztowy", 3)
