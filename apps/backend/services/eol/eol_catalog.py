"""
EOL evaluation — endoflife.date verdicts for catalog components (Phase M).

Port of BomLens ``docker/lib/enrich-eol.sh`` semantics (jq → Python):

  1. Match a component purl against the vendored whitelist
     (``eol_purl_map.json``): the FIRST rule whose ``purlPrefix`` is a
     prefix of the purl wins. **Unmapped components return ``None`` —
     never a guess** (closed-whitelist philosophy; endoflife.date covers
     runtimes/frameworks, long-tail libraries are intentionally absent).
  2. Derive the release cycle from the version per the rule's granularity
     (``major`` → first numeric segment; ``major.minor`` → first two).
  3. Look the cycle up in the snapshot dataset. ``entry.eol`` may be a
     boolean (verdict as-is) or an ISO date string (EOL iff before today);
     anything else — including a missing cycle — is ``unknown``.

One deliberate divergence from the BomLens jq: purls are normalised
``%40 → @`` before prefix matching. Spec-compliant cdxgen emits scoped npm
packages URL-encoded (``pkg:npm/%40angular/core@…``) while the vendored map
spells ``pkg:npm/@angular/core@`` — absorbing the encoding in the matcher
keeps the map byte-identical to BomLens (the consistency contract stays
exact).

Failure posture: evaluation is pure and total (no I/O after load); the
loaders return ``None``/empty on any problem so the persist hook degrades to
"no enrichment" — an EOL failure must never break a scan.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

from core.config import eol_snapshot_path

if TYPE_CHECKING:
    from models import ComponentVersion

log = structlog.get_logger("services.eol")

_MAP_PATH = Path(__file__).resolve().parent / "eol_purl_map.json"
_SNAPSHOT_PATH = Path(__file__).resolve().parent / "eol_snapshot.json"

_LEADING_INT = re.compile(r"^([0-9]+)")

EolState = Literal["eol", "supported", "unknown"]

# Closed vocabulary persisted into component_versions.eol_state (0038).
EOL_STATES: tuple[EolState, ...] = ("eol", "supported", "unknown")


@dataclass(frozen=True)
class EolRule:
    purl_prefix: str
    product: str
    cycle: str  # "major" | "major.minor"


@dataclass(frozen=True)
class EolVerdict:
    state: EolState
    product: str
    cycle: str | None
    date: date | None
    source: str


@dataclass(frozen=True)
class EolDataset:
    """Compact endoflife.date snapshot: product slug → cycle entries."""

    snapshot: str  # ISO date the snapshot was built
    products: dict[str, list[dict[str, Any]]]

    def cycles(self, product: str) -> list[dict[str, Any]]:
        entries = self.products.get(product)
        return entries if isinstance(entries, list) else []


# ---------------------------------------------------------------------------
# Loading (vendored package data — static files, lru_cache is correct here;
# CLAUDE.md rule #11 concerns env config, and the ONLY env-driven part —
# EOL_SNAPSHOT_PATH — is resolved per call in load_dataset).
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_rules() -> tuple[EolRule, ...]:
    """Parse and cache the vendored purl→product whitelist."""
    try:
        raw = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
        rules = []
        for entry in raw.get("rules", []):
            if not isinstance(entry, dict):
                continue
            prefix = entry.get("purlPrefix")
            product = entry.get("product")
            cycle = entry.get("cycle")
            if (
                isinstance(prefix, str)
                and prefix
                and isinstance(product, str)
                and product
                and cycle in ("major", "major.minor")
            ):
                rules.append(
                    EolRule(purl_prefix=prefix, product=product, cycle=cycle)
                )
        return tuple(rules)
    except (OSError, ValueError) as exc:  # pragma: no cover — vendored file
        log.warning("eol_rules_load_failed", error=str(exc))
        return ()


def load_dataset() -> EolDataset | None:
    """Load the effective snapshot dataset; ``None`` disables enrichment.

    Resolution order: operator override (``EOL_SNAPSHOT_PATH``, read at call
    time — air-gapped installs mount a fresher file) → the vendored package
    file. PR M-3 extends this with the ``eol_sync_state`` DB snapshot,
    preferring the newer of the two.
    """
    override = eol_snapshot_path()
    path = Path(override) if override else _SNAPSHOT_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("eol_dataset_load_failed", path=str(path), error=str(exc))
        return None
    if not isinstance(raw, dict):
        return None
    snapshot = raw.get("_snapshot")
    if not isinstance(snapshot, str) or not snapshot:
        return None
    products = {
        key: value
        for key, value in raw.items()
        if not key.startswith("_") and isinstance(value, list)
    }
    if not products:
        return None
    return EolDataset(snapshot=snapshot, products=products)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def derive_cycle(version: str, granularity: str) -> str | None:
    """Release cycle from a version string (BomLens ``enrich-eol.sh:77-85``).

    Strip a leading ``v``, split on ``.``, keep the leading numeric part of
    each segment and stop at the first segment without one. ``major`` →
    first segment; ``major.minor`` → first two (falls back to the first when
    only one exists). Fully non-numeric → ``None`` (unknown).

    Note: the BomLens jq errors out (→ unknown) on mid-string non-numeric
    segments; this port implements the documented "stop at the first
    non-numeric-lead segment" semantics — table-tested.
    """
    segments: list[str] = []
    for part in version.lstrip("v").split("."):
        match = _LEADING_INT.match(part)
        if not match:
            break
        segments.append(match.group(1))
    if not segments:
        return None
    if granularity == "major":
        return segments[0]
    if len(segments) >= 2:
        return f"{segments[0]}.{segments[1]}"
    return segments[0]


def evaluate(
    purl_with_version: str,
    version: str,
    *,
    rules: tuple[EolRule, ...],
    dataset: EolDataset,
    today: date,
) -> EolVerdict | None:
    """Verdict for one component; ``None`` = unmapped (leave columns NULL)."""
    purl = purl_with_version.replace("%40", "@")
    rule = next((r for r in rules if purl.startswith(r.purl_prefix)), None)
    if rule is None:
        return None
    source = f"endoflife.date@{dataset.snapshot}"
    cycle = derive_cycle(version, rule.cycle)
    if cycle is None:
        return EolVerdict("unknown", rule.product, None, None, source)
    entry = next(
        (
            e
            for e in dataset.cycles(rule.product)
            if isinstance(e, dict) and str(e.get("cycle")) == cycle
        ),
        None,
    )
    if entry is None:
        return EolVerdict("unknown", rule.product, cycle, None, source)
    eol_value = entry.get("eol")
    if isinstance(eol_value, bool):
        state: EolState = "eol" if eol_value else "supported"
        return EolVerdict(state, rule.product, cycle, None, source)
    if isinstance(eol_value, str):
        try:
            eol_date = date.fromisoformat(eol_value)
        except ValueError:
            return EolVerdict("unknown", rule.product, cycle, None, source)
        state = "eol" if eol_date < today else "supported"
        return EolVerdict(state, rule.product, cycle, eol_date, source)
    return EolVerdict("unknown", rule.product, cycle, None, source)


# ---------------------------------------------------------------------------
# Persistence glue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Evaluator:
    """Rules + dataset bound once per persist call (O(rules) per component)."""

    rules: tuple[EolRule, ...]
    dataset: EolDataset
    today: date

    def verdict_for(self, purl_with_version: str, version: str) -> EolVerdict | None:
        return evaluate(
            purl_with_version,
            version,
            rules=self.rules,
            dataset=self.dataset,
            today=self.today,
        )


def build_evaluator() -> _Evaluator | None:
    """One evaluator per persist call; ``None`` disables stamping quietly."""
    rules = load_rules()
    if not rules:
        return None
    dataset = load_dataset()
    if dataset is None:
        return None
    return _Evaluator(
        rules=rules, dataset=dataset, today=datetime.now(UTC).date()
    )


def stamp_component_version(
    component_version: ComponentVersion,
    verdict: EolVerdict | None,
    now: datetime,
) -> bool:
    """Write a verdict onto the catalog row; changed-value-guarded.

    Returns ``True`` when any column actually changed (the KEV
    ``_apply_listing`` idiom — an unchanged row is not dirtied, so a
    re-scan or the weekly re-stamp pass stays idempotent). ``verdict=None``
    (unmapped) leaves the row untouched: clearing stale stamps after a map
    change is the refresh task's job (PR M-3), not the per-scan hook's.
    """
    if verdict is None:
        return False
    changed = False
    updates = {
        "eol_state": verdict.state,
        "eol_product": verdict.product,
        "eol_cycle": verdict.cycle,
        "eol_date": verdict.date,
        "eol_source": verdict.source,
    }
    for attr, value in updates.items():
        if getattr(component_version, attr) != value:
            setattr(component_version, attr, value)
            changed = True
    if changed:
        component_version.eol_evaluated_at = now
    return changed
