"""Worker arq — przetwarza zadania z kolejki Valkey.

Uruchomienie: ``arq backend.worker.worker.WorkerSettings
              --custom-log-dict backend.worker.logging_config.LOG_CONFIG``
Healthcheck:  ``arq backend.worker.worker.WorkerSettings --check``
"""

from typing import Any, ClassVar

from arq.connections import RedisSettings

from backend.shared.config import get_settings
from backend.shared.jobs import deserialize, serialize
from backend.worker.tasks import analyze_document

_settings = get_settings()  # fail fast przy błędnej konfiguracji


async def ping(_ctx: dict[str, Any]) -> str:
    """Zadanie diagnostyczne: potwierdza, że kolejka i worker działają."""
    return "pong"


async def startup(ctx: dict[str, Any]) -> None:
    ctx["settings"] = _settings


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, analyze_document]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(_settings.valkey_url)
    job_serializer = staticmethod(serialize)
    job_deserializer = staticmethod(deserialize)
    max_jobs = _settings.worker_max_jobs
    job_timeout = _settings.job_timeout_seconds
    keep_result = _settings.job_result_ttl_seconds
    log_results = False  # wyniki nie trafiają do logów (patrz logging_config)
    health_check_interval = 30
