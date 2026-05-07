"""
PyPI license fetcher.

Resolves ``pkg:pypi/<name>@<version>`` PURLs against
``https://pypi.org/pypi/<name>/<version>/json``. The legacy JSON endpoint
returns a stable schema where ``info.license`` is a free-text field and
``info.classifiers`` carries the canonical Trove classifier list. We
prefer the classifier (each Trove classifier maps cleanly to an SPDX
id) and fall back to the free-text ``license`` value through
:func:`normalize_spdx_id`.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
import structlog

from .base import (
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    LicenseFetchResult,
    normalize_spdx_id,
    request_with_retry,
)

log = structlog.get_logger("integrations.license_fetcher.pypi")

_PYPI_HOST = "pypi.org"
_PYPI_BASE = "https://pypi.org/pypi"

# PyPI's docs ask integrations to keep traffic reasonable; the JSON
# endpoint is CDN-fronted but a 100ms gap is courteous and matches
# the cadence we use for Maven.
_MIN_INTERVAL_SECONDS = 0.1

# Trove classifier prefix → SPDX id. Compiled from the live
# classifier list at https://pypi.org/classifiers/. Keep this tight —
# only classifiers we actually expect to see (the same set GitHub's
# license API recognises, plus the Apache 1.x edge cases).
_CLASSIFIER_TO_SPDX: dict[str, str] = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Eclipse Public License 2.0 (EPL-2.0)": "EPL-2.0",
    "License :: OSI Approved :: Eclipse Public License 1.0 (EPL-1.0)": "EPL-1.0",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0-only",
    (
        "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)"
    ): "GPL-2.0-or-later",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0-only",
    (
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)"
    ): "GPL-3.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)": "LGPL-2.0-only",
    (
        "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)"
    ): "LGPL-2.0-or-later",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0-only",
    (
        "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)"
    ): "LGPL-3.0-or-later",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0-only",
    (
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)"
    ): "AGPL-3.0-or-later",
    "License :: OSI Approved :: Python Software Foundation License": "Python-2.0",
    "License :: OSI Approved :: zlib/libpng License": "Zlib",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
}


def _parse_purl(purl: str) -> tuple[str, str] | None:
    """Return ``(name, version)`` for a PyPI PURL, or None.

    Accepts ``pkg:pypi/<name>@<version>`` (with optional query). PyPI
    package names are case-insensitive and dash/underscore-equivalent
    on the lookup side; we preserve the cdxgen-emitted form because
    the JSON endpoint normalizes for us.
    """
    if not purl.startswith("pkg:pypi/"):
        return None
    body = purl[len("pkg:pypi/"):]
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    name, version = body.rsplit("@", 1)
    if not name or not version:
        return None
    return name, version


def _spdx_from_classifiers(classifiers: list[str]) -> str | None:
    """Return the first matching SPDX id from a list of Trove classifiers."""
    for entry in classifiers:
        if not isinstance(entry, str):
            continue
        spdx = _CLASSIFIER_TO_SPDX.get(entry.strip())
        if spdx is not None:
            return spdx
    return None


class PyPILicenseFetcher:
    """Resolve PyPI licenses via the legacy JSON endpoint."""

    source = "pypi"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=timeout,
                follow_redirects=True,
            )
        return self._http

    def close(self) -> None:
        if self._owned and self._http is not None:
            self._http.close()
            self._http = None

    def fetch(
        self,
        purl: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LicenseFetchResult | None:
        parsed = _parse_purl(purl)
        if parsed is None:
            log.info("pypi_purl_unrecognized", purl=purl)
            return None
        name, version = parsed
        url = f"{_PYPI_BASE}/{quote(name)}/{quote(version)}/json"
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_PYPI_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            log.warning("pypi_invalid_json", name=name, version=version)
            return None
        info = payload.get("info") or {}
        if not isinstance(info, dict):
            return None

        # Prefer the classifiers — they are SPDX-mappable. Free-text
        # ``license`` is only used as a fallback because it's free-form
        # (sometimes the literal license file contents).
        classifiers = info.get("classifiers")
        spdx: str | None = None
        if isinstance(classifiers, list):
            spdx = _spdx_from_classifiers(classifiers)

        ref_url = None
        if spdx is None:
            license_text = info.get("license")
            if isinstance(license_text, str):
                spdx = normalize_spdx_id(license_text)
        # PyPI exposes a project-page URL but no canonical license-file
        # URL in the JSON response; leaving ref_url as None is OK —
        # downstream UI links to spdx.org/licenses/<id>.html anyway.
        if spdx is None:
            log.info(
                "pypi_license_unmapped",
                name=name,
                version=version,
                license_text=str(info.get("license"))[:120],
            )
            return None
        return LicenseFetchResult(
            spdx_id=spdx,
            reference_url=ref_url,
            source=self.source,
        )


__all__ = ["PyPILicenseFetcher"]
