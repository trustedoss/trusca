# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
What the portal's own outbound HTTPS calls will trust, reported at boot.

Every feed, notification and webhook the portal sends goes out through httpx:
deps.dev, EPSS, KEV, the end-of-life feed, the licence fetchers, GitHub,
Slack, Teams, the OIDC provider, the ticket webhook. All of them share one
trust decision, and an operator behind a private certificate authority has to
change it.

The trap this exists for
------------------------
``SSL_CERT_FILE`` does not mean the same thing to every tool the worker runs,
and the difference is invisible until something breaks. Go (trivy, cosign,
govulncheck) keeps consulting the system certificate directory when only the
file is set, so there a private CA is ADDED to the public roots. httpx builds
its context from that file alone and never loads its bundled roots, so the
same variable REPLACES the trust set.

An operator who points ``SSL_CERT_FILE`` at a bundle holding only their
corporate CA therefore gets scans that work and feeds that fail. The symptom
reads as a feed outage, not as a certificate setting, and nothing in the
product said otherwise.

Why the count is logged unconditionally
---------------------------------------
Replacing the trust set is not always wrong. A deployment that deliberately
trusts only its own authority is a legitimate configuration, and a warning it
does not deserve is noise, and noise gets silenced. So the size and its origin
are stated as facts on every boot, and the warning is layered on top of them
for the case that is almost certainly a mistake: an override is in force and it
left the portal with fewer authorities than it ships with.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import structlog

log = structlog.get_logger("core.tls_trust")


def describe_trust_store() -> dict[str, Any]:
    """Facts about the trust set our HTTPS clients will use.

    Built through ``httpx.create_ssl_context`` rather than assembled here, so
    what is reported is what the clients actually get. ``trust_env=False``
    asks the same function for the shipped baseline, which is why this needs
    no opinion about where those roots come from.

    Keys:
        authorities: How many certificate authorities the context holds, or
            ``None`` when that cannot be counted. A context built from a
            directory loads certificates on demand and reports none until it
            does, so counting one would print a zero that means nothing.
        bundled_authorities: The count with the environment ignored, for the
            reader to compare against.
        source: Which setting decided it. ``SSL_CERT_FILE`` wins over
            ``SSL_CERT_DIR``; with neither, the shipped bundle.
        path: The value of that setting, or None.
    """
    cert_file = os.getenv("SSL_CERT_FILE") or None
    cert_dir = os.getenv("SSL_CERT_DIR") or None

    if cert_file:
        source, path, countable = "SSL_CERT_FILE", cert_file, True
    elif cert_dir:
        # A capath context loads certificates lazily, so ``get_ca_certs()``
        # answers zero however many the directory holds.
        source, path, countable = "SSL_CERT_DIR", cert_dir, False
    else:
        source, path, countable = "bundled", None, True

    bundled = len(httpx.create_ssl_context(trust_env=False).get_ca_certs())
    authorities = (
        len(httpx.create_ssl_context().get_ca_certs()) if countable else None
    )

    return {
        "authorities": authorities,
        "bundled_authorities": bundled,
        "source": source,
        "path": path,
    }


def log_trust_store(*, process: str) -> dict[str, Any]:
    """State the trust set at boot, and warn when an override shrank it.

    ``process`` names which one is reporting: ``api``, ``worker`` or ``beat``.
    All three report, and the name is on the line, because Compose gives each
    service its own environment and the scanners go out from the worker. One
    line saying the certificate is configured would otherwise be read as
    covering a process that never saw it.

    Never raises: a deployment must not fail to start because this could not
    describe its certificates. Returns what it reported so a caller can assert
    on it.
    """
    try:
        facts = describe_trust_store()
    except Exception as exc:  # noqa: BLE001 - reporting must not stop a boot
        log.warning("tls_trust.describe_failed", process=process, error=str(exc)[:200])
        return {}

    log.info("tls_trust.outbound", process=process, **facts)

    authorities = facts["authorities"]
    if (
        facts["source"] != "bundled"
        and authorities is not None
        and authorities < facts["bundled_authorities"]
    ):
        log.warning(
            "tls_trust.public_roots_dropped",
            process=process,
            source=facts["source"],
            path=facts["path"],
            authorities=authorities,
            bundled_authorities=facts["bundled_authorities"],
            action=(
                "This file replaces the portal's trust set rather than adding "
                "to it, so public endpoints (vulnerability feeds, Slack, "
                "GitHub) may now fail to verify while scans keep working. "
                "Concatenate the public roots into the same file if you did "
                "not mean that. Ignore this if trusting only your own "
                "authority is deliberate."
            ),
        )
    return facts


__all__ = ["describe_trust_store", "log_trust_store"]
