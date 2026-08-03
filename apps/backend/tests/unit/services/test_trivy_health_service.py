"""
Service-layer tests for ``services.trivy_health_service`` — W6-#43e.

Coverage targets:
  - ``metadata.json`` happy path parse → all fields populated.
  - ``metadata.json`` absent → graceful "not yet downloaded" response
    (``last_update is None``, ``freshness == "unknown"``).
  - ``metadata.json`` corrupt → same graceful empty response (no raise).
  - Freshness classification boundaries: < 7d fresh / 7-14d stale /
    > 14d very_stale / unknown.
  - 60s cache TTL — second call inside the window does not re-read disk.
  - Env override paths: ``TRIVY_CACHE_DIR`` / ``TRIVY_DB_REPOSITORY`` /
    ``TRIVY_DB_REFRESH_HOURS``.
  - Pydantic ``TrivyDbStatusOut`` round-trip from the dataclass.

These are pure unit tests — no database, no Redis, no subprocess. We pin
``TRIVY_CACHE_DIR`` to a per-test ``tmp_path`` so each test owns its own
on-disk fixture and tests run in parallel safely.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from integrations.trivy import (
    TrivyDbStatus,
    _classify_freshness,
    _parse_iso,
    get_trivy_db_status,
)
from schemas.admin_ops import TrivyDbStatusOut

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _write_metadata(
    cache_dir: Path,
    *,
    updated_at: str | None = "2026-05-27T03:14:00Z",
    version: int | None = 2,
    vuln_count: int | None = 432_187,
    extra: dict[str, object] | None = None,
) -> Path:
    """Write a Trivy-shaped metadata.json under ``cache_dir/db/``."""
    db_dir = cache_dir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    if updated_at is not None:
        payload["UpdatedAt"] = updated_at
    if version is not None:
        payload["Version"] = version
    if vuln_count is not None:
        payload["VulnerabilityCount"] = vuln_count
    if extra:
        payload.update(extra)
    metadata_path = db_dir / "metadata.json"
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    # Drop a stub blob file so ``db_size_bytes`` is a real, non-zero number.
    (db_dir / "trivy.db").write_bytes(b"\x00" * 1024)
    return metadata_path


@pytest.fixture
def trivy_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Per-test Trivy cache directory; clears the service cache between tests."""
    cache_dir = tmp_path / "trivy-cache"
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(cache_dir))
    # Reset the service-level cache between tests so cached snapshots from
    # earlier tests do not leak across.
    from services.trivy_health_service import reset_cache

    reset_cache()
    yield cache_dir
    reset_cache()


# ---------------------------------------------------------------------------
# ``integrations.trivy.get_trivy_db_status``
# ---------------------------------------------------------------------------


def test_get_trivy_db_status_happy_path_parses_metadata(trivy_cache: Path) -> None:
    """metadata.json present → all fields populated."""
    now = datetime(2026, 5, 28, 3, 14, 0, tzinfo=UTC)
    _write_metadata(
        trivy_cache,
        updated_at="2026-05-27T03:14:00Z",  # 24h before ``now``
        version=2,
        vuln_count=432_187,
    )

    status = get_trivy_db_status(now=now)

    assert status.last_update == datetime(2026, 5, 27, 3, 14, 0, tzinfo=UTC)
    assert status.freshness == "fresh"
    assert status.vuln_count == 432_187
    assert status.db_version == "trivy-db schema v2"
    assert status.db_size_bytes is not None and status.db_size_bytes >= 1024
    assert status.refresh_interval_hours == 168  # default weekly
    assert status.next_refresh_at == datetime(2026, 6, 3, 3, 14, 0, tzinfo=UTC)
    assert status.cache_dir == str(trivy_cache)
    assert status.repository == "ghcr.io/aquasecurity/trivy-db"


def test_get_trivy_db_status_missing_metadata_returns_unknown(
    trivy_cache: Path,
) -> None:
    """metadata.json absent → all dynamic fields ``None``, freshness ``unknown``."""
    # Do not write metadata.json — fresh worker boot state.
    status = get_trivy_db_status()

    assert status.last_update is None
    assert status.next_refresh_at is None
    assert status.vuln_count is None
    assert status.db_version is None
    assert status.db_size_bytes is None
    assert status.freshness == "unknown"
    # Config fields still present.
    assert status.cache_dir == str(trivy_cache)
    assert status.repository == "ghcr.io/aquasecurity/trivy-db"


