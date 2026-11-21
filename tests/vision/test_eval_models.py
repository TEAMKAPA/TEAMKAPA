"""Modele elementów wizualnych (kontrakt 7.5) oraz etykiet, podziału i manifestu zbioru #12."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.shared.schemas.quality import BoundingBox
from backend.worker.vision.models import (
    DocumentKind,
    ElementKind,
    ElementRole,
    ElementSource,
    PageSource,
    VisualElement,
)
from eval.visual.models import (
    DEFAULT_ROLES,
    FileOrigin,
    FileStatus,
    GeometryCheck,
    LabelElement,
    LabelState,
    LabelStatus,
    Manifest,
    ManifestDocument,
    ManifestFile,
    ManifestPage,
    ManifestVariant,
    PageLabels,
    RoleOrigin,
    Split,
    SplitDefinition,
    coi_doc_id,
    dump_json,
    dump_public_manifest,
    label_id_for,
    read_json,
    write_text,
)

H1, H2, H3, H4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
BOX = BoundingBox(x0=0.1, y0=0.2, x1=0.3, y1=0.25)


# --- VisualElement ---


def _element(**overrides: Any) -> VisualElement:
    data: dict[str, Any] = {
        "id": "cv-p1-001",
        "page": 1,
        "kind": ElementKind.CHAR_BOXES,
        "bbox": BOX,
        "cells": 11,
        "source": ElementSource.OPENCV,
        "confidence": 0.9,
    }
    data.update(overrides)
    return VisualElement.model_validate(data)


def test_visual_element_valid() -> None:
    element = _element(confidence_reasons=("NO_ROLE",))
    assert element.role is ElementRole.UNKNOWN
    assert _element(id="vlm-p2-003", page=2, source=ElementSource.VLM).source is ElementSource.VLM
    group = _element(
        id="cv-p1-004",
        kind=ElementKind.CHECKBOX_GROUP,
        cells=None,
        options=("cv-p1-002", "cv-p1-003"),
        multi=False,
    )
    assert group.options == ("cv-p1-002", "cv-p1-003")


@pytest.mark.parametrize(
    "overrides",
    [
        {"page": 2},  # numer strony w id inny niż page
        {"id": "vlm-p1-001"},  # vlm-* przy source=opencv
        {"source": ElementSource.VLM},  # cv-* przy source=vlm
        {"id": "cv-1-001"},
        {"cells": None},  # char_boxes bez liczby kratek
        {"kind": ElementKind.CHECKBOX},  # cells przy checkboxie
        {"kind": ElementKind.CHECKBOX, "cells": None, "multi": True},  # multi poza grupą
        {"bbox": BoundingBox(x0=0.1, y0=0.2, x1=0.1, y1=0.3)},  # zerowa szerokość
        {"confidence": 1.5},
        {"confidence_reasons": ("no role",)},
        {"unknown_field": 1},
    ],
)
def test_visual_element_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _element(**overrides)


@pytest.mark.parametrize(
    "options",
    [("cv-p1-002", "cv-p1-002"), ("cv-p1-004",), ("cv-p2-002",)],
)
def test_visual_element_group_options_rejected(options: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        _element(id="cv-p1-004", kind=ElementKind.CHECKBOX_GROUP, cells=None, options=options)


# --- Podział ---


def _split(**overrides: Any) -> SplitDefinition:
    data: dict[str, Any] = {
        "schema_version": "teamkapa.vision.split/1",
        "registered": date(2026, 11, 21),
        "rule": "po dokumentach",
        "dev": ("f02", "f05"),
        "holdout": ("f01", "f03"),
        "pilot": ("f02-p01",),
    }
    data.update(overrides)
    return SplitDefinition.model_validate(data)


def test_split_definition() -> None:
    split = _split()
    assert split.split_of("f05") is Split.DEV
    assert split.split_of("f01") is Split.HOLDOUT
    with pytest.raises(ValueError, match="f04"):
        split.split_of("f04")


@pytest.mark.parametrize(
    "overrides",
    [
        {"holdout": ("f02",)},  # dokument w obu częściach
        {"dev": ("f02", "f02")},
        {"holdout": ()},
        {"pilot": ("f01-p01",)},  # pilot w holdout
        {"dev": ("2",)},
    ],
)
def test_split_definition_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _split(**overrides)


# --- Etykiety ---


def _label_element(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "gt-p1-001",
        "kind": ElementKind.DOTTED_LINE,
        "bbox": BOX,
        "role": ElementRole.INPUT,
        "role_origin": RoleOrigin.DEFAULT,
    }
    data.update(overrides)
    return data


def _page_labels(elements: list[dict[str, Any]], **overrides: Any) -> PageLabels:
    data: dict[str, Any] = {
        "schema_version": "teamkapa.vision.labels/1",
        "label_id": "f02-p01",
        "doc_id": "f02",
        "source": {
            "file_id": "f02-pdf",
            "sha256": H1,
            "page": 1,
            "width_px": 1654,
            "height_px": 2339,
            "dpi": 200.0,
            "image_sha256": H2,
        },
        "status": LabelStatus.COMPLETE if elements else LabelStatus.EMPTY,
        "annotator": "human-1",
        "tool": {"name": "label-studio", "version": "1.23.2", "config_sha256": H3},
        "elements": elements,
    }
    data.update(overrides)
    return PageLabels.model_validate(data)


def _group_page() -> list[dict[str, Any]]:
    return [
        _label_element(id="gt-p1-001", kind=ElementKind.CHECKBOX, role=ElementRole.CHOICE),
        _label_element(id="gt-p1-002", kind=ElementKind.CHECKBOX, role=ElementRole.CHOICE),
        _label_element(
            id="gt-p1-003",
            kind=ElementKind.CHECKBOX_GROUP,
            role=ElementRole.CHOICE,
            options=["gt-p1-001", "gt-p1-002"],
            multi=False,
        ),
        _label_element(id="gt-p1-004", kind=ElementKind.CHAR_BOXES, cells=11, uncertain=True),
    ]


def test_page_labels_valid_and_roundtrip(tmp_path: Path) -> None:
    labels = _page_labels(_group_page())
    path = tmp_path / "labels" / "f02-p01.json"
    write_text(path, dump_json(labels))
    assert read_json(path, PageLabels) == labels
    assert dump_json(read_json(path, PageLabels)) == dump_json(labels)
    assert _page_labels([]).status is LabelStatus.EMPTY


def test_default_roles_cover_all_kinds_without_unknown() -> None:
    assert set(DEFAULT_ROLES) == set(ElementKind)
    assert ElementRole.UNKNOWN not in DEFAULT_ROLES.values()


@pytest.mark.parametrize(
    "element",
    [
        _label_element(role=ElementRole.UNKNOWN),
        _label_element(kind=ElementKind.CHAR_BOXES),  # brak cells
        _label_element(cells=3),  # cells poza char_boxes
        _label_element(kind=ElementKind.CHECKBOX_GROUP, options=["gt-p1-002"]),  # 1 członek
        _label_element(kind=ElementKind.CHECKBOX_GROUP, options=["gt-p1-002", "gt-p2-003"]),
        _label_element(bbox=BoundingBox(x0=0.1, y0=0.2, x1=0.3, y1=0.2)),
        _label_element(id="cv-p1-001"),
    ],
)
def test_label_element_rejects(element: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LabelElement.model_validate(element)


def test_page_labels_rejects_inconsistent_files() -> None:
    elements = _group_page()
    with pytest.raises(ValidationError, match="empty"):
        _page_labels(elements, status=LabelStatus.EMPTY)
    with pytest.raises(ValidationError, match="empty"):
        _page_labels([], status=LabelStatus.COMPLETE)
    with pytest.raises(ValidationError, match="powtórzone"):
        _page_labels([*elements, elements[0]])
    with pytest.raises(ValidationError, match=r"source\.page"):
        _page_labels(elements, label_id="f02-p02")
    with pytest.raises(ValidationError, match="doc_id"):
        _page_labels(elements, doc_id="f03")
    with pytest.raises(ValidationError, match=r"source\.page"):
        _page_labels([_label_element(id="gt-p2-001")])


def test_page_labels_rejects_bad_groups() -> None:
    not_checkbox = _group_page()
    not_checkbox[1] = _label_element(id="gt-p1-002", kind=ElementKind.DOTTED_LINE)
    with pytest.raises(ValidationError, match="checkboxy"):
        _page_labels(not_checkbox)
    missing = _group_page()[1:]
    with pytest.raises(ValidationError, match="checkboxy"):
        _page_labels(missing)
    two_groups = [
        *_group_page(),
        _label_element(
            id="gt-p1-005",
            kind=ElementKind.CHECKBOX_GROUP,
            role=ElementRole.CHOICE,
            options=["gt-p1-001", "gt-p1-002"],
        ),
    ]
    with pytest.raises(ValidationError, match="więcej niż jednej grupy"):
        _page_labels(two_groups)


def test_label_id_helpers() -> None:
    assert coi_doc_id("1") == "f01"
    assert coi_doc_id("12") == "f12"
    assert label_id_for("f05-png", 1) == "f05-png-p01"
    assert label_id_for("f09", 16) == "f09-p16"


# --- Manifest ---


def _file(file_id: str, sha: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "file_id": file_id,
        "origin": FileOrigin.COI,
        "format": file_id.split("-")[1],
        "sha256": sha,
        "size_bytes": 1000,
        "declared_kind": DocumentKind.PDF,
        "detected_kind": DocumentKind.PDF,
        "status": FileStatus.RENDERED,
        "page_count": 2,
        "page_source": PageSource.NATIVE_PDF,
        "path": f"coi/x/{file_id}.bin",
    }
    data.update(overrides)
    return data


CHECK_OK = GeometryCheck(
    size_equal=True,
    shift_px=(0, 1),
    variant_ink_near_base=0.99,
    base_ink_near_variant=0.98,
    passed=True,
)


def _page(label_id: str, page: int, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label_id": label_id,
        "base_file_id": "f02-pdf",
        "page": page,
        "width_px": 1654,
        "height_px": 2339,
        "dpi": 200.0,
        "image_sha256": H4,
        "variants": [
            {"file_id": "f02-pdf", "page": page, "included": True},
            {"file_id": "f02-png", "page": page, "check": CHECK_OK, "included": True},
        ],
    }
    data.update(overrides)
    return data


def _document(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "doc_id": "f02",
        "folder": "2",
        "split": Split.DEV,
        "files": [
            _file("f02-pdf", H1),
            _file(
                "f02-png",
                H2,
                declared_kind=DocumentKind.PNG,
                detected_kind=DocumentKind.PNG,
                page_source=PageSource.IMAGE,
                issues=("IMAGE_SPLIT_A4",),
            ),
            _file(
                "f02-docx",
                H3,
                declared_kind=DocumentKind.DOCX,
                detected_kind=DocumentKind.DOCX,
                status=FileStatus.EXCLUDED,
                code="OFFICE_FORMAT_NOT_RENDERED",
                page_count=None,
                page_source=None,
            ),
        ],
        "pages": [_page("f02-p01", 1), _page("f02-p02", 2)],
    }
    data.update(overrides)
    return data


def _manifest(documents: list[dict[str, Any]]) -> Manifest:
    return Manifest.model_validate(
        {
            "schema_version": "teamkapa.vision.manifest/1",
            "created": date(2026, 11, 21),
            "split_registered": date(2026, 11, 21),
            "split_rule": "po dokumentach",
            "renderer": {"commit": "f38458a", "dirty": False, "dpi": 200, "versions": {"a": "1"}},
            "labeling": {"tool": "label-studio", "version": "1.23.2", "config_sha256": H3},
            "documents": documents,
        }
    )


def test_manifest_valid_public_and_roundtrip(tmp_path: Path) -> None:
    manifest = _manifest([_document()])
    assert not manifest.is_public
    public = manifest.public()
    assert public.is_public
    text = dump_public_manifest(manifest)
    assert '"path"' not in text
    path = tmp_path / "manifest.json"
    write_text(path, text)
    loaded = read_json(path, Manifest)
    assert loaded == public
    assert dump_public_manifest(loaded) == text
    document, page = loaded.find_page("f02-p02")
    assert (document.doc_id, page.page) == ("f02", 2)
    with pytest.raises(KeyError):
        loaded.find_page("f02-p03")
    assert not document.reference_json


def test_manifest_page_label_state() -> None:
    labelled = _page(
        "f02-p01",
        1,
        label_state=LabelState.COMPLETE,
        labels_sha256=H1,
        element_counts={ElementKind.DOTTED_LINE: 3},
    )
    assert ManifestPage.model_validate(labelled).label_state is LabelState.COMPLETE
    for bad in (
        {"label_state": LabelState.COMPLETE},  # brak hasha etykiet
        {"labels_sha256": H1},  # hash przy pending
        {"label_state": LabelState.EMPTY, "labels_sha256": H1, "element_counts": {"checkbox": 1}},
        {"label_state": LabelState.COMPLETE, "labels_sha256": H1},  # complete bez elementów
        {"page": 2},  # numer strony w label_id inny niż page
    ):
        with pytest.raises(ValidationError):
            ManifestPage.model_validate({**_page("f02-p01", 1), **bad})


@pytest.mark.parametrize(
    "variants",
    [
        [{"file_id": "f02-png", "page": 1, "check": CHECK_OK, "included": True}],  # brak bazy
        [
            {"file_id": "f02-pdf", "page": 1, "included": True},
            {"file_id": "f02-png", "page": 1, "included": True},  # wariant bez kontroli
        ],
        [
            {"file_id": "f02-pdf", "page": 1, "included": True},
            {"file_id": "f02-pdf", "page": 1, "included": True},
        ],
    ],
)
def test_manifest_page_rejects_bad_variants(variants: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        ManifestPage.model_validate(_page("f02-p01", 1, variants=variants))


def test_manifest_variant_rules() -> None:
    failed = CHECK_OK.model_copy(update={"passed": False})
    with pytest.raises(ValidationError):
        ManifestVariant(file_id="f02-png", page=1, check=failed, included=True)
    with pytest.raises(ValidationError):
        ManifestVariant(file_id="f02-png", page=1, check=failed, included=False)
    excluded = ManifestVariant(
        file_id="f02-png", page=1, check=failed, included=False, reason="GEOMETRY_MISMATCH"
    )
    assert not excluded.included


def test_manifest_file_rules() -> None:
    with pytest.raises(ValidationError):
        ManifestFile.model_validate(_file("f02-pdf", H1, page_count=None))
    with pytest.raises(ValidationError):
        ManifestFile.model_validate(_file("f02-pdf", H1, code="X"))
    with pytest.raises(ValidationError):
        ManifestFile.model_validate(_file("f02-pdf", H1, status=FileStatus.EXCLUDED))
    for path in ("/abs/x.pdf", "coi/../x.pdf", "coi\\x.pdf", ""):
        with pytest.raises(ValidationError):
            ManifestFile.model_validate(_file("f02-pdf", H1, path=path))
    reference = ManifestFile.model_validate(
        _file(
            "f02-json",
            H4,
            declared_kind=None,
            detected_kind=None,
            status=FileStatus.REFERENCE,
            code="REFERENCE_JSON",
            page_count=None,
            page_source=None,
        )
    )
    assert reference.status is FileStatus.REFERENCE


def test_manifest_document_rules() -> None:
    with pytest.raises(ValidationError, match="folderu"):
        ManifestDocument.model_validate(_document(folder="3"))
    with pytest.raises(ValidationError, match="nieistniejącą"):
        ManifestDocument.model_validate(_document(pages=[_page("f02-p03", 3)]))
    other_doc_variant = _page("f02-p01", 1)
    other_doc_variant["variants"][1]["file_id"] = "f03-png"
    with pytest.raises(ValidationError, match="nieistniejącą"):
        ManifestDocument.model_validate(_document(pages=[other_doc_variant]))
    shared = _page("f02-png-p01", 1, base_file_id="f02-png")
    shared["variants"] = [{"file_id": "f02-png", "page": 1, "included": True}]
    with pytest.raises(ValidationError, match="więcej niż jednego"):
        ManifestDocument.model_validate(_document(pages=[_page("f02-p01", 1), shared]))
    with pytest.raises(ValidationError, match="powtórzone label_id"):
        ManifestDocument.model_validate(_document(pages=[_page("f02-p01", 1), _page("f02-p01", 1)]))


def test_manifest_rejects_shared_files_and_duplicate_documents() -> None:
    other = _document(doc_id="f03", folder="3", pages=[])
    other["files"] = [_file("f03-pdf", H1)]  # ten sam plik co f02-pdf: wyciek między dokumentami
    with pytest.raises(ValidationError, match="więcej niż raz"):
        _manifest([_document(), other])
    with pytest.raises(ValidationError, match="doc_id"):
        _manifest([_document(), _document()])
