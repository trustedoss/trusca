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
from services.upgrade_recommendation import compare_versions, parse_version

if TYPE_CHECKING:
    from models import ComponentVersion

log = structlog.get_logger("services.eol")

_MAP_PATH = Path(__file__).resolve().parent / "eol_purl_map.json"
_SNAPSHOT_PATH = Path(__file__).resolve().parent / "eol_snapshot.json"

_LEADING_INT = re.compile(r"^([0-9]+)")

# ``EolVerdict.date`` (a field) shadows the ``date`` type inside that class
# body; this alias lets fields declared after it still name the type.
_ReleaseDate = date

# Ceiling on a single numeric segment in a derived cycle. The persisted
# column is VARCHAR(32) (0038) and no real release cycle approaches double
# digits per segment; a crafted 33+-digit "version" in a hostile SBOM would
# otherwise overflow the column at flush time — OUTSIDE the per-component
# best-effort guard — and abort the whole persist (security-reviewer M2).
# Over-long segments make the cycle underivable → "unknown", never a guess.
_MAX_CYCLE_SEGMENT_CHARS = 15

EolState = Literal["eol", "supported", "unknown"]

# Closed vocabulary persisted into component_versions.eol_state (0038).
EOL_STATES: tuple[EolState, ...] = ("eol", "supported", "unknown")

# Version-currency signal (0040), a sibling of EOL derived from the SAME
# endoflife.date match. Where EOL answers "is this release line dead?",
# currency answers "is this version behind the newest patch of its release
# line?" — the snapshot carries ``latest`` (the newest patch in the cycle) and
# ``latestReleaseDate`` per cycle. ``outdated`` = installed < cycle.latest;
# ``current`` = installed >= cycle.latest; ``unknown`` = no cycle match, no
# ``latest``, or an unparseable version. This is the OFFLINE half; the
# deps.dev "absolute newest across the ecosystem / N releases behind" half is
# a separate opt-in egress path (not implemented here).
CurrencyState = Literal["current", "outdated", "unknown"]
CURRENCY_STATES: tuple[CurrencyState, ...] = ("current", "outdated", "unknown")


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
    # Currency (0040) — defaulted so the no-cycle-match branches (where the
    # snapshot carries no ``latest``) stay ``unknown``/NULL without threading.
    currency_state: CurrencyState = "unknown"
    currency_latest: str | None = None
    # The ``date`` field above shadows the ``date`` type inside the class body,
    # so this later field aliases it (see ``_ReleaseDate``).
    currency_latest_release_date: _ReleaseDate | None = None


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
        if len(match.group(1)) > _MAX_CYCLE_SEGMENT_CHARS:
            # Pathological numeric run — no real cycle looks like this, and
            # persisting it would overflow VARCHAR(32). Underivable.
            return None
        segments.append(match.group(1))
    if not segments:
        return None
    if granularity == "major":
        return segments[0]
    if len(segments) >= 2:
        return f"{segments[0]}.{segments[1]}"
    return segments[0]


def _evaluate_currency(
    version: str, entry: dict[str, Any]
) -> tuple[CurrencyState, str | None, date | None]:
    """Currency verdict from a matched cycle entry's ``latest`` field.

    ``outdated`` when the installed version parses strictly below the cycle's
    ``latest`` patch; ``current`` when ``>=``; ``unknown`` when the entry has no
    usable ``latest`` or either version is unparseable. Uses the tolerant,
    cross-ecosystem comparator from ``upgrade_recommendation`` (never raises).
    """
    latest = entry.get("latest")
    if not isinstance(latest, str) or not latest:
        return ("unknown", None, None)
    latest_release_date: date | None = None
    raw_date = entry.get("latestReleaseDate")
    if isinstance(raw_date, str):
        try:
            latest_release_date = date.fromisoformat(raw_date)
        except ValueError:
            latest_release_date = None
    installed_parsed = parse_version(version)
    latest_parsed = parse_version(latest)
    if installed_parsed is None or latest_parsed is None:
        return ("unknown", latest, latest_release_date)
    state: CurrencyState = (
        "outdated" if compare_versions(installed_parsed, latest_parsed) < 0 else "current"
    )
    return (state, latest, latest_release_date)


def evaluate(
    purl_with_version: str,
    version: str,
    *,
    rules: tuple[EolRule, ...],
    dataset: EolDataset,
    today: date,
) -> EolVerdict | None:
    """Verdict for one component; ``None`` = unmapped (leave columns NULL).

    Carries both the EOL state and the version-currency signal (0040) — both
    derive from the SAME endoflife.date cycle match, so they are computed in
    one pass. Currency stays ``unknown`` on any branch without a cycle entry.
    """
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
    cur_state, cur_latest, cur_date = _evaluate_currency(version, entry)
    eol_value = entry.get("eol")
    if isinstance(eol_value, bool):
        state: EolState = "eol" if eol_value else "supported"
        return EolVerdict(
            state, rule.product, cycle, None, source, cur_state, cur_latest, cur_date
        )
    if isinstance(eol_value, str):
        try:
            eol_date = date.fromisoformat(eol_value)
        except ValueError:
            return EolVerdict(
                "unknown", rule.product, cycle, None, source, cur_state, cur_latest, cur_date
            )
        state = "eol" if eol_date < today else "supported"
        return EolVerdict(
            state, rule.product, cycle, eol_date, source, cur_state, cur_latest, cur_date
        )
    return EolVerdict(
        "unknown", rule.product, cycle, None, source, cur_state, cur_latest, cur_date
    )


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

    # Currency (0040) — tracked independently so a currency-only change does
    # not dirty the EOL stamp time, and vice versa, keeping each idempotent.
    currency_changed = False
    currency_updates = {
        "currency_state": verdict.currency_state,
        "currency_latest": verdict.currency_latest,
        "currency_latest_release_date": verdict.currency_latest_release_date,
    }
    for attr, value in currency_updates.items():
        if getattr(component_version, attr) != value:
            setattr(component_version, attr, value)
            currency_changed = True
    if currency_changed:
        component_version.currency_evaluated_at = now

    return changed or currency_changed
