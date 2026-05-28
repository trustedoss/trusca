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
# Self-contained subset committed in-repo so the BUG-008 guard ("scan succeeded
# but 0 components") runs in nightly even without the external bd-scan corpus
# (BD_SCAN_REPO_URL secret). Resolution prefers the external corpus and falls
# back here, so `node` / `python-pip` always run while the full language matrix
# still needs the external clone.
IN_REPO_FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINES = sorted(p.stem for p in rg.BASELINE_DIR.glob("*.json"))


def _resolve_fixture(name: str) -> Path | None:
    """External corpus first, then the committed in-repo subset; None if neither."""
    external = Path(FIXTURES) / name
    if external.is_dir():
        return external
    in_repo = IN_REPO_FIXTURES / name
    if in_repo.is_dir():
        return in_repo
    return None


def _api_up() -> bool:
    try:
        urllib.request.urlopen(f"{API}/health", timeout=3)
        return True
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def _auth():
    # Only the live stack is required up front — fixtures are resolved per-case
    # (external corpus or the in-repo fallback), so a missing external corpus no
    # longer skips the whole suite; it just narrows it to the in-repo subset.
    if not _api_up():
        pytest.skip(f"golden stack not reachable at {API}")
    token = rg.login(API)
    team = rg._team(API, token)
    return token, team


@pytest.mark.skipif(not BASELINES, reason="no golden baselines committed yet")
@pytest.mark.parametrize("name", BASELINES)
def test_fixture_matches_baseline(name: str, _auth) -> None:
    token, team = _auth
    src = _resolve_fixture(name)
    if src is None:
        pytest.skip(f"fixture {name} not present (external corpus or in-repo)")
    got = rg.scan_fixture(API, token, team, name, src)
    want = json.loads((rg.BASELINE_DIR / f"{name}.json").read_text())
    # vulnerabilities_count is NVD-mirror-dependent → asserted in the Tier 5
    # deterministic vuln-detection suite, not here.
    drop = lambda d: {k: v for k, v in d.items() if k != "vulnerabilities_count"}  # noqa: E731
    assert drop(got) == drop(want), (
        f"golden drift for {name}:\n"
        + json.dumps({"baseline": drop(want), "got": drop(got)}, indent=2, sort_keys=True)
    )
