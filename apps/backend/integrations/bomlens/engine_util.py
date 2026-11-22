# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Checks and text helpers of the BomLens engine adapter (:mod:`integrations.bomlens.engine`).

Pure functions with no container in them: the mirror prefix, the image
reference, a proxy that names the worker itself, the age limit of the Trivy
database and the cleaned last lines of a container's output. They were part of
TRUSCA's own runner and are kept as they were written, with their tests.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Final
from urllib.parse import urlsplit

import structlog

from integrations._secret_scrub import scrub_secrets
from integrations.bomlens import contract

log = structlog.get_logger("integrations.bomlens.engine_util")


class BomLensEngineError(contract.BomLensError):
    """The engine could not be run, or left nothing a scan can use."""


#: Upper limit of :func:`trivy_db_max_age_days`. A database older than a month
#: is not one a scan should report from, whatever the refresh interval says.
TRIVY_DB_MAX_AGE_LIMIT_DAYS: Final = 30


def trivy_db_max_age_days() -> int:
    """How old the worker's Trivy database may be before stage 2 degrades it.

    Two refresh intervals (``TRIVY_DB_REFRESH_HOURS`` in whole days, rounded
    up) plus two days: 16 days with the weekly refresh. One failed refresh
    leaves the database one interval older and scans go on; a second failure
    in a row fails them. Past the limit BomLens records ``scan-security`` as
    degraded and the adapter fails the scan (``stages.vulnerability_failure``).
    Capped at :data:`TRIVY_DB_MAX_AGE_LIMIT_DAYS`. Read per call.
    """
    from integrations.trivy import trivy_db_refresh_interval_hours

    hours = trivy_db_refresh_interval_hours()
    # Integer arithmetic: a huge setting must not overflow a float division.
    days = -(-hours // 24) * 2 + 2
    if days > TRIVY_DB_MAX_AGE_LIMIT_DAYS:
        log.warning(
            "bomlens_trivy_db_max_age_capped",
            refresh_hours=contract.clip(hours),
            days=TRIVY_DB_MAX_AGE_LIMIT_DAYS,
        )
        return TRIVY_DB_MAX_AGE_LIMIT_DAYS
    return days


#: Characters of an IPv4 address in any of its legacy spellings.
_LEGACY_IPV4_RE: Final = re.compile(r"^[0-9a-fA-FxX.]{1,64}$")


def is_loopback_proxy(value: str) -> bool:
    """Whether a proxy address names the machine's own loopback interface.

    Inside a container that address is the container itself, so such a proxy
    is not passed on (BomLens' own CLI leaves it out the same way).
    """
    text = value.strip()
    if "://" not in text:
        text = "http://" + text
    try:
        host = urlsplit(text).hostname
    except ValueError:
        return False
    # A fully qualified name may end in a dot ("localhost.", "127.0.0.1.").
    host = (host or "").rstrip(".")
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # The legacy IPv4 spellings resolvers still accept: 127.1,
        # 2130706433, 0x7f000001, 0177.0.0.1.
        if not _LEGACY_IPV4_RE.match(host):
            return False
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    # 0.0.0.0 and :: reach the local machine as well.
    return address.is_loopback or address.is_unspecified


#: ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX``: a registry host, then a repository
#: path of at least one part, with or without the final ``/``. A host alone
#: (``localhost``, ``reg.example:5000``) is refused: the allowed-repository
#: check admits everything under the prefix, so it must name a path that holds
#: only the cdxgen images, not a whole registry. The host and the path
#: follow the same rules as an image name in a plan (plan.py ``_IMAGE_RE``),
#: so a prefix the plan reader would refuse is refused here first, before any
#: container starts. Nothing else gets through: no scheme, tag, digest, space,
#: upper case or shell character.
_STAGE1_PREFIX_RE: Final = re.compile(
    r"(?P<host>[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?)"
    r"(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)+/?"
)


_STAGE1_PREFIX_MAX_CHARS: Final = 200


def stage1_image_prefix(value: str) -> str | None:
    """Check ``SCAN_BOMLENS_STAGE1_IMAGE_PREFIX`` and give it its final ``/``.

    Empty means not set (``None``): PLAN then gets no ``CDXGEN_IMAGE_PREFIX``
    and names the images under BomLens's own default, ``ghcr.io/cdxgen/``.
    BomLens adds a missing final ``/`` itself (``source-detect.sh``); it is
    added here too, so the allowed-repository check compares the same prefix
    the plan uses. The first part must name a registry (a dot, a port or
    ``localhost``): Docker would read ``mirror/cdxgen/`` as a Docker Hub
    account, not as a mirror.

    The error names the rule, never the value: a value with credentials in it
    (``user:token@registry...``) would otherwise reach the scan's error
    message and the worker log.
    """
    if not value:
        return None
    # fullmatch: ``$`` would let a final newline through.
    match = _STAGE1_PREFIX_RE.fullmatch(value)
    host = match.group("host") if match else ""
    if (
        match is None
        or len(value) > _STAGE1_PREFIX_MAX_CHARS
        or not ("." in host or ":" in host or host == "localhost")
    ):
        raise BomLensEngineError(
            "SCAN_BOMLENS_STAGE1_IMAGE_PREFIX is not in the right form: a registry host "
            "(with a dot, a port or localhost) and a path of lower-case parts, such as "
            "registry.example/mirror/cdxgen/, with no scheme, credentials, tag or digest"
        )
    return value if value.endswith("/") else f"{value}/"


#: An image reference as Docker's grammar spells it: an optional registry host
#: (with a port), lower-case path components, an optional tag and an optional
#: sha256 digest. Nothing else: no scheme (``docker-archive:``, ``dir:``,
#: ``oci-dir:``), no space, no ``://``.
_REF_HOST: Final = (
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*(?::[0-9]{1,5})?)"
)


