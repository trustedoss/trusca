"""
Upgrade recommendation engine — v2.2 2.2-a3 ("finding → action").

This module turns the remediation data collected by 2.2-a1
(``vulnerability_findings.fixed_version``) and the graph signals from 2.2-a2
(``ScanComponent.direct`` / ``ScanComponent.depth``) into a concrete,
prioritized "upgrade to X" recommendation, exposed on:

  * the vulnerability drawer / detail response
    (:mod:`services.vulnerability_service`), and
  * the build-gate PR comment (:mod:`services.sca_comment`).

Algorithm — *minimum safe upgrade*
----------------------------------
For one component (a single ``component_version``) inside a scan, several open
findings may each carry their own ``fixed_version`` (the lowest version that
patches THAT CVE for THAT package). The version that resolves *all* of the
component's open findings at once is the **semver maximum** of those
``fixed_version`` strings — the lowest version that is ``>=`` every individual
fix. That is the "minimum safe upgrade": going any lower would leave at least
one CVE unpatched; going higher is unnecessary churn.

Three outcomes are possible per component:

  1. ``recommended_version`` is a string — every contributing finding had a
     parseable ``fixed_version`` and we computed their maximum.
  2. ``recommended_version`` is ``None`` with ``reason="no_fix_version"`` —
     at least one open finding has no ``fixed_version`` (DT reported none, or a
     legacy pre-v2.2 finding). We deliberately refuse to recommend a partial
     upgrade: bumping to the max of the *known* fixes would imply "you are now
     safe" while an un-fixed CVE remains. Safety over optimism.
  3. ``recommended_version`` is ``None`` with ``reason="unparseable_version"``
     — every contributing finding had a ``fixed_version`` string, but none of
     them parsed as a comparable version (malformed DT data). We never raise on
     such input.

Priority signals
----------------
The recommendation carries (but does NOT itself rank by) three signals so the
UI / comment can sort and flag the highest-leverage upgrades first:

  * ``direct`` — the component is a direct dependency (``ScanComponent.direct``
    or ``depth == 1``). Direct deps are the ones a developer can actually bump
    in their own manifest, so they are actionable *now*.
  * ``max_severity`` — the highest CVE severity among the component's open
    findings (``critical`` > ``high`` > … > ``unknown``).
  * ``max_epss`` — the highest EPSS exploit-probability among them.

A composite :func:`priority_rank` folds these into a single sortable integer
for callers that just want "most urgent first" without re-implementing the
tie-breaks. It is purely advisory; the gate verdict itself is unchanged.

Untrusted input
---------------
Every ``fixed_version`` string is DT-derived (untrusted). 2.2-a1's
``_normalize_fixed_version`` already rejects control chars / oversized / junk
*before persistence*, but this module re-parses defensively anyway: legacy rows
predating that guard, or a future ingest path, could still hand us a malformed
string. :func:`parse_version` NEVER raises — anything it cannot understand
returns ``None`` and the component falls into the ``unparseable_version``
outcome. The semver comparison is a tolerant, dependency-free implementation
(we do not pull in ``packaging``/``semver``): real fix versions span many
ecosystems (npm, Maven, PyPI, Go, …) whose version grammars differ, so a
lenient "numeric-release segments, then pre-release tie-break" comparator is
both safer and more portable than any single ecosystem's strict parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

import structlog

log = structlog.get_logger("upgrade_recommendation.service")

# Hard caps so a hostile / malformed version string can never drive a
# pathological parse. Real versions sit far below these. Mirrors the spirit of
# tasks.scan_source._FIXED_VERSION_MAX_LEN (100) but kept independent so this
# module is self-contained.
_MAX_VERSION_LEN = 256
_MAX_RELEASE_SEGMENTS = 16

# A release segment is a run of ASCII digits. We split the "release" portion of
# a version (everything before the first '-' pre-release marker or '+' build
# marker) on the conventional separators '.', '_', ':'.
_RELEASE_SEPARATORS_RE = re.compile(r"[._:]")

# Severity rank — higher is more urgent. 'unknown' sits at the bottom so a CVE
# whose severity DT never classified never outranks a real Low.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
    "unknown": 0,
}

RecommendationReason = Literal[
    "ok",
    "no_fix_version",
    "unparseable_version",
    "no_open_findings",
]


# ---------------------------------------------------------------------------
# Version parsing + comparison (tolerant, dependency-free)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, order=False)
class ParsedVersion:
    """A normalized, comparable version.

    ``release`` is the tuple of leading numeric segments (e.g. ``2.17.1`` →
    ``(2, 17, 1)``). ``is_prerelease`` marks a version carrying a ``-pre`` /
    ``-rc`` / ``-alpha`` style suffix; a pre-release of an otherwise identical
    release sorts *below* the final release (``2.0.0-rc1 < 2.0.0``), matching
    SemVer §11. ``prerelease`` keeps the lowercased suffix identifiers for the
    rare tie-break between two pre-releases of the same release.
    """

    release: tuple[int, ...]
    is_prerelease: bool
    prerelease: tuple[object, ...]
    # The original (stripped, de-``v``-prefixed) string — handed back as the
    # recommendation so the UI shows the version the ecosystem actually
    # publishes, not our normalized reconstruction.
    raw: str


def _coerce_prerelease_segment(token: str) -> object:
    """Numeric pre-release identifiers compare numerically, alphas lexically
    (SemVer §11.4). We return an ``(is_numeric, value)`` pair so a tuple
    comparison never mixes ``int`` and ``str`` (which would raise on Py3)."""
    if token.isdigit():
        # Cap the int width so a pathological all-digits pre-release can't
        # build an enormous integer.
        return (0, int(token[:18]))
    return (1, token)


def parse_version(value: Any) -> ParsedVersion | None:
    """Parse an untrusted version string into a :class:`ParsedVersion`, or
    ``None`` if it is not a comparable version. NEVER raises.

    Accepts the common cross-ecosystem shapes:
      * ``1.2.3`` / ``2.17.1`` / ``1.0`` / ``10``
      * a leading ``v`` / ``V`` (``v2.17.1``)
      * a leading epoch (``1:2.3.4`` — Debian/RPM): the epoch becomes the first
        release segment so ``1:0`` > ``0:9`` holds.
      * pre-release / build suffixes: ``2.0.0-rc1``, ``1.2.3+build.5``
        (build metadata is ignored for ordering, per SemVer §10).

    Rejects (→ ``None``):
      * non-strings, empty / whitespace-only,
      * control characters anywhere,
      * oversized input (> ``_MAX_VERSION_LEN``),
      * range / operator expressions (``>=1.0``, ``[1.0,2.0)``, ``*``, ``~1``),
      * anything whose leading segment is not numeric (``latest``, ``main``).
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if len(candidate) > _MAX_VERSION_LEN:
        return None
    # Reject control characters outright (NUL / CR / LF / tab / DEL / …) — a
    # value that needed stripping is not a trustworthy version.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate):
        return None

    raw = candidate
    # Strip a single leading v/V when followed by a digit (v2.17.1).
    if candidate[:1] in ("v", "V") and len(candidate) > 1 and candidate[1].isdigit():
        candidate = candidate[1:]

    # Split off build metadata ('+...') first — ignored for ordering.
    plus = candidate.find("+")
    if plus != -1:
        candidate = candidate[:plus]
    if not candidate:
        return None

    # Split off the pre-release suffix at the FIRST '-'. SemVer uses '-' as the
    # pre-release separator; some ecosystems also use '-' inside the release
    # (e.g. Debian "1.2-3"), but treating the first '-' onwards as pre-release
    # is a safe, monotonic choice for "minimum safe upgrade": a value with a
    # '-suffix' never outranks the same release without one.
    is_prerelease = False
    prerelease_tokens: tuple[object, ...] = ()
    dash = candidate.find("-")
    if dash != -1:
        release_part = candidate[:dash]
        pre_part = candidate[dash + 1 :]
        is_prerelease = True
        # Pre-release identifiers split on '.' / '-'; cap the count.
        raw_tokens = [t for t in re.split(r"[.\-]", pre_part) if t][:8]
        prerelease_tokens = tuple(_coerce_prerelease_segment(t.lower()) for t in raw_tokens)
    else:
        release_part = candidate

    # Reject a release part that starts or ends with a separator
    # (".1.2", "1.2.", "1..2") — these are malformed, not lenient-parseable.
    # A leading epoch colon is handled below, so exempt ':' from the leading
    # check by inspecting only the dot/underscore separators here.
    if release_part[:1] in (".", "_") or release_part[-1:] in (".", "_", ":"):
        return None
    if ".." in release_part or "__" in release_part:
        return None

    # Handle a leading epoch ("1:2.3.4"). The whole epoch is the first segment.
    epoch_segments: list[str] = []
    if ":" in release_part:
        head, _, tail = release_part.partition(":")
        if head.isdigit():
            epoch_segments = [head]
            release_part = tail
        else:
            # A non-numeric epoch ("x:1.0") is malformed.
            return None

    segments = [s for s in _RELEASE_SEPARATORS_RE.split(release_part) if s != ""]
    segments = epoch_segments + segments
    if not segments:
        return None

    release: list[int] = []
    for seg in segments[:_MAX_RELEASE_SEGMENTS]:
        if not seg.isdigit():
            # A non-numeric release segment (e.g. "1.x", "2.0.GA", "1.0rc")
            # makes the version not safely comparable as a numeric release.
            # Bail rather than guess — the caller treats this as
            # "unparseable_version" and surfaces no recommendation.
            return None
        # Cap each segment width so "9999...9" can't build a giant int.
        release.append(int(seg[:18]))

    if not release:
        return None

    return ParsedVersion(
        release=tuple(release),
        is_prerelease=is_prerelease,
        prerelease=prerelease_tokens,
        raw=raw,
    )


