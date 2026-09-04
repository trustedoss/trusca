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


#: The broker keys a worker on ``PROBE_QUEUE`` brings into existence.
PROBE_KEYS = (
    f"_kombu.binding.{PROBE_QUEUE}",
    PROBE_QUEUE,
    # Every Celery worker declares a control queue, and its name says nothing
    # about who declared it. Kept on the list because leaving it behind blocks
    # the next run, and made safe by the second condition rather than the name.
    "_kombu.binding.celery.pidbox",
)


def _redis() -> Any:
    import redis

    return redis.Redis.from_url(os.environ["REDIS_URL"])


def existing_probe_keys() -> set[str]:
    """Which of ``PROBE_KEYS`` are already there before a run."""
    client = _redis()
    return {name for name in PROBE_KEYS if client.exists(name)}


def delete_probe_keys(before: set[str]) -> None:
    """Remove the probe's keys, except any that were already present.

    Two conditions, and both are load-bearing. The name alone would delete a
    control-queue binding somebody else's worker declared, because that name
    identifies a kind of key and not its owner. "Appeared during the run" alone
    would be a licence to delete whatever else happened to show up.

    ``test_the_cleanup_leaves_a_key_it_did_not_create`` holds the second one.
    Without it the restraint is invisible: an implementation that deletes
    everything and one that deletes only its own both leave the index empty,
    which is the only thing the other tests observe.
    """
    client = _redis()
    for name in PROBE_KEYS:
        if name not in before:
            client.delete(name)


@pytest.fixture(autouse=True)
def _remove_this_test_s_own_broker_keys() -> Iterator[None]:
    """Delete the broker keys this file creates, and only those.

    Booting a real worker leaves kombu bindings behind, and the session-start
    check in ``conftest`` refuses a Redis index that still holds keys. Without
    this, running the file twice needs a manual flush in between, and somebody
    who flushes out of habit has disabled the check that exists to stop two
    runs sharing an index.

    This is not the same act as clearing the index, which is what that
    session-start check exists to prevent.
    """
    try:
        before = existing_probe_keys()
    except Exception:  # noqa: BLE001 - a broker we cannot inspect is not a failure
        before = set()

    yield

    try:
        delete_probe_keys(before)
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


def test_the_cleanup_leaves_a_key_it_did_not_create() -> None:
    """The restraint half of the cleanup, which nothing else observes.

    Every other signal here is "the index ended up empty", and that is equally
    true of an implementation that deletes everything. So this plants one of
    the very keys the cleanup is allowed to remove, declares it as already
    present, and requires it to survive.

    The control-queue binding is the one used deliberately: its name is on the
    delete list, so only the "was it already there" condition can save it. When
    that condition was accidentally disabled by a wrong type annotation, the
    suite stayed green and the index still ended up empty.
    """
    _broker_url()
    name = "_kombu.binding.celery.pidbox"
    client = _redis()

    planted = not client.exists(name)
    if planted:
        client.sadd(name, "planted-by-the-restraint-test")
    try:
        assert client.exists(name), "the key to be protected is not there"

        # Exactly what the fixture does, with this key declared pre-existing.
        delete_probe_keys(before={name})

        assert client.exists(name), (
            "the cleanup deleted a key that was already there when the run "
            "started, so it is clearing the index rather than removing what it "
            "made, and a concurrent run's keys would go with it"
        )

        # And the other direction, so the test is not satisfied by a cleanup
        # that deletes nothing at all.
        delete_probe_keys(before=set())
        assert not client.exists(name), (
            "the cleanup left a key it did create, so a second run of this "
            "file is blocked by the session-start check"
        )
    finally:
        client.delete(name)


class _Stopped(BaseException):
    """Stands in for the process ending, so the test can carry on afterwards."""


