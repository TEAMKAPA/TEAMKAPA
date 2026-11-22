"""Rejestr etapów pipeline'u — tu podłącza się implementacje z kolejnych zadań.

- ``text_extractor`` (krok 02): zadanie #7 — ``Step02TextExtractor`` (adapter kroku 02).
- ``visual_analyzer`` (krok 03): zadania #10/#11. Do czasu scalenia — brak; analiza działa
  na samym tekście, a raport oznacza kanał wizualny jako pominięty i obniża pewność.
- ``llm_session_factory``: model językowy dla etapów 05–08 (#8) — Eden AI albo tryb demo.
- ``knowledge``: baza wiedzy (RAG, pgvector) — rozpoznanie typu i wzorzec formularza.
  Brak (testy, baza niedostępna) → typ z dopasowania leksykalnego do katalogu.
"""

from collections.abc import Callable
from dataclasses import dataclass

from backend.shared.config import Settings
from backend.worker.pipeline.contracts import TextExtractor, VisualAnalyzer
from backend.worker.pipeline.llm import LLMSession, build_llm_session
from backend.worker.pipeline.text_extraction import Step02TextExtractor
from backend.worker.rag.knowledge import KnowledgeBase


@dataclass(frozen=True, slots=True)
class PipelineStages:
    text_extractor: TextExtractor | None
    visual_analyzer: VisualAnalyzer | None
    llm_session_factory: Callable[[], LLMSession]
    knowledge: KnowledgeBase | None = None


def default_stages(settings: Settings, knowledge: KnowledgeBase | None = None) -> PipelineStages:
    return PipelineStages(
        text_extractor=Step02TextExtractor(settings),
        visual_analyzer=None,  # TODO(#10/#11): podłączyć OpenCV + VLM
        llm_session_factory=lambda: build_llm_session(settings),
        knowledge=knowledge,
    )
