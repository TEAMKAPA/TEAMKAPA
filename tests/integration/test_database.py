"""Repozytorium i migracje na prawdziwym PostgreSQL."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest

from backend.shared.db.migrate import MigrationError, apply_migrations, load_migrations
from backend.shared.db.repository import PgJobStore, RejectedEntry
from backend.shared.jobs import DocumentRef, JobError, JobErrorCode, JobStatus
from backend.shared.schemas.coi_form import FormDefinition
from tests.coi_helpers import sample
from tests.fakes import sample_result
from tests.integration.conftest import needs_postgres

pytestmark = [pytest.mark.anyio, needs_postgres]


def _document(owner: str = "urzednik", content: bytes = b"%PDF-1.4 tresc") -> DocumentRef:
    return DocumentRef(
        job_id=uuid4().hex,
        filename="Wniosek_Jan_Kowalski.pdf",
        extension="pdf",
        mime_type="application/pdf",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        owner=owner,
    )


# ------------------------------------------------------------------ migracje


async def test_migrations_are_idempotent(db_pool: Any) -> None:
    assert await apply_migrations(db_pool, load_migrations()) == []
    versions = await db_pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
    assert [r["version"] for r in versions] == [m.version for m in load_migrations()]


async def test_changed_migration_is_rejected(db_pool: Any) -> None:
    migrations = load_migrations()
    tampered = [migrations[0].__class__(migrations[0].version, migrations[0].name, "SELECT 1;")]
    with pytest.raises(MigrationError, match="zmieniona"):
        await apply_migrations(db_pool, tampered)


def test_migration_file_names_validated(tmp_path: Path) -> None:
    (tmp_path / "1_bez_zer.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="Nieprawidłowa nazwa"):
        load_migrations(tmp_path)


# ------------------------------------------------------------------ zadania


async def test_job_lifecycle(store: PgJobStore) -> None:
    document = _document()
    await store.create_job(document)
    job = await store.get_job(document.job_id, "urzednik")
    assert job is not None
    assert (job.status, job.attempts, job.result, job.approved_at) == (
        JobStatus.QUEUED, 0, None, None,
    )  # fmt: skip

    assert await store.mark_started(document.job_id) == 1
    assert await store.mark_retrying(document.job_id, "Ponowienie za 30 s: LLM niedostępny")
    retrying = await store.get_job(document.job_id)
    assert retrying is not None
    assert retrying.status is JobStatus.QUEUED
    assert retrying.last_error_message == "Ponowienie za 30 s: LLM niedostępny"

    assert await store.mark_started(document.job_id) == 2
    result = sample_result(document.sha256)
    assert await store.mark_succeeded(document.job_id, result)
    done = await store.get_job(document.job_id)
    assert done is not None
    assert done.status is JobStatus.SUCCEEDED
    assert done.result == result  # JSONB -> model bez strat (także daty ze strefą)
    assert done.result.form.to_coi_json() == sample()
    assert done.finished_at is not None
    assert done.last_error_message is None


async def test_finished_job_cannot_be_restarted_or_overwritten(store: PgJobStore) -> None:
    document = _document()
    await store.create_job(document)
    await store.mark_started(document.job_id)
    error = JobError(code=JobErrorCode.JOB_LOST, message="utracone")
    assert await store.mark_failed(document.job_id, error)

    assert await store.mark_started(document.job_id) is None
    assert not await store.mark_succeeded(document.job_id, sample_result())
    assert not await store.mark_retrying(document.job_id, "x")
    job = await store.get_job(document.job_id)
    assert job is not None
    assert job.status is JobStatus.FAILED
    assert job.error == error


async def test_database_constraints_guard_consistency(store: PgJobStore, db_pool: Any) -> None:
    document = _document()
    await store.create_job(document)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute("UPDATE jobs SET status = 'SUCCEEDED' WHERE id = $1", document.job_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute("UPDATE jobs SET status = 'DONE' WHERE id = $1", document.job_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_pool.execute("UPDATE jobs SET sha256 = 'abc' WHERE id = $1", document.job_id)


async def test_owner_isolation_and_listing(store: PgJobStore) -> None:
    mine = [_document() for _ in range(3)]
    for document in mine:
        await store.create_job(document)
    other = _document(owner="ktos-inny")
    await store.create_job(other)
    await store.mark_started(mine[0].job_id)
    await store.mark_succeeded(mine[0].job_id, sample_result())

    assert await store.get_job(other.job_id, "urzednik") is None
    page, total = await store.list_jobs("urzednik", limit=2)
    rest, _ = await store.list_jobs("urzednik", limit=2, offset=2)
    assert total == 3
    assert {j.id for j in page + rest} == {d.job_id for d in mine}
    assert all(j.result is None for j in page)  # lista bez pełnych wyników
    succeeded, count = await store.list_jobs("urzednik", status=JobStatus.SUCCEEDED)
    assert count == 1
    assert succeeded[0].id == mine[0].job_id


async def test_batches(store: PgJobStore) -> None:
    batch_id = uuid4().hex
    rejected = [RejectedEntry(filename="c.exe", code="EXTENSION_NOT_ALLOWED", message="x")]
    await store.create_batch(batch_id, "urzednik", 3, rejected)
    for _ in range(2):
        await store.create_job(_document(), batch_id)

    batch = await store.get_batch(batch_id, "urzednik")
    assert batch is not None
    assert (batch.total_files, batch.rejected) == (3, rejected)
    assert await store.get_batch(batch_id, "ktos-inny") is None
    _, total = await store.list_jobs("urzednik", batch_id=batch_id)
    assert total == 2

    extended = [*rejected, RejectedEntry(filename="d.pdf", code="QUEUE_UNAVAILABLE", message="y")]
    await store.update_batch_rejected(batch_id, extended)
    batch = await store.get_batch(batch_id, "urzednik")
    assert batch is not None
    assert batch.rejected == extended

    await store.delete_batch(batch_id)  # kaskadowo usuwa zadania paczki
    assert await store.list_jobs("urzednik") == ([], 0)


async def test_approval_survives_retention(store: PgJobStore) -> None:
    document = _document()
    await store.create_job(document)
    await store.mark_started(document.job_id)
    await store.mark_succeeded(document.job_id, sample_result(document.sha256))

    form = FormDefinition.model_validate(sample())
    approval = await store.save_approval(document.job_id, "urzednik", document.sha256, form)
    assert approval.form == form
    job = await store.get_job(document.job_id)
    assert job is not None
    assert job.approved_at == approval.approved_at
    again = await store.save_approval(document.job_id, "urzednik", document.sha256, form)
    assert again.approved_at >= approval.approved_at  # nadpisanie, nie duplikat

    old = _document()
    await store.create_job(old)
    assert await store.purge_created_before(datetime.now(UTC) + timedelta(seconds=1)) == 2
    assert await store.get_job(document.job_id) is None
    rows = await store._pool.fetch("SELECT job_id, sha256 FROM approved_forms")
    assert [(r["job_id"], r["sha256"]) for r in rows] == [(None, document.sha256)]


async def test_purge_keeps_recent_jobs(store: PgJobStore) -> None:
    document = _document()
    await store.create_job(document)
    assert await store.purge_created_before(datetime.now(UTC) - timedelta(days=30)) == 0
    assert await store.get_job(document.job_id) is not None


async def test_find_unfinished(store: PgJobStore, db_pool: Any) -> None:
    stale, fresh, done = _document(), _document(), _document()
    for document in (stale, fresh, done):
        await store.create_job(document)
    await store.mark_started(done.job_id)
    await store.mark_succeeded(done.job_id, sample_result())
    await db_pool.execute(
        "UPDATE jobs SET updated_at = now() - interval '1 hour' WHERE id = ANY($1)",
        [stale.job_id, done.job_id],
    )
    found = await store.find_unfinished(datetime.now(UTC) - timedelta(minutes=5))
    assert [j.id for j in found] == [stale.job_id]


async def test_ping(store: PgJobStore) -> None:
    assert await store.ping()
