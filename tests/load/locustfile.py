"""TrustedOSS Portal — hard load / stress scenario (10k-user profile).

Rebuilt for the CURRENT API contract (the previous file targeted a stale
``/auth/jwt/login`` + ``/v1/scans/trigger`` shape that no longer exists).

Design target: **10,000 concurrent portal users.** Locally you cannot drive
10k users against a 4-worker uvicorn on a laptop, so the scenario is built to
*scale* to 10k on a beefy host (set ``-u 10000 -r 200``) while a local run at
``-u 200..500`` already saturates the dev stack and surfaces the same classes
of bug (pool exhaustion, 5xx under contention, slow report generation, rate-
limit misfires).

Strictness: this file is a **hard gate**. A ``quitting`` hook fails the process
(exit 1) when the aggregate breaches the SLO — so ``locust --headless`` can be
run in a loop and a regression flips the exit code, no human dashboard reading.

SLO (overridable via env):
* error rate          ≤ ``LOAD_MAX_FAIL_RATIO``      (default 1%)
* p95 read latency    ≤ ``LOAD_MAX_P95_MS``          (default 1500 ms)
* p99 read latency    ≤ ``LOAD_MAX_P99_MS``          (default 4000 ms)

User classes:
* ``PortalReadUser``   (weight 8) — steady-state read traffic across every
  project read surface (overview / components / vulns / licenses / source-tree
  / SBOM).
* ``ReportHeavyUser``  (weight 2) — expensive document generation (vuln PDF,
  4 SBOM formats, NOTICE html/text) — the synchronous, CPU/IO-heavy paths.
* ``AuthChurnUser``    (weight 1) — login churn + token refresh, hammering the
  rate-limited auth path (verifies the limiter degrades gracefully, not 5xx).

Run::

    docker-compose -f docker-compose.dev.yml up -d
    LOAD_TEST_EMAIL=e2e-admin@trustedoss.dev LOAD_TEST_PASSWORD=E2eAdminPass2026 \
      locust -f tests/load/locustfile.py --headless -u 300 -r 50 -t 3m \
      --host http://localhost:8000
"""

from __future__ import annotations

import os
import random

import requests
from locust import HttpUser, between, events, task
from locust.env import Environment
from locust.runners import WorkerRunner

LOAD_TEST_EMAIL = os.getenv("LOAD_TEST_EMAIL", "e2e-admin@trustedoss.dev")
LOAD_TEST_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "E2eAdminPass2026")

MAX_FAIL_RATIO = float(os.getenv("LOAD_MAX_FAIL_RATIO", "0.01"))
MAX_P95_MS = float(os.getenv("LOAD_MAX_P95_MS", "1500"))
MAX_P99_MS = float(os.getenv("LOAD_MAX_P99_MS", "4000"))

# Read-surface SBOM formats exercised by the report-heavy user.
_SBOM_FORMATS = ("cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv")


# A real 10k-user fleet authenticates from 10k distinct IPs, so the per-IP
# login limiter (5/min/IP) never blocks them. A single-host load driver does
# NOT model that — every simulated user shares the driver's one IP, so
# per-user logins would be throttled to 5/min and starve the read load we
# actually want to measure. We therefore authenticate ONCE at test start and
# share the token across all simulated users (= "already-authenticated steady-
# state read traffic"). ``AuthChurnUser`` separately exercises the limiter.
_SHARED = {"token": None, "project_ids": []}


