"""Re-scan license self-heal (Tier 4 / stateful) — _get_or_create_license.

Licenses are deduped globally by spdx_id, so a row created before the classifier
learned an id keeps its stale category forever — a forbidden compound SPDX
("GPL-3.0-or-later AND GPL-3.0-only") staying ``unknown`` is a build-gate risk.
The fix re-classifies an existing ``unknown`` row on re-encounter, but ONLY
upgrades unknown→known (never clobbers an already-classified category).

Fake-session unit test (matches the existing sync-task test pattern — no DB).
"""
from __future__ import annotations

from types import SimpleNamespace

from tasks.scan_source import _get_or_create_license


class _Result:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, existing):
        self._existing = existing
        self.flushed = 0
        self.added: list = []

    def execute(self, *_a, **_k):
        return _Result(self._existing)

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        self.flushed += 1


def test_existing_unknown_compound_is_healed_to_forbidden() -> None:
    row = SimpleNamespace(category="unknown")
    s = _FakeSession(row)
    out = _get_or_create_license(
        s, spdx_id="GPL-3.0-or-later AND GPL-3.0-only", reference_url=None
    )
    assert out is row
    assert row.category == "forbidden"  # upgraded on re-scan
    assert s.flushed == 1


def test_existing_unknown_permissive_compound_healed_to_allowed() -> None:
    row = SimpleNamespace(category="unknown")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="MIT AND ISC AND BSD-3-Clause", reference_url=None)
    assert row.category == "allowed"
    assert s.flushed == 1


def test_existing_classified_row_is_never_clobbered() -> None:
    # An already-known category must not be overwritten even if reclassification
    # would differ — only unknown is healed.
    row = SimpleNamespace(category="allowed")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="GPL-3.0-only", reference_url=None)
    assert row.category == "allowed"  # untouched
    assert s.flushed == 0


def test_existing_unknown_unmappable_stays_unknown() -> None:
    row = SimpleNamespace(category="unknown")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="Custom-A AND Custom-B", reference_url=None)
    assert row.category == "unknown"
    assert s.flushed == 0  # nothing to upgrade → no write


def test_missing_row_is_created_with_classified_category() -> None:
    s = _FakeSession(None)
    out = _get_or_create_license(s, spdx_id="GPL-3.0-only", reference_url="http://x")
    assert out.category == "forbidden"
    assert s.added == [out]
    assert s.flushed == 1
