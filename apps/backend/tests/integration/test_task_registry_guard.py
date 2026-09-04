# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A worker with none of our tasks stops, and says why (ER61).

What is asserted is what happens to a real process, not what happens to a
function. The obvious test drives the signal and checks the handler was
called, and it passes whether the worker stops or not: Celery catches
exceptions raised from signal handlers, so a guard that reads as a refusal can
behave as a warning and nothing notices. Measured before writing this, a
``RuntimeError`` from ``worker_ready`` leaves the worker running, and both
``WorkerShutdown`` and ``sys.exit(1)`` stop it with exit code zero.

So these boot an actual worker in a subprocess and read its exit code.

Both directions are checked. Without the second one, "always exits" would pass
too, and a guard that stops every worker is a worse outage than the one it is
for.

The output is checked as well as the code. ``os._exit`` skips flushing, so a
guard that stops the process and loses its own explanation would still exit 1
and still be useless: the operator would have a worker that dies on boot with
nothing saying why. The order in the guard exists for that, and this is what
holds it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tasks.task_registry_guard import (
    TASK_PREFIX,
    refuse_if_no_tasks,
    registered_task_count,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).resolve().parent / "_taskless_worker.py"

#: The queue the probe worker declares. Named here because the cleanup below
#: deletes by name.
PROBE_QUEUE = "er61-probe"

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _remove_this_test_s_own_broker_keys() -> Iterator[None]:
    """Delete the broker keys this file creates, and only those.

    Booting a real worker leaves kombu bindings behind, and the session-start
    check in ``conftest`` refuses a Redis index that still holds keys. Without
    this, running the file twice needs a manual flush in between, and somebody
    who flushes out of habit has disabled the check that exists to stop two
    runs sharing an index.

    Two conditions together, rather than either alone. The key has to be one of
    the names a worker on this queue produces, AND it has to have appeared
    while this test ran. The name alone is not enough for the control-queue
    binding, whose name says nothing about who made it; "new since the test
    started" alone would be a licence to delete whatever else showed up.

    This is not the same act as clearing the index, which is what the
    session-start check exists to prevent.
    """
    names = [
        f"_kombu.binding.{PROBE_QUEUE}",
        PROBE_QUEUE,
        # Every Celery worker declares a control queue. Generic, so it is only
        # removed when this test is what brought it into existence.
        "_kombu.binding.celery.pidbox",
    ]

    def _client() -> Any:
        import redis

        return redis.Redis.from_url(os.environ["REDIS_URL"])

    before: set[str] = set()
    try:
        client = _client()
        before = {name for name in names if client.exists(name)}  # type: ignore[misc]
    except Exception:  # noqa: BLE001 - a broker we cannot inspect is not a failure
        before = set()

    yield

    try:
        client = _client()
        for name in names:
            if name not in before:
                client.delete(name)
    except Exception:  # noqa: BLE001 - cleanup must not turn a pass into a failure
        pass


def _broker_url() -> str:
    url = os.getenv("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL is not set, so a worker cannot be booted")
    return url


def _run_worker(mode: str, timeout: float = 45.0) -> subprocess.CompletedProcess[str]:
    """Boot the probe worker. PYTHONPATH, not cwd.

    Running a script by path puts the script's directory on ``sys.path``, not
    the working directory, so ``cwd`` alone leaves ``tasks`` unimportable. The
    first version did that and the subprocess died with ModuleNotFoundError
    before reaching the guard, which is exit 1: the exit-code test passed on a
    crash that had nothing to do with the feature. The output assertion below
    is what caught it.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    return subprocess.run(
        [sys.executable, str(PROBE), mode, _broker_url()],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=BACKEND_ROOT,
        env=env,
        check=False,
    )


def test_a_worker_with_none_of_our_tasks_stops_with_a_failure_code() -> None:
    result = _run_worker("empty")

    assert result.returncode != 0, (
        "the worker stayed up, or stopped claiming success. Both leave a "
        "deployment that silently discards every scan it is sent."
    )
    assert result.returncode == 1, (
        f"expected exit 1, got {result.returncode}. Exit 0 in particular tells "
        "an operator and any monitoring reading exit codes that a worker which "
        "cannot run a single task finished normally."
    )


def test_the_stopped_worker_says_why() -> None:
    """The diagnosis is the whole point, and os._exit does not flush.

    Delete the flushing in the guard and the exit code stays 1 while the
    operator gets a worker that dies on boot with nothing explaining it.
    """
    result = _run_worker("empty")
    output = result.stdout + result.stderr

    assert "task_registry.empty" in output, (
        "the guard stopped the worker without its explanation reaching the "
        "log, so nothing says why the container is restarting"
    )
    assert "include" in output, (
        "the message does not point at the include list, which is the only "
        "place the cause can be"
    )


def test_a_worker_that_has_tasks_keeps_running() -> None:
    """Without this, a guard that stops every worker would pass the file.

    The worker is expected to still be alive when the timeout fires, which is
    what ``TimeoutExpired`` here means.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _run_worker("populated", timeout=20.0)


def test_the_count_ignores_celery_own_tasks() -> None:
    """Celery registers builtins on every app, so "no tasks at all" never happens.

    The counting rule is exercised against a registry written here rather than
    against a fresh ``Celery()``. A new app is not empty of our names inside a
    test session: anything declared with ``@shared_task`` anywhere attaches to
    every app, so asserting a bare app counts zero measures what the session
    happened to import.
    """
    from celery import Celery

    # Named rather than read off a live app: inside a test session a new app
    # already carries whatever the session declared with ``@shared_task``, so
    # "everything on a bare app" is not "Celery's builtins". Asserting they are
    # present keeps the check below from passing on an empty list.
    builtins = ["celery.chain", "celery.group", "celery.chord"]
    present = set(Celery("bare").tasks)
    missing = [name for name in builtins if name not in present]
    assert not missing, (
        f"Celery no longer registers {missing} on every app. If it ships no "
        "builtins at all, a plain count would work and the prefix rule could "
        "be revisited."
    )
    assert all(not name.startswith(TASK_PREFIX) for name in builtins), (
        f"a Celery builtin now starts with {TASK_PREFIX}, so the prefix no "
        "longer separates ours from theirs"
    )

    class _Registry:
        def __init__(self, names: list[str]) -> None:
            self.tasks = {n: object() for n in names}

    assert registered_task_count(_Registry(builtins)) == 0
    assert registered_task_count(_Registry([*builtins, "trustedoss.scan_source"])) == 1
    assert registered_task_count(_Registry([])) == 0


def test_the_guard_returns_the_count_when_there_are_tasks() -> None:
    """The healthy path returns rather than exiting, and reports what it saw."""
    from tasks.celery_app import celery_app

    celery_app.loader.import_default_modules()
    count = refuse_if_no_tasks(celery_app)

    assert count == registered_task_count(celery_app)
    assert count > 20, (
        f"only {count} tasks are registered after loading the include list; "
        "either the list shrank or this is not loading it"
    )
