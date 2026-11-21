"""Przygotowanie stron do oznaczania na syntetycznym katalogu ``dane/`` (eval/visual/prepare.py)."""

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.models import DocumentKind
from backend.worker.vision.render import render_document
from eval.visual.geometry import image_sha256
from eval.visual.models import (
    FileStatus,
    Manifest,
    Split,
    SplitDefinition,
    read_json,
)
from eval.visual.prepare import DEFAULT_SPLIT, PrepareError, main, prepare
from tests.vision.synth import SynthPage, encode, fill, make_minimal_docx, make_pdf, white

A4 = (0.0, 0.0, 595.276, 841.89)  # 1654 × 2339 px w 200 DPI
BLACK = (0, 0, 0)
FIRST = "1. Pierwszy wniosek - formularze do cyfryzacji"
SECOND = "2. Drugi wniosek - formularze do cyfryzacji"


def _pdf(pages: int) -> bytes:
    return make_pdf(
        [
            SynthPage(
                mediabox=A4,
                rects=(
                    (80 + 20 * i, 600, 300 + 20 * i, 640, BLACK),
                    (80, 400 - 30 * i, 82, 560, BLACK),
                    (300, 200, 500, 202, BLACK),
                ),
            )
            for i in range(pages)
        ]
    )


def _stitched_png(pdf: bytes) -> bytes:
    """Strony PDF wyrenderowane i zszyte w pionie, jak obrazy w materiałach COI."""
    document = render_document(pdf, DocumentKind.PDF, RenderConfig(dpi=200), isolated=False)
    return encode(np.vstack([page.image for page in document.pages]))


def _scan_jpeg() -> bytes:
    image = white(1100, 1555)
    fill(image, (100, 300, 700, 330), BLACK)
    return encode(image, "JPEG", dpi=(130, 130), quality=90)


def _split(path: Path, **overrides: object) -> Path:
    data: dict[str, object] = {
        "schema_version": "teamkapa.vision.split/1",
        "registered": "2026-11-21",
        "rule": "po dokumentach",
        "dev": ["f01"],
        "holdout": ["f02"],
        "pilot": ["f01-p01"],
    }
    data.update(overrides)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def dane(tmp_path: Path) -> Path:
    root = tmp_path / "dane"
    first = root / "coi" / FIRST
    second = root / "coi" / SECOND
    materials = root / "coi" / "Materiały do zadania dla Uczestników"
    for folder in (first, second, materials):
        folder.mkdir(parents=True)
    pdf = _pdf(2)
    (first / "Formularz.pdf").write_bytes(pdf)
    (first / "Formularz.png").write_bytes(_stitched_png(pdf))
    (first / "Formularz.jpg").write_bytes(_scan_jpeg())
    (first / "Formularz.docx").write_bytes(make_minimal_docx())
    (first / "Formularz – wizualizacja formularza.docx").write_bytes(make_minimal_docx())
    (first / "oczekiwany.json").write_text("{}", encoding="utf-8")
    (second / "Inny.PDF").write_bytes(_pdf(1))
    (materials / "Standardy.pdf").write_bytes(_pdf(1))  # poza folderami dokumentów: pomijany
    return root


def _run(dane: Path, out: Path, split: Path, *, force: bool = False) -> Manifest:
    return prepare(dane, out, split, force=force, isolated=False).manifest


