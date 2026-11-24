# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Seeded notifications carry a message key the SPA catalog knows.

A seeded row without ``message_key`` renders its English text in the Korean
UI. Both seed scripts must pass a key for every row, and each key must exist
with a title and a body in the English and Korean catalogs, with every
``{{param}}`` the text uses supplied by the seed. Read from source because
the seed bodies need a database to run.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT.parent / "frontend" / "src" / "locales"
SEEDS = ("seed_demo.py", "seed_e2e_user.py")

LEGACY_KEYS = (
    "scan_completed",
    "scan_failed",
    "cve_detected",
    "license_violation",
    "policy_gate_failed",
    "approval_pending",
)


def _catalog(lang: str) -> dict[str, Any]:
    path = LOCALES / lang / "notifications.json"
    message: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["message"]
    return message


def test_every_legacy_kind_has_en_and_ko_text_with_matching_placeholders() -> None:
    en, ko = _catalog("en"), _catalog("ko")
    for key in LEGACY_KEYS:
        for part in ("title", "body"):
            en_text, ko_text = en[key][part], ko[key][part]
            assert set(re.findall(r"{{(\w+)}}", en_text)) == set(
                re.findall(r"{{(\w+)}}", ko_text)
            ), f"{key}.{part}"
        assert not re.search(r"#\d", en[key]["title"])


def test_seed_notifications_pass_message_key_and_params() -> None:
    for name in SEEDS:
        tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Notification"
        ]
        assert len(calls) == 1, name
        keywords = {kw.arg for kw in calls[0].keywords}
        assert {"message_key", "params"} <= keywords, name


def test_seed_titles_are_not_title_cased_with_a_counter() -> None:
    source = (ROOT / "scripts" / "seed_e2e_user.py").read_text(encoding="utf-8")
    assert ".title()} #" not in source


def test_seeded_notification_cycles_through_every_kind_with_its_params() -> None:
    from scripts import seed_e2e_user

    kinds = [seed_e2e_user._seeded_notification(i)[0] for i in range(10)]
    assert kinds[:5] == kinds[5:]
    assert set(kinds) == {
        "scan_completed",
        "cve_detected",
        "policy_gate_failed",
        "approval_pending",
        "license_violation",
    }
    kind, title, body, params = seed_e2e_user._seeded_notification(0)
    assert (kind, params["project_name"]) == ("scan_completed", "demo-app")
    assert title and body
