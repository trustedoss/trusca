"""
Unit tests for the audit log SQLAlchemy event listener.

The listener must:
1. Hook into INSERT/UPDATE/DELETE on every mapped class except AuditLog itself
   (otherwise we recurse forever).
2. Capture the actor user_id, request_id, ip, user-agent from contextvars at
   commit time (the audit context is bound by the request middleware).
3. Mask PII fields (password_hash, etc.) before persisting the diff payload.
"""

from __future__ import annotations

import pytest


def test_audit_context_setters_and_getters_round_trip():
    from core.audit import audit_context, get_audit_context

    audit_context.set(
        {
            "user_id": "11111111-1111-1111-1111-111111111111",
            "team_id": None,
            "request_id": "req-abc",
            "ip": "10.0.0.1",
            "user_agent": "pytest/1.0",
        }
    )
    snapshot = get_audit_context()
    assert snapshot["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert snapshot["request_id"] == "req-abc"
    assert snapshot["ip"] == "10.0.0.1"
    assert snapshot["user_agent"] == "pytest/1.0"


def test_audit_context_defaults_when_unbound():
    from core.audit import audit_context, get_audit_context

    audit_context.set({})  # empty = unauthenticated background task
    snapshot = get_audit_context()
    assert snapshot.get("user_id") is None
    assert snapshot.get("request_id") is None


def test_mask_sensitive_columns_removes_password_hash():
    from core.audit import mask_sensitive_columns

    raw = {
        "id": "abc",
        "email": "user@example.com",
        "hashed_password": "$2b$12$...",
        "password": "raw-secret",
        "refresh_token_hash": "abcdef",
    }
    masked = mask_sensitive_columns(raw)

    assert "hashed_password" not in masked or masked["hashed_password"] == "***"
    assert "password" not in masked or masked["password"] == "***"
    assert "refresh_token_hash" not in masked or masked["refresh_token_hash"] == "***"
    # PII columns (email, full_name) are sha256-hashed (security-reviewer F4 /
    # CWE-359). The plaintext must NOT be retained; the hash dict carries the
    # "what changed" semantics for forensics without storing PII at rest.
    assert masked["email"] != "user@example.com"
    assert isinstance(masked["email"], dict)
    assert "sha256" in masked["email"]


def test_listener_skips_audit_log_table():
    """Recursion guard: AuditLog inserts must not trigger another audit row."""
    from core.audit import is_audited_table

    assert is_audited_table("users") is True
    assert is_audited_table("teams") is True
    assert is_audited_table("audit_logs") is False


def test_report_downloads_is_not_audited():
    """M-36: SBOM/NOTICE export leaves a structlog line, not an audit_logs row.

    The export records a report_downloads access row; that INSERT must not trip
    the audit listener (the spec promises no audit_logs row for an export).
    """
    from core.audit import is_audited_table

    assert is_audited_table("report_downloads") is False


def test_status_transition_diff_carries_previous_and_new(monkeypatch):
    """M-7: a status change adds previous_status / new_status to the diff."""
    from core import audit

    class _Hist:
        deleted = ("pending",)

    class _Attr:
        history = _Hist()

    class _Attrs:
        def __getitem__(self, key):
            assert key == "status"
            return _Attr()

    class _State:
        attrs = _Attrs()

    monkeypatch.setattr(audit, "inspect", lambda _instance: _State())
    out = audit._augment_status_transition(object(), {"status": "under_review"})
    assert out["previous_status"] == "pending"
    assert out["new_status"] == "under_review"
    assert out["status"] == "under_review"  # original key preserved

    # No-op when status did not change.
    unchanged = audit._augment_status_transition(object(), {"name": "x"})
    assert "previous_status" not in unchanged


@pytest.mark.parametrize("op", ["insert", "update", "delete"])
def test_listener_records_each_mutating_op(op):
    from core.audit import build_audit_action

    action = build_audit_action(op)
    assert action in ("create", "update", "delete")
