#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Per-stage timing of one SBOM ingest scan, measured from the outside.

Uploads a CycloneDX SBOM to a new project (``POST /v1/projects/{id}/sbom-ingest``)
and polls ``GET /v1/scans/{id}`` until the scan ends, recording when each
``current_step`` was first seen. The ingest task moves through bootstrap,
conformance, components (the persist stage, where the license fetcher runs),
trivy and finalize, so the "components" duration is the persist cost and
``started_at - created_at`` is the queue wait.

It uses only the public API and the standard library, so the same file measures
a laptop stack, a staging cluster or a load-generator run without changes. It
does not toggle anything on the server: the worker's ``LICENSE_FETCH_ENABLED``
is set where the worker runs, and ``--label`` only names the run.

Durations come from poll timestamps, so each has an error of up to one
``--poll-interval``. A stage shorter than that can be missed entirely.

A sample keeps the ecosystem mix: components are taken at a fixed stride, not
from the front. In the 10,198-component fixture the first 1,000 components are
all npm, while the whole document is 71% npm, 16% gem and 12% pypi, and the
license fetcher sends each ecosystem to a different service (gem to RubyGems,
pypi to PyPI) and, by default, makes no request at all for npm, because the
ClearlyDefined fallback is off unless ``CLEARLYDEFINED_ENABLED`` is set. Each
run writes a new ``serialNumber`` so repeated runs are distinct documents.

Example (10,198-component fixture, license fetch off on the worker):

    FIXTURES=apps/backend/tests/fixtures/sbom_ingest
    python3 tests/load/ingest_stage_timing.py \\
        --sbom $FIXTURES/real_cyclonedx_large_multi_10198.cdx.json.gz \\
        --label fetch-off --json out/fetch-off-full.json
