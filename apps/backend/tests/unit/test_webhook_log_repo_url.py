# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Guards on the one value an unauthenticated caller can put in our logs.

A webhook delivery for an unconfigured repository is answered with 401, exactly
as a bad signature is, so the status code cannot be read as "this portal
watches that repository". The server log keeps the distinction — and that log
line is the only remaining asymmetry between the two branches.

``_loggable_repo_url`` is what keeps it from becoming a new oracle. Both of its
properties matter and neither is obvious from reading the call site, so they are
pinned here:

  - it is bounded, so a caller cannot choose how much work the rejected branch
    does (nor how much they can write to our disk); and
  - it is redacted, because a self-hosted GitLab may put credentials in the URL
    it sends, and CLAUDE.md §5 forbids those reaching the log.

The bound is applied before masking on purpose. Masking parses the whole string,
so masking first would make this branch's cost scale with an attacker-chosen
length even though the stored result is short.
"""

from __future__ import annotations

from services.webhook_service import _LOGGED_REPO_URL_MAX, _loggable_repo_url


def test_a_padded_url_cannot_inflate_the_log_line() -> None:
    """The recorded value stays bounded no matter how long the input is.

    ``_normalize_repo_url`` strips trailing slashes, so a caller can pad a real
    repository URL by a megabyte and still have it match a project — which is
    what made an unbounded log line a usable timing signal.
    """
    padded = "https://github.com/acme/widgets" + "/" * 1_000_000

    logged = _loggable_repo_url(padded)

    assert len(logged) <= _LOGGED_REPO_URL_MAX + len("…(truncated)")
    assert logged.endswith("…(truncated)")


def test_a_short_url_is_recorded_whole() -> None:
    """Truncation must not cost operators the ordinary debugging case."""
    assert _loggable_repo_url("https://github.com/acme/widgets") == (
        "https://github.com/acme/widgets"
    )


def test_an_embedded_credential_never_reaches_the_log() -> None:
    url = "https://oauth2:ghp_SUPERSECRETVALUE@github.com/acme/widgets.git"

    logged = _loggable_repo_url(url)

    assert "ghp_SUPERSECRETVALUE" not in logged
    assert "github.com/acme/widgets.git" in logged


def test_a_credential_survives_padding_too() -> None:
    """The pre-mask cut must not be small enough to skip the userinfo segment."""
    url = "https://oauth2:ghp_SUPERSECRETVALUE@github.com/acme/widgets" + "/" * 100_000

    assert "ghp_SUPERSECRETVALUE" not in _loggable_repo_url(url)
