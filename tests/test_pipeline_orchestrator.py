"""Orkiestrator pipeline'u: przebieg etapów, raport jakości i obsługa błędów etapów."""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import SecretStr

from backend.shared.jobs import JobProgress
from backend.shared.schemas.coi_form import FieldType
from backend.shared.schemas.quality import ConfidenceLevel, Severity, StageStatus
from backend.shared.validators.form_rules import has_errors, validate_form_rules
from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.clients.model_api import ModelApiClient, ModelInfo, Usage
from backend.worker.pipeline import llm as llm_module
from backend.worker.pipeline import orchestrator
from backend.worker.pipeline.contracts import ExtractedDocument
from backend.worker.pipeline.errors import (
    DocumentUnreadableError,
    PipelineNotImplementedError,
    PipelineStageError,
    PipelineTemporarilyUnavailableError,
)
from backend.worker.pipeline.llm import LLMSession
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

# W compose worker łączy się z Eden AI przez bramkę, która dokłada klucz.
GATEWAY_URL = "http://llm-gateway:8080/v3"


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
    birth = next(a for a in report.fields if a.tech_name == "data_urodzenia")
    (source,) = birth.sources
    assert (source.page, source.text, source.element_id) == (1, "Data urodzenia", "txt-7")
    assert source.bbox is not None
    assert birth.signals["source_found"] == 1.0

    # Strony „Dane” i „Kontakt i zgody” ze standardu COI: bez źródła w dokumencie i bez
    # ostrzeżeń o brakującym źródle; nazwa urzędu odczytana z dokumentu.
    imie = next(a for a in report.fields if a.tech_name == "imie")
    assert imie.signals == {"rules": 1.0, "standard_template": 1.0}
    assert imie.sources == [] and imie.issues == []
    authority = form.tabs[0].questions[0].field.repeating_section_grid[0].field
    assert getattr(authority, "default_value", None) == "Urząd Miasta Kraków"

    # Bez kanału wizualnego żadne pole nie jest zielone, a raport mówi dlaczego.
    assert all(a.level is not ConfidenceLevel.HIGH for a in report.fields)
    assert "VISUAL_CHANNEL_UNAVAILABLE" in {i.code for i in report.issues}
    assert report.document_type == "wniosek_dowod_osobisty"
    assert report.document_sha256 == incoming(tmp_path).ref.sha256
    assert not report.demo_mode

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
    # Pola od modelu mają źródło w dokumencie, pola szablonu COI — znacznik szablonu.
    assert by_tech["data_urodzenia"].signals["source_element"] == 1.0
    assert "standard_template" in by_tech["numer_pesel"].signals
    assert all(a.level is not ConfidenceLevel.HIGH for a in result.report.fields)
    # Strona końcowa według standardu COI: sekcja podglądu z blokiem tekstu (bez opakowywania).
    finish = result.form.tabs[-1]
    assert finish.title == "Strona końcowa"
    block = finish.questions[0].field.repeating_section_grid[0].field
    assert block.type is FieldType.TEXT_BLOCK
    codes = {i.code for a in result.report.fields for i in a.issues}
    assert "WRAPPED_IN_SECTION" not in codes
    # Tekst standardowy nie pochodzi z dokumentu — bez ostrzeżenia o brakującym źródle.
    (assessment,) = [a for a in result.report.fields if a.field_id == block.id]
    assert "SOURCE_NOT_FOUND" not in {i.code for i in assessment.issues}


async def test_visual_channel_raises_confidence(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, stages(visual=FakeVisualAnalyzer("overlap")))
    report = result.report
    assert {s.name: s.status for s in report.stages}["visual_analysis"] is StageStatus.OK
    assert "VISUAL_CHANNEL_UNAVAILABLE" not in {i.code for i in report.issues}
    by_tech = {a.tech_name: a for a in report.fields}
    assert by_tech["data_urodzenia"].signals["visual_match"] == 1.0
    assert "visual_match" not in by_tech["nazwisko"].signals  # pole szablonu nie jest na obrazie
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
    without_extractor = PipelineStages(None, None, default_stages(settings()).llm_session_factory)
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


async def test_llm_service_down_is_transient(tmp_path: Path) -> None:
    def unreachable() -> LLMSession:  # zamknięty port — odmowa połączenia
        api = ModelApiClient(base_url="http://127.0.0.1:9/v3", model="x/y", timeout_seconds=2)
        return LLMSession(LLMClient(LLMConfig(offline=False), transport=api.llm_transport))

    with pytest.raises(PipelineTemporarilyUnavailableError, match="Struktura"):
        await _run(tmp_path, stages(llm_factory=unreachable))


