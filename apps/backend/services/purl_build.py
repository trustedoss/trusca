# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Shared PURL construction: namespace splitting and encoding.

Extracted from ``services.vulnerability_matching._build_purl`` (which reused
it as a thin wrapper afterward) so a second caller (the deps.dev package
lookup) does not re-derive the same rules from scratch. The rules themselves
came from a real incident: Maven coordinates arrive as ``group:artifact``,
and an earlier version of this logic percent-encoded the colon instead of
splitting on it, so ``group%3Aartifact`` never string-matched the stored
``group/artifact`` PURL and every Maven finding was silently skipped as an
unknown component.
"""

from __future__ import annotations

from urllib.parse import quote

# PURL component names with namespaces are typically expressed as
# ``namespace/name`` (e.g. ``@types/node`` in npm, ``org.apache.commons/...``
# in maven). cdxgen always emits the namespace as a separate PURL path
# segment, so when a name carries one embedded (``@types/node``) we split on
# the first ``/`` and put the namespace on the path.
NAMESPACED_TYPES = frozenset({"npm", "maven", "composer", "golang"})


def build_purl(
    purl_type: str,
    name: str,
    version: str | None,
    *,
    namespaced_types: frozenset[str] = NAMESPACED_TYPES,
) -> str | None:
    """Build a PURL string, splitting a namespace out of ``name`` where one is
    embedded and safely encoding both parts.

    ``version=None`` omits the ``@version`` suffix, producing a bare package
    identity (``pkg:{type}/{name}``) rather than a specific release.

    Returns ``None`` when ``name``/``purl_type`` is missing or the result
    would carry a control character (``ord < 0x20`` or DEL, ``0x7F``); never
    construct a PURL from attacker-influenced input we have not sanitised.

    Examples::

        build_purl("npm", "lodash", "4.17.20")
            → "pkg:npm/lodash@4.17.20"
        build_purl("npm", "@types/node", "20.0.0")
            → "pkg:npm/%40types/node@20.0.0"
        build_purl("maven", "org.apache.commons:commons-text", "1.10.0")
            → "pkg:maven/org.apache.commons/commons-text@1.10.0"
        build_purl("golang", "github.com/foo/bar", "v1.2.3")
            → "pkg:golang/github.com/foo/bar@v1.2.3"
        build_purl("npm", "lodash", None)
            → "pkg:npm/lodash"
    """
    if not purl_type or not isinstance(purl_type, str):
        return None
    if not name or not isinstance(name, str):
        return None

    # Reject control characters in any field: never construct an attacker-
    # influenced PURL string.
    for field in (purl_type, name, version or ""):
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in field):
            return None

    name = name.strip()
    if not name:
        return None
    version = version.strip() if version else None
    if version is not None and not version:
        return None

    # Namespace split for ecosystems that carry one in cdxgen / SBOM output.
    namespace: str | None = None
    if purl_type == "maven" and ":" in name:
        # Maven coordinates arrive as ``groupId:artifactId`` (exactly one
        # colon). The canonical Maven PURL is ``pkg:maven/{groupId}/{artifactId}``,
        # so split on that colon and put the groupId on the namespace path.
        group, _, artifact = name.partition(":")
        if group and artifact:
            namespace = group
            name = artifact
    elif purl_type in namespaced_types and "/" in name:
        first_slash = name.find("/")
        namespace_part = name[:first_slash]
        remainder = name[first_slash + 1 :]
        if namespace_part and remainder:
            namespace = namespace_part
            name = remainder

    # PURL encoding: namespace path segments are encoded for ``@`` but ``/``
    # is preserved (it is the segment separator).
    encoded_name = quote(name, safe="/")
    suffix = f"@{version}" if version is not None else ""
    if namespace is not None:
        encoded_ns = quote(namespace, safe="/")
        return f"pkg:{purl_type}/{encoded_ns}/{encoded_name}{suffix}"
    return f"pkg:{purl_type}/{encoded_name}{suffix}"
