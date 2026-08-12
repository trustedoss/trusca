# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Bounded body reader shared by both webhook receivers.

The receivers are public: the signature covers the body, so the body must be
read and the repository resolved before any credential can be checked. Every
byte of that work is therefore reachable without authentication, and the cost
is not constant: resolving the repository walks the URL character by
character, which puts an attacker-chosen amount of work in front of the first
credential check.

Reading with a cap is what makes that cost bounded. It is not a validation
step: a body over the limit is refused before it is buffered, so the process
never holds it.
"""

from __future__ import annotations

from fastapi import Request

from core.config import webhook_max_body_bytes


async def read_capped_body(request: Request) -> bytes | None:
    """Read the request body, or return ``None`` if it exceeds the cap.

    Two checks, because either one alone can be defeated:

    - ``Content-Length``, when present and over the limit, refuses before a
      single chunk is buffered.
    - The running total across the stream catches a body that under-declares
      its length or arrives chunked with no length at all.

    Returns the bytes on success. ``None`` means "too large": the caller
    answers 413 and does no further work.
    """
    limit = webhook_max_body_bytes()

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                return None
        except ValueError:
            # An unparseable header tells us nothing; fall through to the
            # streaming cap, which does not depend on the caller's honesty.
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = ["read_capped_body"]