def test_get_trivy_db_status_corrupt_metadata_graceful(trivy_cache: Path) -> None:
    """Corrupt metadata.json must not raise — same empty-state as absent."""
    db_dir = trivy_cache / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "metadata.json").write_text("not-json{{{", encoding="utf-8")

    status = get_trivy_db_status()

    assert status.last_update is None
    assert status.freshness == "unknown"
    assert status.cache_dir == str(trivy_cache)


def test_get_trivy_db_status_partial_metadata_keeps_known_fields(
    trivy_cache: Path,
) -> None:
    """metadata.json missing ``VulnerabilityCount`` but with ``UpdatedAt`` is OK."""
    now = datetime(2026, 5, 28, 0, 0, 0, tzinfo=UTC)
    _write_metadata(
        trivy_cache,
        updated_at="2026-05-26T00:00:00Z",
        version=2,
        vuln_count=None,  # do not write the key
    )

    status = get_trivy_db_status(now=now)

    assert status.last_update == datetime(2026, 5, 26, 0, 0, 0, tzinfo=UTC)
    assert status.vuln_count is None
    assert status.db_version == "trivy-db schema v2"
    assert status.freshness == "fresh"


def test_get_trivy_db_status_count_alias_keys(trivy_cache: Path) -> None:
    """``Count`` / ``AdvisoryCount`` are alternative keys older Trivy used."""
    _write_metadata(
        trivy_cache,
        vuln_count=None,
        extra={"AdvisoryCount": 99_999},
    )

    status = get_trivy_db_status()

    assert status.vuln_count == 99_999


# ---------------------------------------------------------------------------
# Freshness classifier boundaries
# ---------------------------------------------------------------------------


def test_classify_freshness_fresh_under_seven_days() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    assert _classify_freshness(now - timedelta(days=0, hours=1), now=now) == "fresh"
    assert _classify_freshness(now - timedelta(days=6, hours=23), now=now) == "fresh"


def test_classify_freshness_stale_between_seven_and_fourteen() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    assert _classify_freshness(now - timedelta(days=7), now=now) == "stale"
    assert _classify_freshness(now - timedelta(days=13, hours=23), now=now) == "stale"


def test_classify_freshness_very_stale_above_fourteen() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    assert _classify_freshness(now - timedelta(days=14), now=now) == "very_stale"
    assert _classify_freshness(now - timedelta(days=365), now=now) == "very_stale"


def test_classify_freshness_unknown_for_none() -> None:
    now = datetime(2026, 5, 28, tzinfo=UTC)
    assert _classify_freshness(None, now=now) == "unknown"


def test_classify_freshness_naive_datetime_assumed_utc() -> None:
    """Trivy occasionally emits naive timestamps — treat as UTC."""
    now = datetime(2026, 5, 28, tzinfo=UTC)
    naive = datetime(2026, 5, 27)  # 24h before, no tz
    assert _classify_freshness(naive, now=now) == "fresh"


# ---------------------------------------------------------------------------
# Service-layer 60s cache + Pydantic shape
# ---------------------------------------------------------------------------


