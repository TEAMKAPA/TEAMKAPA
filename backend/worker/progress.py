"""Raportowanie postępu analizy — widoczne w ``GET /api/v1/jobs/{id}`` (status IN_PROGRESS)."""

from typing import Protocol

from backend.shared.jobs import JobProgress, progress_key


class _KeyValueStore(Protocol):
    async def set(self, name: str, value: str, ex: int | None = None) -> object: ...

    async def delete(self, *names: str) -> object: ...


class ProgressReporter:
    def __init__(self, store: _KeyValueStore, job_id: str, ttl_seconds: int) -> None:
        self._store = store
        self._key = progress_key(job_id)
        self._ttl = ttl_seconds

    async def update(
        self, stage: str, step: int, total_steps: int, message: str | None = None
    ) -> None:
        progress = JobProgress(stage=stage, step=step, total_steps=total_steps, message=message)
        await self._store.set(self._key, progress.model_dump_json(), ex=self._ttl)

    async def clear(self) -> None:
        await self._store.delete(self._key)
