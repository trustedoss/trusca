# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Shared vocabulary for the catalog sync-state tables.

The three sync-state models (``eol_sync_state``, ``kev_sync_state``,
``malicious_sync_state``) and the three refresh tasks that write them all
describe the same closed set of skip reasons. Before this module the set
lived in five places at once: three model docstrings and, as bare string
literals, in each task. They had already drifted apart:

  - ``kev_sync_state`` listed four reasons and omitted ``refresh_disabled``.
  - ``no_dataset`` / ``no_products_mapped`` / ``snapshot_too_large`` are
    written by ``tasks/eol_catalog_refresh.py`` but appeared in no model
    docstring at all.

Drift was possible because prose cannot be compared to code. Everything that
names a skip reason now imports from here, so there is one definition and the
contract test compares real objects rather than parsing English.

``unexpected:<ExceptionName>`` is deliberately not an enumerable member: the
suffix is the class name of whatever escaped the task body. It is a prefix,
and ``unexpected_reason()`` is the only supported way to build one so the
separator never drifts either.

Scope. This vocabulary covers the reasons that reach a ``*_sync_state`` row,
which means the three catalog refresh tasks and nothing else. Other periodic
tasks also put a ``skipped_reason`` in their returned summary dict
(``vulnerability_rematch`` has ``trivy_timeout``, ``queue_backlog_alert`` has
``metrics_disabled``, and so on), but those values are never persisted to
these tables and are not members here. Whether the two families should merge
is a question for whatever ends up recording task runs generally; until then,
widening this tuple with a value no sync-state row can hold would make the
contract test below assert something untrue.
"""

from __future__ import annotations

# Individual members. Call sites import these names rather than repeating the
# string, so a typo is an ImportError instead of a value that silently never
# matches.

#: The feature itself is switched off; the task exits before any fetch.
SYNC_SKIPPED_DISABLED = "disabled"
#: The feature is on but periodic refresh is off, so an existing snapshot is
#: kept as-is.
SYNC_SKIPPED_REFRESH_DISABLED = "refresh_disabled"
#: Upstream feed could not be reached, or answered with an error.
SYNC_SKIPPED_FEED_UNAVAILABLE = "feed_unavailable"
#: Feed answered, but with implausibly few records. Treated as a bad publish
#: upstream rather than a real shrink, so the snapshot is kept.
SYNC_SKIPPED_FEED_BELOW_SANITY_FLOOR = "feed_below_sanity_floor"
#: eol only: the feed carries no dataset for the requested cycle.
SYNC_SKIPPED_NO_DATASET = "no_dataset"
#: eol only: nothing in the catalog maps to a product we track.
SYNC_SKIPPED_NO_PRODUCTS_MAPPED = "no_products_mapped"
#: eol only: the fetched snapshot exceeds the size ceiling for a single row,
#: so persisting it would bloat the sync-state table.
SYNC_SKIPPED_SNAPSHOT_TOO_LARGE = "snapshot_too_large"

#: Closed set of enumerable skip reasons, in the order a refresh task reaches
#: them: configuration first, then feed availability, then payload sanity.
SYNC_SKIPPED_REASON_VALUES: tuple[str, ...] = (
    SYNC_SKIPPED_DISABLED,
    SYNC_SKIPPED_REFRESH_DISABLED,
    SYNC_SKIPPED_FEED_UNAVAILABLE,
    SYNC_SKIPPED_FEED_BELOW_SANITY_FLOOR,
    SYNC_SKIPPED_NO_DATASET,
    SYNC_SKIPPED_NO_PRODUCTS_MAPPED,
    SYNC_SKIPPED_SNAPSHOT_TOO_LARGE,
)

#: Prefix for the open-ended reason. Never write this literal at a call site;
#: call :func:`unexpected_reason` so the separator stays in one place.
SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX = "unexpected:"


def unexpected_reason(exc: BaseException) -> str:
    """Return the ``unexpected:<ExceptionName>`` reason for ``exc``.

    Used by the refresh tasks' catch-all handlers. The exception's message is
    deliberately dropped: the column is 64 characters and an upstream error
    string can carry a URL with credentials in it. The class name is what the
    health panel groups by, and the full traceback is already in the log.
    """
    return f"{SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX}{type(exc).__name__}"


def is_valid_skipped_reason(reason: str) -> bool:
    """Whether ``reason`` is a member of the vocabulary.

    Accepts both an enumerable member and an ``unexpected:<Name>`` value.
    The contract test uses this to check every literal the tasks assign.
    """
    if reason in SYNC_SKIPPED_REASON_VALUES:
        return True
    return reason.startswith(SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX) and len(reason) > len(
        SYNC_SKIPPED_REASON_UNEXPECTED_PREFIX
    )
