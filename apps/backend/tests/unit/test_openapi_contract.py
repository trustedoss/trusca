"""OpenAPI contract drift gate (Tier N).

Catches the bug class where the API contract silently changes — an endpoint is
renamed/removed or its parameters shift — and clients (frontend, CI action, the
load test) keep calling the old shape. The 2026-05-22 session hit two instances
of this class: a stale locustfile targeting removed endpoints, and the frontend
requesting ``?size=200`` while the backend caps ``size`` at 100.

The committed snapshot (``openapi_endpoints.json``) is the source of truth for
the wire surface. A drift fails the PR with an explicit add/remove/changed diff;
an INTENTIONAL change is landed by regenerating the snapshot (reviewed in the
diff), via ``REGEN_OPENAPI_SNAPSHOT=1 pytest -k openapi_no_drift``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from main import app

SNAPSHOT = Path(__file__).parent / "openapi_endpoints.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


def _signature() -> dict[str, list[str]]:
    """METHOD path → sorted parameter names. Stable across unrelated edits."""
    spec = app.openapi()
    sig: dict[str, list[str]] = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() in _METHODS:
                params = sorted(
                    p.get("name", "") for p in op.get("parameters", []) if isinstance(p, dict)
                )
                sig[f"{method.upper()} {path}"] = params
    return sig


def test_openapi_no_drift() -> None:
    current = _signature()
    if os.getenv("REGEN_OPENAPI_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return
    assert SNAPSHOT.exists(), (
        "OpenAPI snapshot missing — generate it with "
        "REGEN_OPENAPI_SNAPSHOT=1 pytest -k openapi_no_drift"
    )
    expected = json.loads(SNAPSHOT.read_text())
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    changed = {
        k: {"snapshot": expected[k], "current": current[k]}
        for k in set(current) & set(expected)
        if current[k] != expected[k]
    }
    assert not (added or removed or changed), (
        "OpenAPI contract drift — if intentional, regenerate the snapshot "
        "(REGEN_OPENAPI_SNAPSHOT=1) and review the diff in the PR:\n"
        + json.dumps(
            {"added": added, "removed": removed, "changed": changed},
            indent=2,
            sort_keys=True,
        )
    )
