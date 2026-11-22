"""Krok 3 pipeline'u: analiza wizualna stron (#10), cienkie wejście.

Renderuje dokument (``vision.render``) i wykrywa elementy formularza na obrazach stron
(``vision.detect``, OpenCV). Wynik to elementy w kontrakcie 7.5 (``vision.models.VisualElement``,
tymczasowy, ``TODO(#17)``).

Podpięcie w pipeline należy do #16: rejestracja w ``stages.default_stages`` i odwzorowanie na
``contracts.VisualAnalysis`` (inny, uproszczony model elementu). Błędy renderowania (np.
``OFFICE_FORMAT_NOT_RENDERED`` dla formatów biurowych) są zgłaszane jako ``RenderError`` z kodem.
"""

from dataclasses import dataclass

from backend.worker.vision.config import DetectConfig, RenderConfig, load_render_config
from backend.worker.vision.detect import PageDetection, detect_document
from backend.worker.vision.models import DocumentKind, DocumentMeta, VisualElement
from backend.worker.vision.render import render_document


@dataclass(frozen=True)
class VisualStepResult:
    document: DocumentMeta
    pages: tuple[PageDetection, ...]

    @property
    def elements(self) -> tuple[VisualElement, ...]:
        return tuple(e for page in self.pages for e in page.elements)


def analyze_visual(
    data: bytes,
    declared_kind: DocumentKind | None,
    *,
    render_cfg: RenderConfig | None = None,
    detect_cfg: DetectConfig | None = None,
    isolated: bool = True,
) -> VisualStepResult:
    """Render i detekcja OpenCV dla wszystkich stron dokumentu."""
    rendered = render_document(
        data, declared_kind, render_cfg or load_render_config(), isolated=isolated
    )
    return VisualStepResult(document=rendered.meta, pages=detect_document(rendered, detect_cfg))
