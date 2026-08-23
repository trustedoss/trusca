"""TrustedOSS Portal: load / capacity / stress scenario.

Rebuilt for the CURRENT API contract (the previous file targeted a stale
``/auth/jwt/login`` + ``/v1/scans/trigger`` shape that no longer exists; M1,
concurrency-scaling plan, restored the scan-trigger and API-key classes on
the current contract, ``POST /v1/projects/{project_id}/scans``, after they
had silently dropped out).

Capacity targets, not "10,000 users" (M1 §0.4 correction). An earlier version
of this file called 10,000 concurrent users the "design target". It is not:
``concurrency-scaling-plan-2026-08-22.md`` §0.3 derives per-deployment-tier
targets from first principles (developer counts, session rates, burst
margin) and the largest realistic self-hosted tier (T3, ~5,000 developers)
comes out to **N=250** concurrent portal users, alongside a CI/API-key rate
of ~17 requests/second. This file now measures three distinct stages, and
only the first two are pass/fail:

1. **Capacity**: ``N=250`` (T3) run WITH an ``ApiKeyCIUser``-heavy mix
   approximating the 17 req/s API-key rate. Must clear the SLO below. This is
   the number an actual deployment should be sized against.
2. **Buffer**: ``N=1,250`` (5x capacity, the plan's burst-margin check).
   Error rate must still stay under the cap; latency is allowed to grow.
3. **Stress**: ``N=10,000``. Not a gate. The point is to see *how* the
   stack fails, not whether it passes, so run this with
   ``LOAD_SLO_ENFORCE=0`` (see below); the point of the run (observing the
   failure mode) should not get lost in a nonzero exit code from a
   deliberately overloaded system.

Strictness: at the capacity/buffer stages this file is a **hard gate**. A
``quitting`` hook fails the process (exit 1) when the aggregate breaches the
SLO, so ``locust --headless`` can be run in a loop and a regression flips
the exit code, no human dashboard reading. Set ``LOAD_SLO_ENFORCE=0`` (or
``false``/``no``/``off``) to downgrade a breach to a printed warning with
exit 0; use this for the stress stage, never for capacity/buffer.

SLO (overridable via env):
* error rate          ≤ ``LOAD_MAX_FAIL_RATIO``      (default 1%)
* p95 read latency    ≤ ``LOAD_MAX_P95_MS``          (default 1500 ms)
* p99 read latency    ≤ ``LOAD_MAX_P99_MS``          (default 4000 ms)

User classes:
* ``PortalReadUser``   (weight 8): steady-state read traffic across every
  project read surface (overview / components / vulns / licenses / source-tree
  / SBOM).
* ``ReportHeavyUser``  (weight 2): expensive document generation (vuln PDF,
  4 SBOM formats, NOTICE html/text), the synchronous, CPU/IO-heavy paths.
* ``AuthChurnUser``    (weight 1): login churn + token refresh, hammering the
  rate-limited auth path (verifies the limiter degrades gracefully, not 5xx).
* ``ScanTriggerUser``  (weight 1): low-frequency scan triggers via
  ``POST /v1/projects/{id}/scans``. The point is connection-pool / Celery
  enqueue pressure, not actually running cdxgen/Trivy. Pair this with
  ``SCAN_LOAD_TEST_DELAY_ENABLED=true`` on the worker (dev-only; see
  ``core.config.scan_load_test_delay_seconds``) if you want triggered scans
  to hold a slot long enough to matter, and see ``scan_queue_wait.py`` in
  this directory for the dedicated queue-depth measurement (M1 §6) instead
  of trying to read queue wait off this file's aggregate stats.
* ``ApiKeyCIUser``     (weight 1): the CI traffic pattern from plan §0.2
  G6 / §6 ("API key scenario ... M1에서 함께 넣는다"): trigger, poll status,
  fetch results (provenance + conformance), then gate verdict, all
  authenticated with a ``tos_`` API key instead of the shared JWT the other
  classes use (API-key auth re-verifies via bcrypt on every request, a
  different cost profile; see plan §1.3/§1.5).

Run::

    docker-compose -f docker-compose.dev.yml up -d
    LOAD_TEST_EMAIL=e2e-admin@trustedoss.dev LOAD_TEST_PASSWORD=E2eAdminPass2026 \
      locust -f tests/load/locustfile.py --headless -u 250 -r 25 -t 5m \
      --host http://localhost:8000

    # buffer stage (5x):
    ... --headless -u 1250 -r 100 -t 5m ...

    # stress stage (breaking-point only, not a gate):
    LOAD_SLO_ENFORCE=0 ... --headless -u 10000 -r 200 -t 5m ...
"""

