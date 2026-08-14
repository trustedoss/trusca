# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``scripts/seed_e2e_user.py --org-suffix`` plumbing.

The seeded team name is rendered in the top bar, so every screenshot the UI
gates capture contains it. A random suffix therefore differed in every
capture and became noise the visual diff ceiling had to tolerate. Capture
runs pass a fixed suffix instead.

What can break silently is the plumbing: the flag parses, the org is still
named from a fresh ``uuid4``, and nothing fails. So these tests assert the
value reaches ``_seed``, and that omitting it still yields distinct names.
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


def test_org_suffix_reaches_the_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parsed flag is forwarded rather than parsed and dropped."""
    captured = _invoke_main(["--org-suffix", "visual"], monkeypatch)
    assert captured["org_suffix"] == "visual"


def test_org_suffix_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the flag the seed keeps its random suffix.

    Repeat seeds against one database rely on that randomness to avoid
    colliding on the org and team slug, so the default must not become a
    constant.
    """
    captured = _invoke_main([], monkeypatch)
    assert captured["org_suffix"] is None


def test_fixed_suffix_names_the_org_and_team(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed suffix is what the name and slug are built from.

    Asserted against the source rather than a live seed so the test needs no
    database: the four f-strings are the contract the capture pipeline reads
    back out of the top bar.
    """
    import inspect

    from scripts import seed_e2e_user

    source = inspect.getsource(seed_e2e_user._seed)
    assert "suffix = org_suffix or uuid.uuid4().hex[:10]" in source
    for template in (
        'f"E2E Org {suffix}"',
        'f"e2e-org-{suffix}"',
        'f"E2E Team {suffix}"',
        'f"e2e-team-{suffix}"',
    ):
        assert template in source
