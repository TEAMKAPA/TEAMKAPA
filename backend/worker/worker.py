"""Worker arq — przetwarza zadania z kolejki Valkey.

Uruchomienie: ``arq backend.worker.worker.WorkerSettings``
Healthcheck:  ``arq backend.worker.worker.WorkerSettings --check``
"""

from typing import Any, ClassVar

from arq.connections import RedisSettings

from backend.shared.config import get_settings


async def ping(_ctx: dict[str, Any]) -> str:
    """Zadanie diagnostyczne: potwierdza, że kolejka i worker działają."""
    return "pong"


async def startup(_ctx: dict[str, Any]) -> None:
    get_settings()  # fail fast przy błędnej konfiguracji


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().valkey_url)
    health_check_interval = 30