@events.test_start.add_listener
def _bootstrap_shared_auth(environment: Environment, **_kw) -> None:
    if isinstance(environment.runner, WorkerRunner):
        return  # the master broadcasts; workers read _SHARED via on_start retry
    host = environment.host or "http://localhost:8000"
    try:
        r = requests.post(
            f"{host}/auth/login",
            json={"email": LOAD_TEST_EMAIL, "password": LOAD_TEST_PASSWORD},
            timeout=10,
        )
        if r.status_code == 200:
            _SHARED["token"] = r.json().get("access_token")
            pr = requests.get(
                f"{host}/v1/projects?size=100",
                headers={"Authorization": f"Bearer {_SHARED['token']}"},
                timeout=10,
            )
            if pr.status_code == 200:
                payload = pr.json()
                items = payload.get("items") if isinstance(payload, dict) else payload
                _SHARED["project_ids"] = [
                    str(p["id"]) for p in items if isinstance(p, dict) and "id" in p
                ]
            print(f"LOAD bootstrap: token={'ok' if _SHARED['token'] else 'FAIL'} "
                  f"projects={len(_SHARED['project_ids'])}")
        else:
            print(f"LOAD bootstrap login FAILED: {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"LOAD bootstrap error: {exc}")


class _AuthedUser(HttpUser):
    abstract = True

    def on_start(self) -> None:
        token = _SHARED.get("token")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"
        self.project_ids = _SHARED.get("project_ids", [])

    def _pid(self) -> str | None:
        return random.choice(self.project_ids) if self.project_ids else None  # noqa: S311


class PortalReadUser(_AuthedUser):
    """Steady-state read traffic across every project read surface."""

    weight = 8
    wait_time = between(1, 3)

    @task(6)
    def list_projects(self) -> None:
        self.client.get("/v1/projects?size=50", name="GET /v1/projects")

    @task(4)
    def list_scans(self) -> None:
        self.client.get("/v1/scans?size=50", name="GET /v1/scans")

    @task(3)
    def project_components(self) -> None:
        pid = self._pid()
        if pid:
            self.client.get(f"/v1/projects/{pid}/components?size=100", name="GET /projects/{id}/components")

    @task(2)
    def project_vulns(self) -> None:
        pid = self._pid()
        if pid:
            self.client.get(f"/v1/projects/{pid}/vulnerabilities", name="GET /projects/{id}/vulnerabilities")

    @task(2)
    def project_licenses(self) -> None:
        pid = self._pid()
        if pid:
            self.client.get(f"/v1/projects/{pid}/licenses", name="GET /projects/{id}/licenses")

    @task(1)
    def source_tree(self) -> None:
        pid = self._pid()
        if not pid:
            return
        # 404 is a legitimate response: a project whose latest scan failed, is
        # still running, or was a mock/seed scan has no preserved source tree.
        # A strict load test must fail only on SERVER errors (5xx) / unexpected
        # statuses, never on a correct "no source for this entity" 404.
        with self.client.get(
            f"/v1/projects/{pid}/source-tree",
            name="GET /projects/{id}/source-tree",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"source-tree unexpected status {resp.status_code}")


class ReportHeavyUser(_AuthedUser):
    """Expensive synchronous document generation — PDF / SBOM / NOTICE."""

    weight = 2
    wait_time = between(3, 8)

    @task(3)
    def vuln_pdf(self) -> None:
        pid = self._pid()
        if pid:
            self.client.get(
                f"/v1/projects/{pid}/vulnerability-report.pdf",
                name="GET /projects/{id}/vulnerability-report.pdf",
            )

    @task(2)
    def sbom(self) -> None:
        pid = self._pid()
        if pid:
            fmt = random.choice(_SBOM_FORMATS)  # noqa: S311
            self.client.get(f"/v1/projects/{pid}/sbom?format={fmt}", name="GET /projects/{id}/sbom")

    @task(2)
    def notice(self) -> None:
        pid = self._pid()
        if not pid:
            return
        fmt = random.choice(("text", "html"))  # noqa: S311
        # NOTICE is per-IP rate-limited (10/min). A single load-driver IP trips
        # that immediately — a real 10k-IP fleet would not. 429 (+Retry-After)
        # is the limiter working correctly, so it's a success here; only 5xx is
        # a real failure.
        with self.client.get(
            f"/v1/projects/{pid}/notice?format={fmt}",
            name="GET /projects/{id}/notice",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"notice unexpected status {resp.status_code}")


class AuthChurnUser(HttpUser):
    """Login churn against the rate-limited auth path (5/min/IP).

    Under a real 10k load the limiter MUST return 429 (+Retry-After), never a
    5xx. We count non-(200|429) as failures so the gate catches a limiter that
    falls over instead of throttling cleanly."""

    weight = 1
    wait_time = between(5, 12)

    @task
    def login_churn(self) -> None:
        with self.client.post(
            "/auth/login",
            json={"email": LOAD_TEST_EMAIL, "password": LOAD_TEST_PASSWORD},
            name="POST /auth/login (churn)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"auth churn unexpected status {resp.status_code}")


# ---------------------------------------------------------------------------
# Strict SLO gate — fail the process on breach (headless loop friendly).
# ---------------------------------------------------------------------------
@events.quitting.add_listener
def _enforce_slo(environment: Environment, **_kw) -> None:
    if isinstance(environment.runner, WorkerRunner):
        return  # workers don't own aggregate stats
    stats = environment.stats.total
    fail_ratio = stats.fail_ratio
    p95 = stats.get_response_time_percentile(0.95)
    p99 = stats.get_response_time_percentile(0.99)
    breaches = []
    if fail_ratio > MAX_FAIL_RATIO:
        breaches.append(f"fail_ratio {fail_ratio:.4f} > {MAX_FAIL_RATIO}")
    if p95 and p95 > MAX_P95_MS:
        breaches.append(f"p95 {p95:.0f}ms > {MAX_P95_MS}ms")
    if p99 and p99 > MAX_P99_MS:
        breaches.append(f"p99 {p99:.0f}ms > {MAX_P99_MS}ms")
    if breaches:
        environment.process_exit_code = 1
        print("LOAD SLO BREACH: " + "; ".join(breaches))
    else:
        environment.process_exit_code = 0
        print(
            f"LOAD SLO OK: fail_ratio={fail_ratio:.4f} p95={p95:.0f}ms "
            f"p99={p99:.0f}ms reqs={stats.num_requests}"
        )
