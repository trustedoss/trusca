"""Golden-fixture drift gate (Tier 1) — pytest wrapper around ``run_golden``.

Runs against a LIVE stack (real cdxgen/scancode/DT). Marked ``golden`` so it is
excluded from the default + PR test runs and executed only in the nightly e2e
workflow:

    pytest -m golden apps/backend/tests/e2e/golden/

Each committed baseline (``baselines/<fixture>.json``) is one parametrized case:
the fixture is scanned through the real pipeline and its normalised output is
diffed against the baseline. Any drift (a new ``pkg:nix`` component, a changed
license category, a vanished transitive dep) fails the case with a field diff.

Skips cleanly when the stack / fixtures aren't reachable, so a developer running
the full suite locally without the e2e stack up doesn't see spurious failures.

Regenerate baselines deliberately (review the diff!):
    python run_golden.py --api ... --fixtures ... --update --names <fixture> ...
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import run_golden as rg  # same dir on sys.path via conftest

pytestmark = [pytest.mark.golden, pytest.mark.integration]

API = os.getenv("GOLDEN_API", "http://localhost:8000")
FIXTURES = os.getenv(
    "GOLDEN_FIXTURES",
    str(Path.home() / "projects" / "bd-scan" / "tests" / "fixtures" / "projects"),
)
BASELINES = sorted(p.stem for p in rg.BASELINE_DIR.glob("*.json"))


def _api_up() -> bool:
    try:
        urllib.request.urlopen(f"{API}/health", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def _auth():
    if not _api_up():
        pytest.skip(f"golden stack not reachable at {API}")
    if not Path(FIXTURES).is_dir():
        pytest.skip(f"fixtures dir not found: {FIXTURES}")
    token = rg.login(API)
    team = rg._team(API, token)
    return token, team


@pytest.mark.skipif(not BASELINES, reason="no golden baselines committed yet")
@pytest.mark.parametrize("name", BASELINES)
def test_fixture_matches_baseline(name: str, _auth) -> None:
    token, team = _auth
    src = Path(FIXTURES) / name
    if not src.is_dir():
        pytest.skip(f"fixture {name} not present")
    got = rg.scan_fixture(API, token, team, name, src)
    want = json.loads((rg.BASELINE_DIR / f"{name}.json").read_text())
    # vulnerabilities_count is NVD-mirror-dependent → asserted in the Tier 5
    # deterministic vuln-detection suite, not here.
    drop = lambda d: {k: v for k, v in d.items() if k != "vulnerabilities_count"}  # noqa: E731
    assert drop(got) == drop(want), (
        f"golden drift for {name}:\n"
        + json.dumps({"baseline": drop(want), "got": drop(got)}, indent=2, sort_keys=True)
    )
