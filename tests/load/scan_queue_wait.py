#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""M1 (concurrency-scaling plan §6): direct scan-queue-wait measurement.

Locust's ``locustfile.py`` (this directory) is built around short, hard-gated
read/write bursts (90s-5m) and is the wrong tool for this measurement: a
scan-trigger class firing once a minute per simulated user almost never
contends for a worker slot, so it cannot see queue depth build up. This
script instead does exactly what the plan's §6 "스캔 처리량" paragraph asks
for: fire N scan triggers at the SAME instant across N distinct projects
(the ``(project, branch)`` active-scan unique index means one project can
only have one in-flight scan, so N concurrent triggers need N projects),
then poll each until it reaches a terminal state and record:

  - trigger time -> ``started_at``     (queue wait)
  - ``started_at`` -> ``completed_at`` (execution duration)
  - completions per hour over the run's span (throughput)
  - how many triggers came back 429 (rate limit / team concurrent-scan cap)

Run this against a dev stack with the worker in load-test delay-injection
mode (dev-only; see ``core.config.scan_load_test_delay_seconds``). The
default ``real``/``mock`` scan backend either takes 5-60 real minutes or
finishes near-instantly, neither of which lets you see a queue build and
drain on a laptop-scale run:

    # backend/worker containers, in addition to the usual dev compose env:
    APP_ENV=dev
    SCAN_LOAD_TEST_DELAY_ENABLED=true
    SCAN_LOAD_TEST_DELAY_SECONDS=20

    # seed enough distinct projects for the largest N you plan to run
    # (5 x the worker slot count, per the plan's 1x/2x/5x sweep):
    docker-compose -f docker-compose.dev.yml exec backend \\
      python scripts/seed_e2e_user.py --project-names \\
      qw01,qw02,qw03,qw04,qw05,qw06,qw07,qw08,qw09,qw10

    # dev compose defaults to CELERY_CONCURRENCY=2, one worker replica -> 2 slots
    python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 1
    python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 2
    python3 tests/load/scan_queue_wait.py --slots 2 --multiplier 5

Compare the printed "predicted vs actual" wait to the plan's §1.1 formula,
``floor((j-1)/S) * M`` (``S`` = slots, ``M`` = this run's own measured mean
duration, not the 20-minute planning assumption). Record what you see in
the tracker (``concurrency-scaling-tracker.md`` §3), not just what passed:
this script has no pass/fail gate; it is a measurement, not a test.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    # API timestamps are ISO 8601 (may carry a trailing "Z"); normalize before
    # datetime.fromisoformat, which does not accept a bare "Z" suffix.
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


@dataclass
class ScanRecord:
    project_id: str
    trigger_time: datetime
    scan_id: str | None = None
    trigger_status: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    final_status: str | None = None
    note: str | None = None


