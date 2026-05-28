#!/usr/bin/env python3
"""scan-bench — fixture/real-world 일괄 스캔 자동화.

CLAUDE.md 운영 규칙 준수:
- os.getenv 런타임 호출 (모듈 상수 캐싱 금지)
- docker-compose V1 가정 (portal 외부에서 호출하므로 영향 없음)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

PORTAL_URL = "http://localhost:8000"
EMAIL = "frontend-admin@demo.trustedoss.dev"
PASSWORD = "DemoTest2026!"

FIXTURES_ROOT = Path.home() / "projects" / "bd-scan" / "tests" / "fixtures" / "projects"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"
ZIP_DIR = Path(__file__).resolve().parent / "sources"

EXCLUDE_DIRS = {
    ".git", "node_modules", "target", "build", "dist", ".gradle",
    ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".next", ".turbo", ".idea", ".vscode",
}
EXCLUDE_FILES = {".DS_Store"}

POLL_INTERVAL_SEC = 5
SCAN_TIMEOUT_SEC = 60 * 60  # 60 min hard ceiling


# ---------------------------------------------------------------------------
# tiny urllib wrapper (no extra dep)
# ---------------------------------------------------------------------------


class PortalClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token: str | None = None
        self.refresh_cookie: str | None = None  # raw value
        self.cookie_jar = CookieJar()

    def login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/auth/login",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            self.access_token = data["access_token"]
            # Extract refresh cookie from Set-Cookie header
            set_cookie = resp.headers.get_all("Set-Cookie") or []
            for c in set_cookie:
                m = re.match(r"refresh_token=([^;]+)", c)
                if m:
                    self.refresh_cookie = m.group(1)
                    break

    def refresh_token(self) -> None:
        if not self.refresh_cookie:
            raise RuntimeError("no refresh cookie — cannot refresh")
        req = urllib.request.Request(
            f"{self.base_url}/auth/refresh",
            method="POST",
            headers={"Cookie": f"refresh_token={self.refresh_cookie}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            self.access_token = data["access_token"]
            set_cookie = resp.headers.get_all("Set-Cookie") or []
            for c in set_cookie:
                m = re.match(r"refresh_token=([^;]+)", c)
                if m:
                    self.refresh_cookie = m.group(1)
                    break

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        multipart: tuple[str, bytes, str] | None = None,
        timeout: int = 60,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        if params:
            q = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
            url = f"{url}?{q}"

        headers = self._auth_headers()
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif multipart is not None:
            field_name, content, filename = multipart
            boundary = f"----scanbench{uuid.uuid4().hex}"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: application/zip\r\n\r\n"
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
            data = body

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"raw": raw.decode("utf-8", errors="replace")}
            return e.code, payload


# ---------------------------------------------------------------------------
# zip builder
# ---------------------------------------------------------------------------


def zip_source(src: Path, dest: Path, *, extra_excludes: list[str] | None = None) -> int:
    """Zip src directory into dest, excluding heavy dirs. Returns byte size.

    ``extra_excludes`` are relative-path prefixes to skip entirely (per-target
    overrides for test corpora that include known zip-bomb-looking fixtures).
    """
    excludes = extra_excludes or []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fn in files:
                if fn in EXCLUDE_FILES:
                    continue
                fpath = Path(root) / fn
                try:
                    arc = fpath.relative_to(src)
                except ValueError:
                    continue
                arc_str = str(arc)
                if any(arc_str == p or arc_str.startswith(p + "/") for p in excludes):
                    continue
                # Skip files larger than 50 MiB (binary blobs)
                try:
                    if fpath.stat().st_size > 50 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                zf.write(fpath, arc)
    return dest.stat().st_size


# ---------------------------------------------------------------------------
# scan orchestration
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    suite: str
    name: str
    slug: str
    source_path: str
    ecosystem: str = ""
    project_id: str = ""
    scan_id: str = ""
    archive_bytes: int = 0
    scan_status: str = ""
    scan_started_at: str = ""
    scan_finished_at: str = ""
    scan_duration_sec: float = 0.0
    component_count: int = 0
    direct_count: int = 0
    license_allowed: int = 0
    license_conditional: int = 0
    license_forbidden: int = 0
    license_unknown: int = 0
    cve_total: int = 0
    cve_critical: int = 0
    cve_high: int = 0
    cve_medium: int = 0
    cve_low: int = 0
    cve_info: int = 0
    cve_unknown: int = 0
    risk_score: float = 0.0
    security_score: float = 0.0
    license_score: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["notes"] = "; ".join(self.notes)
        return d


def slugify(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:48]


def detect_ecosystem(src: Path) -> str:
    files = {p.name for p in src.iterdir() if p.is_file()}
    sub = {p.name for p in src.iterdir() if p.is_dir()}
    if "package.json" in files:
        return "npm"
    if "pom.xml" in files:
        return "maven"
    if "build.gradle" in files or "build.gradle.kts" in files or "settings.gradle" in files:
        return "gradle"
    if "go.mod" in files:
        return "go"
    if "Cargo.toml" in files:
        return "rust"
    if "Gemfile" in files:
        return "ruby"
    if "composer.json" in files:
        return "php"
    if "requirements.txt" in files or "pyproject.toml" in files:
        return "python"
    if any(p.endswith(".csproj") or p.endswith(".sln") for p in files):
        return "dotnet"
    # multi-component / nested
    if sub & {"backend", "frontend", "api", "web"}:
        return "multi"
    return "unknown"


def ensure_project(client: PortalClient, *, team_id: str, name: str, slug: str) -> str:
    """Create project or reuse if slug already exists."""
    code, body = client.request(
        "POST",
        "/v1/projects",
        json_body={
            "team_id": team_id,
            "name": name,
            "slug": slug,
            "description": f"scan-bench fixture ({slug})",
            "visibility": "team",
        },
    )
    if code == 201:
        return body["id"]
    if code == 409:
        # Slug already used — find it
        code2, body2 = client.request(
            "GET",
            "/v1/projects",
            params={"team_id": team_id, "q": slug, "size": 100},
        )
        if code2 == 200:
            for item in body2.get("items", []):
                if item["slug"] == slug:
                    return item["id"]
        raise RuntimeError(f"slug conflict but could not find existing project: {body}")
    raise RuntimeError(f"project create failed {code}: {body}")


def upload_and_scan(
    client: PortalClient,
    *,
    project_id: str,
    zip_path: Path,
    release: str | None = None,
) -> tuple[str, str]:
    """Upload zip, trigger scan, return (archive_id, scan_id)."""
    content = zip_path.read_bytes()
    code, body = client.request(
        "POST",
        f"/v1/projects/{project_id}/source-archive",
        multipart=("upload", content, zip_path.name),
        timeout=120,
    )
    if code != 201:
        raise RuntimeError(f"archive upload failed {code}: {body}")
    archive_id = body["archive_id"]

    metadata: dict[str, Any] = {"source_type": "upload", "archive_id": archive_id}
    if release:
        metadata["release"] = release
    code, body = client.request(
        "POST",
        f"/v1/projects/{project_id}/scans",
        json_body={"kind": "source", "metadata": metadata},
    )
    if code != 202:
        raise RuntimeError(f"scan trigger failed {code}: {body}")
    return archive_id, body["id"]


def poll_scan(client: PortalClient, scan_id: str, *, timeout: int = SCAN_TIMEOUT_SEC) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        code, body = client.request("GET", f"/v1/scans/{scan_id}")
        if code == 401:
            client.refresh_token()
            continue
        if code != 200:
            raise RuntimeError(f"scan read failed {code}: {body}")
        state = body.get("status")
        if state != last_state:
            print(f"      [{scan_id[:8]}] {state}", flush=True)
            last_state = state
        if state in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"scan {scan_id} did not finish within {timeout}s")


def collect_metrics(client: PortalClient, project_id: str, result: BenchResult) -> None:
    # Overview — component count + license distribution + risk scores
    code, body = client.request("GET", f"/v1/projects/{project_id}/overview")
    if code == 200 and body:
        result.component_count = body.get("total_components", 0)
        lic = body.get("license_distribution") or {}
        result.license_allowed = lic.get("allowed", 0)
        result.license_conditional = lic.get("conditional", 0)
        result.license_forbidden = lic.get("forbidden", 0)
        result.license_unknown = lic.get("unknown", 0)
        result.risk_score = float(body.get("risk_score") or 0.0)
        result.security_score = float(body.get("security_score") or 0.0)
        result.license_score = float(body.get("license_score") or 0.0)
    else:
        result.notes.append(f"overview {code}")

    # Vulnerabilities — total finding count + severity breakdown (CVE findings,
    # not components-with-CVE which is what overview's severity_distribution gives)
    code, body = client.request(
        "GET",
        f"/v1/projects/{project_id}/vulnerabilities",
        params={"limit": 1, "offset": 0},
    )
    if code == 200 and body:
        result.cve_total = body.get("total", 0)
        sev = body.get("severity_distribution") or {}
        result.cve_critical = sev.get("critical", 0)
        result.cve_high = sev.get("high", 0)
        result.cve_medium = sev.get("medium", 0)
        result.cve_low = sev.get("low", 0)
        result.cve_info = sev.get("info", 0)
        result.cve_unknown = sev.get("unknown", 0)
    else:
        result.notes.append(f"vulns {code}")

    # Direct deps — separate query
    code, body = client.request(
        "GET",
        f"/v1/projects/{project_id}/components",
        params={"limit": 1, "offset": 0, "direct": "true"},
    )
    if code == 200 and body:
        result.direct_count = body.get("total", 0)


# ---------------------------------------------------------------------------
# suite definitions
# ---------------------------------------------------------------------------


def fixture_targets() -> list[dict[str, str]]:
    """All subdirs under FIXTURES_ROOT."""
    if not FIXTURES_ROOT.exists():
        raise FileNotFoundError(f"fixtures root missing: {FIXTURES_ROOT}")
    out = []
    for p in sorted(FIXTURES_ROOT.iterdir()):
        if not p.is_dir():
            continue
        slug = f"fx-{slugify(p.name)}"
        out.append({"name": f"fixture: {p.name}", "slug": slug, "path": str(p)})
    return out


def realworld_targets() -> list[dict[str, Any]]:
    """Real-world apps. Paths are checked at run time.

    ``exclude_paths`` lets each target opt-out specific subtrees that contain
    test fixtures resembling zip bombs (e.g. juice-shop ships a sparse PDF
    whose member ratio trips the 200x guard in source_archive_service).
    """
    home = Path.home()
    return [
        {
            "name": "Juice Shop (npm)",
            "slug": "rw-juice-shop",
            "path": str(home / "projects" / "scan-bench-corpus" / "juice-shop"),
            # juice-shop test/files/ ships sparse fixtures (invalidSizeForClient.pdf,
            # arbitraryFileWrite.zip) whose member compression ratio (>200x) trips
            # our zip-bomb guard. They're test-suite-only — not deps — so excluding
            # them doesn't change the cdxgen result.
            "exclude_paths": ["test/files", "frontend/src/assets"],
        },
        {
            "name": "WebGoat (Maven)",
            "slug": "rw-webgoat",
            "path": str(home / "projects" / "scan-bench-corpus" / "WebGoat"),
            "exclude_paths": [],
        },
        {
            "name": "TrustedOSS v1 webapp (self-scan)",
            "slug": "rw-trustedoss-v1",
            "path": str(home / "projects" / "trustedoss-portal-v1" / "webapp"),
            "exclude_paths": [],
        },
    ]


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------


def run_one(
    client: PortalClient,
    team_id: str,
    suite: str,
    target: dict[str, str],
) -> BenchResult:
    src = Path(target["path"])
    res = BenchResult(
        suite=suite,
        name=target["name"],
        slug=target["slug"],
        source_path=str(src),
    )
    if not src.exists():
        res.error = "source missing"
        return res
    res.ecosystem = detect_ecosystem(src)

    try:
        proj_id = ensure_project(client, team_id=team_id, name=target["name"], slug=target["slug"])
        res.project_id = proj_id
        zip_path = ZIP_DIR / f"{target['slug']}.zip"
        extra_excludes = target.get("exclude_paths") or []
        res.archive_bytes = zip_source(src, zip_path, extra_excludes=extra_excludes)
        if res.archive_bytes == 0:
            res.notes.append("empty zip")
        archive_id, scan_id = upload_and_scan(client, project_id=proj_id, zip_path=zip_path)
        res.scan_id = scan_id
        started = datetime.now(timezone.utc)
        res.scan_started_at = started.isoformat()
        scan = poll_scan(client, scan_id)
        finished = datetime.now(timezone.utc)
        res.scan_finished_at = finished.isoformat()
        res.scan_duration_sec = (finished - started).total_seconds()
        res.scan_status = scan.get("status", "")
        if res.scan_status == "succeeded":
            collect_metrics(client, proj_id, res)
        else:
            err = scan.get("error_message") or scan.get("metadata", {}).get("error") or ""
            res.error = err[:500]
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"
    return res


def write_outputs(suite: str, results: list[BenchResult]) -> tuple[Path, Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = OUT_DIR / f"{suite}-{ts}"
    csv_path = base.with_suffix(".csv")
    md_path = base.with_suffix(".md")
    jsonl_path = base.with_suffix(".jsonl")

    rows = [r.to_row() for r in results]
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with jsonl_path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # Markdown table
    cols = [
        "name", "ecosystem", "scan_status", "scan_duration_sec",
        "component_count", "direct_count",
        "license_allowed", "license_conditional", "license_forbidden", "license_unknown",
        "cve_total", "cve_critical", "cve_high", "cve_medium", "cve_low", "cve_unknown",
        "risk_score", "archive_bytes", "error",
    ]
    with md_path.open("w") as f:
        f.write(f"# scan-bench — {suite} ({ts})\n\n")
        f.write("| " + " | ".join(cols) + " |\n")
        f.write("|" + "|".join(["---"] * len(cols)) + "|\n")
        for r in rows:
            f.write("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n")
    return csv_path, md_path, jsonl_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", choices=("fixtures", "realworld"), required=True)
    parser.add_argument("--only", help="comma-separated names to run (substring match)")
    parser.add_argument("--portal-url", default=PORTAL_URL)
    args = parser.parse_args()

    client = PortalClient(args.portal_url)
    print(f"[login] {EMAIL}", flush=True)
    client.login(EMAIL, PASSWORD)

    code, me = client.request("GET", "/auth/me")
    if code != 200:
        print(f"FATAL: /auth/me {code}: {me}", file=sys.stderr)
        return 1
    memberships = me.get("memberships") or []
    if not memberships:
        print("FATAL: user has no team memberships", file=sys.stderr)
        return 1
    team_id = memberships[0]["team_id"]
    print(f"[team] {memberships[0]['team_name']} {team_id}", flush=True)

    if args.suite == "fixtures":
        targets = fixture_targets()
    else:
        targets = realworld_targets()

    if args.only:
        wanted = [s.strip().lower() for s in args.only.split(",") if s.strip()]
        targets = [
            t for t in targets
            if any(w in t["name"].lower() or w in t["slug"].lower() for w in wanted)
        ]

    print(f"[run] suite={args.suite} count={len(targets)}", flush=True)
    results: list[BenchResult] = []
    for i, t in enumerate(targets, 1):
        print(f"\n[{i}/{len(targets)}] {t['name']} ({t['slug']})", flush=True)
        if client.access_token is None:
            client.login(EMAIL, PASSWORD)
        res = run_one(client, team_id, args.suite, t)
        results.append(res)
        print(
            f"   -> status={res.scan_status} comp={res.component_count} "
            f"cve={res.cve_total} ({res.scan_duration_sec:.1f}s) "
            f"{('ERR: ' + res.error) if res.error else ''}",
            flush=True,
        )

    csv_path, md_path, jsonl_path = write_outputs(args.suite, results)
    print(f"\n[out] {csv_path}\n[out] {md_path}\n[out] {jsonl_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
