"""Konwerter eksportu Label Studio (#12) na syntetycznej stronie i syntetycznym eksporcie."""

import copy
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image as PILImage

from backend.worker.vision.models import (
    DocumentKind,
    ElementKind,
    ElementRole,
    ImageArray,
    PageSource,
)
from eval.visual import ls
from eval.visual.convert_ls import (
    ConvertParams,
    PageState,
    convert,
    edge_offsets,
    main,
)
from eval.visual.geometry import image_sha256
from eval.visual.models import (
    MANIFEST_SCHEMA,
    FileOrigin,
    FileStatus,
    LabelingInfo,
    LabelStatus,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    PageLabels,
    RendererInfo,
    RoleOrigin,
    Split,
    dump_json,
    read_json,
    write_text,
)
from eval.visual.preview import LEGEND_HEIGHT, PreviewBox, draw_preview, kind_colors

W, H = 200, 300
H1 = "1" * 64
Box = tuple[int, int, int, int]

# Elementy strony syntetycznej (krawędzie pikseli, x1/y1 wyłączne).
CELL = (20, 20, 180, 60)
BOXES = (20, 80, 80, 100)  # 3 kratki po 20 px
CHECK_A = (20, 120, 32, 132)
CHECK_B = (60, 120, 72, 132)
GROUP = (18, 116, 110, 136)
CELL_2 = (20, 150, 180, 200)
DOTS = (30, 184, 170, 188)
LABEL_CELL = (20, 210, 100, 240)


def _frame(image: ImageArray, box: Box, width: int = 2) -> None:
    x0, y0, x1, y1 = box
    image[y0 : y0 + width, x0:x1] = 0
    image[y1 - width : y1, x0:x1] = 0
    image[y0:y1, x0 : x0 + width] = 0
    image[y0:y1, x1 - width : x1] = 0


def _page_image() -> ImageArray:
    image: ImageArray = np.full((H, W, 3), 255, dtype=np.uint8)
    for box in (CELL, CHECK_A, CHECK_B, CELL_2, LABEL_CELL):
        _frame(image, box)
    for x in range(BOXES[0], BOXES[2], 20):
        _frame(image, (x, BOXES[1], x + 20, BOXES[3]))
    image[DOTS[1] + 1 : DOTS[3] - 1, DOTS[0] : DOTS[2] : 4] = 0
    return image


def _manifest(image: ImageArray) -> Manifest:
    base = ManifestFile(
        file_id="f02-pdf",
        origin=FileOrigin.COI,
        format="pdf",
        sha256=H1,
        size_bytes=1,
        declared_kind=DocumentKind.PDF,
        detected_kind=DocumentKind.PDF,
        status=FileStatus.RENDERED,
        page_count=1,
        page_source=PageSource.NATIVE_PDF,
        path="coi/2. x/x.pdf",
    )
    page = ManifestPage(
        label_id="f02-p01",
        base_file_id="f02-pdf",
        page=1,
        width_px=W,
        height_px=H,
        dpi=200.0,
        image_sha256=image_sha256(image),
        variants=(ManifestVariant(file_id="f02-pdf", page=1, included=True),),
    )
    return Manifest(
        schema_version=MANIFEST_SCHEMA,
        created=date(2026, 11, 22),
        split_registered=date(2026, 11, 21),
        split_rule="po dokumentach",
        renderer=RendererInfo(commit="abcdef1", dirty=False, dpi=200, versions={}),
        labeling=LabelingInfo(tool=ls.LS_NAME, version=ls.LS_VERSION, config_sha256=H1),
        documents=(
            ManifestDocument(
                doc_id="f02", folder="2", split=Split.DEV, files=(base,), pages=(page,)
            ),
        ),
    )


def _value(box: Box) -> dict[str, Any]:
    x0, y0, x1, y1 = box
    return {
        "x": x0 / W * 100,
        "y": y0 / H * 100,
        "width": (x1 - x0) / W * 100,
        "height": (y1 - y0) / H * 100,
        "rotation": 0,
    }


