"""Client-abandonment guard (Tier 6): the vuln-report PDF endpoint must NOT run
the expensive weasyprint render when the caller has already disconnected.

Unit-level: the 4 read services + auth are stubbed so the test isolates the
``request.is_disconnected()`` short-circuit and asserts the render is skipped.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import api.v1.reports as reports


def _overview() -> dict:
    return {
        "project_name": "Acme",
        "risk_score": 0.0,
        "total_components": 0,
        "severity_distribution": dict.fromkeys(
            ("critical", "high", "medium", "low", "info", "none"), 0
        ),
        "license_distribution": dict.fromkeys(
            ("forbidden", "conditional", "allowed", "unknown"), 0
        ),
    }


def _patch_reads(monkeypatch, render_spy) -> None:
    pid_team = SimpleNamespace(team_id=uuid.uuid4())
    monkeypatch.setattr(reports, "get_project", AsyncMock(return_value=pid_team))
    monkeypatch.setattr(reports, "assert_team_access", lambda *a, **k: None)
    monkeypatch.setattr(reports, "get_project_overview", AsyncMock(return_value=_overview()))
    monkeypatch.setattr(reports, "list_components_for_project", AsyncMock(return_value=([], 0)))
    monkeypatch.setattr(
        reports, "list_project_vulnerabilities", AsyncMock(return_value=([], 0, {}))
    )
    monkeypatch.setattr(reports, "render_report_pdf", render_spy)
    monkeypatch.setattr(
        reports, "latest_succeeded_scan_id", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        reports, "record_report_download", AsyncMock(return_value=None)
    )


def _request(disconnected: bool):
    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=disconnected)
    req.url.path = "/v1/projects/x/vulnerability-report.pdf"
    return req


@pytest.mark.asyncio
async def test_pdf_skips_render_when_client_disconnected(monkeypatch) -> None:
    render_spy = MagicMock(return_value=b"%PDF-never")
    _patch_reads(monkeypatch, render_spy)

    resp = await reports.get_vulnerability_report_pdf_endpoint(
        request=_request(disconnected=True),
        project_id=uuid.uuid4(),
        session=AsyncMock(),
        actor=SimpleNamespace(is_superuser=True),
    )

    assert resp.status_code == 499
    render_spy.assert_not_called()  # the whole point: no wasted weasyprint render


@pytest.mark.asyncio
async def test_pdf_renders_when_client_connected(monkeypatch) -> None:
    render_spy = MagicMock(return_value=b"%PDF-1.7 ok")
    _patch_reads(monkeypatch, render_spy)

    resp = await reports.get_vulnerability_report_pdf_endpoint(
        request=_request(disconnected=False),
        project_id=uuid.uuid4(),
        session=AsyncMock(),
        actor=SimpleNamespace(is_superuser=True),
    )

    assert resp.status_code == 200
    assert resp.media_type == "application/pdf"
    render_spy.assert_called_once()
