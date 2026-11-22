"""Konfiguracja fuzji i kontroli (Etap E).

Wartości domyślne wynikają z zasad, a nie z dopasowania do zbioru #12 (na ``dev`` są oznaczone
tylko 2 strony). Uzasadnienie każdej wartości: ``docs/vision.md``, sekcja „Fuzja, kontrola
i pewność (Etap E)”.

Progi poziomów pewności (``high``, ``low``) pochodzą z ``CONFIDENCE_HIGH`` / ``CONFIDENCE_LOW``
(``backend/shared/config.py``), żeby kanał wizualny i ``pipeline.scoring`` używały tych samych.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.shared.config import Settings, get_settings

Mm = Annotated[float, Field(gt=0, le=1000)]
Unit = Annotated[float, Field(ge=0, le=1)]
Score = Annotated[float, Field(ge=0, le=100)]


class _FusionParams(BaseModel):
    # Kontrola elementów dodanych przez VLM (E.3): ślad w pikselach z progami złagodzonymi
    # o połowę względem detektorów (DetectConfig), w milimetrach strony.
    evidence_margin_mm: Mm = 1.5  # margines wokół ramki vlm-* (niedokładność ramki VLM)
    evidence_dot_min_count: int = Field(default=4, ge=2, le=100)  # detektor: 8
    evidence_dot_min_span_mm: Mm = 5.0  # detektor: 10 mm
    evidence_line_share: Unit = 0.5  # odcinek poziomy ≥ połowy szerokości ramki
    evidence_line_min_mm: Mm = 4.0  # detektor: 8 mm (jądro morfologii)
    evidence_stroke_share: Unit = 0.5  # kreska pionowa ≥ połowy wysokości ramki (kratki, checkbox)
    evidence_text_cover: Unit = 0.5  # część szerokości ramki zajęta przez bloki tekstu wiersza
    evidence_glyph_min_mm: Mm = 1.2  # składowa tuszu wysokości litery (1,2–6 mm, szer. ≤ 6 mm)
    evidence_glyph_max_mm: Mm = 6.0
    evidence_glyph_count: int = Field(default=3, ge=1, le=100)  # tyle „liter” = wiersz tekstu
    evidence_ink_blank: Unit = 0.002  # udział tuszu poniżej: obszar pusty

    # Źródła i łączenie pól (E.5)
    source_match: Score = 90.0  # rapidfuzz partial_ratio: ok. 1 błąd OCR na 10 znaków
    source_exact_max_len: int = Field(default=4, ge=0, le=20)  # krótsze etykiety: tylko dokładnie
    link_max_gap_mm: Mm = 30.0  # element na prawo lub poniżej etykiety, gdy brak label_block_id
    link_row_tolerance_mm: Mm = 2.0

    # OCR (E.6): poniżej ocr_low → LOW (jak LOW_OCR_CONFIDENCE w D), poniżej ocr_high → MEDIUM
    ocr_low: Unit = 0.6
    ocr_high: Unit = 0.9

    # Etykiety z kilku odczytów (E.4): próg zgodności jak w porównaniu pól Etapu D
    label_agree: Score = 80.0

    # Iteracja naprawcza (E.7), brief 7.6: najwyżej 2
    repair_max_iterations: int = Field(default=2, ge=0, le=2)


class FusionConfig(_FusionParams):
    """Parametry jednego przebiegu fuzji i kontroli."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    high: Unit = 0.85
    low: Unit = 0.5

    @model_validator(mode="after")
    def _ordered(self) -> FusionConfig:
        if self.low > self.high:  # te same warunki co Settings (CONFIDENCE_LOW ≤ CONFIDENCE_HIGH)
            raise ValueError("wymagane low ≤ high")
        if self.ocr_low > self.ocr_high:
            raise ValueError("ocr_low nie może być większy niż ocr_high")
        if self.evidence_glyph_min_mm >= self.evidence_glyph_max_mm:
            raise ValueError("evidence_glyph_min_mm musi być mniejszy niż evidence_glyph_max_mm")
        return self


class FusionSettings(_FusionParams, BaseSettings):
    """Zmienne środowiskowe ``VISION_FUSION_*`` (np. ``VISION_FUSION_SOURCE_MATCH``)."""

    model_config = SettingsConfigDict(
        env_prefix="VISION_FUSION_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


def load_fusion_config(
    settings: Settings | None = None, fusion: FusionSettings | None = None
) -> FusionConfig:
    """``FusionConfig`` z konfiguracji runtime (progi poziomów ze wspólnego ``Settings``)."""
    shared = settings if settings is not None else get_settings()
    own = fusion if fusion is not None else FusionSettings()
    return FusionConfig(high=shared.confidence_high, low=shared.confidence_low, **own.model_dump())