def _padded(release: tuple[int, ...], width: int) -> tuple[int, ...]:
    """Right-pad a release tuple with zeros to ``width`` so ``1.2`` and
    ``1.2.0`` compare equal and ``1.2`` < ``1.2.1``."""
    if len(release) >= width:
        return release
    return release + (0,) * (width - len(release))


def compare_versions(a: ParsedVersion, b: ParsedVersion) -> int:
    """Return -1 / 0 / +1 for ``a`` <, ==, > ``b`` under the tolerant order."""
    width = max(len(a.release), len(b.release))
    ra, rb = _padded(a.release, width), _padded(b.release, width)
    if ra != rb:
        return -1 if ra < rb else 1
    # Releases tie → final outranks pre-release.
    if a.is_prerelease != b.is_prerelease:
        # Not-prerelease (False) should sort higher → invert.
        return -1 if a.is_prerelease else 1
    if a.prerelease != b.prerelease:
        return -1 if a.prerelease < b.prerelease else 1
    return 0


def max_safe_version(versions: list[str]) -> str | None:
    """Return the semver-maximum of ``versions`` (the minimum safe upgrade that
    is ``>=`` all of them), or ``None`` if none parse. NEVER raises.

    The returned value is the ORIGINAL string of the winning version (so the UI
    shows the published form), not a normalized reconstruction.
    """
    best: ParsedVersion | None = None
    for raw in versions:
        parsed = parse_version(raw)
        if parsed is None:
            continue
        if best is None or compare_versions(parsed, best) > 0:
            best = parsed
    return best.raw if best is not None else None


