# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Outbound-license conflict verdicts — gap #27.

Answers a question the policy axis cannot: *this project ships under license X;
can it carry a dependency licensed Y?* The policy axis asks whether Y is allowed
at all, which is a different decision and often has the opposite answer — a team
may permit LGPL-2.1 everywhere and still not be able to ship it inside a
permissively-licensed library.

Hand-ported from ``term_verdict`` / ``expr_verdict`` /
``component_license_conflict`` in BomLens's ``docker/lib/license-flags.jq``,
with the rules themselves vendored as data in ``services/license_compat.json``
(see THIRD_PARTY_NOTICES.md). Rules-as-data is the point: every verdict carries
the sentence that justifies it, and that sentence is what a reviewer argues
with, not a branch in this file.

Advisory, never a determination
-------------------------------
A verdict is a documentation aid. ``conditional`` is used generously and
``incompatible`` is reserved for combinations that are uncontroversial. Nothing
here decides a legal question, and the API surfaces the same caveat to the user.

Absent is not clean
-------------------
With no declared outbound license there is no verdict at all — :func:`assess`
returns ``None`` rather than ``compatible`` or ``unknown``. The distinction is
load-bearing: a project that has not been assessed must never read as one that
came back clean.

Operators decide the fold
-------------------------
A dependency's license arrives either as several entries in ``licenses[]`` or
as one SPDX expression. The two operators mean opposite things:

* ``AND`` — every term applies, so the *worst* verdict wins.
* ``OR`` — the consumer picks one, so the *best* verdict wins. Several
  ``licenses[]`` entries follow the same rule; CycloneDX treats them as
  alternatives.

The OUTBOUND side folds the other way. ``MIT OR Apache-2.0`` means a consumer
may rely on either branch, so the declaration is only honoured if every term it
names can carry the dependency — see :func:`outbound_terms`.

Parsing is delegated to :func:`services.license_expression.fold_expression`,
which already enforces the length, token and nesting bounds and never raises.
Upstream's jq parser gives up on parentheses and records "unknown"; ours folds
them, so ``(MIT OR GPL-3.0) AND Apache-2.0`` gets a real answer.

``WITH`` is where this port improves on the original. Upstream inspects the raw
term for an exception clause and caps ``incompatible`` at ``conditional``,
because an exception exists precisely to permit the combination the base
license would forbid. Our parser splits ``WITH`` into an operator, so the
clause never survives into a term — the cap moves onto the operator itself,
which reaches the same conclusion without a substring search.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import structlog

from services.license_class import UNCATEGORIZED, classify_license_class
from services.license_expression import ExpressionParseError, fold_expression

log = structlog.get_logger("license.conflict")

# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------

COMPATIBLE: Final = "compatible"
CONDITIONAL: Final = "conditional"
UNKNOWN: Final = "unknown"
INCOMPATIBLE: Final = "incompatible"

CONFLICT_VERDICT_VALUES: Final[tuple[str, ...]] = (
    COMPATIBLE,
    CONDITIONAL,
    UNKNOWN,
    INCOMPATIBLE,
)

# Worst-of ordering for the AND fold. ``unknown`` sits BELOW ``incompatible``
# and above ``conditional``: not knowing is worse than a combination somebody
# has to check, and better than one we can name as broken.
VERDICT_RANK: Final[dict[str, int]] = {
    COMPATIBLE: 0,
    CONDITIONAL: 1,
    UNKNOWN: 2,
    INCOMPATIBLE: 3,
}

_COMPAT_PATH: Final[Path] = Path(__file__).with_name("license_compat.json")

# Reasons that are ours rather than the rule data's. Everything else a user
# reads comes from license_compat.json.
_WHY_NO_RULE: Final = "No rule covers this combination."
_WHY_NO_LICENSE: Final = "The component declares no license."
_WHY_UNPARSEABLE: Final = "The declared license could not be read as an SPDX expression."
_WHY_OUTBOUND_UNPARSEABLE: Final = (
    "The project's outbound license could not be read as an SPDX expression, "
    "so there is nothing to judge this dependency against."
)
_WHY_EXCEPTION: Final = (
    "The dependency carries an exception clause, which exists to permit exactly "
    "this combination. Confirm the exception covers your use."
)


@dataclass(frozen=True)
class ConflictVerdict:
    """One verdict plus the sentence that justifies it.

    ``dependency_class`` is the copyleft strength the verdict was reached
    through — carried so the drawer can show *why* a matrix cell applied,
    rather than presenting the verdict as an oracle.
    """

    verdict: str
    why: str
    dependency_class: str


_RULES_CACHE: dict[str, Any] | None = None


