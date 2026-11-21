"""Wspólne fixture'y testów integracyjnych (prawdziwy PostgreSQL i Valkey).

Zmienne środowiskowe:
- ``POSTGRES_TEST_URL`` — DSN bazy testowej; każdy test dostaje własny, świeży schemat
  z zastosowanymi migracjami (usuwany po teście),
- ``VALKEY_TEST_URL`` — Valkey; baza jest czyszczona przed i po teście.

Bez tych zmiennych testy integracyjne są pomijane (w CI są ustawione i wymagane).
"""

import os
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.worker import Worker

from backend.shared.config import Settings
from backend.shared.db.migrate import apply_migrations, load_migrations
from backend.shared.db.pool import create_pool as create_db_pool
from backend.shared.db.repository import PgJobStore
from backend.shared.jobs import deserialize, serialize
from backend.worker.tasks import analyze_document

POSTGRES_URL = os.environ.get("POSTGRES_TEST_URL")
VALKEY_URL = os.environ.get("VALKEY_TEST_URL")

needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ustaw POSTGRES_TEST_URL")
needs_valkey = pytest.mark.skipif(not VALKEY_URL, reason="ustaw VALKEY_TEST_URL")


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    if VALKEY_URL:
        monkeypatch.setenv("VALKEY_URL", VALKEY_URL)
    if POSTGRES_URL:
        monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0")
    return Settings()


@pytest.fixture
async def pg_schema() -> AsyncIterator[str]:
    assert POSTGRES_URL is not None
    schema = f"test_{uuid4().hex[:16]}"
    connection = await asyncpg.connect(POSTGRES_URL)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    yield schema
    connection = await asyncpg.connect(POSTGRES_URL)
    try:
        await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await connection.close()


@pytest.fixture
async def db_pool(pg_schema: str) -> AsyncIterator[Any]:
    assert POSTGRES_URL is not None
    pool = await create_db_pool(POSTGRES_URL, schema=pg_schema)
    await apply_migrations(pool, load_migrations())
    yield pool
    await pool.close()


@pytest.fixture
def store(db_pool: Any) -> PgJobStore:
    return PgJobStore(db_pool)


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[ArqRedis]:
    pool = await create_pool(
        RedisSettings.from_dsn(settings.valkey_url),
        job_serializer=serialize,
        job_deserializer=deserialize,
    )
    await pool.flushdb()
    yield pool
    await pool.flushdb()
    await pool.aclose()


WorkerFactory = Callable[..., Worker]


@pytest.fixture
def make_worker(settings: Settings, store: PgJobStore) -> WorkerFactory:
    """Worker arq z tą samą konfiguracją co produkcyjny (msgpack, limit prób, baza testowa)."""

    def factory(*, burst: bool = True, worker_settings: Settings | None = None) -> Worker:
        effective = worker_settings or settings

        async def startup(ctx: dict[str, Any]) -> None:
            ctx["settings"] = effective
            ctx["store"] = store

        return Worker(
            functions=[analyze_document],
            redis_settings=RedisSettings.from_dsn(effective.valkey_url),
            job_serializer=serialize,
            job_deserializer=deserialize,
            on_startup=startup,
            burst=burst,
            poll_delay=0.05,
            max_tries=effective.max_job_tries,
            job_timeout=effective.job_timeout_seconds + 60,
            keep_result=effective.job_result_ttl_seconds,
            handle_signals=False,
        )

    return factory
