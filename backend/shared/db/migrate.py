"""Migracje schematu: kolejne pliki ``db/migrations/NNN_nazwa.sql`` stosowane raz, w kolejności.

- każda migracja w osobnej transakcji, zapis w ``schema_migrations`` z sumą SHA-256,
- zmiana treści zastosowanej migracji zatrzymuje proces (migracje są niezmienne —
  zmiany schematu tylko nowym plikiem),
- blokada doradcza chroni przed równoległym uruchomieniem.

Uruchomienie: ``python -m backend.shared.db.migrate`` (w compose: jednorazowa usługa ``migrate``).
"""

import asyncio
import hashlib
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.shared.config import get_settings
from backend.shared.db.pool import create_pool, database_dsn

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
_FILENAME_RE = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")
_LOCK_ID = 720_261_115  # stała blokady doradczej migracji


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"Nieprawidłowa nazwa migracji: {path.name} (wymagane NNN_nazwa.sql)"
            )
        migrations.append(Migration(match["version"], path.name, path.read_text(encoding="utf-8")))
    versions = [m.version for m in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"Powtórzony numer migracji w {directory}")
    return migrations


async def apply_migrations(pool: Any, migrations: list[Migration]) -> list[str]:
    """Stosuje brakujące migracje; zwraca nazwy zastosowanych w tym uruchomieniu."""
    applied_now: list[str] = []
    async with pool.acquire() as connection:
        await connection.execute("SELECT pg_advisory_lock($1)", _LOCK_ID)
        try:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    text PRIMARY KEY,
                    name       text NOT NULL,
                    checksum   text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            rows = await connection.fetch("SELECT version, name, checksum FROM schema_migrations")
            applied = {row["version"]: row for row in rows}
            for migration in migrations:
                existing = applied.get(migration.version)
                if existing is not None:
                    if existing["checksum"] != migration.checksum:
                        raise MigrationError(
                            f"Migracja {migration.name} została zmieniona po zastosowaniu — "
                            "zmiany schematu dodawaj nowym plikiem migracji"
                        )
                    continue
                async with connection.transaction():
                    await connection.execute(migration.sql)
                    await connection.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) "
                        "VALUES ($1, $2, $3)",
                        migration.version,
                        migration.name,
                        migration.checksum,
                    )
                applied_now.append(migration.name)
                logger.info("Zastosowano migrację %s", migration.name)
        finally:
            await connection.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)
    return applied_now


async def _main() -> int:
    settings = get_settings()
    pool = await create_pool(database_dsn(settings), max_size=1)
    try:
        applied = await apply_migrations(pool, load_migrations())
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1
    finally:
        await pool.close()
    logger.info("Schemat aktualny (nowe migracje: %d)", len(applied))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(_main()))
