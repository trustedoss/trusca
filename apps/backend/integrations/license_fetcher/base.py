"""
Shared types + helpers for the multi-ecosystem license fetcher.

Why this exists
---------------
cdxgen extracts ``components[].licenses`` from package metadata, but for
transitive dependencies that metadata is often empty — the 2026-05-07 UAT
matrix (`docs/sessions/2026-05-07-uat-multi-ecosystem-matrix.md` §4.1)
counted 91 unknown licenses in pilot-java-maven, 39 in pilot-python, 164
in pilot-rust, 29 in pilot-go. The ``LicenseFetcher`` protocol gives us a
narrow surface to fill those gaps from each ecosystem's authoritative
registry (Maven Central, PyPI, crates.io, pkg.go.dev) without coupling
the scan pipeline to the per-registry HTTP shape.

Design notes
------------
* **Stateless protocol.** Each fetcher exposes ``fetch(purl, *, timeout)``
  and returns ``LicenseFetchResult | None``. ``None`` means *unknown* —
  the upstream registry returned no license metadata, or returned a
  free-text expression we cannot reduce to an SPDX id. ``None`` is
  cached as a *negative* entry (24h TTL) so we don't hammer external
  APIs for the same dead lookup.
* **HTTP via httpx.** The project already pins httpx for the DT client;
  reusing it (instead of pulling in ``requests``) keeps the dependency
  surface narrow and lets unit tests reuse the existing
  ``httpx.MockTransport`` pattern. Per-host concurrency limiting is
  enforced by a small in-process semaphore registry — Celery workers
  are single-process by default, so this is sufficient.
* **No env caching.** ``CLAUDE.md`` core rule #11 forbids module-level
  env access, so the fetchers resolve any tunables at call time.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx
import structlog

# SPDX 3.x licence-id token shape — ASCII letters/digits + ``-`` ``.``
# ``+``. Anchored so partial matches do not slip through. Used inside
# ``normalize_spdx_id`` to gate the "verbatim SPDX id" passthrough.
_SPDX_ID_TOKEN_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9.+\-]*\Z")

log = structlog.get_logger("integrations.license_fetcher")

# crates.io publishes a 1 req/sec policy. Other registries do not publish
# a hard limit but we still cap per-host concurrency at 1 to be polite —
# scans typically include hundreds of components and a worker bursting
# parallel fetchers would risk getting blocked. The min-interval also
# protects against accidental tight loops on retry.
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5

USER_AGENT = (
    "TrustedOSS-Portal/0.1 "
    "(+https://github.com/trustedoss/trustedoss-portal; license-fetcher)"
)


@dataclass(frozen=True)
class LicenseFetchResult:
    """A single SPDX-id + reference URL produced by a registry lookup.

    Attributes:
        spdx_id: Normalized SPDX identifier (e.g. ``"Apache-2.0"``).
            Already passed through :func:`normalize_spdx_id` so callers
            can hand it straight to ``_get_or_create_license``.
        reference_url: License-text URL from the registry (or ``None``
            when the registry did not include one).
        source: Short identifier for the registry that produced this
            result — used in the ``raw_data`` of the persisted
            ``LicenseFinding`` so downstream auditors can tell a
            cdxgen-emitted licence from a fetcher-emitted one.
    """

    spdx_id: str
    reference_url: str | None
    source: str


class LicenseFetcher(Protocol):
    """Per-ecosystem license metadata adapter."""

    def fetch(
        self,
        purl: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LicenseFetchResult | None:
        ...


# ---------------------------------------------------------------------------
# Per-host rate limiting
# ---------------------------------------------------------------------------

# Map of host (str) → (lock, last_call_ts). The lock serialises all
# requests to the same host (concurrency 1) and ``last_call_ts`` lets
# the helper sleep up to ``min_interval`` between calls.
_HOST_LOCKS: dict[str, tuple[threading.Lock, list[float]]] = {}
_REGISTRY_LOCK = threading.Lock()


def _host_gate(host: str) -> tuple[threading.Lock, list[float]]:
    with _REGISTRY_LOCK:
        existing = _HOST_LOCKS.get(host)
        if existing is None:
            existing = (threading.Lock(), [0.0])
            _HOST_LOCKS[host] = existing
    return existing


# ---------------------------------------------------------------------------
# HTTP helper with retry + per-host gate
# ---------------------------------------------------------------------------


def request_with_retry(
    *,
    client: httpx.Client,
    method: str,
    url: str,
    host: str,
    min_interval_seconds: float = 0.0,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    accept_404: bool = True,
) -> httpx.Response | None:
    """Issue an HTTP request through the per-host gate with retry.

    Returns:
        ``httpx.Response`` for any 2xx response, ``None`` for an
        accepted 404 (or after retries are exhausted on transport
        errors). 429 / 5xx trigger exponential backoff up to
        ``max_retries``; persistent failure returns ``None`` (the
        caller treats that as "license unknown" + negative cache).

    Why ``None`` on retry exhaustion (not raise):
        License lookup is best-effort metadata enrichment, not a hot
        path that must crash the scan. The fetcher returning ``None``
        on a flaky external API simply leaves the component's
        ``licenses`` empty — exactly the same observable state as a
        registry that has no metadata for the package.
    """
    lock, last = _host_gate(host)
    attempt = 0
    last_exc: Exception | None = None
    while attempt <= max_retries:
        with lock:
            # Honor min interval (e.g. crates.io 1 req/sec) under the lock so
            # parallel callers cannot race past it.
            now = clock()
            wait = min_interval_seconds - (now - last[0])
            if wait > 0:
                sleep(wait)
            last[0] = clock()
            try:
                response = client.request(method, url)
            except httpx.TimeoutException as exc:
                last_exc = exc
                log.warning(
                    "license_fetch_timeout",
                    host=host,
                    url=url,
                    attempt=attempt,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "license_fetch_network_error",
                    host=host,
                    url=url,
                    attempt=attempt,
                    error=str(exc)[:200],
                )
            else:
                status = response.status_code
                if 200 <= status < 300:
                    return response
                if status == 404 and accept_404:
                    return None
                if 300 <= status < 400:
                    # Registries occasionally redirect mirror traffic, but
                    # the fetcher clients run with ``follow_redirects=False``
                    # (security-reviewer L4, chore PR #6) so an unexpected
                    # 3xx is reported here. Returning ``None`` registers a
                    # negative cache entry — the attacker host in
                    # ``Location`` is never contacted.
                    log.warning(
                        "license_fetch_unexpected_redirect",
                        host=host,
                        url=url,
                        status=status,
                        location=response.headers.get("Location", "")[:500],
                    )
                    return None
                if status == 429 or 500 <= status < 600:
                    log.info(
                        "license_fetch_retryable",
                        host=host,
                        url=url,
                        status=status,
                        attempt=attempt,
                    )
                else:
                    # 4xx (other than 404/429) — caller-side issue, no retry.
                    log.info(
                        "license_fetch_client_error",
                        host=host,
                        url=url,
                        status=status,
                    )
                    return None

        attempt += 1
        if attempt > max_retries:
            break
        sleep(backoff_seconds * (2 ** (attempt - 1)))

    if last_exc is not None:
        log.warning(
            "license_fetch_giving_up",
            host=host,
            url=url,
            error=str(last_exc)[:200],
        )
    return None


# ---------------------------------------------------------------------------
# SPDX normalization
# ---------------------------------------------------------------------------

# Free-text license names → canonical SPDX ids. Intentionally narrow:
# we only normalize the strings each registry actually emits in the wild
# (sampled from the UAT pilot scans). Anything outside this map falls
# through to ``None`` and the fetcher returns "unknown" — that is fine,
# the goal of this layer is to *increase* known coverage, not to be a
# license-text identifier.
_SPDX_ALIASES: dict[str, str] = {
    # Apache
    "apache 2.0": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache software license, version 2.0": "Apache-2.0",
    "the apache software license, version 2.0": "Apache-2.0",
    "apache 1.1": "Apache-1.1",
    "apache license 1.1": "Apache-1.1",
    # MIT
    "mit": "MIT",
    "mit license": "MIT",
    "the mit license": "MIT",
    # BSD family
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "the new bsd license": "BSD-3-Clause",
    "revised bsd license": "BSD-3-Clause",
    # GPL family — these are the *family* spellings registries emit;
    # the SPDX-only-or-later distinction is preserved by our existing
    # _SPDX_ALIASES enumeration in the scan_source classifier.
    "gpl-2.0": "GPL-2.0-only",
    "gpl-3.0": "GPL-3.0-only",
    "gplv2": "GPL-2.0-only",
    "gplv3": "GPL-3.0-only",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-3.0": "LGPL-3.0-only",
    "agpl-3.0": "AGPL-3.0-only",
    # Maven POM free-text variants observed during the chore PR #7
    # UAT v2 spot-check (hibernate-core / aspectjweaver / similar).
    "gnu library general public license v2.1 or later": "LGPL-2.1-or-later",
    "gnu lesser general public license v2.1 or later": "LGPL-2.1-or-later",
    "gnu lesser general public license, version 2.1": "LGPL-2.1-only",
    "gnu lesser general public license version 2.1": "LGPL-2.1-only",
    "gnu lesser general public license v3.0 or later": "LGPL-3.0-or-later",
    "gnu general public license, version 2": "GPL-2.0-only",
    "gnu general public license, version 3": "GPL-3.0-only",
    "gnu general public license v2 or later": "GPL-2.0-or-later",
    "gnu general public license v3 or later": "GPL-3.0-or-later",
    "gnu affero general public license v3 or later": "AGPL-3.0-or-later",
    "eclipse public license - v 2.0": "EPL-2.0",
    "eclipse public license - v 1.0": "EPL-1.0",
    "eclipse public license, version 2.0": "EPL-2.0",
    "eclipse public license version 2.0": "EPL-2.0",
    "the eclipse public license version 2.0": "EPL-2.0",
    "the eclipse public license, version 2.0": "EPL-2.0",
    "epl-2.0 or later": "EPL-2.0",
    # Other commons
    "isc": "ISC",
    "isc license": "ISC",
    "mozilla public license 2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "epl 2.0": "EPL-2.0",
    "epl-2.0": "EPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "cddl 1.0": "CDDL-1.0",
    "cddl-1.0": "CDDL-1.0",
    "common development and distribution license": "CDDL-1.0",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0": "CC0-1.0",
    "wtfpl": "WTFPL",
    "zlib": "Zlib",
    "0bsd": "0BSD",
    "python software foundation license": "Python-2.0",
    "python-2.0": "Python-2.0",
    "psf-2.0": "Python-2.0",
}


def _split_compound(candidate: str, separator_upper: str) -> list[str]:
    """Split ``candidate`` on the case-insensitive separator (e.g. " OR ")."""
    upper = candidate.upper()
    pieces: list[str] = []
    start = 0
    while True:
        idx = upper.find(separator_upper, start)
        if idx == -1:
            pieces.append(candidate[start:].strip())
            break
        pieces.append(candidate[start:idx].strip())
        start = idx + len(separator_upper)
    return [p for p in pieces if p]


def normalize_spdx_id(raw: str | None) -> str | None:
    """Return a canonical SPDX id, or ``None`` if we cannot reduce it.

    Policy:
      * **AND** compound expressions (e.g. ``BSD-3-Clause AND MIT``)
        → ``None``. We cannot represent "must comply with both" as a
        single SPDX id.
      * **OR** compound expressions (e.g. ``MIT OR Apache-2.0`` —
        the dominant convention in the Rust ecosystem) → take the
        first token that normalises successfully. Per SPDX semantics,
        ``X OR Y`` means either license is acceptable, so picking the
        first is sound.
      * **WITH** compound expressions (e.g.
        ``GPL-2.0 WITH Classpath-exception-2.0``) → take the base
        license (left of ``WITH``). The exception clause cannot be
        represented as a single SPDX id; the obligation tracker can
        revisit the exception in a future PR.
      * Strings that *match* a known SPDX id (case-sensitive) pass
        through unchanged. We do not attempt to validate against the
        full SPDX list — the downstream classifier
        (``_LICENSE_CATEGORY_DEFAULTS``) will land any unrecognised
        id in the ``unknown`` bucket.
      * Free-text (e.g. ``"Apache 2.0"``) flows through the alias
        table. Anything not in the table → ``None`` (we'd rather emit
        "license unknown" than commit to a guess).

    The ``OR`` / ``WITH`` policy was added in chore PR #7 after the
    UAT v2 spot-check showed the previous "reject all compounds"
    rule produced 90% unknown for the Rust ecosystem.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    # 0) Alias-first. Phrases like "GNU Lesser General Public License
    # v2.1 or later" contain a literal " or " that the compound
    # splitter would otherwise mis-handle as an SPDX ``OR`` operator;
    # checking the alias map up front lets these pass straight through.
    aliased = _SPDX_ALIASES.get(candidate.lower())
    if aliased is not None:
        return aliased
    upper_padded = f" {candidate.upper()} "
    upper_candidate = candidate.upper()
    # AND is the only compound we reject outright.
    if " AND " in upper_padded:
        return None
    # OR — pick the first token that normalises successfully. The
    # self-reference guard (token == candidate) is critical: a bare
    # ``"OR"`` input would otherwise trip an infinite recursion via
    # ``_split_compound("OR", " OR ")`` → ``["OR"]`` → recurse.
    # security-reviewer High (chore PR #7).
    if " OR " in upper_padded:
        for token in _split_compound(candidate, " OR "):
            if token.upper() == upper_candidate:
                # Bare separator or self-reference — would loop forever.
                continue
            normalised = normalize_spdx_id(token)
            if normalised is not None:
                return normalised
        return None
    # WITH — take the base license, drop the exception. Same guard:
    # ``"WITH"`` and ``"WITH WITH"`` would otherwise recurse forever.
    if " WITH " in upper_padded:
        pieces = _split_compound(candidate, " WITH ")
        if not pieces or pieces[0].upper() == upper_candidate:
            return None
        return normalize_spdx_id(pieces[0])
    # 2) Looks like an SPDX id verbatim — strict charset to avoid
    # accepting attacker payloads as licence ids. SPDX 3.x ids use
    # ASCII letters / digits / ``-`` / ``.`` / ``+`` only; anything
    # outside that set (``:`` / ``<`` / ``/`` / ``\x00`` / unicode)
    # would otherwise leak through into ``licenses.spdx_id`` and on
    # into Excel / PDF / SBOM exports. security-reviewer Medium #2
    # (chore PR #7).
    _BARE_SPDX_IDS = {"MIT", "ISC", "Zlib", "WTFPL", "0BSD", "Unlicense"}
    if candidate in _BARE_SPDX_IDS:
        return candidate
    if (
        len(candidate) <= 64
        and _SPDX_ID_TOKEN_RE.match(candidate) is not None
        and any(ch.isdigit() for ch in candidate)
        and any(ch.isalpha() for ch in candidate)
    ):
        return candidate
    return None


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "LicenseFetchResult",
    "LicenseFetcher",
    "USER_AGENT",
    "normalize_spdx_id",
    "request_with_retry",
]
