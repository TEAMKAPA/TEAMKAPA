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
    worker_startup_timeout_s: PositiveSeconds = 60.0
    render_forms: bool = True
    split_seam_margin_px: int = Field(default=20, ge=0, le=500)
    split_height_tolerance_px: int = Field(default=2, ge=0, le=50)
    split_ink_threshold: int = Field(default=200, ge=1, le=255)
    tall_image_ratio: float = Field(default=1.6, gt=1.0, le=100.0)
    a4_ratio_tolerance: float = Field(default=0.01, ge=0.0, le=0.2)


class RenderConfig(_RenderLimits):
    """Parametry jednego renderowania. Wartości pikselowe cięcia obrazów są w pikselach obrazu."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dpi: int = Field(default=200, ge=72, le=600)


class VisionSettings(_RenderLimits, BaseSettings):
    """Zmienne środowiskowe ``VISION_*`` (np. ``VISION_MAX_PAGES``, ``VISION_PAGE_TIMEOUT_S``)."""

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


# --- Detektory OpenCV (Etap C) ---

Mm = Annotated[float, Field(gt=0, le=1000)]
Ratio = Annotated[float, Field(gt=0, le=100)]


class DetectConfig(BaseModel):
    """Progi detektorów OpenCV (``backend/worker/vision/detect``).

    Wszystkie wielkości geometryczne są w milimetrach strony (``*_mm``) albo są proporcjami; na
    piksele przelicza je ``detect.scale.Scale`` według efektywnego DPI strony. Wartości domyślne
    są uzasadnione fizycznie (typowe wymiary pól formularzy A4), a nie dopasowane do zbioru #12.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Binaryzacja (C.2)
    blur_sigma_mm: Mm = 0.1  # odszumianie przed progowaniem (~0,8 px przy 200 DPI)
    otsu_min: int = Field(default=120, ge=1, le=254)  # granice progu globalnego (Otsu)
    otsu_max: int = Field(default=215, ge=1, le=254)
    adaptive_block_mm: Mm = 7.0  # okno średniej lokalnej (cienie i nierówne tło skanów)
    adaptive_offset: int = Field(default=10, ge=0, le=100)
    low_contrast: int = Field(
        default=110, ge=1, le=255
    )  # różnica papier–tusz poniżej: LOW_CONTRAST

    # Prostowanie (C.2)
    deskew_max_deg: float = Field(default=5.0, ge=0, le=15)
    deskew_coarse_step_deg: float = Field(default=0.25, gt=0, le=2)
    deskew_fine_step_deg: float = Field(default=0.02, gt=0, le=0.5)
    deskew_min_deg: float = Field(default=0.1, ge=0, le=5)  # mniejszy kąt: bez obrotu
    deskew_dpi: float = Field(default=75.0, ge=30, le=300)  # rozdzielczość pomocnicza estymacji

    # Linie (C.3)
    line_min_mm: Mm = 8.0  # jądro morfologii: najkrótszy odcinek linii
    line_bridge_mm: Mm = 0.8  # domykanie przerw w liniach (skany, kreski przerywane)
    text_line_min_mm: Mm = 15.0
    text_line_max_thickness_mm: Mm = (
        0.45  # grubsze poziome paski to separatory, nie linie do pisania
    )

    # Prostokąty (C.4)
    rect_min_mm: Mm = 1.8  # najmniejszy bok wnętrza prostokąta (mniejsze: pętle liter)
    rect_fill: Ratio = 0.85  # wypełnienie ramki wnętrza przez obszar tła (prostokątność)
    border_max_mm: Mm = 1.2  # najgrubsza krawędź ramki (z cieniem znaku ❑)
    border_coverage: Ratio = 0.9  # część długości boku pokryta tuszem
    checkbox_min_mm: Mm = 2.0
    checkbox_max_mm: Mm = 6.5
    square_aspect: Ratio = 1.35  # dopuszczalna proporcja boków „kwadratu”
    char_cell_max_mm: Mm = 10.0
    char_cell_aspect: Ratio = 1.6
    char_gap_mm: Mm = 0.6  # przerwa między wnętrzami sąsiednich kratek ponad grubość linii
    char_height_tolerance: Ratio = 0.15
    cell_min_w_mm: Mm = 5.0
    cell_min_h_mm: Mm = 3.0
    section_min_children: int = Field(default=2, ge=1, le=100)

    # Kropki (C.5)
    dot_max_mm: Mm = 0.8
    dot_row_tolerance_mm: Mm = 0.4
    dot_max_gap_mm: Mm = 2.5
    dot_min_count: int = Field(default=8, ge=3, le=1000)
    dot_min_span_mm: Mm = 10.0
    dot_spacing_cv: Ratio = 0.5  # zmienność odstępów (odchylenie / średnia)

    # Grupy wyboru (C.6)
    group_row_tolerance_mm: Mm = 1.0
    group_row_max_gap_mm: Mm = 70.0
    group_column_max_gap_mm: Mm = 12.0
    group_text_gap_mm: Mm = 3.0  # przerwa kończąca tekst opcji na prawo od checkboxa
    group_text_max_mm: Mm = 60.0

    # Identyfikatory (C.7)
    id_row_mm: Mm = 2.0  # zaokrąglenie y przy sortowaniu (wiersz), potem x

    default_dpi: float = Field(default=200.0, ge=50, le=1200)  # gdy DPI strony nieznane


class DetectSettings(DetectConfig, BaseSettings):
    """Zmienne środowiskowe ``VISION_DETECT_*`` (np. ``VISION_DETECT_LINE_MIN_MM``)."""

    model_config = SettingsConfigDict(
        env_prefix="VISION_DETECT_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


def load_detect_config(settings: DetectSettings | None = None) -> DetectConfig:
    """``DetectConfig`` z konfiguracji runtime (domyślnie z bieżącego środowiska)."""
    own = settings if settings is not None else DetectSettings()
    return DetectConfig(**own.model_dump())
