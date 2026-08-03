"""
Unit tests for the ``scancode_enabled`` config accessor and the
``run_scancode`` disabled short-circuit (feat/demo-sandbox-scan).

scancode is a pure-local pass (no egress), so unlike ``scanoss_enabled`` it
defaults ON and fails OPEN (only ``false`` / ``0`` / ``no`` disable it) — a typo
keeps detection running rather than silently dropping detected-license data. The
public sandbox worker sets ``SCANCODE_ENABLED=false`` to shed the per-scan cost;
``run_scancode`` then raises ``ScancodeDisabled`` (a ``ScancodeError`` subclass)
so the pipeline's existing best-effort handler skips the stage. Read at call time
(CLAUDE.md core rule #11).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from core.config import scancode_enabled
from integrations import scancode as scancode_adapter


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SCANCODE_ENABLED", raising=False)
    yield


def test_defaults_on() -> None:
    assert scancode_enabled() is True


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "NO", " no "])
def test_falsy_tokens_disable(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANCODE_ENABLED", value)
    assert scancode_enabled() is False


@pytest.mark.parametrize(
    "value", ["true", "1", "yes", "on", "", "  ", "off", "disable", "junk"]
)
def test_fails_open_on_non_falsy(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCANCODE_ENABLED", value)
    assert scancode_enabled() is True


def test_run_scancode_short_circuits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Disabled ⇒ ScancodeDisabled BEFORE any disk / subprocess work.

    ``output_dir`` is a NON-existent path: if the guard fired after
    ``output_dir.mkdir(...)`` this would create it, so asserting it stays absent
    proves the short-circuit runs first.
    """
    monkeypatch.setenv("SCANCODE_ENABLED", "false")
    output_dir = tmp_path / "scancode-out"
    with pytest.raises(scancode_adapter.ScancodeDisabled):
        scancode_adapter.run_scancode(
            source_dir=tmp_path,
            output_dir=output_dir,
        )
    assert not output_dir.exists()


def test_scancode_disabled_is_scancode_error() -> None:
    """The pipeline catches ``ScancodeError``; the disabled skip must subclass it
    so the best-effort handler treats it as a skip, not a fatal error."""
    assert issubclass(
        scancode_adapter.ScancodeDisabled, scancode_adapter.ScancodeError
    )
