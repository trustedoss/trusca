# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Preflight: evaluate every environment-variable accessor in `core.config`.

`main.py`'s lifespan only calls a handful of `core.config` accessors before
serving traffic (`secret_key()`, `api_key_hmac_secret()`,
`validate_demo_sandbox_limits()`). That handful exists because each one
already burned someone: a template `SECRET_KEY` or `API_KEY_HMAC_SECRET`
passed the health check and the install smoke test, then 500ed every
authenticated call once real traffic hit the accessor lazily, on whichever
request happened to need it first. `core/config.py` reads roughly 266
environment variables through ~180 zero-argument accessor functions, and
lifespan checks 3 of them. The other ~260 fail the same way, just later and
on a different request path.

This script closes that gap by CALLING every such accessor rather than
listing what they read: a typo'd numeric env var, a malformed rate-limit
string, an out-of-range value, anything an accessor's own validation would
reject is caught here, before a single request is served, instead of on
whichever request first exercises that one accessor.

What it does NOT do: connect to Postgres or Redis, or validate anything that
requires a live network call (a DATABASE_URL/REDIS_URL string is validated as
a string; whether the endpoint answers is `/health/ready`'s job, not this
script's). This is a pure-Python config-surface check, meant to run before
the process that would make those connections starts.

Usage (run inside the backend image, where `core.config`'s dependencies are
installed - a bare host running install.sh/upgrade.sh has neither)::

    python -m scripts.check_config             # human-readable report
    python -m scripts.check_config --quiet     # exit code only, no stdout on success

Exit code 0 when every accessor evaluates without raising; 1 otherwise, with
each failing accessor's name and error printed. Called from `install.sh`,
`upgrade.sh` (both via `docker-compose run --rm backend ...`, before the
stack is brought up), and a Helm pre-upgrade hook.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Callable
from typing import Any


def _accessors() -> list[tuple[str, Callable[[], Any]]]:
    """Every public, zero-required-argument function `core.config` defines.

    Excludes private helpers (`_int_env` and friends) and the handful of
    accessors that take a required argument (`vuln_sla_days(severity)`,
    `scan_progress_channel(scan_id)`, ...) - those are not environment-variable
    accessors in the sense this check cares about; they are parameterised
    lookups a caller drives with a real value, and a real value is exactly
    what a preflight pass run with no request in flight does not have.
    """
    import core.config as config

    found: list[tuple[str, Callable[[], Any]]] = []
    for name, obj in vars(config).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if obj.__module__ != config.__name__:
            continue
        params = inspect.signature(obj).parameters.values()
        if any(p.default is inspect.Parameter.empty for p in params):
            continue
        found.append((name, obj))
    return sorted(found, key=lambda item: item[0])


def run(*, quiet: bool) -> int:
    accessors = _accessors()
    failures: list[tuple[str, str]] = []
    for name, fn in accessors:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the point is to catch every accessor's own validation error, not just ones we anticipated
            failures.append((name, f"{type(exc).__name__}: {exc}"))

    if failures:
        print(f"check-config: {len(failures)}/{len(accessors)} accessor(s) failed:\n")
        for name, error in failures:
            print(f"  {name}: {error}")
        print(
            "\nEach of these raised while reading its environment variable(s); "
            "fix the value(s) in your .env / Helm values before starting the app."
        )
        return 1

    if not quiet:
        print(f"check-config: {len(accessors)}/{len(accessors)} accessor(s) OK")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on success; failures are always printed",
    )
    args = parser.parse_args()
    sys.exit(run(quiet=args.quiet))


if __name__ == "__main__":
    main()
