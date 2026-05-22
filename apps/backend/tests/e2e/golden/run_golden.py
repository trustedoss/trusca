#!/usr/bin/env python3
"""Golden-fixture scan harness — Tier 1 of the test-hardening plan.

Drives each bd-scan fixture through the REAL portal pipeline (cdxgen → scancode
→ DT → preserve) over the live HTTP API, captures a *normalised* snapshot of
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


def scan_fixture(api, token, team, name, src: Path) -> dict:
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
    deadline = time.time() + POLL_TIMEOUT_S
    status = "?"
    while time.time() < deadline:
        s, b, _ = _req("GET", f"{api}/v1/scans/{sid}", token=token)
        status = json.loads(b).get("status", "?")
        if status in ("succeeded", "failed", "cancelled", "completed"):
            break
        time.sleep(5)
    return _capture(api, token, pid, sid, status)


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
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--names", nargs="+", required=True)
    ap.add_argument("--update", action="store_true")
    args = ap.parse_args()

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


if __name__ == "__main__":
    sys.exit(main())
