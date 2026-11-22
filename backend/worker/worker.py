"""Worker arq — przetwarza zadania z kolejki Valkey, stan zadań zapisuje w PostgreSQL.

Uruchomienie: ``arq backend.worker.worker.WorkerSettings
              --custom-log-dict backend.worker.logging_config.LOG_CONFIG``
Healthcheck:  ``arq backend.worker.worker.WorkerSettings --check``
"""

import logging
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings

from backend.shared.config import get_settings
from backend.shared.db.pool import create_pool, database_dsn
from backend.shared.db.repository import PgJobStore
from backend.shared.jobs import deserialize, serialize
from backend.worker.rag.embedder import E5Embedder
from backend.worker.rag.knowledge import PgKnowledgeBase
from backend.worker.tasks import analyze_document, purge_old_jobs, reconcile_stale_jobs

logger = logging.getLogger(__name__)
_settings = get_settings()  # fail fast przy błędnej konfiguracji

# Zapas na zapis wyniku po wewnętrznym limicie czasu analizy (JOB_TIMEOUT_SECONDS).
_QUEUE_TIMEOUT_MARGIN_SECONDS = 60


async def ping(_ctx: dict[str, Any]) -> str:
    """Zadanie diagnostyczne: potwierdza, że kolejka i worker działają."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    ctx["settings"] = _settings
    ctx["db_pool"] = await create_pool(database_dsn(_settings), max_size=_settings.db_pool_max_size)
    ctx["store"] = PgJobStore(ctx["db_pool"])
    ctx["knowledge"] = await _knowledge_base(ctx["db_pool"])


async def _knowledge_base(pool: Any) -> PgKnowledgeBase | None:
    """Baza wiedzy (RAG): model embeddingów i katalog typów. Błąd nie blokuje workera —
    analiza działa wtedy bez RAG (typ z dopasowania leksykalnego, ostrzeżenie w raporcie)."""
    if not _settings.rag_enabled:
        return None
    embedder = E5Embedder(_settings.embedding_model, _settings.embedding_model_revision)
    knowledge = PgKnowledgeBase(pool, embedder)
    try:
        await knowledge.ensure_catalog()
    except Exception:
        logger.exception("Baza wiedzy niedostępna — worker działa bez RAG")
        return None
    return knowledge


async def shutdown(ctx: dict[str, Any]) -> None:
    pool = ctx.get("db_pool")
    if pool is not None:
        await pool.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, analyze_document]
    cron_jobs: ClassVar[list[Any]] = [
        cron(reconcile_stale_jobs, minute=set(range(0, 60, 5)), unique=True, timeout=120),
        cron(purge_old_jobs, hour={3}, minute={15}, unique=True, timeout=600),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.valkey_url)
    job_serializer = staticmethod(serialize)
    job_deserializer = staticmethod(deserialize)
    max_jobs = _settings.worker_max_jobs
    max_tries = _settings.max_job_tries
    job_timeout = _settings.job_timeout_seconds + _QUEUE_TIMEOUT_MARGIN_SECONDS
    keep_result = _settings.job_result_ttl_seconds
    log_results = False  # wyniki nie trafiają do logów (patrz logging_config)
    health_check_interval = 30
