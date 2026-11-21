"""Konfiguracja renderowania stron.

``RenderConfig`` to czysty, niezmienny obiekt przekazywany do renderera (łatwy w testach).
``load_render_config`` składa go z konfiguracji runtime: DPI z ``Settings.render_dpi``
(``backend/shared/config.py``, zmienna ``RENDER_DPI``) i parametrów ``VISION_*``.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.shared.config import Settings, get_settings

PositiveSeconds = Annotated[float, Field(gt=0, le=3600)]


class _RenderLimits(BaseModel):
    """Wspólne pola limitów (domyślne wartości zaakceptowane w planie Etapu A)."""

    max_pages: int = Field(default=50, ge=1, le=1000)
    max_page_pixels: int = Field(default=40_000_000, ge=1_000_000, le=500_000_000)
    max_image_pixels: int = Field(default=100_000_000, ge=1_000_000, le=1_000_000_000)
    page_timeout_s: PositiveSeconds = 30.0
    render_forms: bool = True
    office_enabled: bool = False
    office_timeout_s: PositiveSeconds = 120.0
    soffice_path: str = "soffice"
    split_seam_margin_px: int = Field(default=20, ge=0, le=500)
    split_height_tolerance_px: int = Field(default=2, ge=0, le=50)


class RenderConfig(_RenderLimits):
    """Parametry jednego renderowania. Wartości pikselowe cięcia obrazów są w pikselach obrazu."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dpi: int = Field(default=200, ge=72, le=600)


class VisionSettings(_RenderLimits, BaseSettings):
    """Zmienne środowiskowe ``VISION_*`` (np. ``VISION_MAX_PAGES``, ``VISION_OFFICE_ENABLED``)."""

    model_config = SettingsConfigDict(
        env_prefix="VISION_", env_file=None, case_sensitive=False, extra="ignore", frozen=True
    )


def load_render_config(
    settings: Settings | None = None, vision: VisionSettings | None = None
) -> RenderConfig:
    """Buduje ``RenderConfig`` z konfiguracji runtime (domyślnie z bieżącego środowiska)."""
    shared = settings if settings is not None else get_settings()
    own = vision if vision is not None else VisionSettings()
    return RenderConfig(dpi=shared.render_dpi, **own.model_dump())