def _rules() -> dict[str, Any]:
    """The vendored rule set, parsed once — but only a SUCCESSFUL parse is kept.

    A malformed or missing file is not a reason to fail a page load: the module
    degrades to "no rules", every verdict becomes ``unknown``, and the failure
    is logged rather than raised. The file ships in the image, so this is a
    packaging-error path, not a runtime one.

    Caching the failure would turn a transient read error — an image layer
    swapped under a running worker, a permission fixed a minute later — into a
    process-lifetime outage where every verdict reads ``unknown`` and only one
    log line ever said why. So the cache is filled on success and left empty on
    failure, and the next request tries again.
    """
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    try:
        document: dict[str, Any] = json.loads(_COMPAT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - packaging error
        log.error("license_conflict.rules_unreadable", error=str(exc))
        return {}
    _RULES_CACHE = document
    return document


def _matrix_cell(outbound_class: str, dependency_class: str) -> ConflictVerdict | None:
    matrix = _rules().get("matrix")
    if not isinstance(matrix, dict):
        return None
    row = matrix.get(outbound_class)
    if not isinstance(row, dict):
        return None
    cell = row.get(dependency_class)
    if not isinstance(cell, dict):
        return None
    verdict = cell.get("verdict")
    why = cell.get("why")
    if verdict not in VERDICT_RANK or not isinstance(why, str):
        return None
    return ConflictVerdict(verdict=verdict, why=why, dependency_class=dependency_class)


# SPDX deprecated a bare ``GPL-2.0`` in favour of the explicit
# ``GPL-2.0-only`` / ``GPL-2.0-or-later`` pair, but the bare form is still the
# one most people type. Without this, declaring ``GPL-2.0`` would miss the
# explicit pair that says Apache-2.0 cannot be combined with it, and the class
# matrix would answer ``compatible`` — a rule stated in the data, silently
# reversed by a spelling. Only the ``-only`` direction is added: ``GPL-2.0+``
# means ``-or-later``, which may be satisfied under GPL-3.0 and genuinely does
# not carry the Apache-2.0 problem.
_DEPRECATED_GPL_ID = re.compile(r"^((?:A|L)?GPL-\d+(?:\.\d+)*)$", re.IGNORECASE)


def _canonical_pair_key(value: str) -> str:
    """Upper-cased id with SPDX's deprecated GPL spelling expanded."""
    candidate = value.strip()
    match = _DEPRECATED_GPL_ID.match(candidate)
    if match is not None:
        candidate = f"{match.group(1)}-only"
    return candidate.upper()


def _explicit_pair(outbound: str, dependency: str) -> ConflictVerdict | None:
    """An exception the class matrix cannot express, e.g. GPL-2.0-only + Apache-2.0.

    Matched case-insensitively on the whole id, and consulted BEFORE the
    matrix — the pairs exist precisely because the class-level answer is wrong
    for them.
    """
    pairs = _rules().get("pairs")
    if not isinstance(pairs, list):
        return None
    outbound_key = _canonical_pair_key(outbound)
    dependency_key = _canonical_pair_key(dependency)
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if not isinstance(pair.get("outbound"), str) or not isinstance(
            pair.get("dependency"), str
        ):
            continue
        if (
            _canonical_pair_key(pair["outbound"]) == outbound_key
            and _canonical_pair_key(pair["dependency"]) == dependency_key
        ):
            verdict = pair.get("verdict")
            why = pair.get("why")
            if verdict in VERDICT_RANK and isinstance(why, str):
                return ConflictVerdict(
                    verdict=verdict,
                    why=why,
                    dependency_class=classify_license_class(dependency),
                )
    return None


def term_verdict(term: str, outbound: str) -> ConflictVerdict:
    """Judge ONE dependency license id against the outbound license.

    Explicit pairs win over the class matrix. A combination with no rule at all
    resolves to ``unknown`` — the module never invents an answer for a shape
    the rule data does not cover.
    """
    pair = _explicit_pair(outbound, term)
    if pair is not None:
        return pair

    outbound_class = classify_license_class(outbound)
    dependency_class = classify_license_class(term)
    cell = _matrix_cell(outbound_class, dependency_class)
    if cell is not None:
        return cell
    return ConflictVerdict(
        verdict=UNKNOWN, why=_WHY_NO_RULE, dependency_class=dependency_class
    )


def _combine(
    left: ConflictVerdict, right: ConflictVerdict, operator: str
) -> ConflictVerdict:
    """Fold two verdicts under an SPDX operator."""
    if operator == "WITH":
        # The right operand is a license *exception*, not a license — it has no
        # verdict of its own, and the matrix answer computed for it is
        # meaningless. Its presence caps the base license's verdict: an
        # exception clause exists to allow the combination the base license
        # would otherwise forbid, so "incompatible" softens to "conditional"
        # and a human confirms the exception covers their use.
        if left.verdict == INCOMPATIBLE:
            return ConflictVerdict(
                verdict=CONDITIONAL,
                why=f"{_WHY_EXCEPTION} ({left.why})",
                dependency_class=left.dependency_class,
            )
        return left

    if operator == "OR":
        # The consumer picks one alternative, so the best verdict decides.
        return min(left, right, key=lambda verdict: VERDICT_RANK[verdict.verdict])

    # AND (and any operator the grammar might add): every term applies, so the
    # worst verdict decides.
    return max(left, right, key=lambda verdict: VERDICT_RANK[verdict.verdict])


def outbound_terms(outbound: str) -> list[str] | None:
    """Every license id named by the outbound declaration, or ``None`` if unreadable.

    The outbound side is an expression too — ``MIT OR Apache-2.0`` is a real
    way to ship a project — so it cannot be classified by running the whole
    string through :func:`classify_license_class`. That reading produced two
    silent failures: a compound declaration fell to ``uncategorized`` and made
    every verdict ``unknown`` while the screen still said it had measured
    against the declared license, and ``MIT OR GPL-3.0`` classified as
    strong-copyleft, which softened an AGPL dependency to ``conditional`` by
    hiding the MIT branch a consumer may take.

    Operators are deliberately flattened. Both of them mean "some consumer may
    end up relying on this term", so an outbound declaration is honoured only
    if EVERY term it names can carry the dependency — worst-of across the
    whole set. That is the opposite of the dependency side, where ``OR`` lets
    one clean alternative clear the row.
    """
    try:
        terms: list[str] = fold_expression(
            outbound,
            resolve_id=lambda token: [token],
            combine=lambda left, right, _operator: left + right,
        )
    except ExpressionParseError as exc:
        log.info("license_conflict.unparseable_outbound", warning=exc.code)
        return None
    return terms


def _worst_against_outbound(term: str, outbounds: list[str]) -> ConflictVerdict:
    """Judge one dependency term against every outbound term, worst wins."""
    return max(
        (term_verdict(term, outbound) for outbound in outbounds),
        key=lambda verdict: VERDICT_RANK[verdict.verdict],
    )


def expression_verdict(expression: str, outbound: str) -> ConflictVerdict | None:
    """Judge one license string, which may be a compound SPDX expression.

    Returns ``None`` when the string yields no parseable terms, so the caller
    can decide what an unreadable declaration means in its own context.
    """
    outbounds = outbound_terms(outbound)
    if outbounds is None:
        return ConflictVerdict(
            verdict=UNKNOWN,
            why=_WHY_OUTBOUND_UNPARSEABLE,
            dependency_class=classify_license_class(expression),
        )
    try:
        return fold_expression(
            expression,
            resolve_id=lambda token: _worst_against_outbound(token, outbounds),
            combine=_combine,
        )
    except ExpressionParseError as exc:
        log.info("license_conflict.unparseable_dependency", warning=exc.code)
        return None


def assess(
    license_strings: list[str] | tuple[str, ...],
    *,
    outbound: str | None,
) -> ConflictVerdict | None:
    """The verdict for a component (or a license row) against the outbound license.

    ``license_strings`` are the ids / names / expressions the component
    declares. Several of them are ALTERNATIVES — CycloneDX lets a consumer pick
    one — so the best verdict across them wins, the same rule as ``OR``.

    Returns ``None`` when ``outbound`` is absent or blank: nothing was
    assessed, which the caller must not render as a clean result.
    """
    if outbound is None or not outbound.strip():
        return None

    usable = [value for value in license_strings if value and value.strip()]
    if not usable:
        return ConflictVerdict(
            verdict=UNKNOWN, why=_WHY_NO_LICENSE, dependency_class=UNCATEGORIZED
        )

    verdicts = [
        verdict
        for verdict in (expression_verdict(value, outbound) for value in usable)
        if verdict is not None
    ]
    if not verdicts:
        return ConflictVerdict(
            verdict=UNKNOWN, why=_WHY_UNPARSEABLE, dependency_class=UNCATEGORIZED
        )
    return min(verdicts, key=lambda verdict: VERDICT_RANK[verdict.verdict])


__all__ = [
    "COMPATIBLE",
    "CONDITIONAL",
    "CONFLICT_VERDICT_VALUES",
    "INCOMPATIBLE",
    "UNKNOWN",
    "VERDICT_RANK",
    "ConflictVerdict",
    "assess",
    "expression_verdict",
    "outbound_terms",
    "term_verdict",
]