def _result(ls_id: str, control: str, box: Box, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ls_id,
        "type": "rectanglelabels" if control == ls.KIND_CONTROL else "choices",
        "from_name": control,
        "to_name": ls.IMAGE_TAG,
        "original_width": W,
        "original_height": H,
        "image_rotation": 0,
        "origin": "manual",
        "value": {**_value(box), **extra},
    }


def _region(ls_id: str, kind: str, box: Box, **attrs: Any) -> list[dict[str, Any]]:
    results = [_result(ls_id, ls.KIND_CONTROL, box, {"rectanglelabels": [kind]})]
    if "cells" in attrs:
        results.append(_result(ls_id, ls.CELLS_CONTROL, box, {"number": attrs["cells"]}))
    if "role" in attrs:
        results.append(_result(ls_id, ls.ROLE_CONTROL, box, {"choices": [attrs["role"]]}))
    if "multi" in attrs:
        results.append(_result(ls_id, ls.MULTI_CONTROL, box, {"choices": [attrs["multi"]]}))
    if attrs.get("uncertain"):
        results.append(_result(ls_id, ls.FLAGS_CONTROL, box, {"choices": [ls.UNCERTAIN_FLAG]}))
    return results


def _status(value: str) -> dict[str, Any]:
    return {
        "id": "status",
        "type": "choices",
        "from_name": ls.PAGE_STATUS_CONTROL,
        "to_name": ls.IMAGE_TAG,
        "origin": "manual",
        "value": {"choices": [value]},
    }


def _results() -> list[dict[str, Any]]:
    return [
        *_region("rCell", "table_cell", CELL),
        *_region("rBoxes", "char_boxes", BOXES, cells=3, uncertain=True),
        *_region("rChkB", "checkbox", CHECK_B),
        *_region("rChkA", "checkbox", CHECK_A),
        *_region("rGroup", "checkbox_group", GROUP, multi="single"),
        *_region("rCell2", "table_cell", CELL_2),
        *_region("rDots", "dotted_line", DOTS),
        *_region("rLabel", "table_cell", LABEL_CELL, role="skip"),
        _status("complete"),
    ]


def _task(results: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": 11,
        "data": {
            ls.IMAGE_TAG: ls.image_url("f02-p01"),
            "label_id": "f02-p01",
            "doc_id": "f02",
            "split": "dev",
            "page": 1,
            "order": 1,
        },
        "annotations": [
            {
                "id": 21,
                "completed_by": 1,
                "was_cancelled": False,
                "lead_time": 90.0,
                "prediction": {},
                "parent_prediction": None,
                "result": _results() if results is None else results,
            }
        ],
        "drafts": [],
        "predictions": [],
    }
    task.update(overrides)
    return task


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.image = _page_image()
        self.pages = tmp_path / "pages"
        self.pages.mkdir()
        PILImage.fromarray(self.image).save(self.pages / "f02-p01.png")
        self.manifest = tmp_path / "manifest.full.json"
        write_text(self.manifest, dump_json(_manifest(self.image)))
        self.out = tmp_path / "labels"
        self.export = tmp_path / "export.json"

    def run(self, tasks: list[dict[str, Any]], *, preview: bool = True) -> Any:
        self.export.write_text(json.dumps(tasks), encoding="utf-8")
        return convert(self.export, self.manifest, self.pages, self.out, preview=preview)

    def labels(self) -> PageLabels:
        return read_json(self.out / "f02-p01.json", PageLabels)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_converts_valid_page(env: Env) -> None:
    run = env.run([_task()])
    assert run.ok and run.written == ["f02-p01"]
    labels = env.labels()
    assert labels.status is LabelStatus.COMPLETE
    assert labels.annotator == "human-1"
    assert labels.source.file_id == "f02-pdf" and labels.source.sha256 == H1
    assert labels.source.image_sha256 == image_sha256(env.image)
    assert labels.tool.config_sha256 == ls.config_sha256()
    by_ls = {e.ls_region_id: e for e in labels.elements}
    # Kolejność czytania: od góry, potem od lewej.
    order = ["rCell", "rBoxes", "rGroup", "rChkA", "rChkB", "rCell2", "rDots", "rLabel"]
    assert [e.ls_region_id for e in labels.elements] == order
    assert [e.id for e in labels.elements] == [f"gt-p1-{i:03d}" for i in range(1, 9)]
    cell = by_ls["rCell"]
    assert (cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1) == (0.1, 20 / H, 0.9, 0.2)
    assert (cell.role, cell.role_origin) == (ElementRole.INPUT, RoleOrigin.DEFAULT)
    assert by_ls["rBoxes"].cells == 3 and by_ls["rBoxes"].uncertain
    group = by_ls["rGroup"]
    assert group.options == (by_ls["rChkA"].id, by_ls["rChkB"].id)
    assert group.multi is False and group.role is ElementRole.CHOICE
    assert (by_ls["rCell2"].role, by_ls["rCell2"].role_origin) == (
        ElementRole.SKIP,
        RoleOrigin.INFERRED,
    )
    assert (by_ls["rLabel"].role, by_ls["rLabel"].role_origin) == (
        ElementRole.SKIP,
        RoleOrigin.HUMAN,
    )
    result = run.results[0]
    assert any("rola skip (zawiera 007 dotted_line)" in note for note in result.notes)
    assert not result.warnings  # ramki ciasne
    with PILImage.open(env.out / "preview" / "f02-p01.png") as preview:
        assert preview.size == (W, H + LEGEND_HEIGHT)
    report = json.loads((env.out / "convert_report.json").read_text(encoding="utf-8"))
    page = report["pages"][0]
    assert page["state"] == "converted" and page["elements"] == 8
    assert page["role_origin"] == {"default": 6, "human": 1, "inferred": 1}
    assert page["s_per_element"] == round(90 / 8, 1)
    assert page["edge_offsets"]["brak"] == 0 and page["edge_offsets"][">2"] == 0
    assert "f02-p01 | converted | 8" in (env.out / "convert_report.md").read_text("utf-8")


