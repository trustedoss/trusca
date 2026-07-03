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


def _existing(spdx_id: str, category: str, *, review_flag: str | None = None):
    """A fake existing License row. Carries the attributes ``_get_or_create_license``
    now reads (spdx_id / name / review_flag) so the self-heal + review-flag
    reconcile paths both exercise a realistic row shape.
    """
    return SimpleNamespace(
        spdx_id=spdx_id, name=spdx_id, category=category, review_flag=review_flag
    )


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
    row = _existing("GPL-3.0-or-later AND GPL-3.0-only", "unknown")
    s = _FakeSession(row)
    out = _get_or_create_license(
        s, spdx_id="GPL-3.0-or-later AND GPL-3.0-only", reference_url=None
    )
    assert out is row
    assert row.category == "forbidden"  # upgraded on re-scan
    assert row.review_flag is None  # copyleft is not an AI review class
    assert s.flushed == 1


def test_existing_unknown_permissive_compound_healed_to_allowed() -> None:
    row = _existing("MIT AND ISC AND BSD-3-Clause", "unknown")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="MIT AND ISC AND BSD-3-Clause", reference_url=None)
    assert row.category == "allowed"
    assert s.flushed == 1


def test_existing_classified_row_is_never_clobbered() -> None:
    # An already-known category must not be overwritten even if reclassification
    # would differ — only unknown is healed.
    row = _existing("GPL-3.0-only", "allowed")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="GPL-3.0-only", reference_url=None)
    assert row.category == "allowed"  # untouched
    assert s.flushed == 0


def test_existing_unknown_unmappable_stays_unknown() -> None:
    row = _existing("Custom-A AND Custom-B", "unknown")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="Custom-A AND Custom-B", reference_url=None)
    assert row.category == "unknown"
    assert s.flushed == 0  # nothing to upgrade → no write


def test_missing_row_is_created_with_classified_category() -> None:
    s = _FakeSession(None)
    out = _get_or_create_license(s, spdx_id="GPL-3.0-only", reference_url="http://x")
    assert out.category == "forbidden"
    assert out.review_flag is None
    assert s.added == [out]
    assert s.flushed == 1


# ---------------------------------------------------------------------------
# Phase D1 — review_flag persist boundary on _get_or_create_license
# ---------------------------------------------------------------------------


def test_missing_ai_license_row_is_created_with_review_flag() -> None:
    """A newly-created Llama community license row carries behavioral_use."""
    s = _FakeSession(None)
    out = _get_or_create_license(
        s, spdx_id="LLAMA-2-Community-License", reference_url=None
    )
    assert out.review_flag == "behavioral_use"
    assert s.added == [out]
    assert s.flushed == 1


def test_missing_non_commercial_row_is_created_with_review_flag() -> None:
    s = _FakeSession(None)
    out = _get_or_create_license(s, spdx_id="CC-BY-NC-4.0", reference_url=None)
    assert out.review_flag == "non_commercial"


def test_existing_null_review_flag_is_backfilled_on_touch() -> None:
    """A legacy row created before the classifier existed gets its flag on re-scan."""
    row = _existing("OpenRAIL-M", "unknown", review_flag=None)
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="OpenRAIL-M", reference_url=None)
    assert row.review_flag == "behavioral_use"
    assert s.flushed >= 1  # at least the review-flag reconcile wrote


def test_existing_correct_review_flag_is_left_untouched() -> None:
    """Reconcile is idempotent: an already-correct flag triggers no extra write."""
    row = _existing("CC-BY-NC-4.0", "allowed", review_flag="non_commercial")
    s = _FakeSession(row)
    _get_or_create_license(s, spdx_id="CC-BY-NC-4.0", reference_url=None)
    assert row.review_flag == "non_commercial"
    assert s.flushed == 0  # category known + flag already correct → no write
