"""Orkiestrator pipeline'u: kanały odczytu → etapy 05–09 → normalizacja → kontrola → raport.

Obsługa błędów etapów (jawnie, bez utraty informacji o niepewności):
- brak etapu wymaganego (ekstrakcja tekstu) → ``PipelineNotImplementedError`` (501),
- dokument bez treści → ``DocumentUnreadableError``,
- serwer modelu niedostępny → ``PipelineTemporarilyUnavailableError`` (ponowienie),
- kanał wizualny zawiódł → analiza trwa dalej na tekście, etap ``DEGRADED`` w raporcie,
  pewność pól ograniczona,
- inny błąd etapu → ``PipelineStageError`` z nazwą etapu (zadanie FAILED).
Problemy niekrytyczne (reguły COI, normalizacja, konflikty reguł dziedzinowych) trafiają
do raportu jakości i obniżają pewność pól, zamiast przerywać analizę.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from backend.shared.config import Settings
from backend.shared.schemas.quality import (
    AnalysisResult,
    Issue,
    QualityReport,
    RetrievalReport,
    Severity,
    StageReport,
    StageStatus,
)
from backend.shared.validators.form_rules import validate_form_rules
from backend.worker.document import IncomingDocument
from backend.worker.pipeline import llm
from backend.worker.pipeline.contracts import ExtractedDocument, VisualAnalysis, VisualAnalyzer
from backend.worker.pipeline.errors import (
    DocumentUnreadableError,
    PipelineNotImplementedError,
    PipelineStageError,
    PipelineTemporarilyUnavailableError,
)
from backend.worker.pipeline.normalize import normalize_form
from backend.worker.pipeline.scoring import assess_fields
from backend.worker.pipeline.stages import PipelineStages, default_stages
from backend.worker.pipeline.step05_doc_type import run_step05
from backend.worker.pipeline.step06_structure import run_step06
from backend.worker.pipeline.step07_fields import run_step07
from backend.worker.pipeline.step08_dependencies import run_step08
from backend.worker.pipeline.step09_rules_dicts import run_step09
from backend.worker.progress import ProgressReporter
from backend.worker.rag.embedder import document_signature
from backend.worker.rag.retrieval import TypeDecision, decide, llm_chooser

if TYPE_CHECKING:
    from backend.worker.clients.llm_client import LLMClient

logger = logging.getLogger(__name__)


# (nazwa techniczna, opis dla użytkownika) — kolejność = postęp widoczny w API.
STEPS: tuple[tuple[str, str], ...] = (
    ("extraction", "Odczyt treści i obrazu dokumentu"),
    ("document_type", "Rozpoznanie typu wniosku"),
    ("structure", "Struktura stron i sekcji"),
    ("fields", "Ekstrakcja pól formularza"),
    ("dependencies", "Zależności i przepływ stron"),
    ("rules", "Reguły dziedzinowe i słowniki"),
    ("control", "Kontrola jakości i raport"),
)
_STEP_NUMBER = {name: index + 1 for index, (name, _) in enumerate(STEPS)}
_STEP_LABEL = dict(STEPS)

_PASS_THROUGH = (
    PipelineNotImplementedError,
    DocumentUnreadableError,
    PipelineTemporarilyUnavailableError,
    PipelineStageError,
)


class _Run:
    def __init__(self, progress: ProgressReporter) -> None:
        self.progress = progress
        self.stages: list[StageReport] = []
        self.issues: list[Issue] = []

    @asynccontextmanager
    async def stage(self, name: str) -> AsyncIterator[None]:
        label = _STEP_LABEL[name]
        await self.progress.update(name, _STEP_NUMBER[name], len(STEPS), label)
        started = time.monotonic()
        try:
            yield
        except _PASS_THROUGH:
            raise
        except Exception as exc:
            logger.exception("Etap %s: nieoczekiwany błąd", name)
            raise PipelineStageError(label, "nieoczekiwany błąd wewnętrzny") from exc
        self.stages.append(
            StageReport(
                name=name, status=StageStatus.OK, duration_seconds=time.monotonic() - started
            )
        )

    def record(self, report: StageReport, issue: Issue | None = None) -> None:
        self.stages.append(report)
        if issue is not None:
            self.issues.append(issue)


async def _visual_channel(
    analyzer: VisualAnalyzer | None, document: IncomingDocument
) -> tuple[VisualAnalysis | None, StageReport, Issue | None]:
    if analyzer is None:
        return (
            None,
            StageReport(name="visual_analysis", status=StageStatus.SKIPPED,
                        detail="Kanał wizualny (OpenCV + VLM) nie jest jeszcze podłączony"),
            Issue(
                code="VISUAL_CHANNEL_UNAVAILABLE",
                severity=Severity.INFO,
                message="Brak analizy obrazu stron — pola odczytane jednym kanałem mają "
                "pewność najwyżej średnią",
            ),
        )  # fmt: skip
    started = time.monotonic()
    try:
        result = await analyzer.analyze(document)
    except PipelineTemporarilyUnavailableError, asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Kanał wizualny %s zawiódł — analiza kontynuowana na tekście", analyzer.name
        )
        return (
            None,
            StageReport(name="visual_analysis", status=StageStatus.DEGRADED,
                        duration_seconds=time.monotonic() - started,
                        detail=f"{analyzer.name}: błąd analizy obrazu"),
            Issue(
                code="VISUAL_CHANNEL_FAILED",
                severity=Severity.WARNING,
                message="Analiza obrazu stron nie powiodła się — wynik oparty wyłącznie na "
                "tekście, pewność pól ograniczona",
            ),
        )  # fmt: skip
    report = StageReport(
        name="visual_analysis",
        status=StageStatus.OK,
        duration_seconds=time.monotonic() - started,
        detail=analyzer.name,
    )
    return result, report, None


async def _extract(
    stages: PipelineStages, document: IncomingDocument, run: _Run
) -> tuple[ExtractedDocument, VisualAnalysis | None]:
    extractor = stages.text_extractor
    if extractor is None:
        raise PipelineNotImplementedError(
            "Brak skonfigurowanego etapu ekstrakcji tekstu — analiza dokumentu jest niemożliwa"
        )
    # Kanały tekstowy i wizualny działają równolegle; błąd tekstu przerywa wizualny.
    visual_task = asyncio.create_task(_visual_channel(stages.visual_analyzer, document))
    started = time.monotonic()
    try:
        extracted = await extractor.extract(document)
    except BaseException:
        visual_task.cancel()
        await asyncio.gather(visual_task, return_exceptions=True)
        raise
    run.stages.append(
        StageReport(
            name="text_extraction",
            status=StageStatus.OK,
            duration_seconds=time.monotonic() - started,
            detail=extractor.name,
        )
    )
    visual, visual_report, visual_issue = await visual_task
    run.record(visual_report, visual_issue)

    if not extracted.text.strip():
        raise DocumentUnreadableError(
            "Nie odczytano żadnego tekstu z dokumentu (pusty plik lub skan bez czytelnej treści)"
        )
    run.issues.extend(extracted.issues)
    if visual is not None:
        run.issues.extend(visual.issues)
    return extracted, visual


async def _recognize_type(
    stages: PipelineStages,
    settings: Settings,
    document: IncomingDocument,
    payload: dict[str, Any],
    client: LLMClient,
    run: _Run,
) -> TypeDecision | None:
    """RAG: hash → kNN w bazie wiedzy → wybór modelu z top-k albo nowy typ (``None`` = bez RAG)."""
    if stages.knowledge is None or not settings.rag_enabled:
        return None
    signature = document_signature(payload)
    try:
        retrieval = await stages.knowledge.retrieve(
            job_id=document.ref.job_id,
            sha256=document.ref.sha256,
            signature=signature,
            top_k=settings.rag_top_k,
        )
    except Exception:  # baza wiedzy to wzmocnienie, nie warunek analizy
        logger.warning("Baza wiedzy niedostępna — typ z dopasowania leksykalnego", exc_info=True)
        run.issues.append(
            Issue(
                code="KNOWLEDGE_BASE_UNAVAILABLE",
                severity=Severity.WARNING,
                message="Baza wiedzy niedostępna — typ dokumentu rozpoznano dopasowaniem słów "
                "kluczowych, bez wzorca podobnego formularza",
            )
        )
        return None
    return await llm.run_llm_stage(
        _STEP_LABEL["document_type"],
        decide,
        retrieval,
        signature=signature,
        accept=settings.rag_accept_similarity,
        reject=settings.rag_reject_similarity,
        chooser=llm_chooser(client),
    )


def _sync[T](function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    try:
        return function(*args, **kwargs)
    except ValueError as exc:
        raise PipelineStageError(function.__name__, str(exc)[:500]) from exc


async def run_pipeline(
    document: IncomingDocument,
    progress: ProgressReporter,
    settings: Settings,
    stages: PipelineStages | None = None,
) -> AnalysisResult:
    stages = stages or default_stages(settings)
    run = _Run(progress)

    async with run.stage("extraction"):
        extracted, visual = await _extract(stages, document, run)
    payload = extracted.as_step_payload()

    async with run.stage("document_type"):
        # Budowa sesji sprawdza w katalogu Eden AI, czy model ma otwarte wagi (wywołanie HTTP).
        # Sesja jest potrzebna już tu: w strefie niepewności typ wybiera model językowy.
        session = await asyncio.to_thread(stages.llm_session_factory)
        client = session.client
        decision = await _recognize_type(stages, settings, document, payload, client, run)
        doc_type = _sync(run_step05, payload, decision=decision)
    template = doc_type.get("selected_template")

    async with run.stage("structure"):
        structure = await llm.run_llm_stage(
            _STEP_LABEL["structure"], run_step06, payload, template=template, llm_client=client
        )
    async with run.stage("fields"):
        with_fields = await llm.run_llm_stage(
            _STEP_LABEL["fields"], run_step07, payload, structure,
            template=template, llm_client=client,
        )  # fmt: skip
    async with run.stage("dependencies"):
        with_dependencies = await llm.run_llm_stage(
            _STEP_LABEL["dependencies"], run_step08, with_fields,
            llm_client=client, document=payload,
        )  # fmt: skip
    async with run.stage("rules"):
        raw_form = _sync(run_step09, with_dependencies)

    async with run.stage("control"):
        normalized = normalize_form(raw_form)
        rule_issues = validate_form_rules(normalized.form)
        fields = assess_fields(
            normalized,
            extracted,
            visual,
            rule_issues,
            high=settings.confidence_high,
            low=settings.confidence_low,
            demo_mode=settings.llm_demo_mode,
            example=decision.example if decision else None,
        )
        form_issues = [i for i in [*normalized.issues, *rule_issues] if i.field_id is None]
        if settings.llm_demo_mode:
            form_issues.append(
                Issue(
                    code="DEMO_MODE",
                    severity=Severity.WARNING,
                    message="Wynik wygenerowany w trybie demonstracyjnym, bez modelu językowego "
                    "— nie nadaje się do importu",
                )
            )

    report = QualityReport(
        document_sha256=document.ref.sha256,
        document_type=str(doc_type.get("doc_type") or "") or None,
        retrieval=decision.report() if decision else RetrievalReport(method="lexical"),
        accuracy_mode=settings.accuracy_mode,
        fields=fields,
        issues=[*run.issues, *form_issues],
        models={
            "llm": llm.model_name(settings),
            **(
                {"llm_weights": session.model.hugging_face_id}
                if session.model and session.model.hugging_face_id
                else {}
            ),
            "text_extractor": stages.text_extractor.name if stages.text_extractor else "brak",
            "visual": stages.visual_analyzer.name if stages.visual_analyzer else "brak",
        },
        stages=run.stages,
        dictionary_proposals=normalized.proposals,
        demo_mode=settings.llm_demo_mode,
        usage=session.usage.as_dict() if session.usage else {},
    )
    return AnalysisResult(form=normalized.form, report=report)