def test_conversion_is_deterministic(env: Env) -> None:
    outputs = []
    for results in (_results(), _results(), list(reversed(_results()))):
        env.run([_task(results)])
        outputs.append(
            (
                (env.out / "f02-p01.json").read_bytes(),
                (env.out / "convert_report.json").read_bytes(),
            )
        )
    assert outputs[0] == outputs[1]  # ten sam eksport: identyczne etykiety i raport
    assert outputs[2][0] == outputs[0][0]  # kolejność wyników w eksporcie nie zmienia ID


def _replace(ls_id: str, kind: str, box: Box, **attrs: Any) -> Callable[[list[Any]], list[Any]]:
    def edit(results: list[Any]) -> list[Any]:
        kept = [r for r in results if r.get("id") != ls_id]
        return [*kept, *_region(ls_id, kind, box, **attrs)]

    return edit


def _edit_value(ls_id: str, **changes: Any) -> Callable[[list[Any]], list[Any]]:
    def edit(results: list[Any]) -> list[Any]:
        for result in results:
            if result.get("id") == ls_id and result["from_name"] == ls.KIND_CONTROL:
                result.update({k: v for k, v in changes.items() if k.startswith("original_")})
                result["value"].update({k: v for k, v in changes.items() if k in _value(CELL)})
                if "origin_" in changes:
                    result["origin"] = changes["origin_"]
        return results

    return edit


def _drop_status(results: list[Any]) -> list[Any]:
    return [r for r in results if r["from_name"] != ls.PAGE_STATUS_CONTROL]


def _empty_status(results: list[Any]) -> list[Any]:
    return [*_drop_status(results), _status("empty")]


def _stale_cells(results: list[Any]) -> list[Any]:
    return [*results, _result("rCell", ls.CELLS_CONTROL, CELL, {"number": 4})]


def _orphan_attribute(results: list[Any]) -> list[Any]:
    return [*results, _result("ghost", ls.ROLE_CONTROL, CELL, {"choices": ["skip"]})]


