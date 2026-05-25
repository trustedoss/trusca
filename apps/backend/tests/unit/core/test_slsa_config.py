"""
Unit tests for the SLSA provenance config accessors (v2.3-s2).

Pin the env-resolution contract (CLAUDE.md core rule #11 — read at call time):
  - the builder id / version have documented defaults and honour overrides,
  - blank / whitespace-only values fall back to the default (not an empty
    string, which would yield a meaningless builder.id in the predicate).
"""

from __future__ import annotations

import pytest

from core.config import slsa_builder_id, slsa_builder_version


def test_builder_id_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLSA_BUILDER_ID", raising=False)
    assert slsa_builder_id() == "https://github.com/trustedoss/trustedoss-portal/worker"


def test_builder_id_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLSA_BUILDER_ID", "   ")
    assert slsa_builder_id() == "https://github.com/trustedoss/trustedoss-portal/worker"


def test_builder_id_override_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLSA_BUILDER_ID", "  https://ci.example.com/trustedoss  ")
    assert slsa_builder_id() == "https://ci.example.com/trustedoss"


def test_builder_version_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRUSTEDOSS_VERSION", raising=False)
    assert slsa_builder_version() == "2.3.0-dev"


def test_builder_version_blank_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_VERSION", "  ")
    assert slsa_builder_version() == "2.3.0-dev"


def test_builder_version_override_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTEDOSS_VERSION", "  2.3.0-rc1  ")
    assert slsa_builder_version() == "2.3.0-rc1"
