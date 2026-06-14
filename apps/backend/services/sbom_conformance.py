"""
Received-SBOM conformance scoring — model 3 (supplier-submitted SBOM ingest).

When a customer uploads an SBOM their own CI / local tooling produced, we cannot
trust it blindly: a "shell" SBOM with no versions, no PURLs, or no dependency
graph makes CVE matching meaningless. Before (and regardless of) matching, we
score the *quality* of the submission against a fixed conformance bar so the
portal can surface a pass / warn / fail badge and a supplier can be sent a
rejection with concrete reasons.

This is a faithful Python port of the SK Telecom BomLens
``docker/lib/validate-sbom.sh`` (jq) checks, kept deliberately:

  mandatory   : timestamp, tool info, top-level component (name+version),
                100% component name+version, PURL coverage >= threshold,
                no pkg:generic, transitive dependency edges (> 0)
  recommended : license coverage >= threshold (warn only),
                hash coverage >= threshold (warn only)

Scoring runs on the **original** submission bytes (before any CycloneDX
normalisation) so SPDX-specific metadata is judged accurately. It never raises
on a non-conformant document — a bad SBOM yields ``result="fail"``, not an
exception (the ingest pipeline still runs the match; see
``tasks/scan_sbom_ingest``).

Pure function, no DB / network / filesystem — unit-testable offline (the
backend test lane otherwise needs docker redis).

Thresholds are read at call time via ``os.getenv`` (never module-level
constants — CLAUDE.md §11) so an operator can tune strictness without a rebuild.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Check catalogue. The id set is the SINGLE SOURCE OF TRUTH shared with the
# frontend mirror constant (a contract test asserts set equality — CLAUDE.md
# §2 rule 2). Do not rename an id without updating the FE mirror + test.
# ---------------------------------------------------------------------------
CHECK_IDS: tuple[str, ...] = (
    "timestamp",
    "tools",
    "top-component",
    "name-version",
    "purl",
    "no-generic",
    "transitive",
    "license",
    "hash",
)

# Recognised input serialisations. RDF / XML SPDX are intentionally unsupported
# (see the model-3 plan: no spdx-tools dependency); they detect as "unknown".
FORMAT_CYCLONEDX = "cyclonedx"
FORMAT_SPDX_JSON = "spdx-json"
FORMAT_SPDX_TV = "spdx-tv"
FORMAT_UNKNOWN = "unknown"

# Cap on how many offending items we list per failed check (matches BomLens).
_MISSING_CAP = 50

# Per-format scorer return: (checks, component_count, purl%, license%, hash%).
# The coverage percentages are None for Tag-Value (presence-based only).
_ScoreResult = tuple[list["Check"], int, int | None, int | None, int | None]


def _purl_min_pct() -> int:
    return int(os.getenv("SBOM_CONFORMANCE_PURL_MIN_PCT", "90"))


def _license_min_pct() -> int:
    return int(os.getenv("SBOM_CONFORMANCE_LICENSE_MIN_PCT", "80"))


def _hash_min_pct() -> int:
    return int(os.getenv("SBOM_CONFORMANCE_HASH_MIN_PCT", "50"))


def _pct(n: int, d: int) -> int:
    """Integer percentage with zero-guard (matches jq ``pct``)."""
    if d == 0:
        return 0
    return (n * 100) // d


@dataclass
class Check:
    id: str
    label: str
    required: bool
    status: str  # "pass" | "fail" | "warn"
    detail: str
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "required": self.required,
            "status": self.status,
            "detail": self.detail,
            "missing": self.missing,
        }


@dataclass
class ConformanceResult:
    source_format: str
    result: str  # "pass" | "warn" | "fail"
    checks: list[Check]
    n_fail: int
    n_warn: int
    component_count: int
    purl_coverage_pct: int | None
    license_coverage_pct: int | None
    hash_coverage_pct: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_format": self.source_format,
            "result": self.result,
            "n_fail": self.n_fail,
            "n_warn": self.n_warn,
            "component_count": self.component_count,
            "purl_coverage_pct": self.purl_coverage_pct,
            "license_coverage_pct": self.license_coverage_pct,
            "hash_coverage_pct": self.hash_coverage_pct,
            "checks": [c.as_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Format detection — same rules as validate-sbom.sh / convert-to-cdx.sh.
# ---------------------------------------------------------------------------
def detect_format(raw: bytes) -> tuple[str, dict[str, Any] | None]:
    """Return ``(format, parsed_json_or_None)``.

    JSON is parsed once here and reused by the per-format scorers. Tag-Value is
    line-oriented text, so ``parsed`` is None for it.
    """
    text = raw.decode("utf-8", errors="replace")
    doc: dict[str, Any] | None = None
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            doc = loaded
    except (ValueError, json.JSONDecodeError):
        doc = None

    if doc is not None:
        if doc.get("bomFormat") == "CycloneDX" and doc.get("specVersion") is not None:
            return FORMAT_CYCLONEDX, doc
        if doc.get("spdxVersion") is not None:
            return FORMAT_SPDX_JSON, doc

    # Tag-Value: a top-of-file ``SPDXVersion:`` line.
    if re.search(r"(?m)^SPDXVersion:", text):
        return FORMAT_SPDX_TV, None

    return FORMAT_UNKNOWN, None


# ---------------------------------------------------------------------------
# CycloneDX scorer.
# ---------------------------------------------------------------------------
def _cdx_checks(doc: dict[str, Any]) -> _ScoreResult:
    components = [c for c in (doc.get("components") or []) if isinstance(c, dict)]
    tot = len(components)
    metadata = doc.get("metadata") or {}

    tools = metadata.get("tools")
    if isinstance(tools, list):
        n_tools = len(tools)
    elif isinstance(tools, dict):
        n_tools = len(tools.get("components") or []) + len(tools.get("services") or [])
    else:
        n_tools = 0

    def _name(c: dict[str, Any]) -> str:
        return c.get("name") or c.get("purl") or "(unnamed)"

    miss_nv = [_name(c) for c in components if not c.get("name") or not c.get("version")]
    miss_purl = [c.get("name") or "(unnamed)" for c in components if not c.get("purl")]
    generic = [
        c.get("name") or c.get("purl")
        for c in components
        if isinstance(c.get("purl"), str) and c["purl"].startswith("pkg:generic")
    ]
    lic_ok = sum(1 for c in components if (c.get("licenses") or []))
    hash_ok = sum(1 for c in components if (c.get("hashes") or []))
    dep_edges = sum(
        len(d.get("dependsOn") or [])
        for d in (doc.get("dependencies") or [])
        if isinstance(d, dict)
    )
    ts = metadata.get("timestamp") or ""
    top = metadata.get("component") or {}
    purl_ok = tot - len(miss_purl)

    purl_pct = _pct(purl_ok, tot)
    lic_pct = _pct(lic_ok, tot)
    hash_pct = _pct(hash_ok, tot)
    purlmin, licmin, hashmin = _purl_min_pct(), _license_min_pct(), _hash_min_pct()

    checks = [
        Check("timestamp", "Timestamp (metadata.timestamp)", True,
              "pass" if ts else "fail", str(ts)),
        Check("tools", "Tool info (metadata.tools)", True,
              "pass" if n_tools > 0 else "fail", f"{n_tools} tool(s)"),
        Check("top-component", "Top-level component name+version", True,
              "pass" if top.get("name") and top.get("version") else "fail",
              f"{top.get('name') or '(none)'}@{top.get('version') or ''}"),
        Check("name-version", "Component name+version coverage (100%)", True,
              "pass" if not miss_nv else "fail",
              f"{tot - len(miss_nv)}/{tot}", _cap(miss_nv)),
        Check("purl", f"PURL coverage (>= {purlmin}%)", True,
              "pass" if purl_pct >= purlmin else "fail",
              f"{purl_pct}% ({purl_ok}/{tot})", _cap(miss_purl)),
        Check("no-generic", "No pkg:generic / custom PURL (0)", True,
              "pass" if not generic else "fail",
              f"{len(generic)} offending", _cap([str(g) for g in generic])),
        Check("transitive", "Transitive dependencies (graph edges)", True,
              "pass" if dep_edges > 0 else "fail", f"{dep_edges} edge(s)"),
        Check("license", f"License coverage (>= {licmin}%, recommended)", False,
              "pass" if lic_pct >= licmin else "warn", f"{lic_pct}% ({lic_ok}/{tot})"),
        Check("hash", f"Hash coverage (>= {hashmin}%, recommended)", False,
              "pass" if hash_pct >= hashmin else "warn", f"{hash_pct}% ({hash_ok}/{tot})"),
    ]
    return checks, tot, purl_pct, lic_pct, hash_pct


# ---------------------------------------------------------------------------
# SPDX-JSON scorer.
# ---------------------------------------------------------------------------
def _spdx_json_checks(doc: dict[str, Any]) -> _ScoreResult:
    packages = [p for p in (doc.get("packages") or []) if isinstance(p, dict)]
    tot = len(packages)
    creation = doc.get("creationInfo") or {}
    creators = creation.get("creators") or []
    n_tools = sum(
        1 for c in creators if isinstance(c, str) and c.startswith("Tool:")
    )
    ts = creation.get("created") or ""

    def _ext_purls(p: dict[str, Any]) -> list[str]:
        return [
            ref.get("referenceLocator") or ""
            for ref in (p.get("externalRefs") or [])
            if isinstance(ref, dict) and ref.get("referenceType") == "purl"
        ]

    miss_nv = [
        p.get("name") or "(unnamed)"
        for p in packages
        if not p.get("name") or not p.get("versionInfo")
    ]
    miss_purl = [
        p.get("name") or "(unnamed)" for p in packages if not _ext_purls(p)
    ]
    generic = [
        loc
        for p in packages
        for loc in _ext_purls(p)
        if loc.startswith("pkg:generic")
    ]

    def _has_license(p: dict[str, Any]) -> bool:
        return (p.get("licenseConcluded") or "NOASSERTION") != "NOASSERTION" or (
            p.get("licenseDeclared") or "NOASSERTION"
        ) != "NOASSERTION"

    lic_ok = sum(1 for p in packages if _has_license(p))
    hash_ok = sum(1 for p in packages if (p.get("checksums") or []))
    dep_edges = sum(
        1
        for r in (doc.get("relationships") or [])
        if isinstance(r, dict) and r.get("relationshipType") == "DEPENDS_ON"
    )
    docname = doc.get("name") or ""
    describes = len(doc.get("documentDescribes") or [])
    purl_ok = tot - len(miss_purl)

    purl_pct = _pct(purl_ok, tot)
    lic_pct = _pct(lic_ok, tot)
    hash_pct = _pct(hash_ok, tot)
    purlmin, licmin, hashmin = _purl_min_pct(), _license_min_pct(), _hash_min_pct()

    checks = [
        Check("timestamp", "Timestamp (creationInfo.created)", True,
              "pass" if ts else "fail", str(ts)),
        Check("tools", "Tool info (creationInfo.creators Tool:)", True,
              "pass" if n_tools > 0 else "fail", f"{n_tools} tool(s)"),
        Check("top-component", "Document name + described root", True,
              "pass" if docname and (describes > 0 or tot > 0) else "fail",
              str(docname)),
        Check("name-version", "Package name+version coverage (100%)", True,
              "pass" if not miss_nv else "fail",
              f"{tot - len(miss_nv)}/{tot}", _cap(miss_nv)),
        Check("purl", f"PURL coverage (>= {purlmin}%)", True,
              "pass" if purl_pct >= purlmin else "fail",
              f"{purl_pct}% ({purl_ok}/{tot})", _cap(miss_purl)),
        Check("no-generic", "No pkg:generic / custom PURL (0)", True,
              "pass" if not generic else "fail",
              f"{len(generic)} offending", _cap(generic)),
        Check("transitive", "Transitive dependencies (DEPENDS_ON)", True,
              "pass" if dep_edges > 0 else "fail", f"{dep_edges} edge(s)"),
        Check("license", f"License coverage (>= {licmin}%, recommended)", False,
              "pass" if lic_pct >= licmin else "warn", f"{lic_pct}% ({lic_ok}/{tot})"),
        Check("hash", f"Hash coverage (>= {hashmin}%, recommended)", False,
              "pass" if hash_pct >= hashmin else "warn", f"{hash_pct}% ({hash_ok}/{tot})"),
    ]
    return checks, tot, purl_pct, lic_pct, hash_pct


# ---------------------------------------------------------------------------
# SPDX Tag-Value scorer — coarse, presence-based (per-package coverage is not
# computed for Tag-Value; JSON formats above are exact). Matches BomLens.
# ---------------------------------------------------------------------------
def _spdx_tv_checks(text: str) -> _ScoreResult:
    def g(pattern: str) -> int:
        return len(re.findall(pattern, text, flags=re.MULTILINE))

    ts = g(r"^Created:")
    n_tools = g(r"^Creator: ?Tool:")
    names = g(r"^PackageName:")
    vers = g(r"^PackageVersion:")
    purls = g(r"^ExternalRef: ?PACKAGE-MANAGER purl")
    generic = g(r"purl +pkg:generic")
    deps = g(r"^Relationship:.*DEPENDS_ON")
    lics = g(r"^PackageLicenseConcluded:")
    hashes = g(r"^PackageChecksum:")

    checks = [
        Check("timestamp", "Timestamp (Created:)", True,
              "pass" if ts > 0 else "fail", f"{ts} found"),
        Check("tools", "Tool info (Creator: Tool:)", True,
              "pass" if n_tools > 0 else "fail", f"{n_tools} tool(s)"),
        Check("top-component", "Document/package present", True,
              "pass" if names > 0 else "fail", f"{names} package(s)"),
        Check("name-version", "PackageName + PackageVersion present", True,
              "pass" if names > 0 and vers >= names else "fail",
              f"names={names}, versions={vers}"),
        Check("purl", "PURL external refs present", True,
              "pass" if purls > 0 and purls >= names else "fail",
              f"{purls} purl ref(s) for {names} package(s)"),
        Check("no-generic", "No pkg:generic / custom PURL (0)", True,
              "pass" if generic == 0 else "fail", f"{generic} offending"),
        Check("transitive", "Transitive dependencies (DEPENDS_ON)", True,
              "pass" if deps > 0 else "fail", f"{deps} relationship(s)"),
        Check("license", "License present (recommended)", False,
              "pass" if lics > 0 else "warn", f"{lics} license field(s)"),
        Check("hash", "Checksums present (recommended)", False,
              "pass" if hashes > 0 else "warn", f"{hashes} checksum(s)"),
    ]
    return checks, names, None, None, None


def _cap(items: list[str]) -> list[str]:
    return list(items[:_MISSING_CAP])


# ---------------------------------------------------------------------------
# Public entrypoint.
# ---------------------------------------------------------------------------
def evaluate(raw: bytes) -> ConformanceResult:
    """Score ``raw`` SBOM bytes. Never raises on malformed input."""
    fmt, doc = detect_format(raw)

    purl_pct = lic_pct = hash_pct = None
    component_count = 0
    if fmt == FORMAT_CYCLONEDX and doc is not None:
        checks, component_count, purl_pct, lic_pct, hash_pct = _cdx_checks(doc)
    elif fmt == FORMAT_SPDX_JSON and doc is not None:
        checks, component_count, purl_pct, lic_pct, hash_pct = _spdx_json_checks(doc)
    elif fmt == FORMAT_SPDX_TV:
        text = raw.decode("utf-8", errors="replace")
        checks, component_count, purl_pct, lic_pct, hash_pct = _spdx_tv_checks(text)
    else:
        checks = [
            Check("format", "Recognized SBOM format", True, "fail",
                  "not CycloneDX or SPDX (JSON / Tag-Value)")
        ]

    n_fail = sum(1 for c in checks if c.required and c.status == "fail")
    n_warn = sum(1 for c in checks if c.status == "warn")
    if n_fail > 0:
        result = "fail"
    elif n_warn > 0:
        result = "warn"
    else:
        result = "pass"

    return ConformanceResult(
        source_format=fmt,
        result=result,
        checks=checks,
        n_fail=n_fail,
        n_warn=n_warn,
        component_count=component_count,
        purl_coverage_pct=purl_pct,
        license_coverage_pct=lic_pct,
        hash_coverage_pct=hash_pct,
    )
