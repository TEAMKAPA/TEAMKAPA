"""Cache odpowiedzi VLM i liczniki (#11, D.3)."""

import logging
from pathlib import Path

import pytest

from backend.worker.vision.vlm.cache import CachedVlmClient, cache_key
from backend.worker.vision.vlm.client import (
    VLM_UNAVAILABLE,
    FakeVlmClient,
    VlmImage,
    VlmRequest,
    VlmUnavailable,
)

SECRET_TEXT = "TREŚĆ-DOKUMENTU-NIE-DO-LOGÓW"
PARAMS = {"temperature": 0.0, "max_tokens": 100, "response_format": "json_schema"}


def request(
    image: bytes = b"img", prompt: str = SECRET_TEXT, version: str = "roles_v1:a"
) -> VlmRequest:
    return VlmRequest(
        task="roles",
        prompt=prompt,
        prompt_version=version,
        image=VlmImage(image),
        schema_name="roles",
        schema={"type": "object"},
    )


def test_key_depends_on_every_input() -> None:
    base = cache_key(request(), "m", PARAMS)
    assert cache_key(request(), "m", PARAMS) == base
    assert cache_key(request(image=b"other"), "m", PARAMS) != base
    assert cache_key(request(prompt="inny"), "m", PARAMS) != base
    assert cache_key(request(version="roles_v2:b"), "m", PARAMS) != base
    assert cache_key(request(), "m2", PARAMS) != base
    assert cache_key(request(), "m", {**PARAMS, "temperature": 0.5}) != base


def test_second_call_uses_cache_and_counts_tokens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    fake = FakeVlmClient({"roles": '{"ok": "' + SECRET_TEXT + '"}'})
    client = CachedVlmClient(fake, PARAMS, tmp_path / "vlm_cache")
    first = client.complete(request())
    second = client.complete(request())
    assert not first.cached and second.cached and second.content == first.content
    assert len(fake.requests) == 1
    stats = client.stats.as_dict()
    assert stats["calls"] == 1 and stats["cache_hits"] == 1
    assert stats["prompt_tokens"] == first.usage.prompt_tokens > 0
    files = list((tmp_path / "vlm_cache").rglob("*.json"))
    assert len(files) == 1 and not list((tmp_path / "vlm_cache").rglob("*.tmp"))
    assert all(SECRET_TEXT not in record.getMessage() for record in caplog.records)
    # Nowa instancja (np. ponowna ewaluacja) też korzysta z cache.
    again = CachedVlmClient(FakeVlmClient({"roles": "{}"}), PARAMS, tmp_path / "vlm_cache")
    assert again.complete(request()).content == first.content and again.stats.calls == 0


def test_errors_are_counted_and_not_cached(tmp_path: Path) -> None:
    fake = FakeVlmClient({"roles": "{}"}, failures=[VlmUnavailable("HTTP 503")])
    client = CachedVlmClient(fake, PARAMS, tmp_path)
    with pytest.raises(VlmUnavailable):
        client.complete(request())
    assert client.stats.errors[VLM_UNAVAILABLE] == 1
    assert not list(tmp_path.rglob("*.json"))
    assert client.complete(request()).content == "{}"


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    fake = FakeVlmClient({"roles": "{}"})
    client = CachedVlmClient(fake, PARAMS, None)
    client.complete(request())
    client.complete(request())
    assert len(fake.requests) == 2 and client.stats.cache_hits == 0
