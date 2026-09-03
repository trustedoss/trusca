# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The batch row-status vocabulary, asserted where it is duplicated.

``BatchRowStatus`` is named in four places: the Literal, the two frozensets
that split it into success and failure, the service that assigns the values,
and the documentation a caller reads to decide which ones to branch on. Values
spread across places drift while each place stays internally consistent, which
is why this file exists (hardening rule 2).

The split matters more than the spelling. ``already_exists`` counting as
success is what makes a re-run of an interrupted onboarding report success; if
it slipped into the failure set, every re-run would report failure and
``all_succeeded`` would stop meaning anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from schemas.batch import (
    BATCH_FAILURE_STATUSES,
    BATCH_SUCCESS_STATUSES,
    MAX_BATCH_SIZE,
    BatchRowStatus,
)


def _vocabulary() -> set[str]:
    return set(get_args(BatchRowStatus))


def test_the_success_and_failure_sets_partition_the_vocabulary() -> None:
    """Every status is classified exactly once.

    A status in neither set is counted as a failure by `_summarise` without
    ever being named as one; a status in both makes the counts inconsistent
    with each other.
    """
    assert BATCH_SUCCESS_STATUSES | BATCH_FAILURE_STATUSES == _vocabulary()
    assert not (BATCH_SUCCESS_STATUSES & BATCH_FAILURE_STATUSES)


def test_an_existing_row_counts_as_success() -> None:
    """Pinned on its own because reclassifying it is the tempting mistake.

    `already_exists` reads like a non-result, and moving it to the failure set
    would make every re-run of a batch report failure. Re-running is the normal
    way to finish an interrupted onboarding.
    """
    assert "already_exists" in BATCH_SUCCESS_STATUSES
    assert "created" in BATCH_SUCCESS_STATUSES


def test_the_three_actionable_failures_stay_distinct() -> None:
    """403, 409 and 429 ask different things of the caller.

    Get access, do nothing, retry later. Collapsing them into one status is
    what makes "12 of 300 failed" unactionable.
    """
    assert BATCH_FAILURE_STATUSES == {"forbidden", "invalid", "rate_limited"}


def _docs_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        docs = candidate / "docs-site" / "docs"
        if docs.is_dir():
            return docs
    pytest.skip("docs-site not found above this file (backend-only checkout)")
    raise AssertionError("unreachable")


def test_every_status_is_documented() -> None:
    """A status a caller cannot look up is a status they will not handle."""
    page = _docs_root() / "reference" / "api-overview.md"
    if not page.is_file():
        pytest.skip("api-overview.md not present")

    text = page.read_text(encoding="utf-8")
    missing = sorted(status for status in _vocabulary() if f"`{status}`" not in text)

    assert not missing, f"the API overview does not describe these batch row statuses: {missing}"


def test_the_documented_batch_cap_matches_the_schema() -> None:
    """The cap is a number a caller has to respect, so it must be published."""
    page = _docs_root() / "reference" / "api-overview.md"
    if not page.is_file():
        pytest.skip("api-overview.md not present")

    # Matched in the sentence that states the cap, not anywhere on the page.
    # "200" also appears as an HTTP status and as a pagination maximum, so a
    # bare containment check passed against a page that never mentioned the
    # cap at all: raising MAX_BATCH_SIZE to 500 left this test green.
    text = page.read_text(encoding="utf-8")
    assert f"At most {MAX_BATCH_SIZE} rows per request." in text, (
        f"the API overview does not state the {MAX_BATCH_SIZE}-row batch cap "
        "in the sentence this test reads; change both together"
    )


def test_the_service_assigns_only_known_statuses() -> None:
    """A typo'd literal in the service would be a status nothing classifies."""
    import re

    for candidate in Path(__file__).resolve().parents:
        service = candidate / "services" / "batch_service.py"
        if service.is_file():
            break
    else:
        pytest.skip("batch_service.py not found above this file")

    assigned = set(re.findall(r'status="([a-z_]+)"', service.read_text(encoding="utf-8")))

    assert assigned <= _vocabulary(), (
        f"batch_service assigns statuses outside the vocabulary: "
        f"{sorted(assigned - _vocabulary())}"
    )
    assert assigned == _vocabulary(), (
        f"these statuses are declared but never assigned, so nothing produces "
        f"them: {sorted(_vocabulary() - assigned)}"
    )
