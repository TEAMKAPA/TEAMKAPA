"""Rejestr etapów pipeline'u — tu podłącza się implementacje z kolejnych zadań.

- ``text_extractor`` (krok 02): zadanie #7 — ``Step02TextExtractor`` (adapter kroku 02).
- ``visual_analyzer`` (krok 03): zadania #10/#11. Do czasu scalenia — brak; analiza działa
  na samym tekście, a raport oznacza kanał wizualny jako pominięty i obniża pewność.
- ``llm_client_factory``: klient LLM dla etapów 06–08 (#8).
"""

from collections.abc import Callable
from dataclasses import dataclass

from backend.shared.config import Settings
from backend.worker.clients.llm_client import LLMClient
from backend.worker.pipeline.contracts import TextExtractor, VisualAnalyzer
from backend.worker.pipeline.llm import build_llm_client
from backend.worker.pipeline.text_extraction import Step02TextExtractor


@dataclass(frozen=True, slots=True)
class PipelineStages:
    text_extractor: TextExtractor | None
    visual_analyzer: VisualAnalyzer | None
    llm_client_factory: Callable[[], LLMClient]


def default_stages(settings: Settings) -> PipelineStages:
    return PipelineStages(
        text_extractor=Step02TextExtractor(settings),
        visual_analyzer=None,  # TODO(#10/#11): podłączyć OpenCV + VLM
        llm_client_factory=lambda: build_llm_client(settings),
    )
