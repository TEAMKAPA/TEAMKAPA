"""Predykcje detektora dla zbioru #12 (#10, C.9) na syntetycznym drzewie ``dane``."""

from datetime import date
from pathlib import Path

import pytest

from backend.worker.vision.config import RenderConfig
from backend.worker.vision.models import DocumentKind, ElementKind, PageSource
from backend.worker.vision.render import render_document
from eval.visual import predict as predict_module
from eval.visual.geometry import image_sha256
from eval.visual.models import (
    MANIFEST_SCHEMA,
    FileOrigin,
    FileStatus,
    LabelingInfo,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    PagePredictions,
    RendererInfo,
    Split,
    System,
    dump_json,
    read_json,
    write_text,
)
from eval.visual.run import EvalError
from tests.vision.synth import encode
from tests.vision.synth_forms import make_form

H1 = "1" * 64


def _file(file_id: str, path: str, data: bytes) -> ManifestFile:
    import hashlib

    return ManifestFile(
        file_id=file_id,
        origin=FileOrigin.COI,
        format="png",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        declared_kind=DocumentKind.PNG,
        detected_kind=DocumentKind.PNG,
        status=FileStatus.RENDERED,
        page_count=1,
        page_source=PageSource.IMAGE,
        path=path,
    )


def _page(doc: str, file_id: str, image_hash: str) -> ManifestPage:
    return ManifestPage(
        label_id=f"{doc}-p01",
        base_file_id=file_id,
        page=1,
        width_px=1654,
        height_px=2339,
        dpi=200.0,
        image_sha256=image_hash,
        variants=(ManifestVariant(file_id=file_id, page=1, included=True),),
    )


class Env:
    def __init__(self, root: Path) -> None:
        self.dane = root / "dane"
        png = encode(make_form(seed=0).image, "PNG", dpi=(200.0, 200.0))
        broken = b"\x89PNG uszkodzony"  # holdout: nie może zostać otwarty przy dev
        write_bytes(self.dane / "coi" / "2. a" / "a.png", png)
        write_bytes(self.dane / "coi" / "1. b" / "b.png", broken)
        rendered = render_document(png, DocumentKind.PNG, RenderConfig(dpi=200), isolated=False)
        self.image_hash = image_sha256(rendered.pages[0].image)
        dev_file = _file("f02-png", "coi/2. a/a.png", png)
        holdout_file = _file("f01-png", "coi/1. b/b.png", broken)
        self.manifest = Manifest(
            schema_version=MANIFEST_SCHEMA,
            created=date(2026, 11, 22),
            split_registered=date(2026, 11, 21),
            split_rule="po dokumentach",
            renderer=RendererInfo(commit="abcdef1", dirty=False, dpi=200, versions={}),
            labeling=LabelingInfo(tool="label-studio", version="1.23.2", config_sha256=H1),
            documents=(
                ManifestDocument(
                    doc_id="f02",
                    folder="2",
                    split=Split.DEV,
                    files=(dev_file,),
                    pages=(_page("f02", "f02-png", self.image_hash),),
                ),
                ManifestDocument(
                    doc_id="f01",
                    folder="1",
                    split=Split.HOLDOUT,
                    files=(holdout_file,),
                    pages=(_page("f01", "f01-png", H1),),
                ),
            ),
        )
        self.manifest_path = root / "manifest.full.json"
        write_text(self.manifest_path, dump_json(self.manifest))
        self.labels = root / "labels"
        write_text(self.labels / "f02-p01.json", "{}")  # liczy się obecność pliku etykiet
        write_text(self.labels / "f01-p01.json", "{}")
        self.out = root / "pred"
        self.log = root / "eval"


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_dev_predictions(env: Env) -> None:
    written = predict_module.predict(
        env.manifest,
        Split.DEV,
        System.OPENCV,
        env.out,
        dane=env.dane,
        labels=env.labels,
        isolated=False,
    )
    assert written == ["f02-png-p01.json"]
    predictions = read_json(env.out / "f02-png-p01.json", PagePredictions)
    assert predictions.system is System.OPENCV and (
        predictions.width_px,
        predictions.height_px,
    ) == (1654, 2339)
    cells = sorted(e.cells or 0 for e in predictions.elements if e.kind is ElementKind.CHAR_BOXES)
    assert cells == [2, 3, 11]


def test_unlabeled_pages_need_all_pages(env: Env) -> None:
    (env.labels / "f02-p01.json").unlink()
    args = (env.manifest, Split.DEV, System.OPENCV, env.out)
    assert predict_module.predict(*args, dane=env.dane, labels=env.labels, isolated=False) == []
    written = predict_module.predict(
        *args, dane=env.dane, labels=env.labels, all_pages=True, isolated=False
    )
    assert written == ["f02-png-p01.json"]


def test_image_must_match_manifest_and_system_supported(env: Env) -> None:
    page = env.manifest.documents[0].pages[0].model_copy(update={"image_sha256": H1})
    document = env.manifest.documents[0].model_copy(update={"pages": (page,)})
    changed = env.manifest.model_copy(update={"documents": (document,)})
    with pytest.raises(EvalError, match="różni się od manifestu"):
        predict_module.predict(
            changed,
            Split.DEV,
            System.OPENCV,
            env.out,
            dane=env.dane,
            labels=env.labels,
            isolated=False,
        )
    with pytest.raises(EvalError, match="nie ma jeszcze implementacji"):
        predict_module.predict(env.manifest, Split.DEV, System.VLM, env.out, dane=env.dane)


def test_holdout_guard_and_log(
    env: Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    common = [
        "--system",
        "opencv",
        "--manifest",
        str(env.manifest_path),
        "--labels",
        str(env.labels),
    ]
    common += ["--dane", str(env.dane), "--out", str(env.out), "--log-dir", str(env.log)]
    assert predict_module.main([*common, "--split", "holdout"]) == 2
    assert "holdout tylko z --final" in capsys.readouterr().err
    assert predict_module.main([*common, "--final"]) == 2
    assert not (env.log / "holdout_runs.log").exists()
    monkeypatch.setattr(predict_module, "git_state", lambda repo: ("abcdef1234567", False))
    assert predict_module.main([*common, "--split", "holdout", "--final"]) == 2  # plik uszkodzony
    log = (env.log / "holdout_runs.log").read_text(encoding="utf-8")
    assert "\tabcdef1234567\tpredict:opencv\t" in log
