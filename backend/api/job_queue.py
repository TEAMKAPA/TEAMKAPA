"""Kolejka zadań po stronie API (arq + Valkey). Kontrakt: ``backend.shared.jobs``."""

import asyncio
import logging
from typing import Any, Protocol

from arq.connections import ArqRedis, RedisSettings, create_pool
from arq.jobs import Job
from arq.jobs import JobStatus as ArqJobStatus
from pydantic import ValidationError
from redis.exceptions import RedisError

from backend.shared.config import Settings
from backend.shared.jobs import (
    ANALYZE_TASK,
    DocumentRef,
    JobError,
    JobErrorCode,
    JobOutcome,
    JobProgress,
    JobSnapshot,
    JobStatus,
    deserialize,
    file_key,
    progress_key,
    serialize,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 0.5
_QUEUE_ERRORS = (RedisError, OSError)


class QueueUnavailableError(Exception):
    """Kolejka (Valkey) jest niedostępna."""


class JobQueue(Protocol):
    async def submit(self, document: DocumentRef, content: bytes) -> None: ...

    async def get(self, job_id: str) -> JobSnapshot | None: ...

    async def wait(self, job_id: str, max_seconds: float) -> JobSnapshot | None:
        """Czeka na zakończenie zadania najwyżej ``max_seconds`` sekund; zwraca aktualny stan."""
        ...

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

    async def get(self, job_id: str) -> JobSnapshot | None:
        try:
            return await self._snapshot(job_id)
        except _QUEUE_ERRORS as exc:
            raise QueueUnavailableError(str(exc)) from exc

    async def wait(self, job_id: str, max_seconds: float) -> JobSnapshot | None:
        snapshot = await self.get(job_id)
        try:
            async with asyncio.timeout(max_seconds):
                while snapshot is not None and not snapshot.status.finished:
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    snapshot = await self.get(job_id)
        except TimeoutError:
            pass
        return snapshot

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except _QUEUE_ERRORS:
            return False

    async def close(self) -> None:
        await self._redis.aclose()

    async def _snapshot(self, job_id: str) -> JobSnapshot | None:
        job = Job(job_id, self._redis, _deserializer=deserialize)
        status = await job.status()
        if status is ArqJobStatus.not_found:
            return None

        if status is ArqJobStatus.complete:
            info = await job.result_info()
            if info is None:  # wynik wygasł między odczytami
                return None
            document = _document_from_args(info.args)
            if document is None:
                return None
            return _finished_snapshot(document, info.success, info.result)

        definition = await job.info()
        document = _document_from_args(definition.args) if definition else None
        if document is None:
            return None
        if status is ArqJobStatus.in_progress:
            return JobSnapshot(
                job_id=job_id,
                status=JobStatus.IN_PROGRESS,
                document=document,
                progress=await self._progress(job_id),
            )
        return JobSnapshot(job_id=job_id, status=JobStatus.QUEUED, document=document)

    async def _progress(self, job_id: str) -> JobProgress | None:
        raw = await self._redis.get(progress_key(job_id))
        if raw is None:
            return None
        try:
            return JobProgress.model_validate_json(raw)
        except ValidationError:
            logger.warning("Nieprawidłowy zapis postępu zadania %s", job_id)
            return None


def _document_from_args(args: tuple[Any, ...]) -> DocumentRef | None:
    try:
        return DocumentRef.model_validate(args[0])
    except IndexError, ValidationError:
        logger.warning("Zadanie w kolejce ma nieoczekiwane argumenty")
        return None


def _finished_snapshot(document: DocumentRef, success: bool, result: Any) -> JobSnapshot:
    job_id = document.job_id
    if success:
        try:
            outcome = JobOutcome.model_validate(result)
        except ValidationError:
            logger.exception("Zadanie %s zwróciło wynik niezgodny z kontraktem", job_id)
        else:
            if outcome.status == "succeeded" and outcome.result is not None:
                return JobSnapshot(
                    job_id=job_id,
                    status=JobStatus.SUCCEEDED,
                    document=document,
                    result=outcome.result,
                )
            if outcome.error is not None:
                return JobSnapshot(
                    job_id=job_id, status=JobStatus.FAILED, document=document, error=outcome.error
                )
    # Zadanie przerwane (timeout, awaria) albo wynik niezgodny z kontraktem.
    return JobSnapshot(
        job_id=job_id,
        status=JobStatus.FAILED,
        document=document,
        error=JobError(code=JobErrorCode.JOB_FAILED, message="Analiza została przerwana"),
    )
