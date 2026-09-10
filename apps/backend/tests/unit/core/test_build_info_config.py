# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for the O11 build-info config accessors.

Pin the env-resolution contract (CLAUDE.md core rule #11, read at call time):
the commit / built-at values have "unknown" defaults, honour a
``--build-arg``-injected override, and treat a blank/whitespace-only value
the same as unset (not an empty string, which would render as a build-info
label with nothing in it).
"""

from __future__ import annotations

import pytest

from core.config import trustedoss_built_at, trustedoss_commit


def test_commit_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUSTEDOSS_COMMIT", raising=False)
    assert trustedoss_commit() == "unknown"


def test_commit_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_COMMIT", "   ")
    assert trustedoss_commit() == "unknown"


def test_commit_override_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_COMMIT", "  a0d2bab  ")
    assert trustedoss_commit() == "a0d2bab"


def test_built_at_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUSTEDOSS_BUILT_AT", raising=False)
    assert trustedoss_built_at() == "unknown"


def test_built_at_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_BUILT_AT", "   ")
    assert trustedoss_built_at() == "unknown"


def test_built_at_override_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_BUILT_AT", "  2026-09-06T02:11:00Z  ")
    assert trustedoss_built_at() == "2026-09-06T02:11:00Z"