# ---------------------------------------------------------------------------
# Per-component recommendation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FindingSignal:
    """The remediation + priority inputs for one open finding of a component."""

    fixed_version: str | None
    severity: str
    epss_score: float | None


@dataclass(frozen=True)
class UpgradeRecommendation:
    """The computed recommendation for one component (component_version)."""

    recommended_version: str | None
    reason: RecommendationReason
    direct: bool
    max_severity: str | None
    max_epss: float | None
    # The number of open findings the recommendation resolves (when ``ok``) or
    # the number that contributed to the decision otherwise. Advisory.
    finding_count: int = 0
    # The subset of contributing fix versions that parsed — handy for tests /
    # debugging and for the comment builder to show "fixes N CVEs at X".
    contributing_fix_versions: tuple[str, ...] = field(default_factory=tuple)


def _max_severity(signals: list[FindingSignal]) -> str | None:
    best: str | None = None
    best_rank = -1
    for s in signals:
        rank = _SEVERITY_RANK.get(s.severity, 0)
        if rank > best_rank:
            best_rank = rank
            best = s.severity
    return best


def _max_epss(signals: list[FindingSignal]) -> float | None:
    best: float | None = None
    for s in signals:
        if s.epss_score is None:
            continue
        if best is None or s.epss_score > best:
            best = s.epss_score
    return best


