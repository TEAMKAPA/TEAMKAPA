"""Pula połączeń asyncpg z kodekiem JSONB."""

import json
from typing import Any

import asyncpg

from backend.shared.config import Settings


class DatabaseNotConfiguredError(RuntimeError):
    pass


#: Wyjątki oznaczające chwilową niedostępność bazy (a nie błąd danych/zapytania).
DB_UNAVAILABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


def database_dsn(settings: Settings) -> str:
    if settings.database_url is None:
        raise DatabaseNotConfiguredError("Brak DATABASE_URL — ustaw dane PostgreSQL w .env")
    return settings.database_url.get_secret_value()


async def _init_connection(connection: Any) -> None:
    await connection.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def create_pool(dsn: str, *, max_size: int = 5, schema: str | None = None) -> asyncpg.Pool:
    """``schema`` ustawia search_path (izolacja testów); domyślnie schemat public."""
    server_settings = {"search_path": schema} if schema else None
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=max_size,
        init=_init_connection,
        server_settings=server_settings,
        command_timeout=30,
    )
    if pool is None:  # pragma: no cover — asyncpg zwraca None tylko przy złej konfiguracji
        raise RuntimeError("Nie udało się utworzyć puli połączeń")
    return pool
