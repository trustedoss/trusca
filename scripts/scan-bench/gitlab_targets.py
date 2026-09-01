#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""gitlab_targets.py, source bulk_register.py's targets.json from a
self-hosted GitLab instance whose namespace hierarchy is too fragmented to
use as an organization axis directly.

self-resource-validation-plan-2026-08-30.md S1.5: a large internal GitLab
instance can have a namespace hierarchy sliced far more finely than the
teams bulk_register.py needs (thousands of top-level namespaces, most far
too small to be a useful cohort unit on their own). This instead labels
each repository by the first segment of its ``name_with_namespace`` -- the
top-level group's *display name* -- and groups by that label. Spot-checking
this against a separately maintained project catalog that already assigns
its own project names as ``{label} / {rest of the GitLab path}`` confirmed
the two agree; no call to that catalog's own API is needed here, since the
label falls straight out of GitLab's own listing.

Two-stage, mirroring bulk_register.py's own register/poll split so a full
list run (potentially hundreds of paginated requests against a large
instance) doesn't have to repeat just to try a different org-size cutoff:

    python3 gitlab_targets.py list --gitlab-url https://gitlab.example.com \\
        --output gitlab_projects.json
    python3 gitlab_targets.py stats --input gitlab_projects.json
    python3 gitlab_targets.py build --input gitlab_projects.json --output targets.json \\
        --min-repos 10 --max-repos 30 --max-orgs 120

``build``'s output is bulk_register.py's targets.json shape directly:

    python3 bulk_register.py register --cohort gitlab-2026-09 --input targets.json \\
        --admin-email ... --admin-password ... --git-credential ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_GITLAB_URL = os.getenv("GITLAB_URL")

# Both the team and project slug patterns (schemas/admin.py, schemas/scan.py)
# are ``^[a-z0-9][a-z0-9-]{0,63}$`` / equivalent, 64 chars total. Cap one
# short of that so a dedup suffix always fits.
_MAX_SLUG_LEN = 63
_SLUG_INVALID = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, fallback: str) -> str:
    """ASCII-lowercase-hyphen slug matching the portal's slug patterns.

    Falls back to a caller-supplied deterministic id when the input has no
    ASCII alphanumeric content at all -- all-Korean org labels and project
    names are common in this corpus.
    """
    ascii_only = text.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_INVALID.sub("-", ascii_only).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = fallback
    return slug[:_MAX_SLUG_LEN]


def _dedupe_slug(base: str, used: set[str], *, unique_suffix: str) -> str:
    """Appends ``unique_suffix`` only on collision, so the common case (no
    collision) keeps the readable slug. Deterministic given the same input
    set and processing order, which matters because bulk_register.py's
    cohort tracking is keyed on (team_slug, project_slug) and a re-run
    against the same list cache should resolve to the same teams.
    """
    if base not in used:
        used.add(base)
        return base
    trimmed = base[: _MAX_SLUG_LEN - len(unique_suffix) - 1].rstrip("-")
    candidate = f"{trimmed}-{unique_suffix}"
    used.add(candidate)
    return candidate


def _fetch_page(gitlab_url: str, token: str, page: int, per_page: int, *, retries: int = 3) -> list[dict]:
    url = (
        f"{gitlab_url.rstrip('/')}/api/v4/projects"
        f"?archived=false&simple=false&statistics=true"
        f"&order_by=id&sort=asc&per_page={per_page}&page={page}"
    )
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"page {page} failed after {retries} attempts: {last_exc}")


