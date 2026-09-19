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

# Baselines whose fixture is not in the repository yet. The baselines were
# recorded from a corpus repository that no longer exists, so nothing can
# reproduce them. Each fixture that lands must leave this set and join a shard.
PENDING_FIXTURES = {
    "go",
    "gradle",
    "gradle-kts",
    "maven",
    "multi-component",
    "node-yarn",
    "python-poetry",
    "ruby",
    "rust",
    "scancode-license-files",
    "scancode-license-headers",
    "scancode-mixed-policy",
    "scancode-spdx-tags",
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


def test_baselines_not_in_a_shard_are_exactly_the_pending_ones() -> None:
    # A new baseline must join a shard or be listed as pending; a fixture that
    # lands must leave PENDING_FIXTURES. Either drift fails here, not in CI.
    assert _baselines() - set(_shard_names()) == PENDING_FIXTURES


def test_the_nightly_is_strict_and_scheduled() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert "schedule" in triggers, "a workflow nobody triggers is what was deleted in 7c98b8a"
    steps = workflow["jobs"]["golden"]["steps"]
    gate_step = next(s for s in steps if s.get("name", "").startswith("Golden drift gate"))
    assert gate_step["env"]["GOLDEN_STRICT"] == "1"


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
