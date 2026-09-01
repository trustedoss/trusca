#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""bulk_register.py, register a cohort's teams/projects and trigger scans.

self-resource-validation-plan-2026-08-30.md §6-2: there was no way to
register many repositories as TRUSCA projects at once, or track which of a
large batch registered, scanned, or failed and why. This drives the portal
purely over its existing HTTP API (super-admin creates teams and projects
under any team without needing team membership) and tracks state in
``cohort.py``'s SQLite registry so a run can be interrupted and resumed.

Unlike run_bench.py this never zips or uploads anything: every project is
created with its real ``git_url`` and scanned with ``{"kind": "source"}``
(metadata omitted, so the worker's default ``source_type=git`` clones it
directly), the reason §2.1 of the plan gave for why a cohort of public
repos doesn't need local disk at all.

    python3 bulk_register.py register --cohort my-cohort --input targets.json \\
        --admin-email admin@example.com --admin-password ...
    python3 bulk_register.py poll --cohort my-cohort --admin-email ... --admin-password ...
    python3 bulk_register.py status --cohort my-cohort

See README.md for the ``targets.json`` shape.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cohort
from run_bench import PortalClient

DEFAULT_COHORT_DB = Path(__file__).resolve().parent / "cohort.db"
DEFAULT_PORTAL_URL = "http://localhost:8000"


def _login(args: argparse.Namespace) -> PortalClient:
    email = args.admin_email or os.getenv("COHORT_ADMIN_EMAIL")
    password = args.admin_password or os.getenv("COHORT_ADMIN_PASSWORD")
    if not email or not password:
        print(
            "admin credentials required: --admin-email/--admin-password or "
            "COHORT_ADMIN_EMAIL/COHORT_ADMIN_PASSWORD env vars",
            file=sys.stderr,
        )
        sys.exit(1)
    client = PortalClient(args.portal_url)
    client.login(email, password)
    return client


def _ensure_team(client: PortalClient, *, name: str, slug: str) -> str:
    code, body = client.request(
        "POST", "/v1/admin/teams", json_body={"name": name, "slug": slug}
    )
    if code == 201:
        return body["id"]
    if code == 409:
        code2, body2 = client.request(
            "GET", "/v1/admin/teams", params={"search": name, "page_size": 200}
        )
        if code2 == 200:
            for item in body2.get("items", []):
                if item["slug"] == slug:
                    return item["id"]
        raise RuntimeError(f"team slug conflict but could not find existing team: {body}")
    raise RuntimeError(f"team create failed {code}: {body}")


def _ensure_project(client: PortalClient, *, team_id: str, name: str, slug: str, git_url: str) -> str:
    code, body = client.request(
        "POST",
        "/v1/projects",
        json_body={
            "team_id": team_id,
            "name": name,
            "slug": slug,
            "git_url": git_url,
            "description": "self-resource-validation cohort target",
            "visibility": "team",
        },
    )
    if code == 201:
        return body["id"]
    if code == 409:
        code2, body2 = client.request(
            "GET", "/v1/projects", params={"team_id": team_id, "q": slug, "size": 100}
        )
        if code2 == 200:
            for item in body2.get("items", []):
                if item["slug"] == slug:
                    return item["id"]
        raise RuntimeError(f"project slug conflict but could not find existing project: {body}")
    raise RuntimeError(f"project create failed {code}: {body}")


def _set_git_credential(client: PortalClient, *, project_id: str, credential: str) -> None:
    """PATCH the encrypted git_credential onto a project.

    ``POST /v1/projects`` (``ProjectCreate``) has no ``git_credential`` field,
    it only exists on ``ProjectUpdate`` (self-resource-validation-plan-2026-08-30.md
    S1.5), so internal/private TDE GitLab targets need this follow-up call or
    the worker has no way to authenticate the clone.
    """
    code, body = client.request(
        "PATCH", f"/v1/projects/{project_id}", json_body={"git_credential": credential}
    )
    if code != 200:
        raise RuntimeError(f"git_credential PATCH failed {code}: {body}")


def _trigger_scan(client: PortalClient, *, project_id: str) -> str:
    code, body = client.request(
        "POST", f"/v1/projects/{project_id}/scans", json_body={"kind": "source"}
    )
    if code != 202:
        raise RuntimeError(f"scan trigger failed {code}: {body}")
    return body["id"]


def _resolve_git_credential(args: argparse.Namespace) -> str | None:
    return args.git_credential or os.getenv("COHORT_GIT_CREDENTIAL")


