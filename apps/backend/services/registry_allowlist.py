# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Which registries a container scan may pull from (ER3).

A container scan hands an image reference to the worker, which pulls it. The
reference is checked for being non-empty and nothing else, so any caller who
can trigger a scan can make the worker fetch from any host that worker can
reach. That is an outbound request to an attacker-chosen address from inside
the deployment's network, and on a self-hosted install that network is usually
the trusted one.

This module answers one question: does this reference name a registry the
operator has allowed? An empty allow-list means "no restriction", which is the
behaviour every existing deployment already has, so turning this on is the
operator's decision and upgrading changes nothing on its own.

Parsing, and why it is the whole problem
----------------------------------------
An allow-list is only as good as the host it thinks it is looking at, and
container reference syntax makes that easy to get wrong:

* ``alpine:3.19`` has no registry at all. Docker resolves it to Docker Hub, so
  the host is ``docker.io`` even though the string never says so.
* ``myorg/app:1`` is also Docker Hub: the first segment is a namespace, not a
  host.
* ``ghcr.io/org/app:1`` is a host, because the first segment looks like one.

The rule Docker uses, and the one implemented here, is that the first segment
is a registry host only when it contains a ``.`` or a ``:``, or is exactly
``localhost``. Anything else is a Docker Hub namespace.

Two bypasses this must not allow, both tested:

* ``evil.com/ghcr.io/app`` must NOT satisfy an allow-list entry of ``ghcr.io``.
  The host is ``evil.com``; ``ghcr.io`` merely appears in the path. Any check
  that asks "does the reference contain the allowed string" gets this wrong.
* ``ghcr.io.evil.com/app`` must NOT satisfy ``ghcr.io`` either. A plain
  ``startswith`` gets this wrong, which is why host comparison is an equality
  and any path prefix must end on a ``/`` boundary.
"""

from __future__ import annotations

import os

#: Reference with no registry segment resolves here, the same default Docker
#: and Trivy apply.
DEFAULT_REGISTRY = "docker.io"


def split_registry_host(image_ref: str) -> str:
    """Return the registry host an image reference resolves to.

    Never raises: an unparseable reference yields the default registry, and the
    caller's allow-list decides what happens to it. Raising here would turn a
    malformed reference into a 500 rather than a rejection.
    """
    ref = (image_ref or "").strip()
    if not ref:
        return DEFAULT_REGISTRY

    # Strip a digest or tag only AFTER the host is taken, because a host may
    # carry a port (`registry:5000/app`) that looks exactly like a tag.
    first, sep, _rest = ref.partition("/")
    if not sep:
        # No slash at all: `alpine`, `alpine:3.19`. Docker Hub.
        return DEFAULT_REGISTRY

    # The Docker rule. A first segment that is only a name (`myorg`) is a Hub
    # namespace, not a host.
    if "." in first or ":" in first or first == "localhost":
        return first.lower()
    return DEFAULT_REGISTRY


def _split_entry(entry: str) -> tuple[str, str]:
    """An allow-list entry as ``(host, path prefix)``. Prefix may be empty."""
    host, sep, path = entry.strip().partition("/")
    return host.strip().lower(), (path.strip("/") if sep else "")


def _repository_path(image_ref: str, host: str) -> str:
    """The path portion of a reference, with any host segment removed."""
    ref = (image_ref or "").strip()
    first, sep, rest = ref.partition("/")
    if sep and first.lower() == host and host != DEFAULT_REGISTRY:
        return rest.strip("/")
    if sep and ("." in first or ":" in first or first == "localhost"):
        return rest.strip("/")
    return ref.strip("/")


def parse_allowed_registries(raw: str | None) -> tuple[str, ...]:
    """Parse the operator's comma-separated list. Blank entries are dropped."""
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def allowed_registries() -> tuple[str, ...]:
    """The configured allow-list, read at call time (rule #11)."""
    return parse_allowed_registries(os.getenv("CONTAINER_SCAN_ALLOWED_REGISTRIES"))


def is_registry_allowed(image_ref: str, allowed: tuple[str, ...]) -> bool:
    """Whether ``image_ref`` may be pulled under this allow-list.

    An empty allow-list allows everything: that is the unrestricted behaviour
    every deployment has today, and this control is opt-in.

    An entry is either a bare host (``ghcr.io``), which matches any repository
    on that host, or ``host/prefix`` (``ghcr.io/trustedoss``), which
    additionally requires the repository path to start with that prefix on a
    path-segment boundary. ``ghcr.io/trustedoss`` therefore matches
    ``ghcr.io/trustedoss/app`` and ``ghcr.io/trustedoss`` itself, but not
    ``ghcr.io/trustedoss-evil/app``.
    """
    if not allowed:
        return True

    host = split_registry_host(image_ref)
    path = _repository_path(image_ref, host)

    for entry in allowed:
        entry_host, entry_path = _split_entry(entry)
        if not entry_host or entry_host != host:
            # Host equality, never a prefix: `ghcr.io` must not match
            # `ghcr.io.evil.com`.
            continue
        if not entry_path:
            return True
        if path == entry_path or path.startswith(f"{entry_path}/"):
            # Boundary-anchored: `trustedoss` must not match `trustedoss-evil`.
            return True
    return False


__all__ = [
    "DEFAULT_REGISTRY",
    "allowed_registries",
    "is_registry_allowed",
    "parse_allowed_registries",
    "split_registry_host",
]
