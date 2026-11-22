"""Repozytorium zadań, paczek i zatwierdzonych formularzy (PostgreSQL).

Przejścia statusów są warunkowe (``WHERE status IN (...)``): spóźniony worker nie nadpisze
zadania, które zostało już zakończone (np. uznane za utracone), a zakończone zadanie nie
zostanie ponownie uruchomione.
"""

import re
import unicodedata
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from backend.shared.config import Settings
from backend.shared.db.pool import DB_UNAVAILABLE_ERRORS, create_pool, database_dsn
from backend.shared.jobs import DocumentRef, JobError, JobErrorCode, JobStatus
from backend.shared.schemas.coi_form import FormDefinition
from backend.shared.schemas.quality import AnalysisResult


class RecordModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class JobRecord(RecordModel):
    id: str
    owner: str
    batch_id: str | None
    filename: str
    extension: str
    mime_type: str
    size_bytes: int
    sha256: str
    status: JobStatus
    attempts: int
    error: JobError | None
    last_error_message: str | None
    result: AnalysisResult | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    approved_at: datetime | None


class RejectedEntry(RecordModel):
    filename: str | None
    code: str
    message: str


class BatchRecord(RecordModel):
    id: str
    owner: str
    total_files: int
    rejected: list[RejectedEntry]
    created_at: datetime


class ApprovalRecord(RecordModel):
    job_id: str | None
    owner: str
    sha256: str
    title: str
    form: FormDefinition
    approved_at: datetime


class JobStore(Protocol):
    async def ping(self) -> bool: ...

    async def close(self) -> None: ...

    async def create_batch(
        self, batch_id: str, owner: str, total_files: int, rejected: list[RejectedEntry]
    ) -> None: ...

    async def update_batch_rejected(self, batch_id: str, rejected: list[RejectedEntry]) -> None: ...

    async def delete_batch(self, batch_id: str) -> None: ...

    async def create_job(self, document: DocumentRef, batch_id: str | None = None) -> None: ...

    async def delete_job(self, job_id: str) -> None: ...

    async def get_job(
        self, job_id: str, owner: str | None = None, *, include_result: bool = True
    ) -> JobRecord | None: ...

    async def list_jobs(
        self,
        owner: str,
        *,
        status: JobStatus | None = None,
        batch_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]: ...

    async def get_batch(self, batch_id: str, owner: str) -> BatchRecord | None: ...

    async def mark_started(self, job_id: str) -> int | None: ...

    async def mark_retrying(self, job_id: str, message: str) -> bool: ...

    async def mark_succeeded(self, job_id: str, result: AnalysisResult) -> bool: ...

    async def mark_failed(self, job_id: str, error: JobError) -> bool: ...

    async def find_unfinished(self, updated_before: datetime) -> list[JobRecord]: ...

    async def purge_created_before(self, cutoff: datetime) -> int: ...

    async def save_approval(
        self, job_id: str, owner: str, sha256: str, form: FormDefinition
    ) -> ApprovalRecord: ...

    async def get_approval(self, job_id: str, owner: str) -> ApprovalRecord | None: ...


_JOB_COLUMNS = """
    j.id, j.owner, j.batch_id, j.filename, j.extension, j.mime_type, j.size_bytes, j.sha256,
    j.status, j.attempts, j.error_code, j.error_message, j.created_at, j.started_at,
    j.finished_at, j.updated_at, a.approved_at
"""
_JOB_FROM = "FROM jobs j LEFT JOIN approved_forms a ON a.job_id = j.id"