from __future__ import annotations

import os
import random
import time

import requests
from locust import HttpUser, between, events, task
from locust.env import Environment
from locust.runners import WorkerRunner

LOAD_TEST_EMAIL = os.getenv("LOAD_TEST_EMAIL", "e2e-admin@trustedoss.dev")
LOAD_TEST_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "E2eAdminPass2026")

MAX_FAIL_RATIO = float(os.getenv("LOAD_MAX_FAIL_RATIO", "0.01"))
MAX_P95_MS = float(os.getenv("LOAD_MAX_P95_MS", "1500"))
MAX_P99_MS = float(os.getenv("LOAD_MAX_P99_MS", "4000"))

# Stress-stage escape hatch (M1 §0.4): 10,000 users is a breaking-point probe,
# not a capacity target, so a breach there should not flip the exit code the
# way it must at the 250 / 1,250 stages. Off (enforcing) by default so a
# capacity/buffer run left un-configured stays a real gate.
ENFORCE_SLO = os.getenv("LOAD_SLO_ENFORCE", "true").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

# Read-surface SBOM formats exercised by the report-heavy user.
_SBOM_FORMATS = ("cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv")


# A real 10k-user fleet authenticates from 10k distinct IPs, so the per-IP
# login limiter (5/min/IP) never blocks them. A single-host load driver does
# NOT model that — every simulated user shares the driver's one IP, so
# per-user logins would be throttled to 5/min and starve the read load we
# actually want to measure. We therefore authenticate ONCE at test start and
# share the token across all simulated users (= "already-authenticated steady-
# state read traffic"). ``AuthChurnUser`` separately exercises the limiter.
#
# ``api_key`` / ``api_key_project_id`` are for ``ApiKeyCIUser`` (M1): one
# project-scoped, read-write key is issued at bootstrap (with the same JWT)
# and shared the same way: CI traffic authenticates as ONE machine identity
# per pipeline, not one key per simulated run, so sharing here matches the
# real traffic shape rather than merely working around the login limiter.
_SHARED = {"token": None, "project_ids": [], "api_key": None, "api_key_project_id": None}


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

            if _SHARED["project_ids"]:
                key_project_id = _SHARED["project_ids"][0]
                kr = requests.post(
                    f"{host}/v1/api-keys",
                    json={
                        "name": "load-test-ci-key",
                        "scope": "project",
                        "project_id": key_project_id,
                        "permission_breadth": "read_write",
                    },
                    headers={"Authorization": f"Bearer {_SHARED['token']}"},
                    timeout=10,
                )
                if kr.status_code == 201:
                    _SHARED["api_key"] = kr.json().get("raw_key")
                    _SHARED["api_key_project_id"] = key_project_id
                    print("LOAD bootstrap: api_key=ok")
                else:
                    print(f"LOAD bootstrap api-key issuance FAILED: {kr.status_code}")
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


class ScanTriggerUser(_AuthedUser):
    """Low-frequency scan-trigger traffic: connection-pool / Celery enqueue
    pressure, not (by default) actually running cdxgen/Trivy.

    Restored for M1 (concurrency-scaling plan §1.1, §7.1 unit 12): the
    previous version of this file lost its scan-trigger class when the
    contract moved from ``POST /v1/scans/trigger`` to
    ``POST /v1/projects/{project_id}/scans`` and was never rebuilt against
    the new shape. A scan trigger is a rare, deliberate action (a push, a
    manual "scan now"), not a per-page-load request like the read classes
    above, so this fires roughly once a minute per simulated user rather than
    every few seconds.

    This class alone does not measure queue wait. With the default
    ``real``/``mock`` scan backend a slot frees again almost immediately (real:
    ~5-60 min in the background, off Locust's clock entirely; mock: sub-second),
    so N simulated users firing a minute apart essentially never contend for a
    slot. To see queue depth, run the dev stack with
    ``SCAN_LOAD_TEST_DELAY_ENABLED=true`` on the worker (dev-only guard, see
    ``core.config.scan_load_test_delay_seconds``) and use ``scan_queue_wait.py``
    in this directory, which drives N *simultaneous* triggers and reads back
    ``started_at``/``completed_at``, a shape this weighted, trickle-fire class
    is not built for.
    """

    weight = 1
    wait_time = between(45, 90)

    @task
    def trigger_scan(self) -> None:
        pid = self._pid()
        if not pid:
            return
        with self.client.post(
            f"/v1/projects/{pid}/scans",
            json={"kind": "source"},
            name="POST /projects/{id}/scans (trigger)",
            catch_response=True,
        ) as resp:
            # 202 = queued. 429 = the per-user trigger rate limit or the
            # team's concurrent-scan cap (services/scan_service.py): the
            # limiter/cap doing its job under load, not a server failure.
            # 409 = a scan for this project/branch is already in flight (the
            # partial unique index), expected when several simulated users
            # keep re-triggering the same small seeded project pool.
            if resp.status_code in (202, 429, 409):
                resp.success()
            else:
                resp.failure(f"scan trigger unexpected status {resp.status_code}")


