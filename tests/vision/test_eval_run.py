"""CLI ewaluacji (#12, B.4): kompletność danych, ochrona holdout, baza i kontrola miernika."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import (
    DocumentKind,
    ElementKind,
    ElementRole,
    ElementSource,
    PageSource,
    VisualElement,
)
from eval.visual import run as run_module
from eval.visual import selfcheck
from eval.visual.models import (
    LABELS_SCHEMA,
    MANIFEST_SCHEMA,
    PREDICTIONS_SCHEMA,
    FileOrigin,
    FileStatus,
    GeometryCheck,
    LabelElement,
    LabelingInfo,
    LabelSource,
    LabelStatus,
    LabelTool,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    PageLabels,
    PagePredictions,
    RendererInfo,
    RoleOrigin,
    Split,
    System,
    dump_json,
    predictions_file_name,
    write_text,
)

H1, H2 = "1" * 64, "2" * 64
W, H = 1000, 1400
BOXES = [  # (kind, bbox w pikselach)
    (ElementKind.TABLE_CELL, (100, 100, 900, 160)),
    (ElementKind.CHAR_BOXES, (100, 200, 300, 240)),
    (ElementKind.DOTTED_LINE, (100, 300, 800, 304)),
    (ElementKind.CHECKBOX, (100, 400, 124, 424)),
    (ElementKind.CHECKBOX, (300, 400, 324, 424)),
    (ElementKind.SIGNATURE, (500, 1200, 900, 1204)),
    (ElementKind.TEXT_LINE, (100, 1000, 900, 1003)),
    (ElementKind.SECTION_FRAME, (50, 50, 950, 500)),
    (ElementKind.TABLE_CELL, (100, 600, 400, 650)),
    (ElementKind.TABLE_CELL, (400, 600, 900, 650)),
]


def _bbox(b: tuple[int, int, int, int]) -> BoundingBox:
    return BoundingBox(x0=b[0] / W, y0=b[1] / H, x1=b[2] / W, y1=b[3] / H)


def _file(file_id: str, fmt: str, kind: DocumentKind, sha: str) -> ManifestFile:
    return ManifestFile(
        file_id=file_id,
        origin=FileOrigin.COI,
        format=fmt,
        sha256=sha,
        size_bytes=1,
        declared_kind=kind,
        detected_kind=kind,
        status=FileStatus.RENDERED,
        page_count=2,
        page_source=PageSource.NATIVE_PDF if fmt == "pdf" else PageSource.IMAGE,
    )


CHECK = GeometryCheck(
    size_equal=True,
    shift_px=(0, 0),
    variant_ink_near_base=0.99,
    base_ink_near_variant=0.99,
    passed=True,
)


def _page(doc: str, number: int, image: str) -> ManifestPage:
    return ManifestPage(
        label_id=f"{doc}-p{number:02d}",
        base_file_id=f"{doc}-pdf",
        page=number,
        width_px=W,
        height_px=H,
        dpi=200.0,
        image_sha256=image,
        variants=(
            ManifestVariant(file_id=f"{doc}-pdf", page=number, included=True),
            ManifestVariant(file_id=f"{doc}-png", page=number, check=CHECK, included=True),
        ),
    )


def _document(doc: str, folder: str, split: Split, shas: tuple[str, str]) -> ManifestDocument:
    return ManifestDocument(
        doc_id=doc,
        folder=folder,
        split=split,
        files=(
            _file(f"{doc}-pdf", "pdf", DocumentKind.PDF, shas[0]),
            _file(f"{doc}-png", "png", DocumentKind.PNG, shas[1]),
        ),
        pages=(_page(doc, 1, H1), _page(doc, 2, H2)),
    )


def _manifest() -> Manifest:
    return Manifest(
        schema_version=MANIFEST_SCHEMA,
        created=date(2026, 11, 22),
        split_registered=date(2026, 11, 21),
        split_rule="po dokumentach",
        renderer=RendererInfo(commit="abcdef1", dirty=False, dpi=200, versions={}),
        labeling=LabelingInfo(tool="label-studio", version="1.23.2", config_sha256=H1),
        documents=(
            _document("f02", "2", Split.DEV, ("a" * 64, "b" * 64)),
            _document("f01", "1", Split.HOLDOUT, ("c" * 64, "d" * 64)),
        ),
    )


def _labels(doc: str, number: int, image: str) -> PageLabels:
    elements = [
        LabelElement(
            id=f"gt-p{number}-{i:03d}",
            kind=kind,
            bbox=_bbox(b),
            role={
                ElementKind.CHECKBOX: ElementRole.CHOICE,
                ElementKind.SIGNATURE: ElementRole.E_SIGNATURE,
                ElementKind.SECTION_FRAME: ElementRole.SECTION,
            }.get(kind, ElementRole.INPUT),
            role_origin=RoleOrigin.DEFAULT,
            cells=10 if kind is ElementKind.CHAR_BOXES else None,
        )
        for i, (kind, b) in enumerate(BOXES, start=1)
    ]
    return PageLabels(
        schema_version=LABELS_SCHEMA,
        label_id=f"{doc}-p{number:02d}",
        doc_id=doc,
        source=LabelSource(
            file_id=f"{doc}-pdf",
            sha256=H1,
            page=number,
            width_px=W,
            height_px=H,
            dpi=200.0,
            image_sha256=image,
        ),
        status=LabelStatus.COMPLETE,
        annotator="human-1",
        tool=LabelTool(name="label-studio", version="1.23.2", config_sha256=H1),
        elements=tuple(elements),
    )


def _predictions(file_id: str, page: int, drop: int = 0, system: System = System.OPENCV) -> str:
    elements = [
        VisualElement(
            id=f"cv-p{page}-{i:03d}",
            page=page,
            kind=kind,
            bbox=_bbox((b[0] + 1, b[1] + 1, b[2] + 1, b[3] + 1)),
            cells=10 if kind is ElementKind.CHAR_BOXES else None,
            source=ElementSource.OPENCV,
            confidence=0.9,
        )
        for i, (kind, b) in enumerate(BOXES[drop:], start=1)
    ]
    data = PagePredictions(
        schema_version=PREDICTIONS_SCHEMA,
        system=system,
        file_id=file_id,
        page=page,
        width_px=W,
        height_px=H,
        elements=tuple(elements),
    )
    return dump_json(data)


class Env:
    def __init__(self, root: Path) -> None:
        self.manifest = root / "manifest.full.json"
        write_text(self.manifest, dump_json(_manifest()))
        self.labels = root / "labels"
        write_text(self.labels / "f02-p01.json", dump_json(_labels("f02", 1, H1)))
        # Etykiety holdout celowo uszkodzone: ewaluacja dev nie może ich otworzyć.
        write_text(self.labels / "f01-p01.json", "{uszkodzony")
        self.pred = root / "pred"
        for file_id in ("f02-pdf", "f02-png"):
            write_text(self.pred / predictions_file_name(file_id, 1), _predictions(file_id, 1))
        write_text(self.pred / predictions_file_name("f01-pdf", 1), "{uszkodzony")
        self.out = root / "eval"

    def args(self, *extra: str) -> list[str]:
        return [
            "--system",
            "opencv",
            "--predictions",
            str(self.pred),
            "--manifest",
            str(self.manifest),
            "--labels",
            str(self.labels),
            "--out",
            str(self.out),
            *extra,
        ]

    def report(self, stem: str = "dev-opencv") -> dict[str, Any]:
        result: dict[str, Any] = json.loads((self.out / f"{stem}.json").read_text("utf-8"))
        return result


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.setattr(run_module, "git_state", lambda repo: ("abcdef1234567", False))
    return Env(tmp_path)


def test_dev_run_reports_and_never_opens_holdout(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_module.main(env.args()) == 0
    report = env.report()
    assert report["split"] == "dev" and report["system"] == "opencv"
    assert report["coverage"] == {
        "label_sets": 2,
        "labeled": 1,
        "unlabeled": ["f02-p02"],
        "preliminary": True,
        "evaluation_pages": 2,
        "skipped_prediction_files": 1,
    }
    micro = report["overall"]["micro"]
    assert (micro["tp"], micro["fp"], micro["fn"]) == (20, 0, 0)  # przesunięcie 1 px nie szkodzi
    assert set(report["by_format"]) == {"pdf", "image_variant"}
    assert set(report["by_document"]) == {"f02"}
    assert report["micro_f1_by_iou"] == {"0.3": 1.0, "0.5": 1.0, "0.7": 1.0}
    markdown = (env.out / "dev-opencv.md").read_text(encoding="utf-8")
    assert "Wstępny pomiar" in markdown and "Znane ograniczenia" in markdown
    assert "wstępny pomiar" in capsys.readouterr().out
    assert not (env.out / run_module.HOLDOUT_LOG).exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (("--split", "holdout"), "holdout tylko z --final"),
        (("--final",), "--final dotyczy wyłącznie holdout"),
    ],
)
def test_holdout_guard(
    env: Env, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], message: str
) -> None:
    assert run_module.main(env.args(*extra)) == 2
    assert message in capsys.readouterr().err
    assert not (env.out / run_module.HOLDOUT_LOG).exists()
    assert not (env.out / "holdout-opencv.json").exists()


def test_holdout_final_is_logged(env: Env) -> None:
    write_text(env.labels / "f01-p01.json", dump_json(_labels("f01", 1, H1)))
    for file_id in ("f01-pdf", "f01-png"):
        write_text(env.pred / predictions_file_name(file_id, 1), _predictions(file_id, 1))
    assert run_module.main(env.args("--split", "holdout", "--final")) == 0
    log = (env.out / run_module.HOLDOUT_LOG).read_text(encoding="utf-8").splitlines()
    assert len(log) == 1 and "\tabcdef1234567\topencv\t" in log[0]
    assert env.report("holdout-opencv")["overall"]["micro"]["tp"] == 20
    assert run_module.main(env.args("--split", "holdout", "--final")) == 0
    assert len((env.out / run_module.HOLDOUT_LOG).read_text(encoding="utf-8").splitlines()) == 2


def test_missing_unknown_and_inconsistent_predictions(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    (env.pred / predictions_file_name("f02-png", 1)).unlink()
    assert run_module.main(env.args()) == 2
    assert "brak predykcji dla stron: f02-png-p01.json" in capsys.readouterr().err

    write_text(env.pred / predictions_file_name("f02-png", 1), _predictions("f02-png", 1))
    write_text(env.pred / "f09-pdf-p01.json", "{}")
    assert run_module.main(env.args()) == 2
    assert "nie istnieje w manifeście" in capsys.readouterr().err
    (env.pred / "f09-pdf-p01.json").unlink()

    write_text(env.pred / "notatki.json", "{}")
    assert run_module.main(env.args()) == 2
    assert "spoza wzorca" in capsys.readouterr().err
    (env.pred / "notatki.json").unlink()

    vlm = _predictions("f02-png", 1, system=System.VLM)
    write_text(env.pred / predictions_file_name("f02-png", 1), vlm)
    assert run_module.main(env.args()) == 2
    assert "system vlm, oczekiwano opencv" in capsys.readouterr().err


def test_labels_must_match_manifest_image(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    write_text(env.labels / "f02-p01.json", dump_json(_labels("f02", 1, H2)))
    assert run_module.main(env.args()) == 2
    assert "innego obrazu" in capsys.readouterr().err


def test_no_labeled_pages(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    (env.labels / "f02-p01.json").unlink()
    assert run_module.main(env.args()) == 2
    assert "brak oznaczonych stron" in capsys.readouterr().err


def test_baseline_requires_full_coverage_and_detects_regression(
    env: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.json"
    assert run_module.main(env.args("--write-baseline", str(baseline))) == 2
    assert "pełnego pokrycia" in capsys.readouterr().err and not baseline.exists()

    write_text(env.labels / "f02-p02.json", dump_json(_labels("f02", 2, H2)))
    for file_id in ("f02-pdf", "f02-png"):
        write_text(env.pred / predictions_file_name(file_id, 2), _predictions(file_id, 2))
    assert run_module.main(env.args("--write-baseline", str(baseline))) == 0
    saved = json.loads(baseline.read_text(encoding="utf-8"))
    assert saved["metrics"]["micro_f1"] == 1.0 and saved["tolerance"] == 0.02

    assert run_module.main(env.args("--baseline", str(baseline))) == 0
    for file_id in ("f02-pdf", "f02-png"):  # bez pierwszych 3 elementów
        write_text(env.pred / predictions_file_name(file_id, 2), _predictions(file_id, 2, drop=3))
    assert run_module.main(env.args("--baseline", str(baseline))) == 1
    out = capsys.readouterr().out
    assert "REGRESJA: micro_recall" in out and "REGRESJA: f1[table_cell]" in out
    assert env.report()["regressions"]


def test_compare_rejects_mismatched_baseline() -> None:
    report = {"system": "opencv", "split": "dev", "params": {"iou": 0.5}, "overall": {}}
    with pytest.raises(run_module.EvalError, match="nieznany format"):
        run_module.compare(report, {"schema_version": "x"})
    other = {"schema_version": run_module.BASELINE_SCHEMA, "system": "vlm", "split": "dev"}
    with pytest.raises(run_module.EvalError, match="system=vlm"):
        run_module.compare(report, other)


def test_selfcheck_on_dev_only(env: Env) -> None:
    result = selfcheck.selfcheck(run_module.read_json(env.manifest, Manifest), env.labels)
    rows = result["scenarios"]
    assert result["labeled"] == ["f02-p01"]
    assert rows["etykiety bez zmian"]["f1"] == 1.0
    assert rows["przesunięcie 2 px (x i y)"]["f1"] == 1.0
    assert rows["przesunięcie o pół szerokości"]["recall"] == 0.0
    assert rows["bez co 10. elementu"] == {
        "pages": 2,
        "tp": 18,
        "fp": 0,
        "fn": 2,
        "precision": 1.0,
        "recall": 0.9,
        "f1": round(36 / 38, 4),
    }