def cmd_list(args: argparse.Namespace) -> int:
    if not args.gitlab_url:
        print("GitLab URL required: --gitlab-url or GITLAB_URL env var", file=sys.stderr)
        return 1
    token = args.gitlab_token or os.getenv("GITLAB_TOKEN")
    if not token:
        print("GitLab token required: --gitlab-token or GITLAB_TOKEN env var", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    projects: list[dict] = []
    if args.resume and out_path.exists():
        projects = json.loads(out_path.read_text())
        print(f"[list] resuming with {len(projects)} already-fetched project(s)")

    page = args.start_page
    while True:
        batch = _fetch_page(args.gitlab_url, token, page, args.per_page)
        if not batch:
            break
        for p in batch:
            projects.append(
                {
                    # Not in the plan's original field list, but a stable
                    # unique id is the only thing that makes per-project
                    # slug dedup deterministic (see _dedupe_slug).
                    "id": p["id"],
                    "path_with_namespace": p["path_with_namespace"],
                    "name_with_namespace": p["name_with_namespace"],
                    "http_url_to_repo": p["http_url_to_repo"],
                    "default_branch": p.get("default_branch"),
                    "empty_repo": bool(p.get("empty_repo")),
                    "repo_size": (p.get("statistics") or {}).get("repository_size"),
                }
            )
        print(f"[list] page {page}: {len(batch)} project(s), {len(projects)} total", flush=True)
        out_path.write_text(json.dumps(projects, ensure_ascii=False, indent=2))
        if len(batch) < args.per_page:
            break
        page += 1

    print(f"[list] done: {len(projects)} project(s) -> {out_path}")
    return 0


def _org_label(name_with_namespace: str) -> str:
    return name_with_namespace.split(" / ", 1)[0].strip()


def _group_by_org(projects: list[dict], *, include_empty: bool) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for p in projects:
        if not include_empty and p.get("empty_repo"):
            continue
        groups.setdefault(_org_label(p["name_with_namespace"]), []).append(p)
    return groups


def cmd_stats(args: argparse.Namespace) -> int:
    projects = json.loads(Path(args.input).read_text())
    groups = _group_by_org(projects, include_empty=False)
    total_repos = sum(len(v) for v in groups.values())
    print(f"projects (non-empty): {total_repos} across {len(groups)} org label(s)")

    buckets = [(1, 4), (5, 9), (10, 30), (31, 100), (101, 10**9)]
    for lo, hi in buckets:
        orgs_in_bucket = [g for g in groups.values() if lo <= len(g) <= hi]
        repo_count = sum(len(g) for g in orgs_in_bucket)
        hi_label = str(hi) if hi < 10**9 else "+"
        print(f"  {lo}-{hi_label} repos/org: {len(orgs_in_bucket)} org(s), {repo_count} repo(s)")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    projects = json.loads(Path(args.input).read_text())
    groups = _group_by_org(projects, include_empty=False)

    selected = {
        label: repos
        for label, repos in groups.items()
        if args.min_repos <= len(repos) <= args.max_repos
    }
    # Deterministic order: org label, then GitLab project id within each
    # org -- a re-run against the same list cache with the same --max-orgs
    # always picks the same subset.
    ordered_labels = sorted(selected)
    if args.max_orgs:
        ordered_labels = ordered_labels[: args.max_orgs]

    used_team_slugs: set[str] = set()
    teams = []
    total_repos = 0
    for label in ordered_labels:
        repos = sorted(selected[label], key=lambda p: p["id"])
        label_hash = hashlib.sha1(label.encode()).hexdigest()[:6]
        team_slug = _dedupe_slug(
            _slugify(label, fallback=f"org-{label_hash}"),
            used_team_slugs,
            unique_suffix=label_hash,
        )
        used_project_slugs: set[str] = set()
        repo_entries = []
        for p in repos:
            project_name = p["path_with_namespace"].rsplit("/", 1)[-1]
            project_slug = _dedupe_slug(
                _slugify(project_name, fallback=f"repo-{p['id']}"),
                used_project_slugs,
                unique_suffix=str(p["id"]),
            )
            repo_entries.append(
                {"name": project_name[:200], "slug": project_slug, "git_url": p["http_url_to_repo"]}
            )
        teams.append({"name": label[:120], "slug": team_slug, "repos": repo_entries})
        total_repos += len(repo_entries)

    Path(args.output).write_text(json.dumps({"teams": teams}, ensure_ascii=False, indent=2))
    print(f"[build] {len(teams)} org(s), {total_repos} repo(s) -> {args.output}")
    if args.max_orgs and len(ordered_labels) < len(selected):
        dropped = len(selected) - len(ordered_labels)
        print(f"[build] --max-orgs {args.max_orgs} dropped {dropped} eligible org(s), alphabetically after the cutoff")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="paginate a GitLab instance's project list into a raw JSON cache")
    p_list.add_argument("--gitlab-url", default=DEFAULT_GITLAB_URL, help="also via GITLAB_URL env var")
    p_list.add_argument("--gitlab-token", default=None, help="also via GITLAB_TOKEN env var")
    p_list.add_argument("--output", required=True)
    p_list.add_argument("--per-page", type=int, default=100)
    p_list.add_argument("--start-page", type=int, default=1)
    p_list.add_argument("--resume", action="store_true", help="append to an existing --output instead of overwriting")

    p_stats = sub.add_parser("stats", help="print org-label size distribution from a list cache")
    p_stats.add_argument("--input", required=True)

    p_build = sub.add_parser("build", help="build bulk_register.py's targets.json from a list cache")
    p_build.add_argument("--input", required=True)
    p_build.add_argument("--output", required=True)
    p_build.add_argument("--min-repos", type=int, default=10)
    p_build.add_argument("--max-repos", type=int, default=30)
    p_build.add_argument(
        "--max-orgs", type=int, default=None, help="cap the number of orgs (alphabetical); omit for no cap"
    )

    args = parser.parse_args()
    if args.command == "list":
        return cmd_list(args)
    if args.command == "stats":
        return cmd_stats(args)
    return cmd_build(args)


if __name__ == "__main__":
    sys.exit(main())
