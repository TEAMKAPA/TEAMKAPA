"""Kolejka zadań po stronie API (arq + Valkey). Status zadań czytamy z PostgreSQL."""

import logging
from typing import Protocol

from arq.connections import ArqRedis, RedisSettings, create_pool
from pydantic import ValidationError
from redis.exceptions import RedisError

from backend.shared.config import Settings
from backend.shared.jobs import (
    ANALYZE_TASK,
    DocumentRef,
    JobProgress,
    QueueState,
    deserialize,
    file_key,
    progress_key,
    serialize,
)
from backend.shared.reconcile import queue_state

logger = logging.getLogger(__name__)

_QUEUE_ERRORS = (RedisError, OSError)


class QueueUnavailableError(Exception):
    """Kolejka (Valkey) jest niedostępna."""


class JobQueue(Protocol):
    async def submit(self, document: DocumentRef, content: bytes) -> None: ...

    async def progress(self, job_id: str) -> JobProgress | None: ...

    async def state(self, job_id: str) -> QueueState: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    @classmethod
    async def connect(cls, settings: Settings) -> ArqJobQueue:
        redis = await create_pool(
            RedisSettings.from_dsn(settings.valkey_url),
            job_serializer=serialize,
            job_deserializer=deserialize,
        )
        return cls(redis, settings)

    async def submit(self, document: DocumentRef, content: bytes) -> None:
        ttl = self._settings.job_queue_ttl_seconds
        key = file_key(document.job_id)
        try:
            await self._redis.set(key, content, ex=ttl)
            job = await self._redis.enqueue_job(
                ANALYZE_TASK,
                document.model_dump(mode="json"),
                _job_id=document.job_id,
                _expires=ttl,
            )
        except _QUEUE_ERRORS as exc:
            await self._discard_file(key)
            raise QueueUnavailableError(str(exc)) from exc
        if job is None:  # identyfikator już istnieje — nie powinno się zdarzyć (uuid4)
            await self._discard_file(key)
            raise QueueUnavailableError(f"Zadanie {document.job_id} już istnieje")

    async def _discard_file(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except _QUEUE_ERRORS:
            logger.warning("Nie udało się usunąć %s — wygaśnie po TTL", key)

    async def progress(self, job_id: str) -> JobProgress | None:
        try:
            raw = await self._redis.get(progress_key(job_id))
        except _QUEUE_ERRORS as exc:
            raise QueueUnavailableError(str(exc)) from exc
        if raw is None:
            return None
        try:
            return JobProgress.model_validate_json(raw)
        except ValidationError:
            logger.warning("Nieprawidłowy zapis postępu zadania %s", job_id)
            return None

    async def state(self, job_id: str) -> QueueState:
        try:
            return await queue_state(self._redis, job_id)
        except _QUEUE_ERRORS as exc:
            raise QueueUnavailableError(str(exc)) from exc

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except _QUEUE_ERRORS:
            return False

    async def close(self) -> None:
        await self._redis.aclose()
