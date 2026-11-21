"""Orkiestrator pipeline'u: przebieg etapów, raport jakości i obsługa błędów etapów."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from backend.shared.jobs import JobProgress
from backend.shared.schemas.quality import ConfidenceLevel, Severity, StageStatus
from backend.shared.validators.form_rules import has_errors, validate_form_rules
from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.pipeline import orchestrator
from backend.worker.pipeline.contracts import ExtractedDocument
from backend.worker.pipeline.errors import (
    DocumentUnreadableError,
    PipelineNotImplementedError,
    PipelineStageError,
    PipelineTemporarilyUnavailableError,
)
from backend.worker.pipeline.orchestrator import STEPS, run_pipeline
from backend.worker.pipeline.stages import PipelineStages, default_stages
from backend.worker.progress import ProgressReporter
from tests.coi_helpers import official_schema
from tests.pipeline_helpers import (
    FakeTextExtractor,
    FakeVisualAnalyzer,
    RecordingStore,
    incoming,
    settings,
    stages,
)

pytestmark = pytest.mark.anyio


async def _run(tmp_path: Path, pipeline_stages: PipelineStages, **overrides: Any) -> Any:
    store = RecordingStore()
    progress = ProgressReporter(store, "b" * 32, 60)
    result = await run_pipeline(
        incoming(tmp_path), progress, settings(**overrides), pipeline_stages
    )
    return result, [JobProgress.model_validate_json(v) for v in store.values]


# ------------------------------------------------------------------ reprezentatywny formularz


async def test_representative_form_end_to_end(tmp_path: Path) -> None:
    result, progress = await _run(tmp_path, stages())
    form, report = result.form, result.report

    # Wynik zgodny ze schematem COI i regułami technicznymi.
    coi_json = form.to_coi_json()
    jsonschema.Draft202012Validator(official_schema()).validate(coi_json)
    assert "null" not in json.dumps(coi_json)
    assert not has_errors(validate_form_rules(form))
    assert form.tabs[-1].type == "FINISH"

    # Każde pole ma ocenę pewności i źródło w dokumencie (strona + bbox z ekstrakcji).
    leaves = [leaf for *_, leaf in form.iter_fields() if leaf is not None]
    assert {a.field_id for a in report.fields} == {leaf.id for leaf in leaves}
    pesel = next(a for a in report.fields if a.tech_name == "pesel")
    (source,) = pesel.sources
    assert (source.page, source.text, source.element_id) == (1, "Numer PESEL", "txt-6")
    assert source.bbox is not None
    assert pesel.signals["source_found"] == 1.0

    # Bez kanału wizualnego żadne pole nie jest zielone, a raport mówi dlaczego.
    assert all(a.level is not ConfidenceLevel.HIGH for a in report.fields)
    assert "VISUAL_CHANNEL_UNAVAILABLE" in {i.code for i in report.issues}
    assert report.document_type == "wniosek_dowod_osobisty"
    assert report.document_sha256 == incoming(tmp_path).ref.sha256
    assert not report.demo_mode
    assert len(report.dictionary_proposals) >= 1

    stage_status = {s.name: s.status for s in report.stages}
    assert stage_status["text_extraction"] is StageStatus.OK
    assert stage_status["visual_analysis"] is StageStatus.SKIPPED
    assert all(stage_status[name] is StageStatus.OK for name, _ in STEPS)

    assert [(p.stage, p.step) for p in progress] == [
        (name, i + 1) for i, (name, _) in enumerate(STEPS)
    ]
    assert {p.total_steps for p in progress} == {len(STEPS)}


async def test_normalization_and_uncertainty_are_reported(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, stages())
    by_tech = {a.tech_name: a for a in result.report.fields}
    # Krok 07 oznacza „Twoje dane” jako dane inicjalne bez wskazania źródła — ostrzeżenie.
    imie = by_tech["imie"]
    assert "INITIAL_DATA_SOURCE_MISSING" in {i.code for i in imie.issues}
    assert imie.signals["rules"] == 0.6
    assert imie.level is not ConfidenceLevel.HIGH
    # INFO_SECTION ze strony FINISH opakowana w sekcję — odnotowane w raporcie.
    wrapped = [i for a in result.report.fields for i in a.issues if i.code == "WRAPPED_IN_SECTION"]
    assert wrapped


async def test_visual_channel_raises_confidence(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, stages(visual=FakeVisualAnalyzer("overlap")))
    report = result.report
    assert {s.name: s.status for s in report.stages}["visual_analysis"] is StageStatus.OK
    assert "VISUAL_CHANNEL_UNAVAILABLE" not in {i.code for i in report.issues}
    by_tech = {a.tech_name: a for a in report.fields}
    assert by_tech["nazwisko"].signals["visual_match"] == 1.0
    assert any(a.level is ConfidenceLevel.HIGH for a in report.fields)
    assert report.models["visual"] == "test-wizja"


async def test_demo_mode_is_marked_and_all_low(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, stages(), llm_demo_mode=True)
    report = result.report
    assert report.demo_mode
    assert report.models["llm"].startswith("tryb-demo")
    assert "DEMO_MODE" in {i.code for i in report.issues}
    assert {a.level for a in report.fields} == {ConfidenceLevel.LOW}


# ------------------------------------------------------------------ błędy etapów


async def test_missing_text_extractor_is_not_implemented(tmp_path: Path) -> None:
    without_extractor = PipelineStages(None, None, default_stages(settings()).llm_client_factory)
    with pytest.raises(PipelineNotImplementedError, match="ekstrakcji tekstu"):
        await _run(tmp_path, without_extractor)


def test_default_stages_use_step02_extractor() -> None:
    from backend.worker.pipeline.text_extraction import Step02TextExtractor

    assert isinstance(default_stages(settings()).text_extractor, Step02TextExtractor)


async def test_empty_document_is_unreadable(tmp_path: Path) -> None:
    empty = FakeTextExtractor(ExtractedDocument(page_count=1, elements=[]))
    with pytest.raises(DocumentUnreadableError):
        await _run(tmp_path, stages(extractor=empty))


async def test_visual_failure_degrades_but_continues(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, stages(visual=FakeVisualAnalyzer("fail")))
    report = result.report
    assert {s.name: s.status for s in report.stages}["visual_analysis"] is StageStatus.DEGRADED
    issue = next(i for i in report.issues if i.code == "VISUAL_CHANNEL_FAILED")
    assert issue.severity is Severity.WARNING
    assert all(a.level is not ConfidenceLevel.HIGH for a in report.fields)


async def test_visual_transient_error_triggers_retry(tmp_path: Path) -> None:
    with pytest.raises(PipelineTemporarilyUnavailableError):
        await _run(tmp_path, stages(visual=FakeVisualAnalyzer("transient")))


async def test_text_failure_cancels_visual_channel(tmp_path: Path) -> None:
    visual = FakeVisualAnalyzer("overlap", delay=30)
    extractor = FakeTextExtractor(error=DocumentUnreadableError("plik chroniony hasłem"))
    with pytest.raises(DocumentUnreadableError):
        await _run(tmp_path, stages(extractor=extractor, visual=visual))
    assert visual.cancelled


async def test_llm_server_down_is_transient(tmp_path: Path) -> None:
    def unreachable() -> LLMClient:  # zamknięty port — odmowa połączenia
        return LLMClient(
            LLMConfig(
                offline=False,
                api_key="test-key",
                base_url="http://127.0.0.1:9",
                timeout_seconds=2,
            )
        )

    with pytest.raises(PipelineTemporarilyUnavailableError, match="Struktura"):
        await _run(tmp_path, stages(llm_factory=unreachable))


async def test_llm_invalid_output_is_stage_error(tmp_path: Path) -> None:
    def garbage() -> LLMClient:
        return LLMClient(LLMConfig(offline=False), transport=lambda *_: "to nie jest JSON")

    with pytest.raises(PipelineStageError, match="Struktura stron i sekcji"):
        await _run(tmp_path, stages(llm_factory=garbage))


async def test_llm_model_not_configured(tmp_path: Path) -> None:
    real_factory = default_stages(settings(llm_model=None)).llm_client_factory
    with pytest.raises(PipelineStageError, match="LLM_MODEL"):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_edenai_api_key_not_configured(tmp_path: Path) -> None:
    real_factory = default_stages(
        settings(llm_model="openai/gpt-4o-mini", edenai_api_key=None)
    ).llm_client_factory
    with pytest.raises(PipelineStageError, match="EDENAI_API_KEY"):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_unexpected_step_error_names_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> dict[str, Any]:
        raise KeyError("brak klucza")

    monkeypatch.setattr(orchestrator, "run_step05", broken)
    with pytest.raises(PipelineStageError, match="Rozpoznanie typu wniosku") as exc:
        await _run(tmp_path, stages())
    assert "brak klucza" not in str(exc.value)  # szczegóły wewnętrzne tylko w logach
