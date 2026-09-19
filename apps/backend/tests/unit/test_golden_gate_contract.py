"""The golden gate's three vocabularies must agree.

The nightly workflow (``.github/workflows/golden-nightly.yml``), the committed
baselines and the in-repo fixtures each carry the list of golden fixture names.
The nightly runs only in CI, so a name that drifts between them would show up
as a skipped case or a green run that compared nothing, and nothing in the PR
run would say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[4]
GOLDEN = REPO / "apps/backend/tests/e2e/golden"
WORKFLOW = REPO / ".github/workflows/golden-nightly.yml"

sys.path.insert(0, str(GOLDEN))
import run_golden as rg  # noqa: E402

# Baselines held out of every shard because the product cannot produce them
# yet: their fixtures exist, but the stage they assert on never runs. The
# scancode baselines describe detected licenses and a failing gate; the worker
# image's scancode exits 2 on every call (issue 487), so a nightly run would
# either fail forever or, worse, be regenerated to "no licenses, gate passes".
# When the issue is fixed, regenerate them in CI, move each name into a shard
# and delete its entry here.
HELD_OUT = {
    "scancode-license-files": 487,
    "scancode-license-headers": 487,
    "scancode-mixed-policy": 487,
    "scancode-spdx-tags": 487,
}


def _shards() -> list[dict]:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    shards: list[dict] = workflow["jobs"]["golden"]["strategy"]["matrix"]["include"]
    return shards


def _shard_names() -> list[str]:
    return [n for shard in _shards() for n in shard["names"].split()]


def _baselines() -> set[str]:
    return {p.stem for p in rg.BASELINE_DIR.glob("*.json")}


def _fixtures() -> set[str]:
    root = GOLDEN / "fixtures"
    return {p.name for p in root.iterdir() if p.is_dir()}


def test_every_shard_name_has_a_baseline_and_a_fixture() -> None:
    names = _shard_names()
    assert names, "the nightly has no shard names, so it compares nothing"
    assert set(names) <= _baselines(), sorted(set(names) - _baselines())
    assert set(names) <= _fixtures(), sorted(set(names) - _fixtures())


def test_no_name_is_owned_by_two_shards() -> None:
    names = _shard_names()
    assert len(names) == len(set(names)), sorted(n for n in set(names) if names.count(n) > 1)


def test_every_baseline_belongs_to_a_shard_or_is_held_out_with_a_reason() -> None:
    # A baseline outside every shard is never compared, and the nightly stays
    # green. Either side drifting fails here, not in CI.
    shard_names = set(_shard_names())
    assert not shard_names & set(HELD_OUT), sorted(shard_names & set(HELD_OUT))
    assert _baselines() == shard_names | set(HELD_OUT)
    assert set(HELD_OUT) <= _fixtures(), "a held-out baseline still needs its fixture"


def test_the_nightly_is_strict_and_scheduled() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert "schedule" in triggers, "a workflow nobody triggers is what was deleted in 7c98b8a"
    steps = workflow["jobs"]["golden"]["steps"]
    gate_step = next(s for s in steps if s.get("name", "").startswith("Golden drift gate"))
    assert gate_step["env"]["GOLDEN_STRICT"] == "1"


def test_the_stack_the_nightly_boots_runs_scancode() -> None:
    # docker-compose.dev.yml gives celery-worker SCANCODE_MAX_FILES "0", which
    # makes every scan skip licence detection. The nightly layers an override
    # over it; if the override, the service name or the COMPOSE_FILE wiring
    # drifts, baselines silently lose their detected licences.
    workflow = yaml.safe_load(WORKFLOW.read_text())
    compose_files = workflow["jobs"]["golden"]["env"]["COMPOSE_FILE"].split(":")
    assert compose_files[0] == "docker-compose.dev.yml"
    override_path = REPO / compose_files[1]
    assert override_path == GOLDEN / "compose.scancode.yml"

    dev = yaml.safe_load((REPO / "docker-compose.dev.yml").read_text())
    override = yaml.safe_load(override_path.read_text())
    assert set(override["services"]) <= set(dev["services"])
    value = override["services"]["celery-worker"]["environment"]["SCANCODE_MAX_FILES"]
    assert value.endswith(":-20000}"), value

    # The override is only needed while the dev file still zeroes the ceiling.
    # If that line goes away, delete the override and this test with it.
    assert dev["services"]["celery-worker"]["environment"]["SCANCODE_MAX_FILES"] == "0"


class TestParseNames:
    AVAILABLE = ["node", "maven", "go"]

    def test_empty_means_every_baseline(self) -> None:
        assert rg.parse_names("", self.AVAILABLE) == self.AVAILABLE

    def test_space_and_comma_separators(self) -> None:
        assert rg.parse_names("node, go maven", self.AVAILABLE) == ["node", "go", "maven"]

    def test_unknown_name_is_an_error_not_a_smaller_run(self) -> None:
        with pytest.raises(ValueError, match="nod"):
            rg.parse_names("nod", self.AVAILABLE)


class TestStrict:
    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_on(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("GOLDEN_STRICT", value)
        assert rg.strict_from_env() is True

    @pytest.mark.parametrize("value", ["", "0", "false"])
    def test_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("GOLDEN_STRICT", value)
        assert rg.strict_from_env() is False

    def test_unset_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOLDEN_STRICT", raising=False)
        assert rg.strict_from_env() is False
