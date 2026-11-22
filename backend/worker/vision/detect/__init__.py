"""Detektory elementów formularza w OpenCV (#10, Etap C).

**Zasada:** detektory pracują wyłącznie na obrazie strony (``RenderedPage.image``). Nigdy nie
korzystają z warstwy wektorowej ani tekstowej PDF, żeby ten sam kod działał dla PDF, obrazów
cyfrowych i skanów, a ocena na etykietach (także przyszłych pre-etykietach z warstwy wektorowej)
nie była cykliczna (``docs/vision.md``).

Wszystkie progi są w ``DetectConfig`` (``backend/worker/vision/config.py``) w milimetrach strony
i są przeliczane na piksele według efektywnego DPI (``scale.Scale``).

**Determinizm:** ten sam obraz i konfiguracja dają te same elementy, kolejność i ID. Detekcja nie
używa losowości, a Intel IPP jest na czas detekcji wyłączany (``ipp_disabled``), bo IPP wybiera
ścieżkę obliczeń zależnie od procesora. Przełącznik IPP w OpenCV jest globalny dla procesu: stan
sprzed wywołania jest przywracany.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import cv2

from backend.worker.vision.config import DetectConfig
from backend.worker.vision.detect.page import PageDetection, detect_image
from backend.worker.vision.detect.scale import effective_dpi
from backend.worker.vision.models import RenderedDocument, RenderedPage

__all__ = ["PageDetection", "detect_document", "detect_page", "ipp_disabled"]


@contextmanager
def ipp_disabled() -> Iterator[None]:
    """Wyłącza Intel IPP w OpenCV na czas bloku i przywraca poprzedni stan."""
    previous = bool(cv2.ipp.useIPP())
    cv2.ipp.setUseIPP(False)
    try:
        yield
    finally:
        cv2.ipp.setUseIPP(previous)


def detect_page(page: RenderedPage, cfg: DetectConfig | None = None) -> PageDetection:
    """Elementy wizualne strony (kontrakt 7.5, ``source=opencv``, rola ``unknown``)."""
    cfg = cfg or DetectConfig()
    dpi = effective_dpi(page.meta, cfg.default_dpi)
    with ipp_disabled():
        return detect_image(page.image, page.meta.page, dpi, cfg)


def detect_document(
    document: RenderedDocument, cfg: DetectConfig | None = None
) -> tuple[PageDetection, ...]:
    return tuple(detect_page(page, cfg) for page in document.pages)
