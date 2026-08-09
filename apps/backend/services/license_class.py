# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Copyleft-strength classification — the axis an outbound-license verdict needs.

Hand-ported from the upstream ``license_class`` / ``class_rank`` /
``component_license_class`` definitions in BomLens's ``docker/lib/license-flags.jq``
(see THIRD_PARTY_NOTICES.md). ``services/license_flags.py`` ports the *other*
classifier in that same file — the AI review flag — which is orthogonal: a
license can be both ``network-copyleft`` and ``behavioral_use``.

Why a separate axis from the policy category
--------------------------------------------
``LicensePolicy`` answers "is this license allowed here". Copyleft strength
answers "how far does this license reach into whatever it is combined with".
The two are independent: a team may allow LGPL-2.1 (category ``allowed``) while
that same license is still ``weak-copyleft`` and therefore carries obligations
when the project ships under a permissive outbound license. Only the strength
axis can feed :mod:`services.license_conflict`.

Why patterns rather than a column on the catalog
------------------------------------------------
TRUSCA's static catalog (``services/license_translations.py``) covers 52 SPDX
ids. Scanner output does not stop there: ``GPL-2.0-or-later``, ``LGPL-2.1+``,
``AGPL-3.0-only`` and hundreds of ``LicenseRef-*`` strings arrive from cdxgen
and scancode. A column would be empty for exactly the inputs that need a
verdict. An allowlist of known-permissive ids plus four ordered patterns covers
the variants automatically, and — the headline rule — anything that matches
nothing is ``uncategorized``, NEVER ``permissive``. Not recognising a license
is not evidence that it is harmless.

Order is the rule
-----------------
``AGPL`` and ``LGPL`` are tested before the bare ``GPL`` test, or they would
fall through to ``strong-copyleft`` and lose the distinction the whole module
exists for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

NETWORK_COPYLEFT: Final = "network-copyleft"
STRONG_COPYLEFT: Final = "strong-copyleft"
WEAK_COPYLEFT: Final = "weak-copyleft"
UNCATEGORIZED: Final = "uncategorized"
PERMISSIVE: Final = "permissive"

# Single source of truth for the five class tokens. The API schema Literal
# mirrors this tuple, and ``services/license_compat.json``'s matrix keys are
# reconciled against it in tests (CLAUDE.md hardening rule 2 — the same
# vocabulary in two places needs a contract test).
LICENSE_CLASS_VALUES: Final[tuple[str, ...]] = (
    NETWORK_COPYLEFT,
    STRONG_COPYLEFT,
    WEAK_COPYLEFT,
    UNCATEGORIZED,
    PERMISSIVE,
)

# Worst-of precedence across a component's licenses. Known copyleft outranks an
# unknown license; an unknown license outranks known-permissive. The middle
# placement of ``uncategorized`` is the point: it must never be treated as the
# safe end of the scale.
CLASS_RANK: Final[dict[str, int]] = {
    NETWORK_COPYLEFT: 5,
    STRONG_COPYLEFT: 4,
    WEAK_COPYLEFT: 3,
    UNCATEGORIZED: 2,
    PERMISSIVE: 1,
}

# Known-permissive SPDX ids, upper-cased for comparison. An allowlist, not a
# heuristic: adding an id here is a deliberate statement that the license
# imposes no reciprocal obligation beyond notice retention.
PERMISSIVE_IDS: Final[frozenset[str]] = frozenset(
    {
        "MIT",
        "MIT-0",
        "ISC",
        "0BSD",
        "BSD-2-CLAUSE",
        "BSD-3-CLAUSE",
        "APACHE-2.0",
        "APACHE-1.1",
        "ZLIB",
        "UNLICENSE",
        "BSL-1.0",
        "PSF-2.0",
        "PYTHON-2.0",
        "CC0-1.0",
        "WTFPL",
        "NCSA",
        "X11",
    }
)

# ---------------------------------------------------------------------------
# Patterns — evaluated in this order, first match wins
# ---------------------------------------------------------------------------

# ``\b`` before the family name only: the suffix varies wildly (``-3.0-only``,
# ``+``, ``-or-later``) and anchoring the tail would reject the variants this
# module exists to catch.
_AGPL_RE: Final[re.Pattern[str]] = re.compile(r"\bAGPL", re.IGNORECASE)
_LGPL_RE: Final[re.Pattern[str]] = re.compile(r"\bLGPL", re.IGNORECASE)
# File- or library-scoped reciprocal licenses that are not part of the GPL
# family. Both boundaries anchored — these are whole tokens, and an unanchored
# ``CPL`` would match inside unrelated identifiers.
_WEAK_FAMILY_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(MPL|EPL|CDDL|CPL|OSL|EUPL|CeCILL|Sleepycat)\b", re.IGNORECASE
)
_GPL_RE: Final[re.Pattern[str]] = re.compile(r"\bGPL", re.IGNORECASE)

# A license id is a short token; a name is a sentence at worst. Anything past
# this is not a license identifier, and classifying it would only spend time on
# input that cannot produce a meaningful answer. Bounded here rather than
# trusted from the caller, because DB rows reach this function directly.
MAX_IDENTIFIER_LENGTH: Final = 512


def classify_license_class(value: str | None) -> str:
    """Classify ONE license id / name / expression term into a strength class.

    Returns one of :data:`LICENSE_CLASS_VALUES`. ``None``, empty, over-long and
    unrecognised input all resolve to ``uncategorized`` — never ``permissive``.

    This deliberately ignores SPDX operators. A caller holding a compound
    expression gets the *worst* term's class, which is right for a strength
    label and wrong for a compatibility verdict; that is why
    :mod:`services.license_conflict` parses the operators instead of calling
    this on the whole string.
    """
    if not value:
        return UNCATEGORIZED
    identifier = value.strip()
    if not identifier or len(identifier) > MAX_IDENTIFIER_LENGTH:
        return UNCATEGORIZED
    if identifier.upper() in PERMISSIVE_IDS:
        return PERMISSIVE
    if _AGPL_RE.search(identifier):
        return NETWORK_COPYLEFT
    if _LGPL_RE.search(identifier):
        return WEAK_COPYLEFT
    if _WEAK_FAMILY_RE.search(identifier):
        return WEAK_COPYLEFT
    if _GPL_RE.search(identifier):
        return STRONG_COPYLEFT
    return UNCATEGORIZED


def worst_license_class(values: Iterable[str | None]) -> str:
    """The strongest class across a component's license strings.

    A component with no license information is ``uncategorized`` — the same
    rule as an unrecognised one, and for the same reason.
    """
    classes = [classify_license_class(value) for value in values if value and value.strip()]
    if not classes:
        return UNCATEGORIZED
    return max(classes, key=lambda name: CLASS_RANK[name])


__all__ = [
    "CLASS_RANK",
    "LICENSE_CLASS_VALUES",
    "MAX_IDENTIFIER_LENGTH",
    "NETWORK_COPYLEFT",
    "PERMISSIVE",
    "PERMISSIVE_IDS",
    "STRONG_COPYLEFT",
    "UNCATEGORIZED",
    "WEAK_COPYLEFT",
    "classify_license_class",
    "worst_license_class",
]
