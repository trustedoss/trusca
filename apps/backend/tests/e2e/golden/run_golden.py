#!/usr/bin/env python3
"""Golden-fixture scan harness — Tier 1 of the test-hardening plan.

Drives each baseline-scan fixture through the REAL portal pipeline (cdxgen → scancode
→ Trivy → preserve) over the live HTTP API, captures a *normalised* snapshot of
the scan output (components incl. transitive, license categories, source-tree,
NOTICE/SBOM/PDF availability), and diffs it against a committed baseline.

Why this exists: the fixtures e2e session proved that mocked unit/integration
tests pass while real-tool output drifts (spurious ``pkg:nix`` components,
compound-SPDX mis-classification). Asserting on *normalised output content*
against a baseline catches exactly that drift.

stdlib only (urllib / zipfile / json) so it runs on the host, in the backend
container, or in CI with no extra deps.

Usage:
  python run_golden.py --api http://localhost:8000 --fixtures <dir> \
      --names scancode-mixed-policy gradle maven ...
  python run_golden.py ... --update      # (re)write baselines instead of diffing
  python run_golden.py ... --shadow-out <dir>   # record for shadow_report.py

Exit code 0 = all match (or baselines written); 1 = drift / error.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

DEFAULT_API = os.getenv("GOLDEN_API", "http://localhost:8000")
DEFAULT_EMAIL = os.getenv("GOLDEN_EMAIL", "e2e-admin@trustedoss.dev")
DEFAULT_PASSWORD = os.getenv("GOLDEN_PASSWORD", "E2eAdminPass2026")
DEFAULT_TEAM = os.getenv("GOLDEN_TEAM", "")
BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
POLL_TIMEOUT_S = int(os.getenv("GOLDEN_POLL_TIMEOUT", "240"))


def parse_names(raw: str, available: list[str]) -> list[str]:
    """Resolve ``GOLDEN_NAMES`` (space or comma separated) against the baselines.

    Empty means every baseline. A name with no baseline is an error rather than
    a silently smaller run: a typo in a workflow shard would otherwise shrink
    the gate to nothing and still report green.
    """
    wanted = [n for n in raw.replace(",", " ").split() if n]
    if not wanted:
        return list(available)
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise ValueError(f"GOLDEN_NAMES has no baseline for: {', '.join(unknown)}")
    return wanted


def strict_from_env() -> bool:
    """``GOLDEN_STRICT=1``: an unreachable stack or missing fixture fails the case.

    The default (skip) suits a developer without the stack. In the nightly
    workflow a skip is a hole: the run is green and nothing was compared.
    """
    return os.getenv("GOLDEN_STRICT", "") not in ("", "0", "false")


# ---------------------------------------------------------------------------
# Tiny stdlib HTTP helpers
# ---------------------------------------------------------------------------
def _req(method, url, *, token=None, json_body=None, raw_body=None, ctype=None):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if ctype:
            headers["Content-Type"] = ctype
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _multipart_zip(field, filename, zbytes):
    boundary = f"----golden{uuid.uuid4().hex}"
    body = io.BytesIO()
    body.write(f"--{boundary}\r\n".encode())
    body.write(
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
    )
    body.write(b"Content-Type: application/zip\r\n\r\n")
    body.write(zbytes)
    body.write(f"\r\n--{boundary}--\r\n".encode())
    return body.getvalue(), f"multipart/form-data; boundary={boundary}"


def _zip_dir(src: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pipeline drive + normalised capture
# ---------------------------------------------------------------------------
def login(api):
    s, b, _ = _req("POST", f"{api}/auth/login",
                   json_body={"email": DEFAULT_EMAIL, "password": DEFAULT_PASSWORD})
    if s != 200:
        raise RuntimeError(f"login failed {s}: {b[:120]!r}")
    return json.loads(b)["access_token"]


def _team(api, token):
    if DEFAULT_TEAM:
        return DEFAULT_TEAM
    s, b, _ = _req("GET", f"{api}/v1/admin/teams?size=1", token=token)
    if s == 200:
        items = json.loads(b).get("items") or []
        if items:
            return items[0]["id"]
    raise RuntimeError("no team available (set GOLDEN_TEAM)")


def scan_fixture(api, token, team, name, src: Path, *, shadow: bool = False) -> dict:
    slug = f"golden-{name}-{uuid.uuid4().hex[:8]}"
    s, b, _ = _req("POST", f"{api}/v1/projects", token=token,
                   json_body={"team_id": team, "name": f"golden:{name}",
                              "slug": slug, "visibility": "team"})
    if s != 201 and s != 200:
        raise RuntimeError(f"create project failed {s}: {b[:160]!r}")
    pid = json.loads(b)["id"]
    body, ctype = _multipart_zip("upload", f"{name}.zip", _zip_dir(src))
    s, b, _ = _req("POST", f"{api}/v1/projects/{pid}/source-archive",
                   token=token, raw_body=body, ctype=ctype)
    if s != 200 and s != 201:
        raise RuntimeError(f"upload failed {s}: {b[:160]!r}")
    aid = json.loads(b)["archive_id"]
    s, b, _ = _req("POST", f"{api}/v1/projects/{pid}/scans", token=token,
                   json_body={"kind": "source",
                              "metadata": {"source_type": "upload", "archive_id": aid}})
    if s not in (200, 201, 202):
        raise RuntimeError(f"trigger failed {s}: {b[:160]!r}")
    sid = json.loads(b)["id"]
    started = time.time()
    deadline = started + POLL_TIMEOUT_S
    status = "?"
    scan: dict = {}
    while time.time() < deadline:
        s, b, _ = _req("GET", f"{api}/v1/scans/{sid}", token=token)
        scan = json.loads(b)
        status = scan.get("status", "?")
        if status in ("succeeded", "failed", "cancelled", "completed"):
            break
        time.sleep(5)
    got = _capture(api, token, pid, sid, status)
    if shadow:
        # Shadow runs only: the baseline diff never sees this key.
        got["shadow"] = _shadow_capture(api, token, pid, scan, time.time() - started)
    return got


def _capture(api, token, pid, sid, status) -> dict:
    def g(path):
        s, b, h = _req("GET", f"{api}{path}", token=token)
        return s, b, h

    s, b, _ = g(f"/v1/projects/{pid}/components?size=500")
    comp = json.loads(b) if s == 200 else {"items": [], "total": 0}
    purls = sorted((c.get("purl") or "") for c in comp.get("items", []))
    s, b, _ = g(f"/v1/projects/{pid}/licenses")
    lic = json.loads(b).get("items", []) if s == 200 else []
    licenses = sorted(
        {f"{x.get('spdx_id')}|{x.get('category')}|{x.get('kind')}" for x in lic}
    )
    s, b, _ = g(f"/v1/projects/{pid}/vulnerabilities")
    vc = (json.loads(b).get("total") if s == 200 else None)
    s, b, _ = g(f"/v1/projects/{pid}/source-tree?scan_id={sid}")
    st_entries = len(json.loads(b).get("entries", [])) if s == 200 else -1
    nt = g(f"/v1/projects/{pid}/notice?format=text")[0]
    nh_s, _, nh_h = g(f"/v1/projects/{pid}/notice?format=html")
    csp = "content-security-policy" in {k.lower() for k in nh_h}
    sbom = {}
    for fmt in ("cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv"):
        sbom[fmt] = g(f"/v1/projects/{pid}/sbom?format={fmt}")[0] == 200
    ps, pbytes, _ = g(f"/v1/projects/{pid}/vulnerability-report.pdf")
    pdf_ok = ps == 200 and pbytes[:5] == b"%PDF-"
    # Tier H — governance enforcement end-to-end: a real scan with a forbidden
    # license / critical CVE must drive gate=fail. Captured + baselined so a
    # regression that lets a forbidden license PASS the gate is caught.
    gs, gb, _ = g(f"/v1/projects/{pid}/gate-result")
    gate: dict[str, object] = {}
    if gs == 200:
        gd = json.loads(gb)
        gate = {
            "gate": gd.get("gate"),
            "critical_cve": gd.get("critical_cve_count"),
            "forbidden_license": gd.get("forbidden_license_count"),
        }
    return {
        "scan_status": status,
        "components": {"count": comp.get("total"), "purls": purls,
                       "nix_spurious": sum(1 for p in purls if "pkg:nix" in p)},
        "licenses": licenses,
        "vulnerabilities_count": vc,
        "source_tree_root_entries": st_entries,
        "notice": {"text_ok": nt == 200, "html_ok": nh_s == 200, "csp": csp},
        "sbom": sbom,
        "report_pdf_ok": pdf_ok,
        "gate": gate,
    }


#: Longest error message kept in a shadow capture.
_SHADOW_TEXT_MAX = 300


def _vulnerability_rows(api, token, pid) -> list[dict] | None:
    """Every vulnerability row of the project's latest scan, page by page."""
    rows: list[dict] = []
    offset = 0
    while True:
        s, b, _ = _req("GET", f"{api}/v1/projects/{pid}/vulnerabilities"
                       f"?limit=500&offset={offset}", token=token)
        if s != 200:
            return None
        page = json.loads(b)
        items = page.get("items") or []
        rows.extend(items)
        offset += len(items)
        if not items or offset >= int(page.get("total") or 0):
            return rows


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def shadow_fields(scan: dict, rows: list[dict] | None, wall_seconds: float) -> dict:
    """What a shadow run records beyond the baseline capture.

    Both engines read the same Trivy database in a shadow run, so the
    vulnerability identifiers are comparable there (the baseline diff leaves
    the count out because the database moves between nights). The scan row
    adds the engine that ran, the stages that degraded and the time it took.
    """
    if rows is None:
        ids = findings = None
    else:
        ids = sorted({str(r.get("cve_id") or "") for r in rows})
        findings = sorted(
            f"{r.get('cve_id')}|{r.get('affected_component_name')}"
            f"@{r.get('affected_component_version')}"
            for r in rows
        )
    meta = _as_dict(scan.get("metadata"))
    engine = _as_dict(meta.get("scan_engine"))
    outcomes = _as_dict(meta.get("stage_outcomes"))
    error = scan.get("error_message")
    return {
        "vulnerability_ids": ids,
        "vulnerability_findings": findings,
        "scan": {
            "status": scan.get("status"),
            # The in-process pipeline writes no engine record.
            "engine": engine.get("name") or "inprocess",
            "engine_run_status": engine.get("run_status"),
            "engine_image_digest": engine.get("image_digest"),
            "started_at": scan.get("started_at"),
            "completed_at": scan.get("completed_at"),
            "stage_outcomes": {
                str(k): (v.get("reason") if isinstance(v, dict) else v)
                for k, v in sorted(outcomes.items())
            },
            "scancode_skipped": meta.get("scancode_skipped"),
            "error_message": error[:_SHADOW_TEXT_MAX] if isinstance(error, str) else None,
        },
        "wall_seconds": round(wall_seconds, 1),
    }


