"""Uruchamianie generatora w procesie potomnym z limitem czasu na każdy komunikat.

Po co: renderowania w C (PDFium) nie da się przerwać w tym samym procesie, a złośliwy lub uszkodzony
plik może zawiesić bibliotekę albo zakończyć proces (segfault). Proces potomny (``spawn``) izoluje
worker: przekroczenie czasu kończy się ``PAGE_RENDER_TIMEOUT`` i zabiciem procesu, a nagła śmierć
procesu ``PAGE_RENDER_CRASHED``. Proces potomny jest zawsze sprzątany (``finally``).

Protokół (proces potomny → nadrzędny): ``("ready", None)``, potem dowolnie wiele ``("item", x)``,
na końcu ``("done", None)`` albo ``("error", (kod, szczegóły))`` dla ``RenderError``,
albo ``("fail", nazwa_wyjątku)`` dla innego wyjątku (bez komunikatu, który mógłby zawierać treść).
"""

import multiprocessing
from collections.abc import Callable, Iterable, Iterator
from multiprocessing.connection import Connection, wait
from typing import Any, Protocol, cast

from backend.worker.vision.errors import IssueCode, RenderError

Target = Callable[[Any], Iterable[Any]]

_JOIN_TIMEOUT_S = 5.0


class _Receiver(Protocol):
    """Wspólny interfejs ``Connection`` (POSIX) i ``PipeConnection`` (Windows)."""

    def poll(self, timeout: float | None = ...) -> bool: ...

    def recv(self) -> Any: ...


def _child_main(conn: Connection, target: Target, payload: Any) -> None:
    try:
        conn.send(("ready", None))
        for item in target(payload):
            conn.send(("item", item))
        conn.send(("done", None))
    except RenderError as exc:
        conn.send(("error", (exc.code.value, exc.detail)))
    except Exception as exc:  # każdy inny wyjątek to awaria renderowania
        conn.send(("fail", type(exc).__name__))
    finally:
        conn.close()


class IsolationFailure(RenderError):
    """Przekroczenie czasu lub awaria procesu; ``step``: numer oczekiwanego elementu (od 1)."""

    def __init__(self, code: IssueCode, step: int, detail: str) -> None:
        self.step = step
        super().__init__(code, detail)


def run_isolated(
    target: Target,
    payload: Any,
    *,
    item_timeout_s: float,
    startup_timeout_s: float,
) -> Iterator[Any]:
    """Zwraca kolejne elementy generatora ``target(payload)`` uruchomionego w procesie potomnym.

    ``target`` musi być funkcją najwyższego poziomu modułu (wymóg ``spawn``).
    """
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_child_main, args=(sender, target, payload), daemon=True)
    process.start()
    sender.close()
    try:
        kind, _ = _receive(receiver, process, startup_timeout_s, step=0)
        if kind != "ready":
            raise IsolationFailure(IssueCode.PAGE_RENDER_CRASHED, 0, "protocol")
        step = 0
        while True:
            step += 1
            kind, value = _receive(receiver, process, item_timeout_s, step=step)
            if kind == "item":
                yield value
            elif kind == "done":
                return
            elif kind == "error":
                code, detail = value
                raise RenderError(IssueCode(code), detail)
            else:
                raise IsolationFailure(IssueCode.PAGE_RENDER_CRASHED, step, f"exception={value}")
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join(_JOIN_TIMEOUT_S)


def _receive(
    conn: _Receiver, process: multiprocessing.process.BaseProcess, timeout_s: float, *, step: int
) -> tuple[str, Any]:
    """Czeka na komunikat albo na zakończenie procesu (co nastąpi pierwsze).

    Czekanie na sentinel procesu jest konieczne: na Windows proces, który zginie przed odebraniem
    połączenia, nie zamyka uchwytu pipe'a, więc sam ``poll`` czekałby do końca limitu czasu.
    """
    ready = wait([cast(Connection, conn), process.sentinel], timeout_s)
    if not ready:
        raise IsolationFailure(IssueCode.PAGE_RENDER_TIMEOUT, step, f"timeout={timeout_s:g}s")
    # Dane mają pierwszeństwo: proces mógł wysłać wynik i zakończyć się. Zerwany pipe objawia się
    # jako EOFError (POSIX) albo BrokenPipeError/OSError już przy poll (Windows): to awaria procesu.
    try:
        if conn.poll(0):
            message: tuple[str, Any] = conn.recv()
            return message
    except EOFError, OSError:
        pass
    process.join(_JOIN_TIMEOUT_S)
    raise IsolationFailure(IssueCode.PAGE_RENDER_CRASHED, step, f"exitcode={process.exitcode}")
