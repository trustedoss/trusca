"""
License-name normalization — Phase E (P2-7 catalog expansion).

Ports BomLens ``docker/lib/spdx-normalize.jq`` to Python and extends it with the
Phase E catalog aliases. Maps a free-text license *name* to a canonical SPDX id
for the well-known aliases scanners emit when a component declares its license by
name rather than by SPDX id — the cdxgen ``{"license": {"name": "..."}}`` shape.

Why this exists
---------------
``tasks/scan_source._extract_spdx_ids`` skips ``name``-only license entries: a
free-text name is not a valid ``licenses.spdx_id`` and persisting it verbatim
would pollute the license filter / distribution card with a dozen spellings of
the same license. But skipping also means a component whose SBOM carries only
``"Apache License, Version 2.0"`` (no ``id``) classifies as *unknown*. This
module recovers the common cases: a recognized alias is mapped to its SPDX id
(which then classifies through ``_LICENSE_CATEGORY_DEFAULTS``); an unrecognized
name returns ``None`` and the caller keeps its skip behaviour — we never guess a
single id for a name we do not confidently recognize. This mirrors the jq's
"return the string unchanged" safety valve: a wrong-but-confident rewrite (e.g.
mapping an unfamiliar name onto a specific SPDX id) is worse than ``unknown``.

Compound expressions (``X OR Y`` / ``X AND Y``) are deliberately NOT collapsed
to one id — the check sits *after* the "or later" copyleft rules so that a real
"or later" grant is not mistaken for a disjunction, exactly as the jq orders it.

Safety
------
The input is untrusted scanner output. Normalization lowercases, collapses the
alias-separator class ``[ ,._/-]+`` to a single space, and matches against a
fixed set of precompiled, linear-time patterns (simple ``.*`` between literals —
no nested quantifiers, so no catastrophic backtracking). The scan is bounded to
the first ``_MAX_LEN`` characters.
"""

from __future__ import annotations

import re

# Bound the untrusted input before any regex work; real license names are short.
_MAX_LEN = 200

# Alias separator class (mirrors the jq ``gsub("[ ,._/-]+"; " ")``): spaces,
# commas, dots, underscores, slashes and dashes all read as one word gap so
# "Apache-2.0", "Apache_2.0" and "Apache 2.0" canonicalize identically.
_SEP_RE = re.compile(r"[ ,._/-]+")

# Sentinel: a matched pattern that means "this is a compound expression, do not
# guess a single SPDX id" — the caller treats it exactly like no match (skip).
_COMPOUND = object()

# Ordered (pattern, target) rules. Order matters: version-specific and
# "or later" copyleft rules come first, then the compound guard, then the
# permissive / weak-copyleft aliases with their own internal ordering
# (more specific before more general — MIT-0 before MIT, ShareAlike before
# plain Attribution, zlib/libpng before libpng, BSD-4 before BSD-3/2).
_RULES: tuple[tuple[re.Pattern[str], object], ...] = tuple(
    (re.compile(pat), target)
    for pat, target in (
        # --- AGPL (before GPL: "affero general public" contains "general public") ---
        (r"affero general public.*3.*later", "AGPL-3.0-or-later"),
        (r"affero general public.*3", "AGPL-3.0-only"),
        # --- LGPL ---
        (r"(lesser|library) general public.*2 1.*later", "LGPL-2.1-or-later"),
        (r"(lesser|library) general public.*2 1", "LGPL-2.1-only"),
        (r"(lesser|library) general public.*3.*later", "LGPL-3.0-or-later"),
        (r"(lesser|library) general public.*3", "LGPL-3.0-only"),
        # --- GPL ---
        (r"general public.*2.*later", "GPL-2.0-or-later"),
        (r"general public.*2 0|general public.*v2", "GPL-2.0-only"),
        (r"general public.*3.*later", "GPL-3.0-or-later"),
        (r"general public.*3", "GPL-3.0-only"),
        # --- Compound guard (after the copyleft "or later" rules) ---
        (r" or | and ", _COMPOUND),
        # --- Apache ---
        (r"apache.*2", "Apache-2.0"),
        (r"apache.*1 1", "Apache-1.1"),
        # --- MIT family (MIT-0 before MIT; X11 handled below, after MIT) ---
        (r"mit 0|mit no attribution", "MIT-0"),
        # "MIT/X11" is the conventional shorthand for the MIT License, so pin it
        # to MIT here before the standalone X11 rule below can claim it.
        (r"mit license|^mit$|expat|mit x11|x11 mit", "MIT"),
        # --- Phase E permissive aliases ---
        (r"boost software", "BSL-1.0"),
        (r"artistic.*2", "Artistic-2.0"),
        (r"open font|\bofl\b", "OFL-1.1"),
        (r"postgresql", "PostgreSQL"),
        (r"universal permissive|\bupl\b", "UPL-1.0"),
        (r"academic free", "AFL-3.0"),
        (r"blue oak", "BlueOak-1.0.0"),
        (r"microsoft public|\bms pl\b", "MS-PL"),
        (r"microsoft reciprocal|\bms rl\b", "MS-RL"),
        (r"php license|\bphp\b.*3", "PHP-3.01"),
        (r"openssl", "OpenSSL"),
        (r"\bcurl\b", "curl"),
        (r"\bntp\b", "NTP"),
        (r"ruby license|^ruby$", "Ruby"),
        (r"\bx11\b", "X11"),
        # --- Creative Commons (ShareAlike before plain Attribution; pin 4.0) ---
        (r"creative commons attribution share.*4|\bcc by sa 4\b", "CC-BY-SA-4.0"),
        (r"creative commons attribution.*4|\bcc by 4\b", "CC-BY-4.0"),
        # --- Eclipse ---
        (r"eclipse distribution|^edl ", "BSD-3-Clause"),
        (r"eclipse public.*2", "EPL-2.0"),
        (r"eclipse public.*1", "EPL-1.0"),
        # --- BSD (zlib/libpng first so it wins over libpng; 4 before 3 before 2) ---
        (r"\bzlib\b", "Zlib"),
        (r"libpng", "Libpng"),
        (r"bsd.*4", "BSD-4-Clause"),
        (r"bsd.*3", "BSD-3-Clause"),
        (r"bsd.*2", "BSD-2-Clause"),
    )
)


def _canon(raw: str) -> str:
    return _SEP_RE.sub(" ", raw[:_MAX_LEN].lower()).strip()


def normalize_license_name(raw: str | None) -> str | None:
    """Return the canonical SPDX id for a recognized free-text alias, else ``None``.

    ``None`` is returned for an empty/None input, a compound expression, or a
    name that matches no known alias — in every case the caller keeps its
    existing skip behaviour rather than persisting an uncertain id.
    """
    if not isinstance(raw, str) or not raw:
        return None
    n = _canon(raw)
    if not n:
        return None
    for pattern, target in _RULES:
        if pattern.search(n):
            return None if target is _COMPOUND else target  # type: ignore[return-value]
    return None


__all__ = ["normalize_license_name"]
