# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Operating-system context for distro packages a supplier SBOM omits.

Hand-ported from BomLens's ``docker/lib/enrich-os-context.py`` (see
THIRD_PARTY_NOTICES.md). The distro tables and the voting rule are upstream's;
the byte-in/byte-out shape, the SPDX side and the containment inside a scan
stage are TRUSCA's.

One upstream behaviour is deliberately not ported: rewriting an existing OS
component's version down to its major release ("rocky 8.10" -> "8"). It was
measured here against Trivy 0.71.2 on centos 7.9.2009, rocky 8.10, alma 9.3 and
redhat 8.9, and each matched exactly as many vulnerabilities with its minor
version as without it. Editing a supplier's stated version buys nothing today,
so this module only ever ADDS a component that is missing — it never rewrites
one the document already carries.

The failure this exists for
---------------------------
Trivy matches a distro package against a *distro advisory database*, and it
picks that database from an ``operating-system`` component in the document —
not from the package PURLs. A supplier SBOM can list every rpm on the image,
each PURL perfectly formed, and omit that one component. Trivy then reports
zero vulnerabilities, and nothing in the pipeline says why: the components
persist, the scan succeeds, and the project reads as clean.

Measured on this repository's own reproduction (Trivy 0.71.2, five CentOS 7
rpm PURLs): 0 findings without the component, 306 with it. The same holds for
SPDX uploads, where the component takes the form of a package with
``primaryPackagePurpose: OPERATING_SYSTEM`` — 0 findings became 166.

What is inferred, and what is never guessed
-------------------------------------------
Only namespaces and qualifiers that name a distro Trivy actually carries are
voted on. An rpm PURL states its major version in an ``.elN`` release suffix or
a ``distro=`` qualifier; deb and apk carry it only in ``distro=``, so a deb/apk
PURL without that qualifier contributes no vote — the version is not inferred
from anything else. A document whose packages map to nothing is returned
unchanged. Being wrong about the distro is worse than adding nothing: a wrong
advisory database produces findings against packages that were never affected,
and those are indistinguishable from real ones downstream.

Why the original document is never edited
-----------------------------------------
The upload is the supplier's declared truth. It backs the conformance verdict,
the signature/bundle download, and the ``sbom_cyclonedx`` artifact — all of
which must keep describing what the supplier actually sent. So this module
returns *new bytes* and the caller writes them to a transient per-scan path
that only Trivy reads. What the user uploaded and what we matched against stay
separately inspectable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

import structlog

from services import sbom_component_walk
from services.sbom_conformance import (
    FORMAT_CYCLONEDX,
    FORMAT_SPDX_JSON,
    detect_format,
)

log = structlog.get_logger("services.os_context")

# ---------------------------------------------------------------------------
# Distro tables
# ---------------------------------------------------------------------------

#: rpm PURL namespace -> the OS ``name`` Trivy matches on. Closed list: a
#: namespace that is not here is not voted on at all.
_RPM_NAMESPACE_TO_OS: Final[dict[str, str]] = {
    "centos": "centos",
    "rocky": "rocky",
    "rhel": "redhat",
    "redhat": "redhat",
    "almalinux": "alma",
    "alma": "alma",
    "amazon": "amazon",
    "amzn": "amazon",
    "fedora": "fedora",
}

#: apk / deb distro id (from the ``distro=<id>-<ver>`` qualifier, or failing
#: that the PURL namespace) -> the OS ``name`` Trivy matches on.
_APK_TO_OS: Final[dict[str, str]] = {"alpine": "alpine"}
_DEB_TO_OS: Final[dict[str, str]] = {"debian": "debian", "ubuntu": "ubuntu"}

#: Debian's advisory key is its major release ("11"), so a ``distro=debian-11.7``
#: qualifier is reduced before it becomes an OS version. Alpine (3.17) and
#: Ubuntu (18.04) keep their full release id — that IS their key.
_MAJOR_ONLY_DEB: Final[frozenset[str]] = frozenset({"debian"})