class ApiKeyCIUser(HttpUser):
    """API-key CI traffic: trigger → poll status → fetch results → gate.

    Added for M1 (concurrency-scaling plan §0.2 gap G6 / §6: "API 키 시나리오는
    지금 부하 시험에 없으므로 M1에서 함께 넣는다"). One CI-triggered scan makes
    roughly 20 calls in the real pipeline (trigger 1, status polls several,
    result fetch 2-3, gate verdict 1); this class approximates that shape in
    one task. Authenticates with a ``tos_`` API key (Authorization: Bearer),
    not the JWT session the other classes share, because the auth cost
    profile is different: every API-key request re-verifies via bcrypt on
    the request path (plan §1.3, §1.5), unlike the JWT classes above which
    authenticate once and reuse the token.
    """

    weight = 1
    wait_time = between(60, 180)

    def on_start(self) -> None:
        self.project_id = _SHARED.get("api_key_project_id")
        key = _SHARED.get("api_key")
        if key:
            self.client.headers["Authorization"] = f"Bearer {key}"

    @task
    def ci_run(self) -> None:
        if not self.project_id or "Authorization" not in self.client.headers:
            return

        with self.client.post(
            f"/v1/projects/{self.project_id}/scans",
            json={"kind": "source"},
            name="POST /projects/{id}/scans (api-key trigger)",
            catch_response=True,
        ) as trigger:
            if trigger.status_code in (429, 409):
                # Limiter / concurrent-scan cap / in-flight conflict: the CI
                # run would back off and retry later; nothing to poll.
                trigger.success()
                return
            if trigger.status_code != 202:
                trigger.failure(f"api-key trigger unexpected status {trigger.status_code}")
                return
            trigger.success()
            scan_id = trigger.json().get("id")
        if not scan_id:
            return

        # Status poll: the real scan-action polls every few seconds until a
        # terminal state. Capped short here so one simulated CI run's
        # wait_time budget is not consumed entirely by polling.
        terminal = {"succeeded", "failed", "cancelled"}
        for _ in range(10):
            poll = self.client.get(
                f"/v1/scans/{scan_id}",
                name="GET /v1/scans/{id} (api-key poll)",
            )
            if poll.status_code != 200 or poll.json().get("status") in terminal:
                break
            time.sleep(1)

        # Result fetch (2 of the 2-3 G6 calls): scan provenance is always
        # API-key reachable; SBOM conformance only applies to an ingested-SBOM
        # scan, so a 404 for a "source" scan is an expected miss, not a
        # failure: the CI action would skip that check for this scan kind.
        self.client.get(
            f"/v1/scans/{scan_id}/provenance",
            name="GET /v1/scans/{id}/provenance (api-key)",
        )
        with self.client.get(
            f"/v1/projects/{self.project_id}/scans/{scan_id}/conformance",
            name="GET /projects/{id}/scans/{id}/conformance (api-key)",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"conformance unexpected status {resp.status_code}")

        # Gate verdict (1): what the build-gate step in CI actually reads.
        self.client.get(
            f"/v1/projects/{self.project_id}/gate-result",
            name="GET /projects/{id}/gate-result (api-key)",
        )


# ---------------------------------------------------------------------------
# Strict SLO gate — fail the process on breach (headless loop friendly).
# Downgrades to a printed warning (exit 0) when LOAD_SLO_ENFORCE is falsy;
# use that for the stress stage (N=10,000), which is a breaking-point probe,
# not a pass/fail check (M1 §0.4).
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
    if breaches and not ENFORCE_SLO:
        environment.process_exit_code = 0
        print(
            "LOAD SLO BREACH (NOT ENFORCED, LOAD_SLO_ENFORCE=0, stress-stage "
            "observation only): " + "; ".join(breaches)
        )
    elif breaches:
        environment.process_exit_code = 1
        print("LOAD SLO BREACH: " + "; ".join(breaches))
    else:
        environment.process_exit_code = 0
        print(
            f"LOAD SLO OK: fail_ratio={fail_ratio:.4f} p95={p95:.0f}ms "
            f"p99={p99:.0f}ms reqs={stats.num_requests}"
        )
