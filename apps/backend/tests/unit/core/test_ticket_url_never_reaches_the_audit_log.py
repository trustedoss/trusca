# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A credential pasted into ``ticket_url`` must not land in ``audit_logs``.

`vulnerability_findings` is audited by default (`core.audit` denies by table,
not allows), so ER28a's columns are captured without anyone registering them.
That is wanted for `assignee_user_id` and `due_on`. It is a problem for
`ticket_url`, because `https://user:token@tracker/ABC-1` is a valid https URL:
the scheme check accepts it, and `audit_logs` forbids UPDATE by trigger, so a
token written there cannot be removed afterwards.

Two layers, asserted separately
-------------------------------
The input check refuses userinfo, and the audit masking strips it. The input
check runs first, so in normal operation the masking is never reached, and a
layer that is never reached is one whose removal nothing notices. So this file
calls the masking directly rather than through the API.

Each layer answers a different question. The input check stops new rows. The
masking covers rows written before it existed, and any later decision that a
credentialed tracker URL is legitimate after all.
"""

from __future__ import annotations

import pytest

from core.audit import _URL_REDACT_COLUMNS, mask_sensitive_columns
from services.vulnerability_service import (
    VulnerabilityAssignmentInvalid,
    _validate_ticket_url,
)

CREDENTIALLED = "https://bot:ghp_supersecret@jira.example.com/browse/SEC-1"
SECRET = "ghp_supersecret"


# --- layer 1: the value never gets in ---------------------------------------


def test_a_ticket_url_carrying_a_credential_is_refused() -> None:
    with pytest.raises(VulnerabilityAssignmentInvalid) as caught:
        _validate_ticket_url(CREDENTIALLED)
    # The rejection must not echo the value it rejected: that would move the
    # token into the response body and the caller's logs.
    assert SECRET not in str(caught.value)


def test_an_at_sign_in_the_path_is_still_allowed() -> None:
    """Only the authority is checked. A ticket key can legitimately contain an
    `@`, and rejecting those would be a guard that fires on innocent input."""
    url = "https://tracker.example.com/issues/a@b"
    assert _validate_ticket_url(url) == url


# --- layer 2: and if one did, the audit trail would not keep it -------------


def test_ticket_url_is_registered_for_url_redaction() -> None:
    assert "ticket_url" in _URL_REDACT_COLUMNS


def test_the_audit_diff_strips_userinfo_from_a_ticket_url() -> None:
    """Called directly, because the input check means this path is not
    reachable through the API. Reached only by a row written before that check,
    or after somebody relaxes it."""
    masked = mask_sensitive_columns({"ticket_url": CREDENTIALLED})
    assert SECRET not in masked["ticket_url"]
    assert "bot" not in masked["ticket_url"]
    # Host and path survive: the audit trail's job is to say which ticket the
    # finding pointed at, which a blanket "***" would destroy.
    assert "jira.example.com" in masked["ticket_url"]
    assert "SEC-1" in masked["ticket_url"]


def test_an_ordinary_ticket_url_is_left_intact() -> None:
    """Masking that mangles clean values would push people to stop recording
    tickets, which costs the audit trail more than it saves."""
    clean = "https://jira.example.com/browse/SEC-1"
    assert mask_sensitive_columns({"ticket_url": clean})["ticket_url"] == clean


def test_the_other_new_columns_are_not_masked() -> None:
    """`assignee_user_id` and `due_on` are exactly what an audit reader needs;
    redacting them would defeat the reason findings are audited."""
    payload = {"assignee_user_id": "a-uuid", "due_on": "2026-09-07"}
    assert mask_sensitive_columns(payload) == payload
