"""
Celery dispatcher — `tasks.enqueue_scan`.

The dispatcher branches on `scan.kind` and shells out to the appropriate
Celery task's `.delay(...)`. Tests monkeypatch the `.delay` callable so the
broker / Redis are not touched. The unit test does NOT require Postgres or
a Celery worker — it pins the kind→task routing at the import site.
"""

from __future__ import annotations

import uuid

import pytest


def test_enqueue_scan_routes_source_kind_to_scan_source_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks import enqueue_scan

    invocations: list[tuple[str, str]] = []

    class _AsyncResult:
        id = "fake-task-id"

    # PR-A1: the dispatcher now uses apply_async(args=..., soft_time_limit=...,
    # time_limit=...) so scan tasks are time-boxed. We accept the kwargs and
    # record the dispatch.
    def fake_source_apply_async(
        *, args: tuple[str, ...], soft_time_limit: int, time_limit: int
    ) -> _AsyncResult:
        invocations.append(("source", args[0]))
        return _AsyncResult()

    monkeypatch.setattr(
        "tasks.scan_source.scan_source_task.apply_async",
        fake_source_apply_async,
    )

    class _Scan:
        id = uuid.uuid4()
        kind = "source"

    task_id = enqueue_scan(_Scan())  # type: ignore[arg-type]
    assert task_id == "fake-task-id"
    assert invocations and invocations[0][0] == "source"


def test_enqueue_scan_routes_container_kind_to_scan_container_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks import enqueue_scan

    invocations: list[tuple[str, str]] = []

    class _AsyncResult:
        id = "fake-container-id"

    def fake_container_apply_async(
        *, args: tuple[str, ...], soft_time_limit: int, time_limit: int
    ) -> _AsyncResult:
        invocations.append(("container", args[0]))
        return _AsyncResult()

    monkeypatch.setattr(
        "tasks.scan_container.scan_container_task.apply_async",
        fake_container_apply_async,
    )

    class _Scan:
        id = uuid.uuid4()
        kind = "container"

    task_id = enqueue_scan(_Scan())  # type: ignore[arg-type]
    assert task_id == "fake-container-id"
    assert invocations and invocations[0][0] == "container"


def test_enqueue_scan_rejects_unknown_kind() -> None:
    from tasks import enqueue_scan

    class _Scan:
        id = uuid.uuid4()
        kind = "binary"  # not a real kind

    with pytest.raises(ValueError):
        enqueue_scan(_Scan())  # type: ignore[arg-type]
