"""TrustedOSS Portal — Locust load test scenarios.

Runs against a live dev / staging stack (NOT in CI). Two user classes
share the same host:

* ``AuthenticatedUser``  — logs in once at start, then exercises the four
  read-heavy endpoints that dominate steady-state portal traffic
  (project list, scan list, project detail, component list per project).
  Weights match the rough access pattern observed in the portal UI:
  the project list page is the landing page (5×), scans list is checked
  often (3×), and detail / components are entered on demand (2× each).

* ``ScanTriggerUser``     — fires a scan trigger every ~60s. Weight 1
  keeps trigger pressure low; the goal is connection-pool / Celery
  enqueue stress, not real SCA execution. Backend should accept the
  request and enqueue — the worker can run in mock mode
  (``TRUSTEDOSS_SCAN_BACKEND=mock``) so cdxgen / ORT / Trivy do not run.

Target SLO (from CLAUDE.md §3 quality standards): **p95 < 1s** for the
four GET endpoints under the 50-user / 3-scan scenario. The aggregate is
verified by the operator from the Locust dashboard
(``http://localhost:8089``) — there is no automated assertion because
this scenario is intentionally outside CI (resource-intensive, requires
a beefy host or staging environment).

Run via ``docker-compose -f docker-compose.load.yml up`` after the dev
stack is healthy. See ``tests/load/README.md`` for the full operator
runbook.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task


# Test user credentials. The dev stack ships a deterministic seed via
# ``apps/backend/scripts/seed_e2e_user.py``; operators can override these
# from the Locust UI / env if they seeded a different user.
LOAD_TEST_EMAIL = os.getenv("LOAD_TEST_EMAIL", "e2e@trustedoss.local")
LOAD_TEST_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "trustedoss-e2e-pwd-1234")


class AuthenticatedUser(HttpUser):
    """Steady-state portal user — read-heavy traffic against the API.

    The ``on_start`` hook performs a single login per simulated user and
    caches the access token in the Locust session ``Authorization``
    header for the lifetime of that user. ``project_ids`` is fetched
    once at start so detail / component endpoints can pick a random
    target without hammering the list endpoint just to discover IDs.
    """

    wait_time = between(1, 3)

    project_ids: list[str] = []

    def on_start(self) -> None:
        """Authenticate and bootstrap the project ID pool."""
        # FastAPI-Users default login is form-encoded (OAuth2 password
        # flow). The portal exposes it under /auth/jwt/login.
        resp = self.client.post(
            "/auth/jwt/login",
            data={
                "username": LOAD_TEST_EMAIL,
                "password": LOAD_TEST_PASSWORD,
            },
            name="POST /auth/jwt/login",
        )
        if resp.status_code != 200:
            # No raise — keep the user in the pool so the dashboard
            # surfaces the auth failure rate. Subsequent requests will
            # 401 and Locust will graph that as a failure rate.
            return
        token = resp.json().get("access_token", "")
        self.client.headers["Authorization"] = f"Bearer {token}"

        # Pre-warm the project ID pool. We deliberately use the same
        # endpoint that the project-list task hits, so a slow list
        # endpoint shows up in metrics from request 1.
        list_resp = self.client.get("/v1/projects", name="GET /v1/projects (bootstrap)")
        if list_resp.status_code == 200:
            payload = list_resp.json()
            items = payload.get("items") if isinstance(payload, dict) else payload
            if isinstance(items, list):
                self.project_ids = [str(p["id"]) for p in items if "id" in p]

    @task(5)
    def list_projects(self) -> None:
        """GET /v1/projects — landing page, hit most often."""
        self.client.get("/v1/projects", name="GET /v1/projects")

    @task(3)
    def list_scans(self) -> None:
        """GET /v1/scans — global scan queue page."""
        self.client.get("/v1/scans", name="GET /v1/scans")

    @task(2)
    def project_detail(self) -> None:
        """GET /v1/projects/{id} — project overview drawer."""
        if not self.project_ids:
            return
        pid = random.choice(self.project_ids)  # noqa: S311 — load test, not crypto
        self.client.get(
            f"/v1/projects/{pid}",
            name="GET /v1/projects/{id}",
        )

    @task(2)
    def project_components(self) -> None:
        """GET /v1/components — component list for a random project."""
        if not self.project_ids:
            return
        pid = random.choice(self.project_ids)  # noqa: S311 — load test, not crypto
        self.client.get(
            f"/v1/components?project_id={pid}",
            name="GET /v1/components?project_id={id}",
        )


class ScanTriggerUser(HttpUser):
    """Trigger a scan periodically — Celery enqueue stress.

    Weight is set very low at runtime via ``locust.conf`` so this user
    fires roughly once per minute per simulated user. The goal is
    backend / Redis connection pressure on the trigger path, NOT to
    actually run cdxgen / ORT / Trivy. Run the worker in mock mode for
    load tests (``TRUSTEDOSS_SCAN_BACKEND=mock``).
    """

    # 60 ± 10 s — keep trigger rate low.
    wait_time = between(50, 70)

    def on_start(self) -> None:
        """Authenticate the same way as the read-heavy user."""
        resp = self.client.post(
            "/auth/jwt/login",
            data={
                "username": LOAD_TEST_EMAIL,
                "password": LOAD_TEST_PASSWORD,
            },
            name="POST /auth/jwt/login",
        )
        if resp.status_code != 200:
            return
        token = resp.json().get("access_token", "")
        self.client.headers["Authorization"] = f"Bearer {token}"

    @task
    def trigger_scan(self) -> None:
        """POST /v1/scans/trigger — enqueue a scan against a fixture project.

        Body shape is illustrative; the API will 422 if the trigger
        contract changes — that surface failure is the desired signal,
        the dashboard will graph it.
        """
        self.client.post(
            "/v1/scans/trigger",
            json={"project_ref": "load-test-fixture", "scan_type": "source"},
            name="POST /v1/scans/trigger",
        )