_REF_COMPONENT: Final = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"


_IMAGE_REF_RE: Final = re.compile(
    rf"(?:(?P<host>{_REF_HOST})/)?(?P<path>{_REF_COMPONENT}(?:/{_REF_COMPONENT})*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[0-9a-f]{64}))?"
)


_IMAGE_REF_MAX_CHARS: Final = 512


#: The syft source scheme TRUSCA hands every image reference with.
REGISTRY_SCHEME: Final = "registry:"


def image_target(reference: str) -> str:
    """``registry:<reference>`` for ``MODE=IMAGE``, or refused.

    syft reads a scheme in front of the reference as the kind of source:
    ``docker-archive:``, ``oci-dir:``, ``dir:`` and ``file:`` read a path
    inside the container, ``docker:`` asks a Docker daemon. A container scan
    reference names a registry image, nothing else, so the reference must
    follow Docker's grammar and is passed with the ``registry:`` scheme, which
    makes syft (and Trivy's licence read, ``--image-src remote``) read it from
    the registry and nowhere else, whatever the name looks like (``dir:x`` is
    a valid image name and tag).

    The error names the rule, not the value: it reaches the scan's error
    message, and a malformed value may carry credentials
    (``user:token@registry...``).
    """
    value = reference.strip() if isinstance(reference, str) else ""
    match = _IMAGE_REF_RE.fullmatch(value) if len(value) <= _IMAGE_REF_MAX_CHARS else None
    host = match.group("host") if match else None
    # Docker's rule: the first part is a registry only with a dot, a port or
    # "localhost"; otherwise it is a lower-case path component.
    if (
        match is not None
        and host is not None
        and not ("." in host or ":" in host or host == "localhost")
    ):
        if not re.fullmatch(_REF_COMPONENT, host):
            match = None
    if match is None:
        raise BomLensEngineError(
            "the image reference is not a registry image reference "
            "([registry/]name[:tag][@sha256:digest]); the BomLens engine reads images "
            "from a registry only"
        )
    return f"{REGISTRY_SCHEME}{value}"


#: How much of the end of a container's output :func:`output_tail` looks at.
OUTPUT_TAIL_WINDOW_BYTES: Final = 64 * 1024


#: Lines :func:`output_tail` keeps (the worker log gets them all) and their length.
OUTPUT_TAIL_LINES: Final = 20


OUTPUT_TAIL_LINE_CHARS: Final = 300


#: Of those, the last lines a scan's error carries, each cut shorter.
ERROR_TAIL_LINES: Final = 3


ERROR_TAIL_LINE_CHARS: Final = 100


def output_tail(
    output: bytes, *, lines: int = OUTPUT_TAIL_LINES, line_chars: int = OUTPUT_TAIL_LINE_CHARS
) -> list[str]:
    """The last non-empty lines of a container's output, safe to log and store.

    Each line loses its control and invisible format characters
    (:func:`~integrations.bomlens.contract.storable_text`, first, so a
    zero-width character cannot hide a credential from the patterns), then
    credential-shaped values (:func:`~integrations._secret_scrub.scrub_secrets`),
    and is cut to ``line_chars``. A line is cut before it is cleaned, so a
    runaway one is never walked whole; the word the cut fell in is dropped
    with it, since a cut credential no longer matches a pattern, and so is a
    quoted value the cut left without its closing quote. For the same
    reason the first line of the window is dropped when the output is longer
    than the window: its start is missing.
    """
    window = output[-OUTPUT_TAIL_WINDOW_BYTES:]
    # Split on line feeds only, as the log readers do (``readline``). Every
    # other separator ``splitlines`` knows (a vertical tab, a form feed) stays
    # in the line, where ``storable_text`` removes it, so it cannot part a key
    # from its value (``password=\\x0bvalue``).
    raw_lines = window.decode("utf-8", errors="replace").split("\n")
    if len(output) > len(window) and raw_lines:
        raw_lines = raw_lines[1:]
    limit = line_chars * 4
    kept: list[str] = []
    for raw in reversed(raw_lines):
        piece = raw
        if len(piece) > limit:
            head = piece[:limit]
            # Up to the last whitespace: the word the cut fell in goes too.
            cut_at = max(head.rfind(c) for c in " \t")
            piece = head[:cut_at] if cut_at > 0 else ""
            # A quoted value the cut fell in (``"password": "hunter2 ...``,
            # Gradle's ``password "x y"``) has no closing quote left for the
            # patterns to stop at: it goes from its opening quote on.
            for quote in "\"'":
                if piece.count(quote) % 2:
                    piece = piece[: piece.rfind(quote)]
        line = scrub_secrets(contract.storable_text(piece))[:line_chars]
        if line:
            kept.append(line)
            if len(kept) == lines:
                break
    kept.reverse()
    return kept
