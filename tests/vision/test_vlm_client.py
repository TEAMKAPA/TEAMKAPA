"""Konfiguracja i klient VLM (#11, D.2): bez sieci (httpx.MockTransport) i prawdziwego tokenu."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from backend.worker.vision.vlm.client import (
    VLM_INVALID_RESPONSE,
    VLM_UNAVAILABLE,
    FakeVlmClient,
    OpenAICompatibleClient,
    VlmImage,
    VlmInvalidResponse,
    VlmRequest,
    VlmUnavailable,
    build_client,
    build_payload,
)
from backend.worker.vision.vlm.config import (
    EDENAI_MODELS,
    AccuracyMode,
    ResponseFormat,
    VlmBackend,
    VlmSettings,
)

FAKE_KEY = "test-key-not-a-real-token-0123456789"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def settings(tmp_path: Path, **overrides: Any) -> VlmSettings:
    """Ustawienia z pustego pliku .env w katalogu testu (nigdy z prawdziwego .env)."""
    env_file = tmp_path / "test.env"
    env_file.touch()
    data: dict[str, Any] = {"edenai_api_key": FAKE_KEY, "retry_backoff_s": 0.0}
    data.update(overrides)
    return VlmSettings(_env_file=env_file, **data)  # type: ignore[call-arg]


def request() -> VlmRequest:
    return VlmRequest(
        task="roles",
        prompt="instrukcje",
        prompt_version="roles_v1:abc",
        image=VlmImage(b"\x89PNG-test"),
        schema_name="roles",
        schema=SCHEMA,
    )


def ok_body(content: str = '{"ok": true}') -> dict[str, Any]:
    return {
        "model": "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 30, "total_tokens": 1230},
    }


def test_defaults_and_allowed_models(tmp_path: Path) -> None:
    cfg = settings(tmp_path)
    assert cfg.backend is VlmBackend.EDENAI
    assert cfg.model == "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct" and cfg.model in EDENAI_MODELS
    assert cfg.temperature == 0.0 and cfg.accuracy_mode is AccuracyMode.MAX
    assert cfg.api_key_length() == len(FAKE_KEY)
    assert FAKE_KEY not in repr(cfg) and FAKE_KEY not in str(cfg.model_dump())
    with pytest.raises(ValidationError, match="spoza listy"):
        settings(tmp_path, edenai_model="openai/gpt-4o-mini")
    with pytest.raises(ValidationError, match="https"):
        settings(tmp_path, edenai_base_url="http://api.edenai.run/v3")
    with pytest.raises(ValidationError, match="lokalny"):
        settings(tmp_path, backend=VlmBackend.LOCAL, local_model="inny/model")
    local = settings(tmp_path, backend=VlmBackend.LOCAL)
    assert local.model == "Qwen/Qwen3-VL-30B-A3B-Instruct" and local.base_url.startswith("http://")


def test_env_variable_wins_over_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "x.env"
    env_file.write_text("EDENAI_API_KEY=from-file\nVLM_MAX_TOKENS=999\nACCURACY_MODE=balanced\n")
    from_file = VlmSettings(_env_file=env_file)  # type: ignore[call-arg]
    assert from_file.api_key_length() == len("from-file") and from_file.max_tokens == 999
    assert from_file.accuracy_mode is AccuracyMode.BALANCED
    monkeypatch.setenv("EDENAI_API_KEY", "from-environment")
    monkeypatch.setenv("VLM_MAX_TOKENS", "1234")
    from_env = VlmSettings(_env_file=env_file)  # type: ignore[call-arg]
    assert from_env.api_key_length() == len("from-environment") and from_env.max_tokens == 1234


def test_payload_shape(tmp_path: Path) -> None:
    payload = build_payload(request(), settings(tmp_path))
    assert [m["role"] for m in payload["messages"]] == ["user"]  # bez komunikatu systemowego
    content = payload["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "instrukcje"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert payload["temperature"] == 0.0 and "tools" not in payload and "seed" not in payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"] == {
        "name": "roles",
        "strict": True,
        "schema": SCHEMA,
    }
    seeded = build_payload(request(), settings(tmp_path, send_seed=True, seed=7))
    assert seeded["seed"] == 7
    loose = build_payload(request(), settings(tmp_path, response_format=ResponseFormat.JSON_OBJECT))
    assert loose["response_format"] == {"type": "json_object"}


def _client(tmp_path: Path, handler: Any, **overrides: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        settings(tmp_path, **overrides),
        transport=httpx.MockTransport(handler),
        sleep=lambda s: None,
    )


def test_success_sends_bearer_and_reads_usage(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=ok_body())

    client = _client(tmp_path, handler)
    response = client.complete(request())
    assert response.content == '{"ok": true}'
    assert response.usage.prompt_tokens == 1200 and response.usage.completion_tokens == 30
    assert str(seen[0].url) == "https://api.edenai.run/v3/chat/completions"
    assert seen[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(seen[0].content)["model"] == "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"
    assert FAKE_KEY not in repr(client)


def test_local_backend_has_no_token(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=ok_body())

    _client(tmp_path, handler, backend=VlmBackend.LOCAL).complete(request())
    assert "Authorization" not in seen[0].headers
    assert str(seen[0].url) == "http://vlm:8001/v1/chat/completions"


@pytest.mark.parametrize(
    ("responses", "calls", "message"),
    [
        ([429, 200], 2, None),
        ([503, 502], 2, "HTTP 502"),
        ([401], 1, "HTTP 401"),
        ([302], 1, "HTTP 302"),  # przekierowanie nie jest wykonywane
    ],
)
def test_status_handling_and_retries(
    tmp_path: Path, responses: list[int], calls: int, message: str | None
) -> None:
    queue = list(responses)
    count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        status = queue.pop(0)
        if status == 200:
            return httpx.Response(200, json=ok_body())
        headers = {"Location": "https://inny-host.example/"} if status == 302 else {}
        return httpx.Response(status, text=f"treść błędu {FAKE_KEY}", headers=headers)

    client = _client(tmp_path, handler)
    if message is None:
        assert client.complete(request()).content
    else:
        with pytest.raises(VlmUnavailable) as caught:
            client.complete(request())
        assert caught.value.code == VLM_UNAVAILABLE and message in str(caught.value)
        assert FAKE_KEY not in str(caught.value) and "treść błędu" not in str(caught.value)
    assert count == calls


def test_timeout_and_missing_token(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=req)

    with pytest.raises(VlmUnavailable, match="limit czasu"):
        _client(tmp_path, handler).complete(request())
    with pytest.raises(VlmUnavailable, match="brak tokenu"):
        _client(tmp_path, handler, edenai_api_key=None).complete(request())


@pytest.mark.parametrize("body", [{"choices": []}, {"choices": [{"message": {"content": ""}}]}])
def test_invalid_envelope(tmp_path: Path, body: dict[str, Any]) -> None:
    with pytest.raises(VlmInvalidResponse) as caught:
        _client(tmp_path, lambda req: httpx.Response(200, json=body)).complete(request())
    assert caught.value.code == VLM_INVALID_RESPONSE


def test_fake_client_and_factory(tmp_path: Path) -> None:
    fake = FakeVlmClient({"roles": ['{"a": 1}', '{"a": 2}']}, failures=[VlmUnavailable("x")])
    with pytest.raises(VlmUnavailable):
        fake.complete(request())
    assert [fake.complete(request()).content for _ in range(3)] == [
        '{"a": 1}',
        '{"a": 2}',
        '{"a": 2}',
    ]
    assert len(fake.requests) == 4
    assert isinstance(build_client(settings(tmp_path)), OpenAICompatibleClient)
    with pytest.raises(ValueError, match="fake"):
        build_client(settings(tmp_path, backend=VlmBackend.FAKE))