#: ``distro=<name>-<major>`` on an rpm PURL. ``[0-9]+`` stops at the dot, which
#: is the major reduction for the qualifier path.
_RPM_DISTRO_QUALIFIER: Final = re.compile(r"[?&]distro=([a-z]+)-([0-9]+)")
#: The ``.elN`` release suffix, the other place an rpm states its major.
_EL_SUFFIX: Final = re.compile(r"\.el(\d+)")
#: ``distro=<id>-<versionId>`` on an apk/deb PURL (alpine-3.17.10, debian-11,
#: ubuntu-18.04). Required — these formats carry the version nowhere else.
_DEB_APK_DISTRO_QUALIFIER: Final = re.compile(r"[?&]distro=([a-z][a-z0-9]*)-([0-9][0-9.]*)")

#: CycloneDX component type for the OS component.
_CDX_OS_TYPE: Final = "operating-system"

#: SPDX identifies its OS package by SPDXID, not by ``primaryPackagePurpose``.
#: Measured against Trivy 0.71.2: a package carrying only the purpose is not
#: read as an operating system (0 findings), while ``SPDXRef-OperatingSystem``
#: and any ``SPDXRef-OperatingSystem-*`` suffix are (166). So the prefix is
#: what decides whether a document already has a usable OS package, and the
#: package we add must carry it.
_SPDX_OS_ID_PREFIX: Final = "SPDXRef-OperatingSystem"
_SPDX_OS_PURPOSE: Final = "OPERATING_SYSTEM"

#: ``bom-ref`` / ``SPDXID`` for the component we add. Named so that anyone
#: reading the document Trivy consumed can tell it apart from the supplier's
#: own entries — the SPDX one keeps the prefix Trivy requires and carries the
#: provenance in its suffix.
_SYNTHETIC_CDX_REF: Final = "trusca-os-context"
_SYNTHETIC_SPDX_ID: Final = f"{_SPDX_OS_ID_PREFIX}-trusca-os-context"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OsContext:
    """A distro Trivy can match on, and how much of the document voted for it."""

    name: str
    version: str
    votes: int
    total: int


@dataclass(frozen=True)
class EnrichResult:
    """The document to scan, and the context that was added to it."""

    document: bytes
    synthesized: OsContext


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def _namespace(purl: str, prefix: str) -> str:
    """``pkg:<type>/<ns>/name@ver`` -> ``<ns>``, lowercased."""
    return purl[len(prefix) :].split("/", 1)[0].split("@", 1)[0].lower()


def classify_purl(purl: str) -> tuple[str, str] | None:
    """Map one distro PURL to the ``(os_name, version)`` Trivy keys on.

    Returns None for a PURL that is not a distro package, names a distro we
    cannot place, or — for deb/apk — carries no ``distro=`` version.
    """
    if not purl or not isinstance(purl, str):
        return None

    if purl.startswith("pkg:rpm/"):
        os_name = _RPM_NAMESPACE_TO_OS.get(_namespace(purl, "pkg:rpm/"))
        if not os_name:
            return None
        # A ``distro=`` qualifier is authoritative over the namespace: an rpm
        # rebuilt on RHEL and shipped under a vendor namespace states where its
        # advisories come from there.
        qualifier = _RPM_DISTRO_QUALIFIER.search(purl)
        if qualifier:
            named = _RPM_NAMESPACE_TO_OS.get(qualifier.group(1))
            if not named:
                return None
            return (named, qualifier.group(2))
        suffix = _EL_SUFFIX.search(purl)
        if suffix:
            return (os_name, suffix.group(1))
        return None

    is_apk = purl.startswith("pkg:apk/")
    if is_apk or purl.startswith("pkg:deb/"):
        qualifier = _DEB_APK_DISTRO_QUALIFIER.search(purl)
        if not qualifier:
            return None
        table = _APK_TO_OS if is_apk else _DEB_TO_OS
        prefix = "pkg:apk/" if is_apk else "pkg:deb/"
        os_name = table.get(qualifier.group(1)) or table.get(_namespace(purl, prefix))
        if not os_name:
            return None
        version = qualifier.group(2)
        if os_name in _MAJOR_ONLY_DEB:
            version = version.split(".", 1)[0]
        return (os_name, version)

    return None


