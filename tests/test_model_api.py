"""Klient API modeli (Eden AI v3, zgodny z OpenAI) — na lokalnym serwerze HTTP udającym Eden AI."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from backend.worker.clients.llm_client import LLMClient, LLMConfig
from backend.worker.clients.model_api import (
    ModelApiClient,
    ModelInfo,
    ModelNotAllowedError,
    ModelRequestRejectedError,
    ModelServiceUnavailableError,
    _model_catalog,
    ensure_open_weights,
)

MODEL = "deepinfra/Qwen/Qwen3-VL-30B-A3B-Instruct"
CATALOG = {
    "object": "list",
    "data": [
        {
            "id": MODEL,
            "open_weights": True,
            "hugging_face_id": "Qwen/Qwen3-VL-30B-A3B-Instruct",
            "pricing": {"input_cost_per_token": 1.5e-7, "output_cost_per_token": 6e-7},
            "capabilities": {"supports_system_messages": False},
        },
        {"id": "openai/gpt-4o", "open_weights": None},
        {"id": "vendor/closed", "open_weights": False},
    ],
}


class FakeEdenAI:
    """Minimalny serwer: /v3/models i /v3/chat/completions, zapisuje odebrane zapytania."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.reply: dict[str, Any] = {
            "choices": [{"message": {"role": "assistant", "content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: Any) -> None:
                return

            def _send(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(200, CATALOG) if self.path == "/v3/models" else self._send(404, {})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                fake.requests.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                self._send(fake.status, fake.reply if fake.status == 200 else {"detail": "x"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v3"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def eden() -> Iterator[FakeEdenAI]:
    fake = FakeEdenAI()
    yield fake
    fake.server.shutdown()


def _client(eden: FakeEdenAI, **kwargs: Any) -> ModelApiClient:
    return ModelApiClient(base_url=eden.base_url, model=MODEL, timeout_seconds=5, **kwargs)


# ------------------------------------------------------------------ zapytanie


def test_chat_request_shape(eden: FakeEdenAI) -> None:
    content = _client(eden, api_key="sekret").chat(
        [{"role": "user", "content": "Cześć"}], json_mode=True
    )
    assert content == '{"ok": true}'
    (request,) = eden.requests
    assert request["path"] == "/v3/chat/completions"
    assert request["auth"] == "Bearer sekret"
    body = request["body"]
    assert body["model"] == MODEL
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["routing"] == {"zero_data_retention": True}
    assert "fallbacks" not in body  # bez cichego przełączenia na inny (np. zamknięty) model


def test_no_key_no_header_and_zdr_optional(eden: FakeEdenAI) -> None:
    _client(eden, zero_data_retention=False).chat([{"role": "user", "content": "x"}])
    request = eden.requests[0]
    assert request["auth"] is None  # w compose klucz dokłada bramka
    assert "routing" not in request["body"]
    assert "response_format" not in request["body"]


def test_usage_and_cost(eden: FakeEdenAI) -> None:
    info = ensure_open_weights(
        eden.base_url, MODEL, catalog=lambda *_: {m["id"]: m for m in CATALOG["data"]}
    )
    client = _client(eden, model_info=info)
    client.chat([{"role": "user", "content": "a"}])
    client.chat([{"role": "user", "content": "b"}])
    assert client.usage.as_dict() == {
        "llm_calls": 2,
        "prompt_tokens": 200,
        "completion_tokens": 40,
        "cost_usd": round(200 * 1.5e-7 + 40 * 6e-7, 6),
    }


def test_system_prompt_folded_for_models_without_system_role(eden: FakeEdenAI) -> None:
    no_system = ModelInfo(MODEL, None, 0.0, 0.0, supports_system_messages=False)
    _client(eden, model_info=no_system).llm_transport("Wyekstrahuj pola", "Zwracaj JSON", 0)
    _client(eden).llm_transport("Wyekstrahuj pola", "Zwracaj JSON", 0)
    folded, normal = (r["body"]["messages"] for r in eden.requests)
    assert folded == [{"role": "user", "content": "Zwracaj JSON\n\nWyekstrahuj pola"}]
    assert [m["role"] for m in normal] == ["system", "user"]


def test_transport_works_with_step_client(eden: FakeEdenAI) -> None:
    """Klient #8 (generate_structured) działa na transporcie Eden AI bez zmian w jego kodzie."""
    client = LLMClient(LLMConfig(offline=False), transport=_client(eden).llm_transport)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    assert client.generate_structured("prompt", schema) == {"ok": True}


# ------------------------------------------------------------------ błędy


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (401, ModelRequestRejectedError, "nieprawidłowy klucz"),
        (402, ModelRequestRejectedError, "brak środków"),
        (404, ModelRequestRejectedError, "nie istnieje"),
        (422, ModelRequestRejectedError, "HTTP 422"),
        (429, ModelServiceUnavailableError, "429"),
        (503, ModelServiceUnavailableError, "503"),
    ],
)
def test_http_errors_classified(
    eden: FakeEdenAI, status: int, error: type[Exception], message: str
) -> None:
    eden.status = status
    with pytest.raises(error, match=message):
        _client(eden).chat([{"role": "user", "content": "x"}])


def test_connection_refused_is_unavailable() -> None:
    client = ModelApiClient(base_url="http://127.0.0.1:9/v3", model=MODEL, timeout_seconds=2)
    with pytest.raises(ModelServiceUnavailableError):
        client.chat([{"role": "user", "content": "x"}])


def test_reply_without_content_is_unavailable(eden: FakeEdenAI) -> None:
    eden.reply = {"choices": []}
    with pytest.raises(ModelServiceUnavailableError, match="bez treści"):
        _client(eden).chat([{"role": "user", "content": "x"}])


def test_only_http_urls() -> None:
    with pytest.raises(ModelRequestRejectedError, match="adres"):
        ModelApiClient(base_url="file:///etc", model=MODEL).chat([])


# ------------------------------------------------------------------ otwarte wagi


def test_catalog_fetched_over_http(eden: FakeEdenAI) -> None:
    _model_catalog.cache_clear()
    info = ensure_open_weights(eden.base_url, MODEL)
    assert info.hugging_face_id == "Qwen/Qwen3-VL-30B-A3B-Instruct"
    assert info.supports_system_messages is False
    assert info.input_cost_per_token == 1.5e-7


@pytest.mark.parametrize(
    ("model", "message"),
    [
        ("openai/gpt-4o", "otwartych wag"),
        ("vendor/closed", "otwartych wag"),
        ("missing/model", "nie występuje"),
        ("gpt-4o", "dostawca/model"),
    ],
)
def test_closed_and_unknown_models_refused(eden: FakeEdenAI, model: str, message: str) -> None:
    _model_catalog.cache_clear()
    with pytest.raises(ModelNotAllowedError, match=message):
        ensure_open_weights(eden.base_url, model)