def test_the_explanation_is_written_before_the_process_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is driven in this process, in order, with the exit stubbed.

    The subprocess tests above prove the worker stops and that its reason
    reaches the output, but they observe the result and not the sequence. The
    sequence is the fragile part: ``os._exit`` skips every buffer, so an
    explanation written after it is an explanation nobody gets, and moving one
    line would produce a worker that dies silently while both subprocess tests
    still pass. It also runs the refusal branch where coverage can see it.
    """
    import logging as logging_module
    import sys as sys_module

    from tasks import task_registry_guard as guard

    order: list[str] = []

    class _Stderr:
        def write(self, text: str) -> int:
            order.append(f"write:{text.split(':')[0]}")
            return len(text)

        def flush(self) -> None:
            order.append("stderr-flush")

    def _shutdown() -> None:
        order.append("logging.shutdown")

    def _exit(code: int) -> None:
        order.append(f"os._exit:{code}")
        raise _Stopped

    monkeypatch.setattr(sys_module, "__stderr__", _Stderr())
    monkeypatch.setattr(logging_module, "shutdown", _shutdown)
    monkeypatch.setattr(os, "_exit", _exit)

    class _EmptyApp:
        tasks: dict[str, object] = {}

        class conf:
            include: list[str] = []

    with pytest.raises(_Stopped):
        guard.refuse_if_no_tasks(_EmptyApp())

    assert order, "the refusal path did not run"
    assert order[-1] == "os._exit:1", (
        f"the process ended before finishing its explanation: {order}"
    )
    assert order.index("write:FATAL task_registry.empty") < order.index(
        "logging.shutdown"
    ) < order.index("os._exit:1"), (
        "the explanation has to be written and the handlers flushed before the "
        f"process ends, and this order does not do that: {order}"
    )


def test_the_healthy_path_touches_none_of_that(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker with tasks must not have its logging shut down on the way past.

    Guards the test above from being satisfied by a function that always
    refuses, and guards the product from a guard that closes the log handlers
    of every healthy worker at boot.
    """
    import logging as logging_module

    from tasks import task_registry_guard as guard

    def _fail(*_: object) -> None:
        raise AssertionError("the healthy path must not reach here")

    monkeypatch.setattr(logging_module, "shutdown", _fail)
    monkeypatch.setattr(os, "_exit", _fail)

    class _App:
        tasks = {"trustedoss.scan_source": object(), "celery.chain": object()}

        class conf:
            include = ["tasks.scan_source"]

    assert guard.refuse_if_no_tasks(_App()) == 1


def test_the_signal_handler_reads_the_real_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, exercised in this process rather than only in a worker.

    A handler that read the wrong app, or a delegation that quietly stopped
    calling the decision, would leave every subprocess test passing for the
    reason it always did: the app the worker builds is the one being checked
    there, and nothing looks at which app this handler picks up.
    """
    from tasks import task_registry_guard as guard
    from tasks.celery_app import celery_app

    seen: list[object] = []

    def _record(app: object) -> int:
        seen.append(app)
        return 1

    monkeypatch.setattr(guard, "refuse_if_no_tasks", _record)
    guard._on_worker_ready()

    assert seen == [celery_app], (
        "the handler checked something other than the application's own Celery "
        "app, so a worker's real registry would go unexamined"
    )


def test_a_stream_that_cannot_be_flushed_does_not_bury_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken stream must not turn a clean stop into a traceback.

    The refusal is what the operator reads. A flush that raises on the way out
    would replace it with a stack trace about the flush, which is the least
    useful thing that could be on the screen at that moment.
    """
    import logging as logging_module
    import sys as sys_module

    from tasks import task_registry_guard as guard

    class _Broken:
        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            raise OSError("stream is gone")

    monkeypatch.setattr(sys_module, "__stderr__", _Broken())
    monkeypatch.setattr(sys_module, "stdout", _Broken())
    monkeypatch.setattr(sys_module, "stderr", _Broken())
    monkeypatch.setattr(logging_module, "shutdown", lambda: None)

    exits: list[int] = []

    def _exit(code: int) -> None:
        exits.append(code)
        raise _Stopped

    monkeypatch.setattr(os, "_exit", _exit)

    class _EmptyApp:
        tasks: dict[str, object] = {}

        class conf:
            include: list[str] = []

    with pytest.raises(_Stopped):
        guard.refuse_if_no_tasks(_EmptyApp())

    assert exits == [1], "the broken stream stopped the refusal from completing"
