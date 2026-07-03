"""
AI-relevant license review-flag classifier (Phase D / D1).

This is a Python port of BomLens' ``license-flags.jq`` — the single source of
truth shared, on the shell side, by ``generate-notice.sh`` (the "License review
needed" section) and ``normalize-sbom.sh`` (the ``bomlens:licenseReview``
component property the web UI badges). We port it verbatim so the portal's
NOTICE renderer and the Licenses tab surface the *same* class the upstream
tooling does.

Original jq (preserved as the behavioural oracle)::

    # license-flags.jq — classify a license id/name into an AI-relevant
    # restriction class that a human must review.
    def license_flag($s):
      (($s // "") | ascii_downcase | gsub("[ ._/-]+"; " ")) as $n |
      # behavioral-use regex (wrapped here for width; one alternation in jq):
      #   openrail|\brail\b|responsible ai|community license
      #     |\bllama|\bgemma\b|falcon llm
      # non-commercial regex:  cc by nc|non ?commercial
      if   ($n | test(<behavioral-use regex>)) then "behavioral-use"
      elif ($n | test(<non-commercial regex>)) then "non-commercial"
      else "" end;

Scope is deliberately narrow. We flag the licenses that ordinary OSS-compliance
tooling does NOT already make obvious and that the AI guidance (OpenChain 3.5,
G7) calls out:

  * behavioral-use restrictions — RAIL / OpenRAIL, and the Llama / Gemma /
    Falcon community licenses;
  * non-commercial terms — CC-BY-NC and friends.

Permissive (MIT, Apache-2.0) and ordinary copyleft (GPL, LGPL) are
**intentionally NOT flagged**, so a normal software scan's NOTICE is unchanged.

Philosophy: this classifier SURFACES the class only. Whether a given
restriction actually applies to a given use is a human / legal judgement — the
flag is a prompt for review, never a verdict.

Value mapping vs. the jq original
---------------------------------
The jq function returns the hyphenated tokens ``"behavioral-use"`` /
``"non-commercial"``. The portal's ``licenses.review_flag`` column stores the
snake_case forms ``"behavioral_use"`` / ``"non_commercial"`` (migration 0036),
which is what this module returns. :data:`REVIEW_FLAG_VALUES` is the single
source of truth for those two tokens — the DB persistence layer, the API schema
Literal, and any future frontend mirror all reconcile against it (testing
standard §2: same vocabulary in ≥2 places ⇒ a contract test is mandatory).
"""

from __future__ import annotations

import re
from typing import Final

# Single source of truth for the two persisted review-flag tokens. Mirrored by
# the API schema Literal (``schemas.license_detail.ReviewFlag``) and reconciled
# in ``tests/unit/test_catalog_contracts.py``. Order is behavioral-first, which
# matches the jq precedence (behavioral-use wins when a name matches both).
REVIEW_FLAG_VALUES: Final[tuple[str, str]] = ("behavioral_use", "non_commercial")

# Normalisation: lowercase, then collapse any run of space / dot / underscore /
# slash / hyphen into a single space. Mirrors jq's
# ``ascii_downcase | gsub("[ ._/-]+"; " ")`` byte-for-byte so e.g.
# "OpenRAIL-M" → "openrail m", "CC-BY-NC-4.0" → "cc by nc 4 0". The class keeps
# ``-`` last so it stays a literal, not a range.
_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(r"[ ._/-]+")

# Behavioral-use restrictions (RAIL family + AI community licenses). Pre-compiled
# once at import; the ``\b`` anchors survive normalisation because separators
# have already become spaces.
_BEHAVIORAL_USE_RE: Final[re.Pattern[str]] = re.compile(
    r"openrail|\brail\b|responsible ai|community license|\bllama|\bgemma\b|falcon llm"
)

# Non-commercial terms. ``non ?commercial`` matches both "noncommercial" and
# "non commercial" (the normaliser turns "non-commercial" into the latter).
_NON_COMMERCIAL_RE: Final[re.Pattern[str]] = re.compile(r"cc by nc|non ?commercial")


def _normalize(value: str | None) -> str:
    """Lowercase + separator-collapse a single id/name token.

    None / empty is safe — returns ``""`` (matches jq's ``$s // ""``). The
    result is NOT stripped (neither is jq's), which is harmless: the regexes
    never anchor on start/end of string, only on ``\\b`` word boundaries.
    """
    if not value:
        return ""
    return _SEPARATOR_RE.sub(" ", value.lower())


def classify_review_flag(spdx_id: str | None, name: str | None) -> str | None:
    """Classify a license into an AI review-flag class, or ``None`` if out of scope.

    Both the SPDX id and the human name are examined; a match on *either* wins,
    because upstream metadata is inconsistent about which field carries the
    tell-tale token (cdxgen may put "LLAMA-2" in ``id`` or "Llama 2 Community
    License" in ``name``). Behavioral-use takes precedence over non-commercial
    when both would match, mirroring the jq ``if/elif`` order.

    Returns one of :data:`REVIEW_FLAG_VALUES` (``"behavioral_use"`` /
    ``"non_commercial"``) or ``None``. Permissive and ordinary-copyleft
    licenses are intentionally out of scope and return ``None``.
    """
    normalized = (_normalize(spdx_id), _normalize(name))

    # Behavioral-use first (jq precedence): a name that trips both patterns is
    # reported as behavioral-use.
    if any(_BEHAVIORAL_USE_RE.search(text) for text in normalized if text):
        return "behavioral_use"
    if any(_NON_COMMERCIAL_RE.search(text) for text in normalized if text):
        return "non_commercial"
    return None


__all__ = [
    "REVIEW_FLAG_VALUES",
    "classify_review_flag",
]
