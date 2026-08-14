# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``scripts/seed_e2e_user.py --stable-suffix`` plumbing.

The seed generates the team name, the user emails, the project slug behind
the seeded git URL and the synthetic CVE ids from one random suffix, and all
of them are rendered in the screens the UI gates photograph. A random suffix
therefore differed in every capture and became noise the visual diff ceiling
had to tolerate. Capture runs pass a fixed suffix instead.

What can break silently is the plumbing: the flag parses, the names are
still built from a fresh ``uuid4``, and nothing fails. So these tests assert
the value reaches ``_seed`` and that it is what the names are built from.
They need no database because ``_seed`` is patched out.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest


def _invoke_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run ``main()`` with ``_seed`` replaced, returning its kwargs."""
    from scripts import seed_e2e_user

    captured: dict[str, Any] = {}

    async def fake_seed(**kwargs: Any) -> Any:
        captured.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(seed_e2e_user, "_seed", fake_seed)
    monkeypatch.setattr(sys, "argv", ["seed_e2e_user.py", *argv])
    with pytest.raises(SystemExit):
        seed_e2e_user.main()
    return captured


def test_stable_suffix_reaches_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parsed flag is forwarded rather than parsed and dropped."""
    captured = _invoke_main(["--stable-suffix", "visual"], monkeypatch)
    assert captured["stable_suffix"] == "visual"


def test_stable_suffix_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the flag the seed keeps its random suffix.

    Repeat seeds against one database rely on that randomness to avoid
    colliding on the org and team slug, so the default must not become a
    constant.
    """
    captured = _invoke_main([], monkeypatch)
    assert captured["stable_suffix"] is None


def test_fixed_suffix_is_what_the_names_are_built_from() -> None:
    """Every on-screen generated string hangs off the one suffix.

    Asserted against the source rather than a live seed so the test needs no
    database. Each template below is a string the capture pipeline photographs:
    the team in the top bar, the emails in the admin table, and the CVE ids in
    the vulnerability table.
    """
    import inspect

    from scripts import seed_e2e_user

    source = inspect.getsource(seed_e2e_user._seed)
    assert "suffix = stable_suffix or uuid.uuid4().hex[:10]" in source
    for template in (
        'f"E2E Org {suffix}"',
        'f"e2e-org-{suffix}"',
        'f"E2E Team {suffix}"',
        'f"e2e-team-{suffix}"',
        'f"e2e-{suffix}@example.com"',
        'f"e2e-extra-{i}-{suffix}@example.com"',
        'f"CVE-2099-VLN-{suffix}-{idx:05d}"',
    ):
        assert template in source


def test_fixed_suffix_drops_the_random_tail_from_the_project_slug() -> None:
    """The slug reaches the screen through the seeded git URL.

    Leaving the ``uuid4`` tail on would put a fresh six-character string into
    every capture of the project page, which is the noise this flag exists to
    remove.
    """
    import inspect

    from scripts import seed_e2e_user

    source = inspect.getsource(seed_e2e_user._seed)
    assert "if stable_suffix" in source
    assert 'name.lower()\n' in source
