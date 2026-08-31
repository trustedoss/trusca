#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""collector.py, polls GET /metrics and appends every scrape to store.py.

self-resource-validation-plan-2026-08-30.md §6-6. Meant to run for the
whole of S4's soak window (90 days), so it is built to survive that
unattended: a fetch failure (network, 404 because the endpoint is off or
the token is wrong, a 5xx) is recorded as a failed row and the loop keeps
going rather than raising, and SIGINT/SIGTERM stop it between scrapes
rather than mid-request.

Usage:
    python3 collector.py --portal-url http://localhost:8000 \\
        --interval 300 --token "$METRICS_TOKEN"

See README.md for how to enable METRICS_ENABLED / QUEUE_BACKLOG_METRICS_ENABLED
on the soak instance first -- this collects whatever the endpoint answers,
it does not turn either flag on itself.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import store

DEFAULT_DB = Path(__file__).resolve().parent / "soak-metrics.db"
DEFAULT_INTERVAL_SECONDS = 300

_stop = False


def _handle_stop(_signum: int, _frame: object) -> None:
    global _stop
    _stop = True


def _fetch(url: str, *, token: str | None, timeout: int) -> tuple[int | None, str | None, str | None]:
    """One scrape attempt. Returns (status_code, body, error) -- exactly one
    of body/error is set on a completed request; both are None only if the
    request never got a response at all (shouldn't happen, defensive)."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as exc:
        # A 404 is this endpoint's normal "off, or wrong token" answer
        # (api/v1/metrics.py) -- still worth recording so a report can show
        # the collector was configured but the target wasn't ready yet,
        # rather than that indistinguishable from a network outage.
        return exc.code, None, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, None, str(exc.reason)
    except Exception as exc:  # noqa: BLE001 -- must never kill a 90-day loop
        return None, None, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--portal-url", default=os.getenv("PORTAL_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("METRICS_TOKEN"))
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--db", default=os.getenv("SOAK_METRICS_DB", str(DEFAULT_DB)))
    parser.add_argument(
        "--once", action="store_true", help="scrape a single time and exit (smoke test)"
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    url = f"{args.portal_url.rstrip('/')}/metrics"
    conn = store.connect(Path(args.db))
    print(f"[soak-metrics] polling {url} every {args.interval}s -> {args.db}", flush=True)

    ok = 0
    failed = 0
    while True:
        status, body, error = _fetch(url, token=args.token, timeout=30)
        run_id = store.record_scrape(conn, status_code=status, body=body, error=error)
        if body is not None:
            ok += 1
            samples = store.parse_samples(body)
            print(f"[soak-metrics] scrape {run_id}: {len(samples)} sample(s), status={status}", flush=True)
        else:
            failed += 1
            print(f"[soak-metrics] scrape {run_id} FAILED: status={status} error={error}", flush=True)

        if args.once:
            break
        for _ in range(args.interval):
            if _stop:
                break
            time.sleep(1)
        if _stop:
            break

    print(f"[soak-metrics] stopping: {ok} ok, {failed} failed", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