def _second_group(results: list[Any]) -> list[Any]:
    return [*results, *_region("rGroup2", "checkbox_group", (50, 110, 120, 140), multi="multi")]


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (_empty_status, "status strony empty, a strona ma 8 regionów"),
        (_drop_status, "status strony musi mieć jedną wartość"),
        (_replace("rBoxes", "char_boxes", BOXES, cells=30), "cells=30 nieprawdopodobne"),
        (_replace("rBoxes", "char_boxes", BOXES), "wymaga liczby kratek"),
        (_replace("rTiny", "table_cell", (100, 250, 103, 252)), "region za mały (3×2 px)"),
        (_replace("rGroup", "checkbox_group", GROUP), "wymaga wyboru multi"),
        (_replace("rGroup", "checkbox_group", (18, 116, 40, 136), multi="single"), "≥ 2"),
        (_replace("rDots", "dotted_line", DOTS, role="e_signature"), "niedozwolona"),
        (_replace("rDots", "dotted_line", DOTS, multi="single"), "multi ustawione"),
        (_replace("rDots", "stamp", DOTS), "nieznana kategoria 'stamp'"),
        (_second_group, "środek checkboxa leży w kilku grupach"),
        (_stale_cells, "cells ustawione dla table_cell"),
        (_orphan_attribute, "atrybuty bez ramki"),
        (_edit_value("rCell", rotation=5), "obrót jest niedozwolony"),
        (_edit_value("rCell", x=-2.0), "poza stronę o 4.0 px"),
        (_edit_value("rCell", original_width=W * 2), "rozmiar obrazu w LS"),
        (_edit_value("rCell", origin_="prediction"), "pre-anotacji"),
    ],
)
def test_page_errors_block_labels(
    env: Env, edit: Callable[[list[Any]], list[Any]], message: str
) -> None:
    run = env.run([_task(edit(copy.deepcopy(_results())))])
    result = run.results[0]
    assert not run.ok and result.state is PageState.ERROR
    assert any(message in error for error in result.errors), result.errors
    assert not (env.out / "f02-p01.json").exists()
    assert (env.out / "preview" / "f02-p01.png").exists()  # nakładka pokazuje błędy
    assert "**BŁĄD:**" in (env.out / "convert_report.md").read_text(encoding="utf-8")


def test_implausible_cells_only_warn_when_uncertain(env: Env) -> None:
    edit = _replace("rBoxes", "char_boxes", BOXES, cells=30, uncertain=True)
    run = env.run([_task(edit(_results()))])
    assert run.ok
    assert any("cells=30 nieprawdopodobne" in w for w in run.results[0].warnings)


def test_small_overhang_is_clipped_and_reported(env: Env) -> None:
    run = env.run([_task(_edit_value("rCell", width=(W + 0.4 - 20) / W * 100)(_results()))])
    assert run.ok
    assert {e.ls_region_id: e for e in env.labels().elements}["rCell"].bbox.x1 == 1.0
    assert any("przycięto x1 o 0.40 px" in note for note in run.results[0].notes)


def test_task_level_rules(env: Env) -> None:
    pending = _task()
    pending["annotations"] = []
    cancelled = _task()
    cancelled["annotations"][0]["was_cancelled"] = True
    assert [r.state for r in env.run([pending]).results] == [PageState.PENDING]
    assert env.run([cancelled]).results[0].state is PageState.PENDING
    assert not (env.out / "f02-p01.json").exists()
    assert not (env.out / "preview" / "f02-p01.png").exists()

    two = _task()
    two["annotations"].append({**two["annotations"][0], "id": 22})
    assert "2 anotacje" in env.run([two]).results[0].errors[0]

    predicted = _task(predictions=[{"result": []}])
    assert any("pre-anotacje" in e for e in env.run([predicted]).results[0].errors)
    derived = _task()
    derived["annotations"][0]["parent_prediction"] = 5
    assert any("pre-anotacji" in e for e in env.run([derived]).results[0].errors)

    draft = env.run([_task(drafts=[{"id": 1}])]).results[0]
    assert draft.state is PageState.CONVERTED and "szkic" in draft.warnings[0]

    duplicate = env.run([_task(), _task()])
    assert all("więcej niż raz" in r.errors[0] for r in duplicate.results)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("label_id", "f09-p01", "nie istnieje w manifeście"),
        ("split", "holdout", "data.split"),
        (ls.IMAGE_TAG, "/data/local-files/?d=pages/inna.png", "data.image"),
    ],
)
def test_task_data_must_match_manifest(env: Env, key: str, value: str, message: str) -> None:
    task = _task()
    task["data"][key] = value
    assert any(message in e for e in env.run([task]).results[0].errors)


