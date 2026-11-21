"""Konfiguracja renderowania: wartości domyślne, zmienne VISION_* i DPI z RENDER_DPI."""

import pytest
from pydantic import ValidationError

from backend.worker.vision.config import RenderConfig, VisionSettings, load_render_config


def test_defaults_match_accepted_plan() -> None:
    cfg = load_render_config()
    assert cfg.dpi == 200
    assert (cfg.max_pages, cfg.max_page_pixels, cfg.max_image_pixels) == (
        50,
        40_000_000,
        100_000_000,
    )
    assert (cfg.page_timeout_s, cfg.office_timeout_s) == (30.0, 120.0)
    assert cfg.render_forms is True
    assert cfg.office_enabled is False  # LibreOffice domyślnie wyłączony


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_DPI", "300")
    monkeypatch.setenv("VISION_MAX_PAGES", "7")
    monkeypatch.setenv("VISION_OFFICE_ENABLED", "true")
    cfg = load_render_config()
    assert (cfg.dpi, cfg.max_pages, cfg.office_enabled) == (300, 7, True)


def test_invalid_environment_value_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_MAX_PAGES", "0")
    with pytest.raises(ValidationError):
        VisionSettings()


def test_render_config_is_frozen_and_strict() -> None:
    cfg = RenderConfig()
    with pytest.raises(ValidationError):
        cfg.dpi = 300  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RenderConfig(unknown_option=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RenderConfig(dpi=1000)
