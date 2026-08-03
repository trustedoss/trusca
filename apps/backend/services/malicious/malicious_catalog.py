# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Malicious-package evaluation — OSV ``MAL-`` verdicts for catalog components.

Port of BomLens ``docker/lib/enrich-malicious.sh`` semantics (jq → Python).

Why this is a separate axis from vulnerabilities
------------------------------------------------
A CVE says an honest package has a flaw you can patch; the response is an
upgrade. A malicious package was published to attack whoever installs it, and
the response is removal plus rotation of every credential the build could
reach. Reporting it as another row in the severity table would tell the reader
to schedule an upgrade, which is the wrong action. So:

* no ``vulnerability_findings`` row is created,
* the count never enters severity aggregates or ``maxSeverity``-style rollups,
* absence is "not assessed", never "safe" — an unloaded snapshot stamps
  nothing and the UI shows no count rather than a reassuring zero,
* every verdict carries its snapshot date, so the claim is "not in the
  2026-08-03 snapshot", not "safe today".

Matching: PURL only, never by name
----------------------------------
Malicious packages are deliberately named to resemble real ones — that is the
attack — so a name match is exactly the wrong tool. Only the base PURL is
compared.

**No ``%40 → @`` normalisation, unlike the EOL matcher.** That divergence is
load-bearing and was measured (2026-08-03): OSV spells scoped npm packages
URL-encoded (``pkg:npm/%40ctrl/tinycolor``, verified through
``api.osv.dev/v1/query``) and cdxgen's ``components[].purl`` — the field
``persist_sbom_components`` stores — spells them the same way. The two key
spaces already line up. Normalising either side would move one and not the
other, and every scoped package would silently stop matching: 15,867 of the
snapshot's PURLs, including the packages recent supply-chain attacks actually
hit. The EOL matcher normalises because *its* map (endoflife.date) spells them
``@``; same code, different counterparty, different rule.

Failure posture mirrors EOL: loading returns ``None`` on any problem and the
persist hook degrades to "no enrichment" — a snapshot problem must never break
a scan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from models.component import ComponentVersion

logger = structlog.get_logger(__name__)

#: Closed vocabulary for ``component_versions.malicious_state``.
#:
#: ``NULL`` (absent from this tuple) means NOT ASSESSED — no snapshot, feature
#: off, or a row predating the column. It is deliberately distinct from
#: ``clear``: the index is a DENY list, so "absent from the index" is a real
#: finding ("this snapshot does not know it"), whereas a missing evaluation is
#: no finding at all. Mirrored in the frontend as ``MALICIOUS_STATES``; a
#: contract test asserts the two sets are equal.
MALICIOUS_STATES: tuple[str, ...] = ("flagged", "clear")

MaliciousState = Literal["flagged", "clear"]

SNAPSHOT_PATH = Path(__file__).resolve().parent / "malicious_snapshot.json"


@dataclass(frozen=True)
class MaliciousVerdict:
    """One component's verdict. ``state='clear'`` still carries the source."""

    state: MaliciousState
    #: OSV advisory id (``MAL-2025-47141``); only set when flagged.
    advisory_id: str | None
    #: ``osv.dev@YYYY-MM-DD`` — the snapshot the verdict came from.
    source: str


@dataclass(frozen=True)
class MaliciousIndex:
    """Loaded snapshot: base purl → advisory id, plus optional version pins."""

    snapshot: str
    ecosystems: tuple[str, ...]
    packages: dict[str, str]
    versions: dict[str, list[str]]
    #: purl → the release the compromise ended at. Versions at or after it are
    #: clean. Rare (23 npm entries at 2026-08-03) but load-bearing: `fsevents`
    #: is one, and without the boundary every 2.x install reads as malicious.
    fixed_before: dict[str, str]


def base_purl(purl_with_version: str) -> str:
    """Strip qualifiers, subpath and version from a PURL.

    ``pkg:npm/%40babel/core@7.29.7`` → ``pkg:npm/%40babel/core``. The encoded
    ``%40`` of a scoped name is untouched because it is not a literal ``@`` —
    which is precisely why the encoding must be preserved rather than
    normalised (see the module docstring).
    """
    head = purl_with_version.split("?", 1)[0].split("#", 1)[0]
    # Only an '@' AFTER the last '/' can be the version separator. An earlier
    # one is an unencoded npm scope marker ('pkg:npm/@scope/name'), which the
    # persist hook can still produce: it falls back to `bom-ref` when a
    # component carries no `purl`, and cdxgen spells the scope plainly there
    # even though it encodes it in `purl`. Searching from the end without this
    # guard turns 'pkg:npm/@scope/name' into 'pkg:npm/'.
    slash = head.rfind("/")
    at = head.find("@", slash + 1) if slash >= 0 else head.find("@")
    return head[:at] if at > 0 else head


