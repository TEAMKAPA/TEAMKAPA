"""Konfiguracja detektorów i skala mm → px (#10, C.1)."""

import pytest
from pydantic import ValidationError

from backend.worker.vision.config import DetectConfig, DetectSettings, load_detect_config
from backend.worker.vision.detect.scale import Scale, a4_dpi, effective_dpi
from backend.worker.vision.models import DpiEstimateSource, DpiSource, PageMeta, PageSource


def _meta(width: int, height: int, **kw: object) -> PageMeta:
    data: dict[str, object] = {
        "page": 1,
        "width_px": width,
        "height_px": height,
        "dpi": None,
        "dpi_source": DpiSource.UNKNOWN,
        "source": PageSource.IMAGE,
    }
    data.update(kw)
    return PageMeta.model_validate(data)


def test_defaults_are_consistent() -> None:
    cfg = DetectConfig()
    assert cfg.otsu_min < cfg.otsu_max
    assert cfg.checkbox_min_mm < cfg.checkbox_max_mm <= cfg.char_cell_max_mm
    assert cfg.rect_min_mm <= cfg.checkbox_min_mm
    with pytest.raises(ValidationError):
        DetectConfig(unknown=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        cfg.line_min_mm = 1.0  # type: ignore[misc]


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_DETECT_LINE_MIN_MM", "12.5")
    cfg = load_detect_config()
    assert cfg.line_min_mm == 12.5
    assert type(cfg) is DetectConfig
    assert load_detect_config(DetectSettings(dot_min_count=5)).dot_min_count == 5


def test_scale() -> None:
    scale = Scale(dpi=200.0)
    assert scale.px(25.4) == 200.0
    assert scale.ipx(5.0) == 39 and scale.ipx(0.01) == 1
    assert scale.odd(7.0) == 55 and scale.odd(7.0) % 2 == 1 and scale.odd(0.1) == 3
    assert scale.mm(200.0) == pytest.approx(25.4)


def test_effective_dpi_order() -> None:
    assert effective_dpi(_meta(1000, 1000, dpi=150.0, dpi_source=DpiSource.RENDER), 200) == 150
    estimated = _meta(
        1000, 1000, dpi_estimate=123.0, dpi_estimate_source=DpiEstimateSource.A4_WIDTH
    )
    assert effective_dpi(estimated, 200) == 123
    assert effective_dpi(_meta(1654, 2339), 300) == pytest.approx(200.05, abs=0.1)
    assert effective_dpi(_meta(1000, 1000), 300) == 300


def test_a4_dpi() -> None:
    assert a4_dpi(2339, 1654) == pytest.approx(200.05, abs=0.1)  # poziomo
    assert a4_dpi(1000, 1600) is None
