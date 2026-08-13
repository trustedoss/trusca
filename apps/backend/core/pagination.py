# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Shared bounds for offset pagination.

``page`` had a lower bound (``ge=1``) on every listing endpoint and no upper
bound on any of them. Python integers are unbounded, so a page number of any
size parsed fine, got multiplied into an OFFSET, and reached asyncpg as a
value outside int64:

    asyncpg.exceptions.DataError: invalid input for query argument $2:
    423599349510580893366758764207878963180 (value out of int64 range)

which surfaced as a 500. Schema-based fuzzing found it on two endpoints;
every listing endpoint had it.

PAGE_MAX is the cap. It is far above any page a real dataset reaches and far
below the point where ``(page - 1) * page_size`` threatens int64, so the
failure mode becomes a 422 naming the parameter instead of a server error.
Deep offsets are also slow, which is a second reason not to leave this open.
"""

# (PAGE_MAX - 1) * 200 (the largest page_size any endpoint allows) is ~2e8,
# comfortably inside int64.
PAGE_MAX = 1_000_000

__all__ = ["PAGE_MAX"]
