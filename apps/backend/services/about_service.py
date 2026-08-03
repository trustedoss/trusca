# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Product-identity and license-notice reads for ``GET /v1/about``.

Why this exists
---------------
Apache-2.0 §4(d) is satisfied for TRUSCA's own distribution by the files shipped
at ``/licenses/`` in every image and inside the Helm chart. That covers the
operator who has shell access to the container. It does not help the person
looking at the portal in a browser, and it does not help an air-gapped
deployment where "go read it on GitHub" is not an option.

So the notices are readable from the product itself. This module locates them and
serves their text; the API layer decides who may read it.

Resolution order for the notice directory:

1. ``/licenses/`` — where every TRUSCA image places them (see the Dockerfiles).
2. Each ancestor directory of this module, nearest first — a developer running
   uvicorn straight from a checkout, and the test suite.

The first directory holding BOTH ``LICENSE`` and ``NOTICE`` wins. No environment
variable: an operator has no reason to relocate these, and a misconfigured
override would silently serve the wrong license text.

Why ancestors rather than a fixed depth: the first version of this module
computed the repo root as ``parents[3]``, which is correct in a checkout and an
``IndexError`` in the container, where this file sits at
``/app/services/about_service.py`` and has only three ancestors. That index ran
at import time, so it did not fail this endpoint — it stopped the application
from starting at all, and the backend never went healthy. Walking ancestors has
no depth to get wrong.

Pure filesystem reads — no DB, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import structlog

from core.config import slsa_builder_version

log = structlog.get_logger("services.about")

PRODUCT_NAME = "TRUSCA"
LICENSE_SPDX_ID = "Apache-2.0"
LICENSE_NAME = "Apache License, Version 2.0"
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
COPYRIGHT = "Copyright 2026 TRUSCA contributors"
SOURCE_URL = "https://github.com/trustedoss/trusca"

# A notice file is text a human reads. 1 MiB is far above the largest of ours
# (the Apache-2.0 text, ~11 KB) and still bounds the response if a deployment
# somehow mounts something enormous at /licenses/.
MAX_DOCUMENT_BYTES = 1024 * 1024


@dataclass(frozen=True)
class NoticeDocument:
    """One notice file the portal can display."""

    id: str
    title: str
    filename: str
    description: str


# Order is the order the UI shows them in: what the product is licensed under,
# then who holds copyright, then what it borrows from others.
NOTICE_DOCUMENTS: tuple[NoticeDocument, ...] = (
    NoticeDocument(
        id="license",
        title=LICENSE_NAME,
        filename="LICENSE",
        description="The license TRUSCA itself is distributed under.",
    ),
    NoticeDocument(
        id="notice",
        title="Notice",
        filename="NOTICE",
        description="TRUSCA's own copyright notice, per Apache-2.0 §4(d).",
    ),
    NoticeDocument(
        id="third-party-notices",
        title="Third-party notices",
        filename="THIRD_PARTY_NOTICES.md",
        description=(
            "Attribution for third-party material vendored into the source tree "
            "and for the tools bundled in the container images."
        ),
    ),
)

_DOCUMENTS_BY_ID = {doc.id: doc for doc in NOTICE_DOCUMENTS}

_IMAGE_NOTICE_DIR = Path("/licenses")


def notice_dir_candidates(module_file: Path | None = None) -> tuple[Path, ...]:
    """Directories to check for the notice files, in priority order.

    ``/licenses/`` first — every image puts them there, so a deployed container
    stops on the first candidate. Then this module's ancestors, nearest first, so
    a checkout resolves at whatever depth it happens to sit.

    ``module_file`` exists for tests: it lets a case pin the container's shallow
    layout (``/app/services/about_service.py``) without moving the file.
    """
    base = (module_file or Path(__file__)).resolve()
    return (_IMAGE_NOTICE_DIR, *base.parents)


class NoticeNotFoundError(LookupError):
    """No such document id, or its file is absent from this deployment."""


class NoticeTooLargeError(ValueError):
    """The file on disk exceeds :data:`MAX_DOCUMENT_BYTES`."""


@lru_cache(maxsize=1)
def notice_dir() -> Path | None:
    """The directory holding the notice files, or ``None`` if there is none.

    Cached: the answer is a property of the deployment's filesystem layout and
    cannot change while the process runs. This is not configuration caching —
    CLAUDE.md rule #11 is about environment variables, and this reads none.

    Requires BOTH LICENSE and NOTICE so walking up to ``/`` cannot latch onto an
    unrelated LICENSE file somewhere above the checkout.
    """
    candidates = notice_dir_candidates()
    for candidate in candidates:
        if (candidate / "LICENSE").is_file() and (candidate / "NOTICE").is_file():
            return candidate
    log.warning(
        "about_notice_dir_missing",
        checked=[str(c) for c in candidates],
        detail="license notices will be reported as unavailable",
    )
    return None


def available_documents() -> list[tuple[NoticeDocument, int | None]]:
    """Every known document with its size, or ``None`` size when absent.

    Absent files are still listed. A deployment missing a notice file is a
    packaging fault worth surfacing in the UI, not something to hide by
    shortening the list.
    """
    base = notice_dir()
    out: list[tuple[NoticeDocument, int | None]] = []
    for doc in NOTICE_DOCUMENTS:
        size: int | None = None
        if base is not None:
            path = base / doc.filename
            if path.is_file():
                size = path.stat().st_size
        out.append((doc, size))
    return out


def read_document(document_id: str) -> tuple[NoticeDocument, str]:
    """Return ``(document, text)`` for ``document_id``.

    Raises:
        NoticeNotFoundError: unknown id, or the file is not in this deployment.
        NoticeTooLargeError: the file exceeds :data:`MAX_DOCUMENT_BYTES`.
    """
    doc = _DOCUMENTS_BY_ID.get(document_id)
    if doc is None:
        raise NoticeNotFoundError(document_id)

    base = notice_dir()
    if base is None:
        raise NoticeNotFoundError(document_id)

    path = base / doc.filename
    if not path.is_file():
        raise NoticeNotFoundError(document_id)

    size = path.stat().st_size
    if size > MAX_DOCUMENT_BYTES:
        raise NoticeTooLargeError(f"{doc.filename} is {size} bytes")

    return doc, path.read_text(encoding="utf-8")


def product_identity() -> dict[str, str]:
    """Name, version, license and source URL for the About surface."""
    return {
        "product": PRODUCT_NAME,
        "version": slsa_builder_version(),
        "license_spdx_id": LICENSE_SPDX_ID,
        "license_name": LICENSE_NAME,
        "license_url": LICENSE_URL,
        "copyright": COPYRIGHT,
        "source_url": SOURCE_URL,
    }
