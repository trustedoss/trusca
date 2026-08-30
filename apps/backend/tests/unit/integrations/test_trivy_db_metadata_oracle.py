"""
``get_trivy_db_status`` vs. a zero-value ``DownloadedAt`` (E2, unit half).

Context: ``~/projects/trusca-internal/docs/testing-hardening-plan-2026-08.md``,
type E ("offline operating mode"), unit ``E2. metadata.json 판정 일치``.

The on-disk ``metadata.json`` written by ``trivy --download-db-only`` carries
four fields: ``Version``, ``NextUpdate``, ``UpdatedAt``, ``DownloadedAt``.
``get_trivy_db_status`` reads ``DownloadedAt`` alongside ``UpdatedAt`` /
``Version`` when it classifies freshness for the admin/health panel: a
zero-value ``DownloadedAt`` (Go's zero ``time.Time``, the real CLI's
corruption signal meaning the download never actually completed) is treated
as if the metadata file did not exist, so the panel no longer disagrees with
the CLI by reporting an unusable DB "fresh".

This module only fixes the panel's judgement in isolation; it does not
invoke the real ``trivy`` binary. The cross-check against actual CLI
acceptance is unit E1 (Wave 3, out of scope here).

Fixture provenance: ``fixtures/trivy/db-metadata-real-download.json`` is a
verbatim capture of a real ``trivy --download-db-only`` run (trivy 0.71.2,
``$TRIVY_CACHE_DIR/db/metadata.json``) on a contributor workstation, not a
hand-written minimal JSON (hardening rule #3). The zero-value case is built
by copying that capture in-memory and overwriting only ``DownloadedAt``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

FIXTURE_PATH = Path(__file__).parents[2] / "fixtures" / "trivy" / "db-metadata-real-download.json"

# Trivy's sentinel for "never actually downloaded": Go's zero-value
# ``time.Time`` serialises to this RFC3339 string.
_ZERO_TIME = "0001-01-01T00:00:00Z"


def _load_real_capture() -> dict[str, Any]:
    with FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def _write_metadata(cache_dir: Path, payload: dict[str, Any]) -> Path:
    db_dir = cache_dir / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = db_dir / "metadata.json"
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")
    return metadata_path


@pytest.fixture
def trivy_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Per-test Trivy cache dir, mirroring the pattern in
    ``test_trivy_health_service.py`` so this file's fixture setup stays
    recognisable to anyone who has read that one."""
    cache_dir = tmp_path / "trivy-cache"
    monkeypatch.setenv("TRIVY_CACHE_DIR", str(cache_dir))
    return cache_dir


def test_real_capture_fixture_has_all_four_trivy_fields() -> None:
    """Sanity check on the fixture itself, guarding against silent
    truncation if someone re-captures it later and drops a field."""
    data = _load_real_capture()
    assert set(data.keys()) == {"Version", "NextUpdate", "UpdatedAt", "DownloadedAt"}
    assert isinstance(data["Version"], int)
    assert data["DownloadedAt"] != _ZERO_TIME, (
        "fixture must be a real, successful download; the zero-value "
        "variant is built separately in the test below"
    )


def test_real_capture_metadata_classifies_fresh_shortly_after_update(
    trivy_cache: Path,
) -> None:
    """Baseline: the unmodified real capture, read shortly after its own
    ``UpdatedAt``, is fresh. This pins the happy path before we mutate
    ``DownloadedAt`` below, so a future change to ``_classify_freshness``
    thresholds does not silently invalidate the oracle test's premise."""
    from integrations.trivy import get_trivy_db_status

    data = _load_real_capture()
    _write_metadata(trivy_cache, data)

    updated_at = datetime.fromisoformat(data["UpdatedAt"].replace("Z", "+00:00"))
    now = updated_at + timedelta(hours=1)

    status = get_trivy_db_status(now=now)

    assert status.freshness == "fresh"


def test_zero_value_downloaded_at_is_not_reported_fresh(trivy_cache: Path) -> None:
    """A metadata.json whose ``DownloadedAt`` is the Go zero-value must not
    be classified 'fresh' by the admin panel, even though ``UpdatedAt``
    alone looks recent: the real trivy CLI treats this shape as a
    corrupted/never-completed download and would refuse to use the DB."""
    from integrations.trivy import get_trivy_db_status

    data = _load_real_capture()
    zero_downloaded = dict(data)
    zero_downloaded["DownloadedAt"] = _ZERO_TIME
    _write_metadata(trivy_cache, zero_downloaded)

    updated_at = datetime.fromisoformat(data["UpdatedAt"].replace("Z", "+00:00"))
    now = updated_at + timedelta(hours=1)

    status = get_trivy_db_status(now=now)

    assert status.freshness != "fresh"


def test_zero_value_downloaded_at_only_field_changed(trivy_cache: Path) -> None:
    """Guard against the mutation itself drifting: the zero-value variant
    must be identical to the real capture except for ``DownloadedAt``, so
    the test above is actually isolating that one field and not silently
    also changing ``UpdatedAt``/``Version``."""
    data = _load_real_capture()
    zero_downloaded = dict(data)
    zero_downloaded["DownloadedAt"] = _ZERO_TIME

    diff_keys = {key for key in data if data.get(key) != zero_downloaded.get(key)}
    assert diff_keys == {"DownloadedAt"}
    assert zero_downloaded["UpdatedAt"] == data["UpdatedAt"]
    assert zero_downloaded["Version"] == data["Version"]