def cmd_register(args: argparse.Namespace) -> int:
    conn = cohort.connect(Path(args.cohort_db))
    spec = json.loads(Path(args.input).read_text())
    rows = cohort.load_spec(spec)
    inserted = cohort.seed_targets(conn, args.cohort, rows)
    print(f"[seed] {len(rows)} target(s) in spec, {inserted} newly tracked")

    pending = cohort.targets_to_register(conn, args.cohort, include_failed=args.retry_failed)
    if args.limit:
        pending = pending[: args.limit]
    print(f"[register] {len(pending)} target(s) to process")
    if not pending:
        return 0

    git_credential = _resolve_git_credential(args)
    client = _login(args)
    ok = 0
    for i, row in enumerate(pending, 1):
        label = f"{row['team_slug']}/{row['project_slug']}"
        try:
            team_id = row["team_id"] or _ensure_team(
                client, name=row["team_name"], slug=row["team_slug"]
            )
            cohort.mark_progress(conn, row["id"], team_id=team_id)

            project_id = row["project_id"] or _ensure_project(
                client, team_id=team_id, name=row["project_name"],
                slug=row["project_slug"], git_url=row["git_url"],
            )
            cohort.mark_progress(conn, row["id"], project_id=project_id)

            # Every pass re-sends the credential (even for a reused project_id
            # from a prior/failed pass) rather than tracking a separate
            # "credential set" flag: the PATCH is idempotent, and this is
            # the only way a retry-after-PATCH-failure catches up.
            if git_credential:
                _set_git_credential(client, project_id=project_id, credential=git_credential)

            scan_id = _trigger_scan(client, project_id=project_id)
            cohort.mark_progress(conn, row["id"], scan_id=scan_id)
            ok += 1
            print(f"  [{i}/{len(pending)}] {label} -> scan {scan_id[:8]}", flush=True)
        except Exception as exc:
            cohort.mark_failed(conn, row["id"], f"{type(exc).__name__}: {exc}")
            print(f"  [{i}/{len(pending)}] {label} FAILED: {exc}", flush=True)

    print(f"[register] {ok}/{len(pending)} succeeded this pass")
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    conn = cohort.connect(Path(args.cohort_db))
    client = _login(args)

    def _pass() -> tuple[int, int]:
        rows = cohort.targets_awaiting_scan(conn, args.cohort)
        for row in rows:
            code, body = client.request("GET", f"/v1/scans/{row['scan_id']}")
            if code == 401:
                client.refresh_token()
                code, body = client.request("GET", f"/v1/scans/{row['scan_id']}")
            if code != 200:
                cohort.update_scan_status(conn, row["id"], scan_status="unknown", error=f"poll {code}")
                continue
            status = body.get("status", "unknown")
            error = body.get("error_message") or body.get("metadata", {}).get("error")
            cohort.update_scan_status(conn, row["id"], scan_status=status, error=error)
        return len(rows), sum(1 for r in rows if r["scan_status"] not in cohort.TERMINAL_SCAN_STATUSES)

    if args.watch:
        while True:
            checked, still_running = _pass()
            print(f"[poll] checked {checked}, {still_running} still in flight", flush=True)
            if checked == 0:
                break
            time.sleep(args.interval)
    else:
        checked, still_running = _pass()
        print(f"[poll] checked {checked}, {still_running} still in flight")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = cohort.connect(Path(args.cohort_db))
    s = cohort.summary(conn, args.cohort)
    print(f"cohort={args.cohort!r} total={s['total']}")
    print(f"  register_status: {s['by_register_status']}")
    print(f"  scan_status:     {s['by_scan_status']}")
    if s["failed"]:
        print(f"  failed ({len(s['failed'])}):")
        for f in s["failed"]:
            print(f"    {f['team_slug']}/{f['project_slug']}: {f['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cohort-db", default=os.getenv("COHORT_DB", str(DEFAULT_COHORT_DB)))
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cohort", required=True, help="cohort name, e.g. github-2026-09")
    common.add_argument("--portal-url", default=DEFAULT_PORTAL_URL)
    common.add_argument("--admin-email", default=None)
    common.add_argument("--admin-password", default=None)

    p_register = sub.add_parser("register", parents=[common], help="create teams/projects, trigger scans")
    p_register.add_argument("--input", required=True, help="targets.json (see README.md)")
    p_register.add_argument("--retry-failed", action="store_true", help="also retry previously failed targets")
    p_register.add_argument("--limit", type=int, default=None, help="process at most N targets this pass")
    p_register.add_argument(
        "--git-credential",
        default=None,
        help=(
            "read-only PAT/deploy token PATCHed onto every project via the encrypted "
            "git_credential field, for internal/private targets (e.g. TDE GitLab) the "
            "worker cannot clone unauthenticated. Also via COHORT_GIT_CREDENTIAL env var. "
            "Omit for public targets."
        ),
    )

    p_poll = sub.add_parser("poll", parents=[common], help="check in-flight scan status")
    p_poll.add_argument("--watch", action="store_true", help="loop until nothing is in flight")
    p_poll.add_argument("--interval", type=int, default=30, help="seconds between passes with --watch")

    p_status = sub.add_parser("status", help="print a summary (no portal access needed)")
    p_status.add_argument("--cohort", required=True)

    args = parser.parse_args()
    if args.command == "register":
        return cmd_register(args)
    if args.command == "poll":
        return cmd_poll(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())