async def test_llm_invalid_output_is_stage_error(tmp_path: Path) -> None:
    def garbage() -> LLMSession:
        client = LLMClient(LLMConfig(offline=False), transport=lambda *_: "to nie jest JSON")
        return LLMSession(client)

    with pytest.raises(PipelineStageError, match="Struktura stron i sekcji"):
        await _run(tmp_path, stages(llm_factory=garbage))


def _catalog(entries: dict[str, Any]) -> Any:
    def fake(_url: str, _key: str | None, _timeout: float) -> dict[str, Any]:
        return entries

    return fake


@pytest.mark.parametrize(
    ("model", "entries", "message"),
    [
        ("openai/gpt-4o", {"openai/gpt-4o": {"open_weights": None}}, "otwartych wag"),
        ("deepinfra/x", {}, "nie występuje w katalogu"),
        ("qwen3-32b", {}, "dostawca/model"),
    ],
)
async def test_closed_or_unknown_model_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    entries: dict[str, Any],
    message: str,
) -> None:
    from backend.worker.clients import model_api

    monkeypatch.setattr(model_api, "_model_catalog", _catalog(entries))
    monkeypatch.setattr(
        llm_module,
        "ensure_open_weights",
        lambda url, model_id, **kw: model_api.ensure_open_weights(
            url, model_id, catalog=_catalog(entries), **kw
        ),
    )
    real_factory = default_stages(
        settings(llm_model=model, edenai_base_url=GATEWAY_URL)
    ).llm_session_factory
    with pytest.raises(PipelineStageError, match=message):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_model_catalog_unreachable_is_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.clients.model_api import ModelServiceUnavailableError

    def down(*_: object, **__: object) -> Any:
        raise ModelServiceUnavailableError("brak połączenia")

    monkeypatch.setattr(llm_module, "ensure_open_weights", down)
    real_factory = default_stages(settings(edenai_base_url=GATEWAY_URL)).llm_session_factory
    with pytest.raises(PipelineTemporarilyUnavailableError, match="katalog modeli"):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_usage_and_model_weights_in_report(tmp_path: Path) -> None:
    usage = Usage()
    usage.add(1200, 300, ModelInfo("deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
                                   "Qwen/Qwen3-VL-30B-A3B-Instruct", 1.5e-7, 6e-7))  # fmt: skip

    def session() -> LLMSession:
        return LLMSession(
            LLMClient(LLMConfig(offline=True)),
            usage=usage,
            model=ModelInfo("m/x", "Qwen/Qwen3-VL-30B-A3B-Instruct", 0.0, 0.0),
        )

    result, _ = await _run(tmp_path, stages(llm_factory=session))
    report = result.report
    assert report.usage == {
        "llm_calls": 1,
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "cost_usd": 0.00036,
    }
    assert report.models["llm_weights"] == "Qwen/Qwen3-VL-30B-A3B-Instruct"


async def test_edenai_api_key_required_for_direct_access(tmp_path: Path) -> None:
    # Bezpośrednio do api.edenai.run (bez bramki) klucz jest wymagany.
    real_factory = default_stages(settings(edenai_api_key=None)).llm_session_factory
    with pytest.raises(PipelineStageError, match="EDENAI_API_KEY"):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_placeholder_api_key_is_refused(tmp_path: Path) -> None:
    real_factory = default_stages(
        settings(edenai_api_key=SecretStr("change-me-in-runtime"))
    ).llm_session_factory
    with pytest.raises(PipelineStageError, match="wartość przykładową"):
        await _run(tmp_path, stages(llm_factory=real_factory))


async def test_gateway_needs_no_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W compose klucz dokłada bramka — worker łączy się z nią bez klucza."""
    seen: dict[str, Any] = {}

    def fake_catalog(url: str, model: str, *, api_key: str | None = None) -> ModelInfo:
        seen.update(url=url, model=model, api_key=api_key)
        return ModelInfo(model, "Qwen/Qwen3-VL-30B-A3B-Instruct", 0.0, 0.0)

    monkeypatch.setattr(llm_module, "ensure_open_weights", fake_catalog)
    session = default_stages(
        settings(edenai_base_url=GATEWAY_URL, edenai_api_key=None)
    ).llm_session_factory()
    assert seen == {
        "url": GATEWAY_URL,
        "model": "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
        "api_key": None,
    }
    assert session.model is not None


async def test_unexpected_step_error_names_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> dict[str, Any]:
        raise KeyError("brak klucza")

    monkeypatch.setattr(orchestrator, "run_step05", broken)
    with pytest.raises(PipelineStageError, match="Rozpoznanie typu wniosku") as exc:
        await _run(tmp_path, stages())
    assert "brak klucza" not in str(exc.value)  # szczegóły wewnętrzne tylko w logach