def _shadow_capture(api, token, pid, scan: dict, wall_seconds: float) -> dict:
    return shadow_fields(scan, _vulnerability_rows(api, token, pid), wall_seconds)


def write_shadow(out_dir: Path, name: str, record: dict) -> Path:
    """Write one shadow capture as ``<out_dir>/<name>.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--names", nargs="+", required=True)
    ap.add_argument("--update", action="store_true")
    ap.add_argument(
        "--shadow-out",
        help="shadow run: write each capture, with vulnerability ids and the scan "
        "row, to this folder instead of diffing; a difference never fails it",
    )
    args = ap.parse_args()
    if args.shadow_out and args.update:
        ap.error("--shadow-out and --update are exclusive")
    if args.shadow_out:
        return shadow_main(args.api, Path(args.fixtures), args.names, Path(args.shadow_out))

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    token = login(args.api)
    team = _team(args.api, token)
    drift = 0
    for name in args.names:
        src = Path(args.fixtures) / name
        if not src.is_dir():
            print(f"MISSING {name}")
            drift += 1
            continue
        try:
            got = scan_fixture(args.api, token, team, name, src)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {name}: {exc}")
            drift += 1
            continue
        bl = BASELINE_DIR / f"{name}.json"
        if args.update:
            bl.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
            print(f"WROTE {name}: comps={got['components']['count']} "
                  f"nix={got['components']['nix_spurious']} lic={len(got['licenses'])}")
            continue
        if not bl.exists():
            print(f"NO BASELINE {name} (run --update)")
            drift += 1
            continue
        want = json.loads(bl.read_text())
        # vulnerabilities_count is NVD-dependent → excluded from the diff.
        gc = {k: v for k, v in got.items() if k != "vulnerabilities_count"}
        wc = {k: v for k, v in want.items() if k != "vulnerabilities_count"}
        if gc == wc:
            print(f"OK {name}")
        else:
            drift += 1
            print(f"DRIFT {name}:")
            for k in sorted(set(gc) | set(wc)):
                if gc.get(k) != wc.get(k):
                    print(f"   {k}: baseline={wc.get(k)} got={gc.get(k)}")
    print(f"=== golden: {'updated' if args.update else 'checked'} "
          f"{len(args.names)} fixtures, drift={drift} ===")
    return 1 if (drift and not args.update) else 0


def shadow_main(api: str, fixtures: Path, names: list[str], out_dir: Path) -> int:
    """Scan each fixture once and record it for the shadow comparison.

    A failed scan or a missing fixture is recorded, not raised: the report
    shows it beside the other engine's result. Only a stack that cannot be
    logged into stops the run, since then nothing was measured at all.
    """
    token = login(api)
    team = _team(api, token)
    for name in names:
        src = fixtures / name
        record: dict
        if not src.is_dir():
            record = {"error": "fixture missing"}
        else:
            try:
                record = scan_fixture(api, token, team, name, src, shadow=True)
            except Exception as exc:  # noqa: BLE001
                record = {"error": f"{type(exc).__name__}: {str(exc)[:_SHADOW_TEXT_MAX]}"}
        write_shadow(out_dir, name, record)
        print(f"SHADOW {name}: {record.get('scan_status') or record.get('error')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
