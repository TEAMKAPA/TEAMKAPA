"""Krok 4 pipeline'u: fuzja elementów OpenCV i VLM (Etap E, #9), cienkie wejście.

Deleguje do ``backend.worker.vision.fusion.elements`` (reguły w docstringu modułu i w
``docs/vision.md``). Podpięcie w ``stages`` i orkiestratorze należy do #16; proponowany podział
kroku 3 na fazy 3a/3b opisuje ``docs/vision.md`` (sekcja „Propozycja dla #8, #16 i #17”).
"""

from collections.abc import Sequence

from backend.worker.vision.fusion.config import FusionConfig
from backend.worker.vision.fusion.elements import PageFusion, PageFusionInput, fuse_document
from backend.worker.vision.vlm.roles import TextBlock

__all__ = ["PageFusion", "PageFusionInput", "fuse"]


def fuse(
    pages: Sequence[PageFusionInput], blocks: Sequence[TextBlock], cfg: FusionConfig | None = None
) -> tuple[PageFusion, ...]:
    """Fuzja elementów wszystkich stron dokumentu (kolejność stron rosnąca)."""
    return fuse_document(pages, blocks, cfg)