def test_page_image_must_match_manifest(env: Env) -> None:
    changed = env.image.copy()
    changed[0, 0] = 0
    PILImage.fromarray(changed).save(env.pages / "f02-p01.png")
    assert any("różni się od manifestu" in e for e in env.run([_task()]).results[0].errors)
    (env.pages / "f02-p01.png").unlink()
    assert any("brak obrazu" in e for e in env.run([_task()]).results[0].errors)


def test_annotator_never_uses_email(env: Env) -> None:
    task = _task()
    task["annotations"][0]["completed_by"] = {"id": 7, "email": "osoba@example.com"}
    env.run([task])
    text = (env.out / "f02-p01.json").read_text(encoding="utf-8")
    assert env.labels().annotator == "human-7" and "example.com" not in text


def test_failed_page_keeps_previous_labels(env: Env) -> None:
    env.run([_task()])
    before = (env.out / "f02-p01.json").read_bytes()
    env.run([_task(_empty_status(_results()))])
    assert (env.out / "f02-p01.json").read_bytes() == before


def test_loose_frame_is_reported(env: Env) -> None:
    loose = (CELL[0] - 4, CELL[1], CELL[2], CELL[3])
    run = env.run([_task(_replace("rCell", "table_cell", loose)(_results()))])
    assert run.ok
    assert any("krawędź lewa 4 px od linii" in w for w in run.results[0].warnings)


def test_edge_offsets() -> None:
    ink = _page_image().min(axis=2) < 160
    params = ConvertParams()
    assert edge_offsets(ink, CELL, params) == (0, 0, 0, 0)
    assert edge_offsets(ink, (22, 20, 178, 60), params) == (1, 0, 1, 0)
    assert edge_offsets(ink, (100, 250, 150, 280), params) == (None, None, None, None)


def test_main_exit_codes(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    args = ["--manifest", str(env.manifest), "--pages", str(env.pages), "--out", str(env.out)]
    env.export.write_text(json.dumps([_task()]), encoding="utf-8")
    assert main([str(env.export), *args]) == 0
    env.export.write_text(json.dumps([_task(_drop_status(_results()))]), encoding="utf-8")
    assert main([str(env.export), *args, "--no-preview"]) == 1
    env.export.write_text("{nie json", encoding="utf-8")
    assert main([str(env.export), *args]) == 2
    env.export.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    assert main([str(env.export), *args]) == 2
    assert "BŁĄD" in capsys.readouterr().err


def test_draw_preview_marks_kinds_roles_and_errors() -> None:
    image = _page_image()
    colors = kind_colors()
    boxes = [
        PreviewBox(box=CELL, kind=ElementKind.TABLE_CELL, tag="001 CEL"),
        PreviewBox(box=LABEL_CELL, kind=ElementKind.TABLE_CELL, tag="002 CEL skip", dashed=True),
        PreviewBox(box=(100, 250, 103, 252), kind=None, tag="ERR", error=True),
    ]
    canvas = np.asarray(draw_preview(image, boxes, "nota"))
    assert canvas.shape == (H + LEGEND_HEIGHT, W, 3)
    assert tuple(canvas[40, CELL[0]]) == colors[ElementKind.TABLE_CELL]
    dashed = [tuple(canvas[y, LABEL_CELL[0]]) for y in range(LABEL_CELL[1] + 2, LABEL_CELL[3] - 2)]
    assert colors[ElementKind.TABLE_CELL] in dashed and len(set(dashed)) > 1
    assert tuple(canvas[251, 101]) == (255, 0, 0)