def recommend_for_component(
    signals: list[FindingSignal],
    *,
    direct: bool,
) -> UpgradeRecommendation:
    """Compute the minimum-safe-upgrade recommendation for one component.

    ``signals`` is the list of the component's OPEN findings (the caller is
    responsible for filtering out dispositioned statuses — not_affected / fixed
    / false_positive — exactly as the build gate does).

    Outcomes (see module docstring):
      * ``ok`` — every finding had a parseable fix; ``recommended_version`` is
        their semver maximum.
      * ``no_fix_version`` — at least one open finding has no ``fixed_version``.
      * ``unparseable_version`` — all findings had a ``fixed_version`` string
        but none parsed.
      * ``no_open_findings`` — ``signals`` was empty.
    """
    if not signals:
        return UpgradeRecommendation(
            recommended_version=None,
            reason="no_open_findings",
            direct=direct,
            max_severity=None,
            max_epss=None,
            finding_count=0,
        )

    max_sev = _max_severity(signals)
    max_epss = _max_epss(signals)
    finding_count = len(signals)

    # If ANY open finding lacks a fix version we refuse to recommend — bumping
    # to the max of the *known* fixes would falsely imply full remediation.
    if any(s.fixed_version is None for s in signals):
        return UpgradeRecommendation(
            recommended_version=None,
            reason="no_fix_version",
            direct=direct,
            max_severity=max_sev,
            max_epss=max_epss,
            finding_count=finding_count,
        )

    fix_strings = [s.fixed_version for s in signals if s.fixed_version is not None]
    parsed_pairs = [(raw, parse_version(raw)) for raw in fix_strings]
    parseable = [(raw, p) for raw, p in parsed_pairs if p is not None]

    if not parseable:
        # Every finding carried a fix string, but all were malformed.
        return UpgradeRecommendation(
            recommended_version=None,
            reason="unparseable_version",
            direct=direct,
            max_severity=max_sev,
            max_epss=max_epss,
            finding_count=finding_count,
        )

    best_raw, best = parseable[0]
    for raw, p in parseable[1:]:
        if compare_versions(p, best) > 0:
            best_raw, best = raw, p

    return UpgradeRecommendation(
        recommended_version=best_raw,
        reason="ok",
        direct=direct,
        max_severity=max_sev,
        max_epss=max_epss,
        finding_count=finding_count,
        contributing_fix_versions=tuple(raw for raw, _ in parseable),
    )


# ---------------------------------------------------------------------------
# Priority ranking (advisory)
# ---------------------------------------------------------------------------


def priority_rank(rec: UpgradeRecommendation) -> tuple[int, int, float]:
    """A sortable key for "most urgent first" (descending).

    Folds the three priority signals into a tuple the caller can ``sorted(...,
    reverse=True)`` on:

      1. ``direct`` (1 if the component is a direct dependency, else 0) — the
         developer can act on it immediately.
      2. severity rank (critical=5 … unknown=0).
      3. max EPSS (0.0 when unknown).

    A recommendation we cannot actually act on (``recommended_version is None``)
    is demoted below every actionable one by zeroing the ``direct`` term: there
    is no upgrade to perform, so it should not crowd out a fixable direct dep.
    """
    actionable = rec.recommended_version is not None
    direct_term = (1 if rec.direct else 0) if actionable else 0
    sev_term = _SEVERITY_RANK.get(rec.max_severity or "unknown", 0)
    epss_term = rec.max_epss if rec.max_epss is not None else 0.0
    return (direct_term, sev_term, epss_term)


__all__ = [
    "FindingSignal",
    "ParsedVersion",
    "RecommendationReason",
    "UpgradeRecommendation",
    "compare_versions",
    "max_safe_version",
    "parse_version",
    "priority_rank",
    "recommend_for_component",
]