def _job(row: Any, *, with_result: bool) -> JobRecord:
    status = JobStatus(row["status"])
    error = (
        JobError(code=JobErrorCode(row["error_code"]), message=row["error_message"] or "")
        if row["error_code"]
        else None
    )
    result = row["result"] if with_result else None
    return JobRecord(
        id=row["id"],
        owner=row["owner"],
        batch_id=row["batch_id"],
        filename=row["filename"],
        extension=row["extension"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        status=status,
        attempts=row["attempts"],
        error=error,
        last_error_message=None if error else row["error_message"],
        result=AnalysisResult.model_validate(result) if result is not None else None,
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
        approved_at=row["approved_at"],
    )


def _approval(row: Any) -> ApprovalRecord:
    return ApprovalRecord(
        job_id=row["job_id"],
        owner=row["owner"],
        sha256=row["sha256"],
        title=row["title"],
        form=FormDefinition.model_validate(row["form"]),
        approved_at=row["approved_at"],
    )


def result_json(result: AnalysisResult) -> dict[str, Any]:
    """Wynik w postaci JSON zgodnej z odpowiedzią API (plik COI bez pustych kluczy)."""
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def document_type_code(title: str) -> str:
    """Kod nowego typu dokumentu z tytułu formularza (snake_case, bez polskich znaków)."""
    folded = unicodedata.normalize("NFKD", title.lower().replace("ł", "l"))
    ascii_text = "".join(ch for ch in folded if not unicodedata.combining(ch))
    code = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")[:100].rstrip("_")
    return code or "dokument"


async def _feed_knowledge_base(
    connection: Any, job_id: str, approved_id: int, title: str, form: dict[str, Any]
) -> None:
    """Pętla zwrotna RAG: zatwierdzony formularz trafia do bazy wiedzy jako wzorzec typu.

    Wektor dokumentu policzył worker podczas analizy (API nie ma modelu embeddingów).
    Dokument nierozpoznany (``unknown``) zakłada nowy typ z tytułu formularza. Bez wektora
    (analiza bez bazy wiedzy) zatwierdzenie zapisuje się, a baza wiedzy zostaje bez zmian.
    """
    job = await connection.fetchrow(
        "SELECT embedding::text AS embedding, embedding_model, sha256, "
        "result->'report'->>'document_type' AS doc_type FROM jobs WHERE id = $1",
        job_id,
    )
    if job is None or job["embedding"] is None:
        return
    doc_type = job["doc_type"]
    known = doc_type and await connection.fetchval(
        "SELECT 1 FROM document_types WHERE code = $1", doc_type
    )
    if not known:
        doc_type = document_type_code(title)
        await connection.execute(
            "INSERT INTO document_types (code, title, source) VALUES ($1, $2, 'approved') "
            "ON CONFLICT (code) DO NOTHING",
            doc_type,
            title[:255],
        )
    await connection.execute(
        """
        INSERT INTO knowledge_examples
            (doc_type, source, sha256, embedding_model, embedding, form, approved_form_id)
        VALUES ($1, 'approved', $2, $3, $4::vector, $5, $6)
        ON CONFLICT (approved_form_id) DO UPDATE
            SET doc_type = EXCLUDED.doc_type, form = EXCLUDED.form, created_at = now()
        """,
        doc_type,
        job["sha256"],
        job["embedding_model"],
        job["embedding"],
        form,
        approved_id,
    )


class PgJobStore:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, settings: Settings) -> PgJobStore:
        return cls(await create_pool(database_dsn(settings), max_size=settings.db_pool_max_size))

    async def ping(self) -> bool:
        try:
            return bool(await self._pool.fetchval("SELECT 1") == 1)
        except DB_UNAVAILABLE_ERRORS:
            return False

    async def close(self) -> None:
        await self._pool.close()

    async def create_batch(
        self, batch_id: str, owner: str, total_files: int, rejected: list[RejectedEntry]
    ) -> None:
        await self._pool.execute(
            "INSERT INTO batches (id, owner, total_files, rejected) VALUES ($1, $2, $3, $4)",
            batch_id,
            owner,
            total_files,
            [r.model_dump() for r in rejected],
        )

    async def update_batch_rejected(self, batch_id: str, rejected: list[RejectedEntry]) -> None:
        await self._pool.execute(
            "UPDATE batches SET rejected = $2 WHERE id = $1",
            batch_id,
            [r.model_dump() for r in rejected],
        )

    async def delete_batch(self, batch_id: str) -> None:
        await self._pool.execute("DELETE FROM batches WHERE id = $1", batch_id)

    async def create_job(self, document: DocumentRef, batch_id: str | None = None) -> None:
        await self._pool.execute(
            """
            INSERT INTO jobs (id, owner, batch_id, filename, extension, mime_type, size_bytes,
                              sha256, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
            """,
            document.job_id,
            document.owner,
            batch_id,
            document.filename,
            document.extension,
            document.mime_type,
            document.size_bytes,
            document.sha256,
            document.created_at,
        )

    async def delete_job(self, job_id: str) -> None:
        await self._pool.execute("DELETE FROM jobs WHERE id = $1", job_id)

    async def get_job(
        self, job_id: str, owner: str | None = None, *, include_result: bool = True
    ) -> JobRecord | None:
        columns = _JOB_COLUMNS + (", j.result" if include_result else "")
        row = await self._pool.fetchrow(
            f"SELECT {columns} {_JOB_FROM} WHERE j.id = $1 AND ($2::text IS NULL OR j.owner = $2)",
            job_id,
            owner,
        )
        return _job(row, with_result=include_result) if row else None

    async def list_jobs(
        self,
        owner: str,
        *,
        status: JobStatus | None = None,
        batch_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[JobRecord], int]:
        where = (
            "WHERE j.owner = $1 AND ($2::text IS NULL OR j.status = $2) "
            "AND ($3::text IS NULL OR j.batch_id = $3)"
        )
        args = (owner, status.value if status else None, batch_id)
        async with self._pool.acquire() as connection:
            total = await connection.fetchval(f"SELECT count(*) FROM jobs j {where}", *args)  # noqa: S608
            rows = await connection.fetch(
                f"SELECT {_JOB_COLUMNS} {_JOB_FROM} {where} "
                "ORDER BY j.created_at DESC, j.id LIMIT $4 OFFSET $5",
                *args,
                limit,
                offset,
            )
        return [_job(row, with_result=False) for row in rows], int(total)

    async def get_batch(self, batch_id: str, owner: str) -> BatchRecord | None:
        row = await self._pool.fetchrow(
            "SELECT id, owner, total_files, rejected, created_at FROM batches "
            "WHERE id = $1 AND owner = $2",
            batch_id,
            owner,
        )
        if row is None:
            return None
        return BatchRecord(
            id=row["id"],
            owner=row["owner"],
            total_files=row["total_files"],
            rejected=[RejectedEntry.model_validate(r) for r in row["rejected"]],
            created_at=row["created_at"],
        )

    async def mark_started(self, job_id: str) -> int | None:
        attempts: int | None = await self._pool.fetchval(
            """
            UPDATE jobs SET status = 'IN_PROGRESS', attempts = attempts + 1,
                            started_at = COALESCE(started_at, now()), updated_at = now()
            WHERE id = $1 AND status IN ('QUEUED', 'IN_PROGRESS')
            RETURNING attempts
            """,
            job_id,
        )
        return attempts

    async def mark_retrying(self, job_id: str, message: str) -> bool:
        result = await self._pool.execute(
            """
            UPDATE jobs SET status = 'QUEUED', error_message = $2, updated_at = now()
            WHERE id = $1 AND status IN ('QUEUED', 'IN_PROGRESS')
            """,
            job_id,
            message,
        )
        return bool(result == "UPDATE 1")

    async def mark_succeeded(self, job_id: str, result: AnalysisResult) -> bool:
        status = await self._pool.execute(
            """
            UPDATE jobs SET status = 'SUCCEEDED', result = $2, error_code = NULL,
                            error_message = NULL, finished_at = now(), updated_at = now()
            WHERE id = $1 AND status IN ('QUEUED', 'IN_PROGRESS')
            """,
            job_id,
            result_json(result),
        )
        return bool(status == "UPDATE 1")

    async def mark_failed(self, job_id: str, error: JobError) -> bool:
        status = await self._pool.execute(
            """
            UPDATE jobs SET status = 'FAILED', error_code = $2, error_message = $3,
                            result = NULL, finished_at = now(), updated_at = now()
            WHERE id = $1 AND status IN ('QUEUED', 'IN_PROGRESS')
            """,
            job_id,
            error.code.value,
            error.message,
        )
        return bool(status == "UPDATE 1")

    async def find_unfinished(self, updated_before: datetime) -> list[JobRecord]:
        rows = await self._pool.fetch(
            f"SELECT {_JOB_COLUMNS} {_JOB_FROM} "
            "WHERE j.status IN ('QUEUED', 'IN_PROGRESS') AND j.updated_at < $1 "
            "ORDER BY j.created_at LIMIT 500",
            updated_before,
        )
        return [_job(row, with_result=False) for row in rows]

    async def purge_created_before(self, cutoff: datetime) -> int:
        async with self._pool.acquire() as connection, connection.transaction():
            deleted: int = await connection.fetchval(
                "WITH d AS (DELETE FROM jobs WHERE created_at < $1 RETURNING 1) "
                "SELECT count(*) FROM d",
                cutoff,
            )
            await connection.execute(
                "DELETE FROM batches b WHERE b.created_at < $1 "
                "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.batch_id = b.id)",
                cutoff,
            )
        return deleted

    async def save_approval(
        self, job_id: str, owner: str, sha256: str, form: FormDefinition
    ) -> ApprovalRecord:
        coi_json = form.to_coi_json()
        async with self._pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO approved_forms (job_id, owner, sha256, title, form)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (job_id) DO UPDATE
                    SET owner = EXCLUDED.owner, title = EXCLUDED.title, form = EXCLUDED.form,
                        approved_at = now()
                RETURNING id, job_id, owner, sha256, title, form, approved_at
                """,
                job_id,
                owner,
                sha256,
                form.title,
                coi_json,
            )
            await _feed_knowledge_base(connection, job_id, row["id"], form.title, coi_json)
        return _approval(row)

    async def get_approval(self, job_id: str, owner: str) -> ApprovalRecord | None:
        row = await self._pool.fetchrow(
            "SELECT job_id, owner, sha256, title, form, approved_at FROM approved_forms "
            "WHERE job_id = $1 AND owner = $2",
            job_id,
            owner,
        )
        return _approval(row) if row else None
