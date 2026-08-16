# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Turning a CSV stream into a response, once (B5).

Every export route has the same problem: the row cap can only be answered
after a COUNT, but a ``StreamingResponse`` has already committed to 200 by
the time the first chunk is asked for. Yielding an error into the body would
produce a file that says nothing about being wrong, and a reader who opens
it in a spreadsheet has no way to tell.

So the first chunk is pulled here, before the response object exists. If the
generator raises instead, there is still a chance to answer with a 413 and an
RFC 7807 body the SPA can key a message off.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from core.errors import problem_response
from services.csv_export import CSV_MEDIA_TYPE, ExportTooLarge

__all__ = ["csv_stream_response"]


async def csv_stream_response(
    request: Request,
    *,
    stream: AsyncIterator[str],
    filename: str,
) -> Response:
    """
    Serve ``stream`` as a downloadable CSV, or a 413 if it refuses to start.

    ``filename`` is placed in ``Content-Disposition``; it must not contain a
    quote or a newline, which is why callers build it from fixed prefixes and
    formatted dates rather than from anything a user typed.
    """
    try:
        first = await stream.__anext__()
    except ExportTooLarge as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc),
            instance=request.url.path,
            type_=exc.type_uri,
            **exc.extensions,
        )
    except StopAsyncIteration:
        # An empty generator cannot happen today (the header row is
        # unconditional) but a future export that decides otherwise should
        # produce an empty file rather than a 500.
        first = ""

    async def body() -> AsyncIterator[str]:
        yield first
        async for chunk in stream:
            yield chunk

    return StreamingResponse(
        body(),
        media_type=CSV_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