@lru_cache(maxsize=1)
def load_index() -> MaliciousIndex | None:
    """Load the vendored snapshot once per process. ``None`` on any problem.

    12.8 MB of JSON, so this is cached for the life of the worker and the
    persist hook builds its evaluator once per scan rather than per component.
    """
    try:
        with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("malicious.snapshot_load_failed", error=str(exc))
        return None

    packages = raw.get("packages")
    snapshot = raw.get("_snapshot")
    if not isinstance(packages, dict) or not isinstance(snapshot, str):
        logger.warning("malicious.snapshot_malformed", path=str(SNAPSHOT_PATH))
        return None

    versions = raw.get("versions")
    fixed_before = raw.get("fixed_before")
    return MaliciousIndex(
        snapshot=snapshot,
        ecosystems=tuple(raw.get("_ecosystems") or ()),
        packages=packages,
        versions=versions if isinstance(versions, dict) else {},
        fixed_before=fixed_before if isinstance(fixed_before, dict) else {},
    )


def _version_key(value: str) -> tuple[int, ...]:
    """Numeric segments of a version, for comparing against a fix boundary.

    Same crude parse as the builder's, and for the same reason: it only ever
    compares two releases of ONE package, so a full semver implementation
    would buy nothing. A non-numeric segment stops the parse.
    """
    parts: list[int] = []
    for segment in value.split("."):
        digits = ""
        for ch in segment:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


class _Evaluator:
    """Pure verdict function over a loaded index (no I/O after construction)."""

    __slots__ = ("_index", "_source")

    def __init__(self, index: MaliciousIndex) -> None:
        self._index = index
        self._source = f"osv.dev@{index.snapshot}"

    @property
    def snapshot(self) -> str:
        return self._index.snapshot

    def verdict_for(self, purl_with_version: str, version: str | None) -> MaliciousVerdict:
        """Verdict for one component. Never ``None`` — the index is a deny list.

        A PURL in the index means every published version is malicious unless
        the advisory named specific versions, in which case only those match.
        """
        key = base_purl(purl_with_version)
        advisory_id = self._index.packages.get(key)
        if advisory_id is None:
            return MaliciousVerdict("clear", None, self._source)

        pinned = self._index.versions.get(key)
        if pinned and version is not None and version not in pinned:
            # The advisory named versions and this is not one of them.
            return MaliciousVerdict("clear", None, self._source)

        fixed = self._index.fixed_before.get(key)
        if fixed and version is not None:
            installed_key, fixed_key = _version_key(version), _version_key(fixed)
            # Only decide when both parse; an unparseable version keeps the
            # flag rather than being waved through on a guess.
            if installed_key and fixed_key and installed_key >= fixed_key:
                return MaliciousVerdict("clear", None, self._source)

        return MaliciousVerdict("flagged", advisory_id, self._source)


def build_evaluator() -> _Evaluator | None:
    """Evaluator over the vendored snapshot, or ``None`` when unavailable."""
    index = load_index()
    if index is None:
        return None
    return _Evaluator(index)


def stamp_component_version(
    component_version: ComponentVersion,
    verdict: MaliciousVerdict | None,
    now: datetime,
) -> bool:
    """Write a verdict onto the catalog row; changed-value-guarded.

    Returns ``True`` when a column actually changed (the KEV ``_apply_listing``
    idiom), so a re-scan or the weekly re-stamp pass stays idempotent.
    ``verdict=None`` — no snapshot loaded — leaves the row untouched rather
    than clearing it: losing the snapshot must not silently un-flag a package
    that is still malicious.

    The guard is direction-agnostic, which is what lets a withdrawn advisory
    flow back to ``clear`` on the next snapshot without any special case.
    """
    if verdict is None:
        return False

    updates = {
        "malicious_state": verdict.state,
        "malicious_id": verdict.advisory_id,
        "malicious_source": verdict.source,
    }
    changed = False
    for attr, value in updates.items():
        if getattr(component_version, attr) != value:
            setattr(component_version, attr, value)
            changed = True
    if changed:
        component_version.malicious_evaluated_at = now
    return changed
