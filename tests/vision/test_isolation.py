"""Proces potomny: wyniki, limit czasu, nagła śmierć procesu, sprzątanie."""

import multiprocessing
import time
from collections.abc import Callable, Iterator

import pytest

from backend.worker.vision.errors import IssueCode, RenderError
from backend.worker.vision.render.isolation import IsolationFailure, run_isolated
from tests.vision import isolation_targets as t

STARTUP = 60.0


def _no_children_left() -> bool:
    deadline = time.monotonic() + 5
    while multiprocessing.active_children() and time.monotonic() < deadline:
        time.sleep(0.05)
    return not multiprocessing.active_children()


def test_items_are_returned_in_order() -> None:
    items = list(run_isolated(t.count_up, 5, item_timeout_s=10, startup_timeout_s=STARTUP))
    assert items == [0, 1, 2, 3, 4]
    assert _no_children_left()


def test_timeout_kills_child() -> None:
    received = []
    start = time.monotonic()
    with pytest.raises(IsolationFailure) as info:
        for item in run_isolated(
            t.hang_after_first, None, item_timeout_s=0.5, startup_timeout_s=STARTUP
        ):
            received.append(item)
    assert received == [1]
    assert info.value.code is IssueCode.PAGE_RENDER_TIMEOUT
    assert info.value.step == 2
    assert time.monotonic() - start < 30  # nie czekamy na 60 s snu w procesie potomnym
    assert _no_children_left()


@pytest.mark.parametrize(("target", "step"), [(t.die_after_first, 2), (t.die_immediately, 1)])
def test_abrupt_death_is_reported_as_crash(
    target: Callable[[object], Iterator[int]], step: int
) -> None:
    start = time.monotonic()
    with pytest.raises(IsolationFailure) as info:
        list(run_isolated(target, None, item_timeout_s=30, startup_timeout_s=STARTUP))
    assert info.value.code is IssueCode.PAGE_RENDER_CRASHED
    assert info.value.step == step
    assert "exitcode=" in info.value.detail
    assert time.monotonic() - start < 25  # awaria wykryta od razu, nie po limicie czasu
    assert _no_children_left()


def test_render_error_code_is_propagated() -> None:
    with pytest.raises(RenderError) as info:
        list(run_isolated(t.raise_render_error, None, item_timeout_s=10, startup_timeout_s=STARTUP))
    assert not isinstance(info.value, IsolationFailure)
    assert (info.value.code, info.value.detail) == (IssueCode.PDF_UNREADABLE, "page=2 geometry")


def test_other_exception_reports_only_type_name() -> None:
    with pytest.raises(IsolationFailure) as info:
        list(run_isolated(t.raise_other, None, item_timeout_s=10, startup_timeout_s=STARTUP))
    assert info.value.code is IssueCode.PAGE_RENDER_CRASHED
    assert info.value.detail == "exception=ValueError"
    assert "TREŚĆ" not in str(info.value)


def test_closing_generator_early_kills_child() -> None:
    gen = run_isolated(t.hang_after_first, None, item_timeout_s=30, startup_timeout_s=STARTUP)
    assert next(gen) == 1
    gen.close()
    assert _no_children_left()