def login(host: str, email: str, password: str) -> str:
    r = requests.post(
        f"{host}/auth/login", json={"email": email, "password": password}, timeout=10
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("login succeeded but no access_token in the response")
    return str(token)


def list_projects(host: str, token: str, limit: int) -> list[str]:
    r = requests.get(
        f"{host}/v1/projects?size={limit}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    r.raise_for_status()
    payload = r.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    return [str(p["id"]) for p in items if isinstance(p, dict) and "id" in p]


def _trigger_one(host: str, token: str, project_id: str) -> ScanRecord:
    trigger_time = datetime.now(timezone.utc)
    rec = ScanRecord(project_id=project_id, trigger_time=trigger_time)
    try:
        r = requests.post(
            f"{host}/v1/projects/{project_id}/scans",
            json={"kind": "source"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
    except requests.RequestException as exc:
        rec.note = f"trigger request failed: {exc}"
        return rec
    rec.trigger_status = r.status_code
    if r.status_code == 202:
        rec.scan_id = r.json().get("id")
    else:
        rec.note = f"trigger body: {r.text[:200]}"
    return rec


def _poll_until_terminal(
    host: str, token: str, rec: ScanRecord, *, timeout_s: float, interval_s: float
) -> None:
    if not rec.scan_id:
        return
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = requests.get(
                f"{host}/v1/scans/{rec.scan_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            rec.note = f"poll request failed: {exc}"
            return
        if r.status_code != 200:
            rec.note = f"poll unexpected status {r.status_code}"
            return
        body = r.json()
        rec.started_at = _parse_dt(body.get("started_at")) or rec.started_at
        rec.completed_at = _parse_dt(body.get("completed_at")) or rec.completed_at
        rec.final_status = body.get("status")
        if rec.final_status in _TERMINAL_STATUSES:
            return
        time.sleep(interval_s)
    rec.note = f"timed out after {timeout_s}s waiting for a terminal state"


def run(
    host: str, token: str, project_ids: list[str], *, poll_timeout: float, poll_interval: float
) -> list[ScanRecord]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(project_ids)) as pool:
        records = list(
            pool.map(lambda pid: _trigger_one(host, token, pid), project_ids)
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(project_ids)) as pool:
        list(
            pool.map(
                lambda rec: _poll_until_terminal(
                    host, token, rec, timeout_s=poll_timeout, interval_s=poll_interval
                ),
                records,
            )
        )
    return records


def summarize(records: list[ScanRecord], *, slots: int) -> None:
    n = len(records)
    ok = [r for r in records if r.trigger_status == 202]
    rate_limited = [r for r in records if r.trigger_status == 429]
    other_failed = [r for r in records if r.trigger_status not in (202, 429)]
    print(
        f"N={n} slots={slots} triggered_202={len(ok)} "
        f"rate_limited_429={len(rate_limited)} other_failed={len(other_failed)}"
    )
    for rec in other_failed:
        print(
            f"  UNEXPECTED trigger_status={rec.trigger_status} "
            f"project={rec.project_id} note={rec.note}"
        )

    durations = [
        (r.completed_at - r.started_at).total_seconds()
        for r in ok
        if r.started_at and r.completed_at
    ]
    mean_duration = statistics.mean(durations) if durations else 0.0

    ordered = sorted(ok, key=lambda r: r.trigger_time)
    queue_waits: list[float] = []
    print(f"per-scan (ordered by trigger time; §1.1 predicted wait = floor((j-1)/{slots}) * M, "
          f"M = this run's own mean duration = {mean_duration:.2f}s):")
    for j, rec in enumerate(ordered, start=1):
        predicted = ((j - 1) // slots) * mean_duration
        if rec.started_at:
            wait = (rec.started_at - rec.trigger_time).total_seconds()
            queue_waits.append(wait)
            print(
                f"  j={j:>3} scan={rec.scan_id} queue_wait={wait:7.2f}s "
                f"predicted={predicted:7.2f}s status={rec.final_status}"
            )
        else:
            reason = rec.note or rec.final_status
            print(f"  j={j:>3} scan={rec.scan_id} queue_wait=<none: {reason}>")

    if queue_waits:
        print(
            f"queue_wait: mean={statistics.mean(queue_waits):.2f}s "
            f"median={statistics.median(queue_waits):.2f}s max={max(queue_waits):.2f}s"
        )
    if durations:
        print(
            f"duration:   mean={statistics.mean(durations):.2f}s "
            f"median={statistics.median(durations):.2f}s max={max(durations):.2f}s"
        )
    completed = [r for r in ok if r.completed_at]
    if completed:
        last_completion = max(r.completed_at for r in completed)
        first_trigger = min(r.trigger_time for r in ok)
        span = (last_completion - first_trigger).total_seconds()
        if span > 0:
            per_hour = len(completed) / span * 3600
            print(f"throughput: {len(completed)} completed in {span:.1f}s -> {per_hour:.1f}/hour")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--email", default="e2e-admin@trustedoss.dev")
    parser.add_argument("--password", default="E2eAdminPass2026")
    parser.add_argument(
        "--slots",
        type=int,
        required=True,
        help="worker slot count of the stack under test "
        "(CELERY_CONCURRENCY x worker replica count)",
    )
    parser.add_argument(
        "--multiplier",
        type=int,
        default=1,
        help="N = slots * multiplier concurrent triggers (plan §6: sweep 1x, 2x, 5x)",
    )
    parser.add_argument("--poll-timeout", type=float, default=180.0)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args()

    n = args.slots * args.multiplier
    token = login(args.host, args.email, args.password)
    project_ids = list_projects(args.host, token, limit=max(n, 100))
    if len(project_ids) < n:
        print(
            f"need >= {n} distinct projects (N = slots * multiplier) so each trigger lands "
            f"on its own project (the (project, branch) active-scan unique index means a "
            f"second trigger on the SAME project 409s instead of queuing). Only "
            f"{len(project_ids)} available for this user. Seed more, e.g.:\n"
            f"  docker-compose -f docker-compose.dev.yml exec backend "
            f"python scripts/seed_e2e_user.py --project-names p1,p2,...,p{n}",
            file=sys.stderr,
        )
        return 2
    project_ids = project_ids[:n]

    print(f"triggering N={n} scans across {n} distinct projects at host={args.host} ...")
    records = run(
        args.host,
        token,
        project_ids,
        poll_timeout=args.poll_timeout,
        poll_interval=args.poll_interval,
    )
    summarize(records, slots=args.slots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
