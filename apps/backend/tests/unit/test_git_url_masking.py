"""C-2 guard — an embedded git_url credential must never leak across surfaces.

Pattern P2 (uniform sensitive-data masking). A private repo may legitimately
carry a PAT as ``https://<token>@github.com/org/repo`` — the documented inline
mechanism the clone path honours. The token must be stripped on every OUTBOUND
surface: the read API (``ProjectPublic`` serialization) and the audit diff
(``mask_sensitive_columns``). The project-update path must also refuse to
round-trip a masked value back into storage.

These are pure-logic assertions (no DB / Redis). The adversarial parametrize
covers the userinfo shapes an attacker or careless operator might supply.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from core.audit import mask_sensitive_columns
from core.pii_mask import (
    mask_git_url,
    url_userinfo_is_redacted,
)
from schemas.scan import ProjectPublic

# (raw git_url, the substring that must be GONE from every masked surface)
_CREDENTIALED_URLS = [
    ("https://ghp_SECRETTOKEN123456@github.com/org/repo.git", "ghp_SECRETTOKEN123456"),
    ("https://oauth2:glpat-XXXXXXXX@gitlab.com/g/p.git", "glpat-XXXXXXXX"),
    ("https://user:p%40ss-word@example.com/a/b", "p%40ss-word"),
    ("https://x-access-token:ghs_aaaaaa@github.com/o/r", "ghs_aaaaaa"),
]

# URLs that must pass through byte-for-byte unchanged: http/https with no
# userinfo, and ssh/scp forms whose ``git@host`` user is conventional, not a
# secret (scheme-aware masking leaves them alone).
_CLEAN_URLS = [
    "https://github.com/org/repo.git",
    "https://gitlab.com:8443/group/project",
    "ssh://git@github.com/org/repo.git",
    "git@github.com:org/repo.git",
]


def _project_public(git_url: str | None) -> dict:
    model = ProjectPublic(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="p",
        slug="p",
        description=None,
        git_url=git_url,
        default_branch=None,
        visibility="team",
        archived_at=None,
        created_by_user_id=None,
        latest_scan_id=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return model.model_dump(mode="json")


@pytest.mark.parametrize("raw, secret", _CREDENTIALED_URLS)
def test_read_api_masks_git_url_userinfo(raw: str, secret: str) -> None:
    dumped = _project_public(raw)
    assert secret not in dumped["git_url"]
    assert "***@" in dumped["git_url"]
    # host/path survive so the field stays meaningful.
    host = raw.split("@", 1)[1].split("/", 1)[0].split(":", 1)[0]
    assert host in dumped["git_url"]


@pytest.mark.parametrize("raw, secret", _CREDENTIALED_URLS)
def test_audit_diff_redacts_git_url_userinfo(raw: str, secret: str) -> None:
    masked = mask_sensitive_columns({"git_url": raw, "name": "p"})
    assert secret not in masked["git_url"]
    assert "***@" in masked["git_url"]
    # Non-credential columns are untouched.
    assert masked["name"] == "p"


@pytest.mark.parametrize("clean", _CLEAN_URLS)
def test_clean_urls_pass_through_unchanged(clean: str) -> None:
    assert mask_git_url(clean) == clean
    assert _project_public(clean)["git_url"] == clean
    assert mask_sensitive_columns({"git_url": clean})["git_url"] == clean


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_git_url_is_safe(empty) -> None:
    # None/empty must not crash and must not become the bare "***" marker.
    assert _project_public(empty)["git_url"] == empty
    assert mask_sensitive_columns({"git_url": empty})["git_url"] == empty


@pytest.mark.parametrize("raw, _secret", _CREDENTIALED_URLS)
def test_masked_value_is_detected_for_round_trip_guard(raw: str, _secret: str) -> None:
    masked = mask_git_url(raw)
    # The masked value a client could re-submit is recognised as redacted...
    assert url_userinfo_is_redacted(masked) is True


@pytest.mark.parametrize("clean", _CLEAN_URLS + ["", "not a url", "https://"])
def test_real_values_are_not_flagged_as_redacted(clean: str) -> None:
    # ...while genuine new URLs (and junk) are NOT, so real edits still apply.
    assert url_userinfo_is_redacted(clean) is False
