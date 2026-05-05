"""
Unit tests for the Celery application factory.

Phase 0: just enough coverage to assert the factory is wired correctly and
respects CLAUDE.md core rule #11 (environment variables read at runtime, not
import time).
"""

from __future__ import annotations

import os

import pytest
from celery import Celery

from tasks.celery_app import celery_app, create_celery_app


def test_celery_app_singleton_is_configured() -> None:
    assert isinstance(celery_app, Celery)
    assert celery_app.main == "trustedoss"
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_default_queue == "trustedoss.default"
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True


def test_create_celery_app_reads_redis_url_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLAUDE.md core rule #11: env vars are read at runtime, not module load.

    Flipping REDIS_URL between two factory calls must produce two apps with
    different broker URLs; if anything had cached at import time the second
    call would still see the first value.
    """
    monkeypatch.setenv("REDIS_URL", "redis://example-a:6379/0")
    app_a = create_celery_app()
    assert app_a.conf.broker_url == "redis://example-a:6379/0"
    assert app_a.conf.result_backend == "redis://example-a:6379/0"

    monkeypatch.setenv("REDIS_URL", "redis://example-b:6379/1")
    app_b = create_celery_app()
    assert app_b.conf.broker_url == "redis://example-b:6379/1"
    assert app_b.conf.result_backend == "redis://example-b:6379/1"

    assert app_a is not app_b


def test_create_celery_app_uses_redis_url_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6390/3")
    app = create_celery_app()
    expected = os.environ["REDIS_URL"]
    assert app.conf.broker_url == expected