def infer_os_context(purls: Iterable[str]) -> OsContext | None:
    """Vote a dominant ``(os_name, version)`` across distro PURLs.

    Ties are broken by the first candidate seen, which is document order — an
    arbitrary but stable choice. A document mixing two distros in equal measure
    is not one this can be right about, and picking neither would leave every
    package unmatched; picking one at least matches that half.
    """
    votes: dict[tuple[str, str], int] = {}
    for purl in purls:
        key = classify_purl(purl)
        if key is not None:
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return None
    (name, version), count = max(votes.items(), key=lambda kv: kv[1])
    return OsContext(name=name, version=version, votes=count, total=sum(votes.values()))


# ---------------------------------------------------------------------------
# Per-format read + write
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _cdx_enrich(doc: dict[str, Any]) -> EnrichResult | None:
    """CycloneDX: append an ``operating-system`` component if one is missing."""
    components = doc.get("components")
    if not isinstance(components, list):
        return None

    # Nested components count: Trivy reads them, so a document that describes
    # its packages one level down is exactly the case this is for.
    flattened = sbom_component_walk.iter_components(components)
    if any(c.get("type") == _CDX_OS_TYPE for c in flattened):
        return None

    context = infer_os_context(str(c.get("purl")) for c in flattened if c.get("purl") is not None)
    if context is None:
        return None

    components.append(
        {
            "type": _CDX_OS_TYPE,
            "name": context.name,
            "version": context.version,
            "bom-ref": _SYNTHETIC_CDX_REF,
        }
    )
    return EnrichResult(document=_dump(doc), synthesized=context)


def _spdx_purls(package: dict[str, Any]) -> list[str]:
    """The purl locators on one SPDX package."""
    out: list[str] = []
    for ref in _as_list(package.get("externalRefs")):
        if not isinstance(ref, dict):
            continue
        if str(ref.get("referenceType") or "").lower() != "purl":
            continue
        locator = ref.get("referenceLocator")
        if isinstance(locator, str):
            out.append(locator)
    return out


def _spdx_enrich(doc: dict[str, Any]) -> EnrichResult | None:
    """SPDX JSON: append an operating-system package if one is missing."""
    packages = doc.get("packages")
    if not isinstance(packages, list):
        return None
    entries = [p for p in packages if isinstance(p, dict)]

    # "Already has one" means "already has one Trivy will read". A package
    # marked only with ``primaryPackagePurpose: OPERATING_SYSTEM`` does not
    # count — Trivy ignores it, so that document carries the same gap as one
    # with no OS package at all, and we add the package that closes it.
    if any(str(p.get("SPDXID") or "").startswith(_SPDX_OS_ID_PREFIX) for p in entries):
        return None

    context = infer_os_context(purl for package in entries for purl in _spdx_purls(package))
    if context is None:
        return None

    packages.append(
        {
            "SPDXID": _SYNTHETIC_SPDX_ID,
            "name": context.name,
            "versionInfo": context.version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "primaryPackagePurpose": _SPDX_OS_PURPOSE,
        }
    )
    return EnrichResult(document=_dump(doc), synthesized=context)


def _dump(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enrich_sbom_bytes(raw: bytes) -> EnrichResult | None:
    """Return the document Trivy should read, or None to read the original.

    None means "nothing to add" and is the ordinary outcome: a JavaScript or
    Java SBOM has no distro packages, and a document that already carries a
    usable OS component needs no edit. Tag-Value SPDX also returns None — it is
    a line-oriented serialisation this does not rewrite, so an OS-less
    Tag-Value upload keeps the gap. That is a stated limit, not an oversight;
    the format is rare enough among uploads that text surgery is the larger
    risk.

    Never raises. A document this cannot parse or understand is one the scan
    should still run on unchanged.
    """
    try:
        fmt, doc = detect_format(raw)
        if doc is None:
            return None
        if fmt == FORMAT_CYCLONEDX:
            return _cdx_enrich(doc)
        if fmt == FORMAT_SPDX_JSON:
            return _spdx_enrich(doc)
        return None
    except Exception:  # pragma: no cover — defensive, see docstring
        log.warning("os_context_enrich_failed", exc_info=True)
        return None