def test_service_caches_snapshot_for_ttl(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two calls inside the 60s window hit disk exactly once."""
    from integrations import trivy as trivy_module
    from services import trivy_health_service as service

    _write_metadata(trivy_cache)

    calls = {"n": 0}
    real_get = trivy_module.get_trivy_db_status

    def counting_get(*args: object, **kwargs: object) -> TrivyDbStatus:
        calls["n"] += 1
        return real_get(*args, **kwargs)

    # Patch at the service module's import site, not the source module —
    # the service module did ``from integrations.trivy import ...`` so the
    # symbol is rebound there.
    monkeypatch.setattr(service, "get_trivy_db_status", counting_get)

    # Pin the monotonic clock to a fixed value so both calls land inside
    # the TTL window deterministically.
    monkeypatch.setattr(service, "_clock", lambda: 1000.0)

    snap1 = service.get_trivy_db_status_cached()
    snap2 = service.get_trivy_db_status_cached()

    assert calls["n"] == 1, "second call should be served from cache"
    assert snap1 is snap2


def test_service_refreshes_after_ttl(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advancing the clock past the TTL re-stats disk."""
    from integrations import trivy as trivy_module
    from services import trivy_health_service as service

    _write_metadata(trivy_cache)

    calls = {"n": 0}
    real_get = trivy_module.get_trivy_db_status

    def counting_get(*args: object, **kwargs: object) -> TrivyDbStatus:
        calls["n"] += 1
        return real_get(*args, **kwargs)

    monkeypatch.setattr(service, "get_trivy_db_status", counting_get)

    clock = {"t": 1000.0}
    monkeypatch.setattr(service, "_clock", lambda: clock["t"])

    service.get_trivy_db_status_cached()
    clock["t"] += 61.0  # past the 60s TTL
    service.get_trivy_db_status_cached()

    assert calls["n"] == 2


def test_get_trivy_db_health_returns_pydantic(trivy_cache: Path) -> None:
    """Public entry point produces a serialisable Pydantic model."""
    from services.trivy_health_service import get_trivy_db_health

    _write_metadata(trivy_cache)

    out = get_trivy_db_health()
    assert isinstance(out, TrivyDbStatusOut)
    # round-trip JSON; FastAPI will hit the same path.
    payload = json.loads(out.model_dump_json())
    assert payload["cache_dir"] == str(trivy_cache)
    assert payload["repository"] == "ghcr.io/aquasecurity/trivy-db"
    assert payload["refresh_interval_hours"] == 168
    assert payload["freshness"] in {"fresh", "stale", "very_stale"}


def test_get_trivy_db_health_empty_state_serialisable(trivy_cache: Path) -> None:
    """Empty-state response (no metadata.json) is a valid model with nulls."""
    from services.trivy_health_service import get_trivy_db_health

    out = get_trivy_db_health()
    payload = json.loads(out.model_dump_json())

    assert payload["last_update"] is None
    assert payload["next_refresh_at"] is None
    assert payload["vuln_count"] is None
    assert payload["db_version"] is None
    assert payload["db_size_bytes"] is None
    assert payload["freshness"] == "unknown"


def test_get_trivy_db_health_swallows_unexpected_error(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blow-up in the underlying probe must degrade to empty-state."""
    from services import trivy_health_service as service

    def boom() -> TrivyDbStatus:
        raise RuntimeError("disk yanked")

    monkeypatch.setattr(service, "get_trivy_db_status_cached", boom)

    out = service.get_trivy_db_health()
    assert isinstance(out, TrivyDbStatusOut)
    assert out.freshness == "unknown"
    assert out.last_update is None
    # Config fields still present so the FE can render the repository line.
    assert out.repository == "ghcr.io/aquasecurity/trivy-db"


# ---------------------------------------------------------------------------
# Env overrides
# ---------------------------------------------------------------------------


def test_env_overrides_propagate(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``TRIVY_DB_REPOSITORY`` / ``TRIVY_DB_REFRESH_HOURS`` reach the response."""
    monkeypatch.setenv("TRIVY_DB_REPOSITORY", "registry.internal/mirror/trivy-db")
    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", "24")

    _write_metadata(trivy_cache)
    status = get_trivy_db_status()

    assert status.repository == "registry.internal/mirror/trivy-db"
    assert status.refresh_interval_hours == 24


def test_refresh_hours_invalid_falls_back_to_default(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-integer ``TRIVY_DB_REFRESH_HOURS`` does not crash — fall back."""
    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", "not-a-number")

    _write_metadata(trivy_cache)
    status = get_trivy_db_status()

    assert status.refresh_interval_hours == 168


def test_refresh_hours_zero_clamped_to_one(
    trivy_cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0/negative is clamped — Trivy never refreshes faster than 1h."""
    monkeypatch.setenv("TRIVY_DB_REFRESH_HOURS", "0")

    _write_metadata(trivy_cache)
    status = get_trivy_db_status()

    assert status.refresh_interval_hours == 1


# ---------------------------------------------------------------------------
# Timestamp parser
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_suffix() -> None:
    parsed = _parse_iso("2026-05-27T03:14:00Z")
    assert parsed == datetime(2026, 5, 27, 3, 14, 0, tzinfo=UTC)


def test_parse_iso_handles_offset() -> None:
    parsed = _parse_iso("2026-05-27T03:14:00+00:00")
    assert parsed == datetime(2026, 5, 27, 3, 14, 0, tzinfo=UTC)


def test_parse_iso_rejects_garbage() -> None:
    assert _parse_iso("not-a-timestamp") is None
    assert _parse_iso(None) is None
    assert _parse_iso(42) is None  # type: ignore[arg-type]
