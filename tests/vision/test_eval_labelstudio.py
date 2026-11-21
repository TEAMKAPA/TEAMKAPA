"""Konfiguracja Label Studio zgodna z modelami etykiet (eval/visual/labelstudio, ls.py)."""

import re
import xml.etree.ElementTree as ET

from backend.worker.vision.models import ElementKind, ElementRole
from eval.visual import ls
from eval.visual.models import LabelStatus


def _config() -> ET.Element:
    # Plik konfiguracji z repozytorium (zaufany), nie dane wejściowe.
    return ET.parse(ls.CONFIG_PATH).getroot()  # noqa: S314


def _one(root: ET.Element, tag: str, name: str) -> ET.Element:
    found = [e for e in root.iter(tag) if e.get("name") == name]
    assert len(found) == 1, (tag, name)
    return found[0]


def _values(element: ET.Element, child: str) -> list[str]:
    return [c.get("value", "") for c in element.iter(child)]


def _parent_view(root: ET.Element, element: ET.Element) -> ET.Element:
    parents = {child: parent for parent in root.iter() for child in parent}
    return parents[element]


def test_image_and_rectangle_labels() -> None:
    root = _config()
    image = _one(root, "Image", ls.IMAGE_TAG)
    assert image.get("value") == "$image"
    assert image.get("rotateControl") == "false"
    kinds = _one(root, "RectangleLabels", ls.KIND_CONTROL)
    assert kinds.get("toName") == ls.IMAGE_TAG
    assert kinds.get("canRotate") == "false"
    assert kinds.get("snap") == "pixel"
    assert _values(kinds, "Label") == [k.value for k in ElementKind]
    hotkeys = [label.get("hotkey") for label in kinds.iter("Label")]
    assert hotkeys == [str(i) for i in range(1, len(ElementKind) + 1)]


def test_region_attributes() -> None:
    root = _config()
    cells = _one(root, "Number", ls.CELLS_CONTROL)
    assert cells.get("perRegion") == "true"
    assert _parent_view(root, cells).get("whenLabelValue") == ElementKind.CHAR_BOXES.value

    multi = _one(root, "Choices", ls.MULTI_CONTROL)
    assert multi.get("perRegion") == "true"
    assert tuple(_values(multi, "Choice")) == ls.MULTI_VALUES
    assert _parent_view(root, multi).get("whenLabelValue") == ElementKind.CHECKBOX_GROUP.value

    role = _one(root, "Choices", ls.ROLE_CONTROL)
    assert role.get("perRegion") == "true" and role.get("choice") == "single"
    assert _values(role, "Choice") == [r.value for r in ElementRole if r is not ElementRole.UNKNOWN]

    flags = _one(root, "Choices", ls.FLAGS_CONTROL)
    assert flags.get("perRegion") == "true" and flags.get("choice") == "multiple"
    assert _values(flags, "Choice") == [ls.UNCERTAIN_FLAG]

    for control in (cells, multi, role, flags):
        assert control.get("toName") == ls.IMAGE_TAG


def test_page_status_is_required_task_choice() -> None:
    status = _one(_config(), "Choices", ls.PAGE_STATUS_CONTROL)
    assert status.get("required") == "true"
    assert status.get("perRegion") is None
    assert _values(status, "Choice") == [s.value for s in LabelStatus]


def test_server_env_disables_telemetry_and_outbound_traffic() -> None:
    env: dict[str, str] = {}
    for line in ls.ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            assert key not in env, key
            env[key] = value
    assert env["LABEL_STUDIO_SENTRY_DSN"] == ""
    assert env["LABEL_STUDIO_FRONTEND_SENTRY_DSN"] == ""
    assert env["LABEL_STUDIO_COLLECT_ANALYTICS"] == "false"
    assert env["LABEL_STUDIO_LATEST_VERSION_CHECK"] == "false"
    assert env["LABEL_STUDIO_FEATURE_FLAGS_OFFLINE"] == "true"
    assert env["LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED"] == "true"
    assert env["LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT"] == "/label-studio/files"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[key].startswith("http://127.0.0.1:")
    for key in ("NO_PROXY", "no_proxy"):
        assert set(env[key].split(",")) == {"localhost", "127.0.0.1"}
    assert not any(re.search("PASSWORD|TOKEN|SECRET", key) for key in env)


def test_pinned_image_and_task_urls() -> None:
    assert re.fullmatch(
        rf"heartexlabs/label-studio:{re.escape(ls.LS_VERSION)}@sha256:[0-9a-f]{{64}}", ls.LS_IMAGE
    )
    assert ls.image_url("f02-p01") == "/data/local-files/?d=pages/f02-p01.png"
    assert ls.page_file_name("f02-p01") == "f02-p01.png"
    assert re.fullmatch(r"[0-9a-f]{64}", ls.config_sha256())
