"""
DT vulnerability cache resync — Celery Beat (1 hour).

Pulls every vulnerability page from DT and upserts the metadata into the
local ``vulnerabilities`` table so the portal can render finding details
even when DT is OPEN-circuit-breakered (CLAUDE.md core rule #4).

Idempotency: ``Vulnerability.external_id`` is unique. We treat the resync as
a series of single-row upserts (insert if missing, update otherwise) so
multiple resync runs converge on the same state. Network failures abort the
current run cleanly — the next hour's run picks up where this one left off.

Phase 2.7 (CVE re-detection) will extend this task to fan out notifications
when a previously-clean component picks up a new CVE; for PR #8 we just keep
the cache fresh.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from integrations.dt import DTError
from integrations.dt.breaker import get_breaker
from integrations.dt.client import build_client
from models import Vulnerability
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.dt_resync")

_PAGE_SIZE = 500


@celery_app.task(name="trustedoss.dt_resync")  # type: ignore[misc]
def dt_resync_task() -> dict[str, Any]:
    """
    Resync the local vulnerability cache from Dependency-Track.

    Returns a small summary dict: ``{"pages": N, "upserts": M, "skipped": K}``.
    Tasks that consume this return value (e.g. an admin "last resync" widget
    in Phase 7) only need to read these counts.
    """
    structlog.contextvars.bind_contextvars(task_name="dt_resync")
    breaker = get_breaker()
    client = build_client()
    pages = 0
    upserts = 0
    skipped = 0
    try:
        page_number = 1
        while True:
            # Bind ``page_number`` to a positional default in a tiny inner
            # function rather than capturing the loop variable through a
            # closure — this dodges the "late-binding lambda" gotcha and
            # gives mypy a typed callable to verify.
            def _fetch(pn: int = page_number) -> list[dict[str, Any]]:
                return client.list_vulnerabilities(page_size=_PAGE_SIZE, page_number=pn)

            try:
                vulns = breaker.call(_fetch)
            except DTError as exc:
                log.warning("dt_resync_aborted", error=str(exc), page=page_number)
                break
            if not vulns:
                break
            pages += 1
            with sync_session_scope() as session:
                for raw in vulns:
                    if _upsert_vulnerability(session, raw):
                        upserts += 1
                    else:
                        skipped += 1
                session.commit()
            if len(vulns) < _PAGE_SIZE:
                break
            page_number += 1
    finally:
        client.close()
        structlog.contextvars.unbind_contextvars("task_name")

    log.info("dt_resync_done", pages=pages, upserts=upserts, skipped=skipped)
    return {"pages": pages, "upserts": upserts, "skipped": skipped}


def _upsert_vulnerability(session: Session, raw: dict[str, Any]) -> bool:
    """Insert / update one DT vulnerability row. Returns True on a write."""
    external_id = raw.get("vulnId") or raw.get("source", {}).get("name")
    if not isinstance(external_id, str) or not external_id:
        return False

    existing = session.execute(
        select(Vulnerability).where(Vulnerability.external_id == external_id)
    ).scalar_one_or_none()

    severity = _normalize_severity(raw.get("severity"))
    cvss_score = _coerce_cvss(raw.get("cvssV3BaseScore") or raw.get("cvssV2BaseScore"))
    cvss_vector = raw.get("cvssV3Vector") or raw.get("cvssV2Vector")
    summary = raw.get("title") or raw.get("description")
    details = raw.get("description")
    references = raw.get("references") or []
    published = _parse_dt_timestamp(raw.get("published"))
    modified = _parse_dt_timestamp(raw.get("updated"))
    source = (raw.get("source") or {}).get("name") or "DT"

    if existing is None:
        session.add(
            Vulnerability(
                id=uuid.uuid4(),
                external_id=external_id,
                source=source,
                severity=severity,
                cvss_score=cvss_score,
                cvss_vector=cvss_vector,
                summary=summary,
                details=details,
                published_at=published,
                modified_at=modified,
                references=references,
                last_seen_at=datetime.now(UTC),
            )
        )
        return True

    existing.severity = severity
    existing.cvss_score = cvss_score
    existing.cvss_vector = cvss_vector
    existing.summary = summary
    existing.details = details
    existing.published_at = published
    existing.modified_at = modified
    existing.references = references
    existing.last_seen_at = datetime.now(UTC)
    return True


def _normalize_severity(value: Any) -> str:
    raw = (str(value or "")).lower()
    if raw in ("critical", "high", "medium", "low", "info"):
        return raw
    return "unknown"


def _coerce_cvss(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.1"))
    except (ValueError, ArithmeticError):
        return None


def _parse_dt_timestamp(value: Any) -> datetime | None:
    """DT emits ISO-8601 strings; parse defensively."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = ["dt_resync_task"]
