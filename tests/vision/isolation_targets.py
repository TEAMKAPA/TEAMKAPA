"""Funkcje uruchamiane w procesie potomnym w testach izolacji (muszą być na poziomie modułu)."""

import os
import time
from collections.abc import Iterator

from backend.worker.vision.errors import IssueCode, RenderError


def count_up(n: int) -> Iterator[int]:
    yield from range(n)


def hang_after_first(_: object) -> Iterator[int]:
    yield 1
    time.sleep(60)
    yield 2


def die_after_first(_: object) -> Iterator[int]:
    yield 1
    os._exit(3)  # nagła śmierć procesu (jak segfault w bibliotece C)


def die_immediately(_: object) -> Iterator[int]:
    os._exit(5)
    yield 0  # pragma: no cover


def raise_render_error(_: object) -> Iterator[int]:
    yield 1
    raise RenderError(IssueCode.PDF_UNREADABLE, "page=2 geometry")


def raise_other(_: object) -> Iterator[int]:
    raise ValueError("TREŚĆ DOKUMENTU, KTÓRA NIE MOŻE TRAFIĆ DO LOGÓW")
    yield 0  # pragma: no cover
