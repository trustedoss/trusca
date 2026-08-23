"""
Unit tests for W9 (concurrency-scaling-plan-2026-08-22.md §3.5) retention
config accessors: defaults, env override, non-negative clamping, and
read-at-call-time (CLAUDE.md core rule #11: no module-level caching).

Six settings across three modules:
  - ``tasks.auth_token_retention``: refresh + password-reset token grace.
  - ``tasks.operational_retention``: notification / webhook-delivery /
    report-download age.
  - ``tasks.audit_log_retention`` (local passthrough) and
    ``core.config.audit_log_retention_days`` (the canonical accessor):
    audit-log purge-readiness age.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# tasks.auth_token_retention
# ---------------------------------------------------------------------------


def test_refresh_token_grace_days_default_is_one() -> None:
    from tasks.auth_token_retention import _refresh_token_grace_days

    assert _refresh_token_grace_days() == 1


def test_password_reset_token_grace_days_default_is_one() -> None:
    from tasks.auth_token_retention import _password_reset_token_grace_days

    assert _password_reset_token_grace_days() == 1


def test_refresh_token_grace_days_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks.auth_token_retention import _refresh_token_grace_days

    monkeypatch.setenv("REFRESH_TOKEN_RETENTION_GRACE_DAYS", "3")
    assert _refresh_token_grace_days() == 3

    monkeypatch.setenv("REFRESH_TOKEN_RETENTION_GRACE_DAYS", "0")
    assert _refresh_token_grace_days() == 0


def test_refresh_token_grace_days_clamped_non_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks.auth_token_retention import _refresh_token_grace_days

    monkeypatch.setenv("REFRESH_TOKEN_RETENTION_GRACE_DAYS", "-5")
    assert _refresh_token_grace_days() == 0


def test_password_reset_token_grace_days_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasks.auth_token_retention import _password_reset_token_grace_days

    monkeypatch.setenv("PASSWORD_RESET_TOKEN_RETENTION_GRACE_DAYS", "5")
    assert _password_reset_token_grace_days() == 5


# ---------------------------------------------------------------------------
# tasks.operational_retention
# ---------------------------------------------------------------------------


def test_notification_retention_days_default_is_180() -> None:
    from tasks.operational_retention import _notification_retention_days

    assert _notification_retention_days() == 180


def test_webhook_delivery_retention_days_default_is_90() -> None:
    from tasks.operational_retention import _webhook_delivery_retention_days

    assert _webhook_delivery_retention_days() == 90


def test_report_download_retention_days_default_is_365() -> None:
    from tasks.operational_retention import _report_download_retention_days

    assert _report_download_retention_days() == 365


@pytest.mark.parametrize(
    ("env_name", "accessor_name"),
    [
        ("NOTIFICATION_RETENTION_DAYS", "_notification_retention_days"),
        ("WEBHOOK_DELIVERY_RETENTION_DAYS", "_webhook_delivery_retention_days"),
        ("REPORT_DOWNLOAD_RETENTION_DAYS", "_report_download_retention_days"),
    ],
)
def test_operational_retention_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch, env_name: str, accessor_name: str
) -> None:
    import tasks.operational_retention as mod

    accessor = getattr(mod, accessor_name)

    monkeypatch.setenv(env_name, "30")
    first = accessor()
    monkeypatch.setenv(env_name, "60")
    second = accessor()

    assert first == 30
    assert second == 60


@pytest.mark.parametrize(
    "accessor_name",
    [
        "_notification_retention_days",
        "_webhook_delivery_retention_days",
        "_report_download_retention_days",
    ],
)
def test_operational_retention_clamped_non_negative(
    monkeypatch: pytest.MonkeyPatch, accessor_name: str
) -> None:
    import tasks.operational_retention as mod

    env_names = {
        "_notification_retention_days": "NOTIFICATION_RETENTION_DAYS",
        "_webhook_delivery_retention_days": "WEBHOOK_DELIVERY_RETENTION_DAYS",
        "_report_download_retention_days": "REPORT_DOWNLOAD_RETENTION_DAYS",
    }
    monkeypatch.setenv(env_names[accessor_name], "-10")
    assert getattr(mod, accessor_name)() == 0


# ---------------------------------------------------------------------------
# tasks.audit_log_retention / core.config.audit_log_retention_days
# ---------------------------------------------------------------------------


def test_audit_log_retention_days_default_is_90() -> None:
    from core.config import audit_log_retention_days

    assert audit_log_retention_days() == 90


def test_audit_log_retention_days_matches_auditlog_model_docstring_default() -> None:
    """Regression guard: the AuditLog model docstring has quoted "90 days"
    since Phase 5 (models/auth.py). This is the first code to read that
    figure; a future edit that changes one without the other should fail
    loudly rather than silently drift.
    """
    from core.config import audit_log_retention_days
    from models.auth import AuditLog

    assert audit_log_retention_days() == 90
    assert "90 days" in (AuditLog.__doc__ or "")


def test_audit_log_retention_days_reads_env_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import audit_log_retention_days

    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "30")
    assert audit_log_retention_days() == 30

    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "365")
    assert audit_log_retention_days() == 365


def test_audit_log_retention_days_clamped_non_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.config import audit_log_retention_days

    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "-1")
    assert audit_log_retention_days() == 0


def test_audit_log_retention_task_local_accessor_matches_core_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task module keeps a thin local passthrough (same convention as
    tasks.scan_retention's local env accessors); it must not drift from the
    canonical core.config value."""
    from core.config import audit_log_retention_days
    from tasks.audit_log_retention import _retention_days

    monkeypatch.setenv("AUDIT_LOG_RETENTION_DAYS", "45")
    assert _retention_days() == audit_log_retention_days() == 45
