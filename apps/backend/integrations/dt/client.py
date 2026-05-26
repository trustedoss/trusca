"""
Dependency-Track REST client (synchronous, for Celery workers).

Surface used by Phase 2 PR #8:

- :meth:`DTClient.health`               — heartbeat used by the monitor.
- :meth:`DTClient.upsert_project`       — idempotent get-or-create by name+version.
- :meth:`DTClient.upload_sbom`          — push CycloneDX JSON for analysis.
- :meth:`DTClient.get_findings`         — pull findings once analysis is done.
- :meth:`DTClient.list_projects`        — used by the orphan cleaner.
- :meth:`DTClient.list_vulnerabilities` — used by ``dt_resync`` to refresh the
                                          PostgreSQL vulnerability cache.

CLAUDE.md core rule #11: every method resolves ``DT_URL`` / ``DT_API_KEY`` at
call time. No module-level caching.

CLAUDE.md core rule #4: this client raises :class:`DTUnavailable` on 5xx /
network errors so the breaker can classify outages correctly. 4xx errors
become :class:`DTClientError` and do NOT count toward the breaker (they are
client-side mistakes, not DT outages).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from core.config import dt_api_key, dt_request_timeout_seconds, dt_url

from . import DTClientError, DTUnavailable

log = structlog.get_logger("integrations.dt.client")


# ---------------------------------------------------------------------------
# DTClient
# ---------------------------------------------------------------------------


class DTClient:
    """
    Thin synchronous wrapper around DT's REST API.

    The ``httpx.Client`` is created lazily and cached on the instance so a
    Celery worker re-uses one connection pool across tasks. Callers SHOULD
    wrap each method invocation with :class:`integrations.dt.breaker.CircuitBreaker`::

        breaker.call(lambda: dt.upload_sbom(project_id, sbom))

    Direct invocation works (the client itself does not consult the breaker)
    so the health monitor can probe DT without recursing through the breaker.
    """

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._closed = False

    # ------------------------------------------------------------------ lifecycle

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=dt_url(),
                headers={
                    "X-API-Key": dt_api_key(),
                    "Accept": "application/json",
                    "User-Agent": "trustedoss-portal/0.1",
                },
                timeout=dt_request_timeout_seconds(),
            )
        return self._http

    def close(self) -> None:
        if self._http is not None and not self._closed:
            self._http.close()
            self._closed = True

    def __enter__(self) -> DTClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ------------------------------------------------------------------ low-level

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """
        Issue a request and classify the response.

        Network errors and 5xx → :class:`DTUnavailable` (breaker fodder).
        4xx → :class:`DTClientError` (NOT breaker fodder).
        2xx → returned as-is.
        """
        client = self._client()
        try:
            response = client.request(
                method,
                path,
                json=json_body,
                params=params,
                files=files,
                data=data,
            )
        except httpx.TimeoutException as exc:
            log.warning("dt_request_timeout", method=method, path=path)
            raise DTUnavailable(f"DT request timeout: {method} {path}") from exc
        except httpx.NetworkError as exc:
            log.warning("dt_request_network_error", method=method, path=path, error=str(exc))
            raise DTUnavailable(f"DT network error: {method} {path}: {exc}") from exc

        if 500 <= response.status_code < 600:
            log.warning(
                "dt_request_5xx",
                method=method,
                path=path,
                status=response.status_code,
            )
            raise DTUnavailable(
                f"DT {response.status_code} on {method} {path}: {response.text[:500]}",
            )
        if 400 <= response.status_code < 500:
            log.info(
                "dt_request_4xx",
                method=method,
                path=path,
                status=response.status_code,
            )
            raise DTClientError(
                f"DT {response.status_code} on {method} {path}: {response.text[:500]}",
            )
        return response

    # ------------------------------------------------------------------ health

    def health(self) -> dict[str, Any]:
        """Return DT's version document; raises ``DTUnavailable`` on outage."""
        response = self._request("GET", "/api/version")
        body: dict[str, Any] = response.json()
        return body

    # ------------------------------------------------------------------ projects

    def list_projects(
        self, *, page_size: int = 100, page_number: int = 1
    ) -> list[dict[str, Any]]:
        response = self._request(
            "GET",
            "/api/v1/project",
            params={"pageSize": page_size, "pageNumber": page_number},
        )
        body: list[dict[str, Any]] = response.json()
        return body

    def upsert_project(
        self,
        *,
        name: str,
        version: str,
        tags: list[str] | None = None,
    ) -> str:
        """
        Get-or-create a DT project for (name, version).

        Returns the DT project UUID. Idempotent — re-running with the same
        (name, version) yields the same project, which keeps the scan task
        re-executable without orphaning DT projects.
        """
        try:
            response = self._request(
                "GET",
                "/api/v1/project/lookup",
                params={"name": name, "version": version},
            )
            project: dict[str, Any] = response.json()
            project_uuid: str = project["uuid"]
            return project_uuid
        except DTClientError:
            # 404 from /lookup → fall through to create.
            pass

        body = {
            "name": name,
            "version": version,
            "tags": [{"name": t} for t in (tags or [])],
        }
        response = self._request("PUT", "/api/v1/project", json_body=body)
        created: dict[str, Any] = response.json()
        created_uuid: str = created["uuid"]
        return created_uuid

    # ------------------------------------------------------------------ sbom

    def upload_sbom(
        self,
        *,
        project_uuid: str,
        sbom_json: bytes | str,
    ) -> str:
        """
        Upload a CycloneDX SBOM to DT.

        Returns the DT analysis token. The caller polls findings only after
        the token's analysis completes; for Phase 2 we poll
        :meth:`get_findings` directly with a short retry loop.
        """
        if isinstance(sbom_json, str):
            sbom_bytes = sbom_json.encode("utf-8")
        else:
            sbom_bytes = sbom_json
        # DT's /api/v1/bom endpoint accepts multipart with project + bom fields.
        # We use the JSON variant (Content-Type application/json, base64'd
        # bom in the JSON body) because it is more reliable across DT
        # versions.
        import base64

        body = {
            "project": project_uuid,
            "bom": base64.b64encode(sbom_bytes).decode("ascii"),
        }
        response = self._request("PUT", "/api/v1/bom", json_body=body)
        result: dict[str, Any] = response.json()
        token: str = result.get("token", "")
        return token

    # ------------------------------------------------------------------ findings

    def get_findings(self, *, project_uuid: str) -> list[dict[str, Any]]:
        """Return the project's current findings list."""
        response = self._request("GET", f"/api/v1/finding/project/{project_uuid}")
        findings: list[dict[str, Any]] = response.json()
        return findings

    # ------------------------------------------------------------------ vulns

    def list_vulnerabilities(
        self,
        *,
        page_size: int = 500,
        page_number: int = 1,
    ) -> list[dict[str, Any]]:
        """
        Page through DT's vulnerability catalog.

        Used by ``dt_resync_task`` to refresh the local PostgreSQL cache.
        """
        response = self._request(
            "GET",
            "/api/v1/vulnerability",
            params={"pageSize": page_size, "pageNumber": page_number},
        )
        vulns: list[dict[str, Any]] = response.json()
        return vulns

    def count_vulnerabilities(self) -> int:
        """
        Total vulnerabilities in DT's database — the NVD/OSV/GHSA mirror size.

        Reads the ``X-Total-Count`` header DT sets on its paginated list
        endpoints, so we ask for the smallest possible page (``pageSize=1``)
        and never materialise rows. A return of ``0`` means DT's vulnerability
        mirror has not been populated (NVD mirroring disabled, or still
        downloading): scans will then find components but report no CVEs, which
        looks indistinguishable from a genuinely clean project. The admin DT
        status surfaces this so an operator can tell the two apart.
        """
        response = self._request(
            "GET",
            "/api/v1/vulnerability",
            params={"pageSize": 1, "pageNumber": 1},
        )
        raw = response.headers.get("X-Total-Count")
        if raw is None:
            return 0
        try:
            return max(int(raw), 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ admin

    def delete_project(self, *, project_uuid: str) -> None:
        """Remove a DT project (used by the orphan cleaner once approved)."""
        self._request("DELETE", f"/api/v1/project/{project_uuid}")


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------


def build_client() -> DTClient:
    """Return a fresh DT client. Caller is responsible for closing it."""
    return DTClient()


__all__ = ["DTClient", "build_client"]