def test_prepare_builds_label_pages_variants_and_tasks(dane: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    split = _split(tmp_path / "split.json")
    result = prepare(dane, out, split, isolated=False)
    manifest = result.manifest

    assert [d.doc_id for d in manifest.documents] == ["f01", "f02"]
    first, second = manifest.documents
    assert (first.split, second.split) == (Split.DEV, Split.HOLDOUT)
    assert first.reference_json and not second.reference_json
    files = {f.file_id: f for f in first.files}
    assert files["f01-pdf"].status is FileStatus.RENDERED and files["f01-pdf"].page_count == 2
    assert files["f01-png"].page_count == 2  # obraz zszyty pocięty na strony
    assert files["f01-docx"].code == "OFFICE_CONVERSION_DISABLED"
    assert files["f01-docx-2"].code == "NOT_A_FORM"  # formularz dostaje ID przed wizualizacją
    assert files["f01-json"].status is FileStatus.REFERENCE
    assert files["f01-pdf"].path == f"coi/{FIRST}/Formularz.pdf"
    assert second.files[0].format == "pdf"

    pages = {p.label_id: p for p in first.pages}
    assert list(pages) == ["f01-p01", "f01-p02", "f01-jpg-p01"]
    variants = {v.file_id: v for v in pages["f01-p01"].variants}
    assert variants["f01-pdf"].check is None
    assert variants["f01-png"].included and variants["f01-png"].check is not None
    assert not variants["f01-jpg"].included and variants["f01-jpg"].reason == "GEOMETRY_MISMATCH"
    scan = pages["f01-jpg-p01"]
    assert (scan.width_px, scan.height_px, scan.dpi) == (1100, 1555, 130.0)
    assert [v.file_id for v in scan.variants] == ["f01-jpg"]

    assert [t["data"]["label_id"] for t in result.tasks] == [
        "f01-p01",  # pilot
        "f02-p01",  # holdout
        "f01-p02",
        "f01-jpg-p01",
    ]
    assert result.tasks[0]["data"]["image"] == "/data/local-files/?d=pages/f01-p01.png"
    assert result.written_images == 4
    for page in [*first.pages, *second.pages]:
        with PILImage.open(out / "pages" / f"{page.label_id}.png") as png:
            stored = np.asarray(png.convert("RGB"), dtype=np.uint8)
        assert image_sha256(stored) == page.image_sha256

    assert read_json(out / "manifest.full.json", Manifest) == manifest
    public_text = (out / "manifest.public.json").read_text(encoding="utf-8")
    assert '"path"' not in public_text and "Pierwszy" not in public_text
    assert read_json(out / "manifest.public.json", Manifest) == manifest.public()
    tasks = json.loads((out / "tasks.json").read_text(encoding="utf-8"))
    assert tasks == result.tasks


def test_prepare_is_repeatable_and_protects_page_images(dane: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    split = _split(tmp_path / "split.json")
    first = _run(dane, out, split)
    before = (out / "manifest.public.json").read_bytes()
    again = prepare(dane, out, split, isolated=False)
    assert again.written_images == 0
    assert again.manifest == first
    assert (out / "manifest.public.json").read_bytes() == before

    page = out / "pages" / "f01-p02.png"
    with PILImage.open(page) as png:
        pixels = np.asarray(png.convert("RGB"), dtype=np.uint8).copy()
    pixels[5, 5] = (0, 0, 0)
    PILImage.fromarray(pixels).save(page, format="PNG")
    with pytest.raises(PrepareError, match="różni się"):
        _run(dane, out, split)
    _run(dane, out, split, force=True)
    with PILImage.open(page) as png:
        restored = np.asarray(png.convert("RGB"), dtype=np.uint8)
    assert image_sha256(restored) == first.find_page("f01-p02")[1].image_sha256


def test_prepare_rejects_inconsistent_inputs(dane: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    with pytest.raises(PrepareError, match="f03"):
        _run(dane, out, _split(tmp_path / "a.json", holdout=["f02", "f03"]))
    with pytest.raises(ValueError, match="f02"):
        _run(dane, out, _split(tmp_path / "b.json", holdout=["f05"]))
    with pytest.raises(PrepareError, match="pilota"):
        _run(dane, out, _split(tmp_path / "c.json", pilot=["f01-p09"]))
    (dane / "coi" / SECOND / "Inny.PDF").unlink()
    with pytest.raises(PrepareError, match="f02"):
        _run(dane, out, _split(tmp_path / "d.json"))


def test_main_reports_errors_without_traceback(
    dane: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"
    missing = tmp_path / "brak.json"
    assert main(["--dane", str(dane), "--out", str(out), "--split", str(missing)]) == 1
    invalid = _split(tmp_path / "split.json", holdout=["f01"])  # dokument w obu częściach
    assert main(["--dane", str(dane), "--out", str(out), "--split", str(invalid)]) == 1
    assert capsys.readouterr().err.count("BŁĄD") == 2
    assert not out.exists()


def test_repo_split_registers_accepted_decision() -> None:
    split = read_json(DEFAULT_SPLIT, SplitDefinition)
    assert split.dev == ("f02", "f05", "f07", "f09")
    assert split.holdout == ("f01", "f03", "f04", "f06", "f08")
    assert split.pilot == ("f02-p01", "f07-p01")
