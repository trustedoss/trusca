"""
Bundled SPDX license full texts — Phase B (NOTICE license-text sections).

The ``services/license_texts/`` directory vendors the standard full text for
every SPDX id the obligation catalog covers (``<spdx-id>.txt``, 52 files:
21 mirrored from BomLens ``docker/lib/licenses/`` + 31 from the official SPDX
``license-list-data`` set — the Phase B 11 plus the Phase E catalog expansion).
This module is the read-only loader the NOTICE
generator uses to append a "License Texts" section — offline, no network at
notice time, mirroring BomLens ``generate-notice.sh``.

Contract (guarded by ``tests/unit/test_catalog_contracts.py``): the set of
bundled ``*.txt`` stems must equal ``obligation_catalog.catalog_spdx_ids()``.
A catalog id without a text (or an orphan text file) is the same vocabulary
drift class as H-5.

Safety
------
``license_text`` receives SPDX ids that ultimately originate from scanner
output (``License.spdx_id`` rows are created from cdxgen metadata), so even
though the NOTICE path only looks up ids we split out of our own DB column,
the id is treated as untrusted: a strict character allowlist rejects anything
that could steer the path lookup (``/``, ``\\``, ``..``-as-prefix, NUL, ...)
BEFORE any filesystem access, and the lookup itself is a set-membership check
against the directory snapshot — an unknown id never touches the disk. File
contents are cached in a module-level dict (bounded by the vendored file
count; this is static package data, not configuration, so module-level
caching does not violate the runtime-``os.getenv()`` rule).
"""

from __future__ import annotations

import re
from pathlib import Path

from services.obligation_catalog import _split_compound

# The vendored texts live next to this file (this module IS the package
# ``services.license_texts``; the ``*.txt`` files are its package data).
_TEXT_DIR = Path(__file__).resolve().parent

# Strict allowlist for a single SPDX id: starts alphanumeric, then SPDX's
# id vocabulary (alnum / dot / dash / plus). Bounded at 64 chars — the
# ``licenses.spdx_id`` column width. Anything else (path separators, ``..``
# prefixes, control chars, unicode tricks) is rejected before any I/O.
_SAFE_SPDX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$")

# Directory snapshot + per-file content cache. Both are bounded by the
# vendored file set (52 today) — misses are answered from the snapshot set,
# so hostile ids can never grow the cache.
_available_ids: frozenset[str] | None = None
_text_cache: dict[str, str] = {}


def is_safe_spdx_id(spdx_id: str | None) -> bool:
    """True when *spdx_id* matches the strict SPDX-id character allowlist.

    Shared with the NOTICE renderer (security-reviewer F-2): a scan-derived id
    that fails this check must be scrubbed before being interpolated into the
    plain-text ``----- <id> -----`` section divider, or a hostile id could
    forge section boundaries in the downloaded document.
    """
    return bool(spdx_id and _SAFE_SPDX_ID_RE.match(spdx_id))


def bundled_spdx_ids() -> frozenset[str]:
    """All SPDX ids with a bundled full text (the ``*.txt`` stems)."""
    global _available_ids
    if _available_ids is None:
        _available_ids = frozenset(p.stem for p in _TEXT_DIR.glob("*.txt"))
    return _available_ids


def license_text(spdx_id: str | None) -> str | None:
    """The bundled full text for a SINGLE SPDX id, or ``None``.

    Exact-match lookup (no fuzzy / case-insensitive matching — mirrors
    ``obligation_catalog.get_license_obligations``). Returns ``None`` for a
    compound expression, an unknown id, or anything failing the character
    allowlist. Never raises for hostile input.
    """
    if not is_safe_spdx_id(spdx_id):
        return None
    if spdx_id not in bundled_spdx_ids():
        return None
    cached = _text_cache.get(spdx_id)
    if cached is None:
        cached = (_TEXT_DIR / f"{spdx_id}.txt").read_text(encoding="utf-8")
        _text_cache[spdx_id] = cached
    return cached


def spdx_ids_for_expression(expression: str | None) -> list[str]:
    """Split an SPDX expression into its unique operand ids, order-preserving.

    A simple id passes through as ``[id]``; a compound (``A OR B``,
    ``A WITH exc``) is split with :func:`obligation_catalog._split_compound`
    — the SAME splitter ``obligations_for`` uses, so the NOTICE's license-text
    section and its obligation rows always agree on what the expression's
    operands are. Empty tokens are dropped; duplicates are removed.
    """
    if not expression:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tok in _split_compound(expression):
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def texts_for_expression(expression: str | None) -> list[tuple[str, str]]:
    """``(operand_id, full_text)`` pairs for every bundled operand of *expression*.

    Operands without a bundled text are skipped — the caller keeps the
    license entry's ``reference_url`` as the fallback pointer to the text.
    """
    pairs: list[tuple[str, str]] = []
    for spdx_id in spdx_ids_for_expression(expression):
        text = license_text(spdx_id)
        if text is not None:
            pairs.append((spdx_id, text))
    return pairs


__all__ = [
    "bundled_spdx_ids",
    "is_safe_spdx_id",
    "license_text",
    "spdx_ids_for_expression",
    "texts_for_expression",
]
