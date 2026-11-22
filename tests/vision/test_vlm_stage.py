"""Etap VLM (#11, D.6): tryby ACCURACY_MODE max i balanced, raport przeglądu, odporność na błędy."""

from pathlib import Path
from typing import Any

import pytest

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.detect.page import detect_image
from backend.worker.vision.models import ElementKind
from backend.worker.vision.vlm.cache import CachedVlmClient
from backend.worker.vision.vlm.client import FakeVlmClient, VlmUnavailable, generation_params
from backend.worker.vision.vlm.codes import (
    CELLS_LABEL_CONFLICT,
    MISSING_IN_TEXT,
    MISSING_IN_VLM,
    VLM_UNAVAILABLE,
)
from backend.worker.vision.vlm.config import AccuracyMode, VlmBackend, VlmSettings
from backend.worker.vision.vlm.double_read import FormField
from backend.worker.vision.vlm.roles import TextBlock
from backend.worker.vision.vlm.stage import PageInput, run_vlm_stage
from tests.vision import vlm_synth
from tests.vision.synth_forms import contrast, make_form


def settings(tmp_path: Path, **overrides: Any) -> VlmSettings:
    env = tmp_path / "t.env"
    env.touch()
    return VlmSettings(_env_file=env, backend=VlmBackend.FAKE, **overrides)  # type: ignore[call-arg]


@pytest.fixture(scope="module")
def pages() -> list[PageInput]:
    clean = make_form(seed=0)
    faded = contrast(make_form(seed=1), 0.4)
    result = []
    for number, form in ((1, clean), (2, faded)):
        detection = detect_image(form.image, page=number, dpi=form.dpi)
        result.append(PageInput(number, form.image, detection.elements, detection.reasons))
    return result


def blocks(pages: list[PageInput]) -> list[TextBlock]:
    return [
        TextBlock(
            id=f"p{p.page}-b1",
            page=p.page,
            text="Nagłówek",
            bbox=BoundingBox(x0=0.1, y0=0.02, x1=0.3, y1=0.03),
        )
        for p in pages
    ]


TEXT_FIELDS = [
    FormField(id="nr", label="Numer identyfikacyjny", kind="text"),
    FormField(id="brak", label="Pole nieobecne na obrazie", kind="text"),
]


def test_max_mode_reads_every_page(tmp_path: Path, pages: list[PageInput]) -> None:
    client = FakeVlmClient(vlm_synth.echo_responder)
    result = run_vlm_stage(pages, blocks(pages), TEXT_FIELDS, client, settings(tmp_path))
    assert [r.task for r in client.requests] == ["roles", "fields", "roles", "fields"]
    assert [p.double_read for p in result.pages] == [True, True]
    assert {e.page for e in result.elements} == {1, 2}
    codes = {r.code for r in result.review}
    assert MISSING_IN_VLM in codes and MISSING_IN_TEXT in codes
    assert result.comparison is not None and any(
        m.text.id == "nr" for m in result.comparison.matches
    )
    report = result.report()
    assert report["mode"] == "max" and report["codes"][MISSING_IN_VLM] == 1
    assert report["usage"]["prompt_tokens"] > 0


def test_balanced_mode_reads_only_uncertain_pages(tmp_path: Path, pages: list[PageInput]) -> None:
    client = FakeVlmClient(vlm_synth.echo_responder)
    cfg = settings(tmp_path, accuracy_mode=AccuracyMode.BALANCED)
    result = run_vlm_stage(pages, blocks(pages), TEXT_FIELDS, client, cfg)
    assert [r.task for r in client.requests] == ["roles", "roles", "fields"]
    assert [(p.double_read, p.double_read_reason) for p in result.pages] == [
        (False, "pewna strona"),
        (True, "kod strony: LOW_CONTRAST"),
    ]
    # Pola z JSON-a bez numeru strony: przy części stron brak pary nie jest raportowany.
    assert MISSING_IN_VLM not in {r.code for r in result.review}
    assert any("MISSING_IN_VLM pominięte" in n for n in result.notes)


def test_balanced_reads_pages_without_text_blocks(tmp_path: Path, pages: list[PageInput]) -> None:
    client = FakeVlmClient(vlm_synth.echo_responder)
    cfg = settings(tmp_path, accuracy_mode=AccuracyMode.BALANCED)
    result = run_vlm_stage(pages[:1], [], None, client, cfg)
    assert result.pages[0].double_read_reason == "brak bloków tekstu"
    assert result.comparison is None and any("bez porównania" in n for n in result.notes)


def test_vlm_unavailable_keeps_pipeline_running(tmp_path: Path, pages: list[PageInput]) -> None:
    failures: list[Any] = [VlmUnavailable("HTTP 503") for _ in range(4)]
    client = FakeVlmClient(vlm_synth.echo_responder, failures=failures)
    result = run_vlm_stage(pages, blocks(pages), TEXT_FIELDS, client, settings(tmp_path))
    assert [p.roles_status for p in result.pages] == ["unavailable", "unavailable"]
    assert [p.fields_status for p in result.pages] == ["unavailable", "unavailable"]
    assert len(result.elements) == sum(len(p.elements) for p in pages)
    assert all(VLM_UNAVAILABLE in e.confidence_reasons for e in result.elements)
    assert result.report()["codes"][VLM_UNAVAILABLE] == 4


def test_cells_label_conflict_from_label_block(tmp_path: Path, pages: list[PageInput]) -> None:
    page = pages[0]
    boxes = next(e for e in page.elements if e.kind is ElementKind.CHAR_BOXES and e.cells == 3)
    pesel = TextBlock(
        id="p1-b7", page=1, text="Numer PESEL", bbox=BoundingBox(x0=0, y0=0, x1=0.1, y1=0.1)
    )

    def responder(request: Any) -> str:
        content = vlm_synth.echo_responder(request)
        if request.task == "roles":
            content = content.replace(
                f'"id": "{boxes.id}", "role": "input", "label_block_id": null',
                f'"id": "{boxes.id}", "role": "input", "label_block_id": "p1-b7"',
            )
        return content

    result = run_vlm_stage([page], [pesel], None, FakeVlmClient(responder), settings(tmp_path))
    conflict = [r for r in result.review if r.code == CELLS_LABEL_CONFLICT]
    assert [r.element_id for r in conflict] == [boxes.id] and "oczekiwano [11]" in conflict[
        0
    ].detail
    updated = next(e for e in result.elements if e.id == boxes.id)
    assert CELLS_LABEL_CONFLICT in updated.confidence_reasons


def test_rerun_uses_cache(tmp_path: Path, pages: list[PageInput]) -> None:
    cfg = settings(tmp_path)
    fake = FakeVlmClient(vlm_synth.echo_responder)
    first = CachedVlmClient(fake, generation_params(cfg), tmp_path / "cache")
    run_vlm_stage(pages, blocks(pages), TEXT_FIELDS, first, cfg)
    again = CachedVlmClient(fake, generation_params(cfg), tmp_path / "cache")
    result = run_vlm_stage(pages, blocks(pages), TEXT_FIELDS, again, cfg)
    assert len(fake.requests) == 4 and again.stats.calls == 0 and again.stats.cache_hits == 4
    assert result.pages[0].roles_status == "ok"