"""

from __future__ import annotations

import argparse
import copy
import gzip
import io
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

TERMINAL = ("succeeded", "failed", "cancelled", "completed")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/unit/test_load_ingest_stage_timing.py)
# ---------------------------------------------------------------------------
def count_unlicensed(components: list[dict[str, Any]]) -> int:
    """Components with no license entry: the ones the license fetcher may call out for."""
    return sum(1 for c in components if not c.get("licenses"))


def sample_document(doc: dict[str, Any], n: int) -> dict[str, Any]:
    """A copy of ``doc`` with ``n`` components taken at a fixed stride.

    ``n <= 0`` or ``n >= len(components)`` keeps every component. The dependency
    graph is cut to the kept components so no edge points at a missing one, and
    ``serialNumber`` is replaced so the copy is a new document.
    """
    out = copy.deepcopy(doc)
    comps = out.get("components", [])
    if 0 < n < len(comps):
        total = len(comps)
        picked = sorted({i * total // n for i in range(n)})
        comps = [comps[i] for i in picked]
        out["components"] = comps
    kept = {c["bom-ref"] for c in comps if c.get("bom-ref")}
    root = (out.get("metadata", {}).get("component") or {}).get("bom-ref")
    if root:
        kept.add(root)
    deps = []
    for entry in out.get("dependencies", []):
        if entry.get("ref") not in kept:
            continue
        entry = dict(entry)
        entry["dependsOn"] = [r for r in entry.get("dependsOn", []) if r in kept]
        deps.append(entry)
    if "dependencies" in out:
        out["dependencies"] = deps
    out["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"
    return out


def derive_steps(
    observations: list[tuple[float, str | None]], finished_at: float
) -> list[dict[str, Any]]:
    """Collapse polled ``(time, current_step)`` samples into one span per step.

    A span starts when its step was first seen and ends when the next step was
    first seen, or at ``finished_at`` for the last one. Samples with no step
    (a queued scan) are skipped.
    """
    spans: list[dict[str, Any]] = []
    for t, step in observations:
        if not step:
            continue
        if spans and spans[-1]["step"] == step:
            continue
        spans.append({"step": step, "start": t})
    for i, span in enumerate(spans):
        end = spans[i + 1]["start"] if i + 1 < len(spans) else finished_at
        span["seconds"] = round(max(end - span["start"], 0.0), 3)
    return spans


def seconds_between(first: str | None, second: str | None) -> float | None:
    """Server-side ISO timestamps to a difference in seconds, or None if either is missing."""
    if not first or not second:
        return None
    a = datetime.fromisoformat(first.replace("Z", "+00:00"))
    b = datetime.fromisoformat(second.replace("Z", "+00:00"))
    return round((b - a).total_seconds(), 3)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    json_body: Any = None,
    raw_body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    headers: dict[str, str] = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"only http and https URLs are supported: {url!r}")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - scheme checked above
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _multipart(field: str, filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = f"----ingesttiming{uuid.uuid4().hex}"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    disposition = f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'
    body.write(f"{disposition}\r\n".encode())
    body.write(b"Content-Type: application/json\r\n\r\n")
    body.write(payload)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def _load_document(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    doc: dict[str, Any] = json.loads(raw)
    return doc


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> dict[str, Any]:
    api = args.api.rstrip("/")
    doc = sample_document(_load_document(Path(args.sbom)), args.sample)
    components = doc.get("components", [])
    payload = json.dumps(doc, separators=(",", ":")).encode()

    status, body = _request(
        "POST", f"{api}/auth/login", json_body={"email": args.email, "password": args.password}
    )
    if status != 200:
        raise SystemExit(f"login failed: {status} {body[:160]!r}")
    token = json.loads(body)["access_token"]

    team = args.team
    if not team:
        status, body = _request("GET", f"{api}/v1/admin/teams?size=1", token=token)
        items = json.loads(body).get("items") if status == 200 else None
        if not items:
            raise SystemExit("no team found; pass --team")
        team = items[0]["id"]

    slug = f"ingest-timing-{uuid.uuid4().hex[:10]}"
    status, body = _request(
        "POST",
        f"{api}/v1/projects",
        token=token,
        json_body={"team_id": team, "name": slug, "slug": slug, "visibility": "team"},
    )
    if status not in (200, 201):
        raise SystemExit(f"create project failed: {status} {body[:160]!r}")
    project_id = json.loads(body)["id"]

    multipart, content_type = _multipart("sbom", "timing.cdx.json", payload)
    uploaded_at = time.monotonic()
    status, body = _request(
        "POST",
        f"{api}/v1/projects/{project_id}/sbom-ingest",
        token=token,
        raw_body=multipart,
        content_type=content_type,
    )
    accepted_at = time.monotonic()
    if status != 202:
        raise SystemExit(f"ingest failed: {status} {body[:200]!r}")
    scan = json.loads(body)
    scan_id = scan["id"]

    observations: list[tuple[float, str | None]] = []
    last: dict[str, Any] = scan
    deadline = uploaded_at + args.timeout
    finished_at = accepted_at
    while time.monotonic() < deadline:
        status, body = _request("GET", f"{api}/v1/scans/{scan_id}", token=token)
        now = time.monotonic()
        if status == 200:
            last = json.loads(body)
            observations.append((now - accepted_at, last.get("current_step")))
            if last.get("status") in TERMINAL:
                finished_at = now
                break
        time.sleep(args.poll_interval)
    else:
        raise SystemExit(f"scan {scan_id} did not finish within {args.timeout}s")

    status, body = _request(
        "GET", f"{api}/v1/projects/{project_id}/components?size=1", token=token
    )
    persisted = json.loads(body).get("total") if status == 200 else None

    steps = derive_steps(observations, finished_at - accepted_at)
    return {
        "label": args.label,
        "scan_id": scan_id,
        "project_id": project_id,
        "final_status": last.get("status"),
        "error_message": last.get("error_message"),
        "components_uploaded": len(components),
        "components_unlicensed": count_unlicensed(components),
        "components_persisted": persisted,
        "upload_bytes": len(payload),
        "upload_seconds": round(accepted_at - uploaded_at, 3),
        "queue_wait_seconds": seconds_between(scan.get("created_at"), last.get("started_at")),
        "run_seconds": seconds_between(last.get("started_at"), last.get("completed_at")),
        "steps": steps,
        "poll_interval": args.poll_interval,
        "client": {
            "cpu_count": os.cpu_count(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
    }


def print_report(result: dict[str, Any]) -> None:
    print(f"label            {result['label']}")
    print(
        f"components       {result['components_uploaded']} uploaded, "
        f"{result['components_unlicensed']} without a license, "
        f"{result['components_persisted']} persisted"
    )
    print(f"final status     {result['final_status']}")
    if result["error_message"]:
        print(f"error            {result['error_message']}")
    print(f"upload           {result['upload_seconds']} s ({result['upload_bytes']} bytes)")
    print(f"queue wait       {result['queue_wait_seconds']} s")
    print(f"run              {result['run_seconds']} s")
    for span in result["steps"]:
        print(f"  {span['step']:<14} {span['seconds']:>9} s")
    print(f"(poll interval {result['poll_interval']} s; each span is good to about one interval)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--api", default=os.getenv("LOAD_HOST", "http://localhost:8000"))
    ap.add_argument("--email", default=os.getenv("LOAD_TEST_EMAIL", "e2e-admin@trustedoss.dev"))
    ap.add_argument("--password", default=os.getenv("LOAD_TEST_PASSWORD", ""))
    ap.add_argument("--team", default=os.getenv("LOAD_TEAM", ""))
    ap.add_argument("--sbom", required=True, help="CycloneDX JSON, or .json.gz")
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="components to keep, at a fixed stride; 0 keeps all",
    )
    ap.add_argument("--label", default="run")
    ap.add_argument("--poll-interval", type=float, default=0.25)
    ap.add_argument("--timeout", type=float, default=7200.0)
    ap.add_argument("--json", default="", help="also write the result to this file")
    args = ap.parse_args()
    if not args.password:
        print("set --password or LOAD_TEST_PASSWORD", file=sys.stderr)
        return 2

    result = run(args)
    print_report(result)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["final_status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
