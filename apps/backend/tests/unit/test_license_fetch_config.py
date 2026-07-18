"""
Unit tests for the ``license_fetch_enabled`` config accessor (no DB) — W8-#48.

The flag gates the post-cdxgen license fetcher's scan-time egress to public
package registries. Unlike SCANOSS (fingerprint egress → default OFF, fail
closed), a license lookup carries only a package name+version to the public
registry the package manager already contacts, so it defaults ON for the
enrichment value and an air-gapped deployment sets it OFF. It reads
``os.getenv`` at call time (CLAUDE.md core rule #11).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from core.config import license_fetch_enabled


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("LICENSE_FETCH_ENABLED", raising=False)
    yield


def test_defaults_on() -> None:
    assert license_fetch_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "False", "0", "no", "NO", " no "])
def test_explicit_falsy_tokens_disable(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", value)
    assert license_fetch_enabled() is False


@pytest.mark.parametrize(
    "value",
    ["true", "1", "yes", "on", "", "  ", "nonsense", "disabled", "off"],
)
def test_non_falsy_stays_on(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Default-ON posture: only the exact falsy tokens disable. A typo like
    # "off"/"disabled" reads as ON — the enrichment keeps working rather than
    # silently degrading to 90%-unknown. (The air-gap operator uses the
    # documented false/0/no tokens.)
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", value)
    assert license_fetch_enabled() is True


def test_reads_env_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md rule #11 — no module-level caching."""
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "false")
    assert license_fetch_enabled() is False
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    assert license_fetch_enabled() is True
