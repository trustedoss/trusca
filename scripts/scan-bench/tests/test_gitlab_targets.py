# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Unit tests for scripts/scan-bench/gitlab_targets.py.

Pure logic only (slugify, grouping, targets.json assembly) against synthetic
project listings -- no network, no real GitLab. Run with:
    python3 -m pytest scripts/scan-bench/tests
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gitlab_targets as tt


def _project(id_, org, path, name=None, empty=False):
    name = name or path.rsplit("/", 1)[-1]
    return {
        "id": id_,
        "path_with_namespace": path,
        "name_with_namespace": f"{org} / {name}",
        "http_url_to_repo": f"https://gitlab.example.com/{path}.git",
        "default_branch": "main",
        "empty_repo": empty,
        "repo_size": 1024,
    }


def test_slugify_ascii():
    assert tt._slugify("My Cool App", fallback="x") == "my-cool-app"


def test_slugify_collapses_and_trims_invalid_chars():
    assert tt._slugify("  A__B..C  ", fallback="x") == "a-b-c"


def test_slugify_falls_back_when_no_ascii_content():
    assert tt._slugify("한글전용조직명", fallback="org-abc123") == "org-abc123"


def test_dedupe_slug_no_collision_keeps_base():
    used = set()
    assert tt._dedupe_slug("app", used, unique_suffix="1") == "app"
    assert used == {"app"}


def test_dedupe_slug_collision_appends_suffix():
    used = {"app"}
    result = tt._dedupe_slug("app", used, unique_suffix="42")
    assert result == "app-42"
    assert result in used


def test_org_label_splits_on_first_separator():
    assert tt._org_label("Team A / subgroup / repo") == "Team A"


def test_group_by_org_excludes_empty_by_default():
    projects = [
        _project(1, "Team A", "team-a/repo1"),
        _project(2, "Team A", "team-a/repo2", empty=True),
    ]
    groups = tt._group_by_org(projects, include_empty=False)
    assert list(groups["Team A"]) == [projects[0]]


def test_group_by_org_can_include_empty():
    projects = [_project(1, "Team A", "team-a/repo1", empty=True)]
    groups = tt._group_by_org(projects, include_empty=True)
    assert len(groups["Team A"]) == 1


def test_build_filters_by_repo_count_and_writes_expected_shape(tmp_path):
    projects = (
        [_project(i, "Small Org", f"small/repo{i}") for i in range(3)]
        + [_project(100 + i, "Mid Org", f"mid/repo{i}") for i in range(12)]
    )
    input_path = tmp_path / "projects.json"
    output_path = tmp_path / "targets.json"
    input_path.write_text(json.dumps(projects))

    args = argparse.Namespace(
        input=str(input_path), output=str(output_path), min_repos=10, max_repos=30, max_orgs=None
    )
    assert tt.cmd_build(args) == 0

    spec = json.loads(output_path.read_text())
    team_names = {t["name"] for t in spec["teams"]}
    assert team_names == {"Mid Org"}
    mid = next(t for t in spec["teams"] if t["name"] == "Mid Org")
    assert len(mid["repos"]) == 12
    assert mid["slug"] == "mid-org"
    assert {r["slug"] for r in mid["repos"]} == {f"repo{i}" for i in range(12)}
    assert all(r["git_url"].endswith(".git") for r in mid["repos"])


def test_build_is_deterministic_across_runs(tmp_path):
    projects = [_project(i, "Repeatable Org", f"r/repo{i}") for i in range(10)]
    input_path = tmp_path / "projects.json"
    input_path.write_text(json.dumps(projects))

    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    args_a = argparse.Namespace(input=str(input_path), output=str(out_a), min_repos=1, max_repos=100, max_orgs=None)
    args_b = argparse.Namespace(input=str(input_path), output=str(out_b), min_repos=1, max_repos=100, max_orgs=None)
    tt.cmd_build(args_a)
    tt.cmd_build(args_b)

    assert out_a.read_text() == out_b.read_text()


def test_build_max_orgs_caps_alphabetically(tmp_path):
    projects = [_project(i, "Z Org", f"z/repo{i}") for i in range(10)] + [
        _project(100 + i, "A Org", f"a/repo{i}") for i in range(10)
    ]
    input_path = tmp_path / "projects.json"
    output_path = tmp_path / "targets.json"
    input_path.write_text(json.dumps(projects))

    args = argparse.Namespace(
        input=str(input_path), output=str(output_path), min_repos=1, max_repos=100, max_orgs=1
    )
    tt.cmd_build(args)

    spec = json.loads(output_path.read_text())
    assert [t["name"] for t in spec["teams"]] == ["A Org"]


def test_build_dedupes_team_slug_collision(tmp_path):
    # Two distinct org labels that slugify to the same ASCII string.
    projects = [_project(i, "App!!!", f"app1/repo{i}") for i in range(10)] + [
        _project(100 + i, "App???", f"app2/repo{i}") for i in range(10)
    ]
    input_path = tmp_path / "projects.json"
    output_path = tmp_path / "targets.json"
    input_path.write_text(json.dumps(projects))

    args = argparse.Namespace(
        input=str(input_path), output=str(output_path), min_repos=1, max_repos=100, max_orgs=None
    )
    tt.cmd_build(args)

    spec = json.loads(output_path.read_text())
    slugs = [t["slug"] for t in spec["teams"]]
    assert len(slugs) == len(set(slugs)) == 2


def test_stats_reports_bucket_counts(tmp_path, capsys):
    projects = [_project(i, "Small", f"s/repo{i}") for i in range(3)] + [
        _project(100 + i, "Mid", f"m/repo{i}") for i in range(15)
    ]
    input_path = tmp_path / "projects.json"
    input_path.write_text(json.dumps(projects))

    args = argparse.Namespace(input=str(input_path))
    assert tt.cmd_stats(args) == 0
    out = capsys.readouterr().out
    assert "18 across 2 org label(s)" in out.replace("projects (non-empty): ", "")
    assert "10-30 repos/org: 1 org(s), 15 repo(s)" in out
