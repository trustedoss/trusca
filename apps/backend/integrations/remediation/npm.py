"""
npm manifest-remediation adapter — v2.2 2.2-b2.

Given a ``package.json`` text and a set of :class:`~integrations.remediation.base.VersionBump`
targets, produce the EDITED ``package.json`` text + a structured diff, rewriting
only the dependency ranges that need to change to satisfy each target.

Range-rewrite policy (documented, tested)
-----------------------------------------
We PRESERVE the user's range-operator style and rewrite only the version number
inside it. The recommendation from a3 is the minimum-safe *exact* version; we map
it onto the existing operator:

  ============================  ===================  ===================
  existing range                target ``1.3.0``     result
  ============================  ===================  ===================
  ``^1.2.3`` (caret)            1.3.0                ``^1.3.0``
  ``~1.2.3`` (tilde)            1.3.0                ``~1.3.0``
  ``1.2.3``  (pinned/exact)     1.3.0                ``1.3.0``
  ``>=1.2.3`` (single relop)    1.3.0                ``>=1.3.0``
  ``v1.2.3`` (v-prefix)         1.3.0                ``v1.3.0``  (prefix kept)
  ``1.2.x`` / ``1.x``           1.3.0                ``^1.3.0``  (widen → caret)
  ``*`` / ``""`` / ``latest``   1.3.0                left UNCHANGED + warning
  ``npm:alias@^1.2.3``          1.3.0                left UNCHANGED + warning
  ``file:`` / ``git+...`` / URL 1.3.0                left UNCHANGED + warning
  compound ``>=1 <2`` / ``||``  1.3.0                left UNCHANGED + warning
  ============================  ===================  ===================

Rationale: a single-operator range maps cleanly onto a "bump the number, keep
the operator" rewrite. Anything that is NOT a simple ``[operator]version`` (a
compound/OR range, an alias, a non-registry source, a wildcard) is *flagged and
left alone* rather than guessed — silently turning ``npm:foo@^1`` or ``git+ssh``
into a registry pin would be a correctness/security regression. A wildcard
(``*`` / ``""``) is intentionally untouched: the range already permits the fix,
so there is nothing to rewrite (we still warn so the reviewer knows it was seen).

Idempotency / minimality
------------------------
If the existing range already satisfies the target (its lower bound is ``>=``
target for caret/tilde/relop, or a pinned version is ``==`` target), we make NO
edit and emit an ``already_satisfied`` warning. Re-running the adapter on its own
output is a no-op. We only ever change packages that have a bump; unrelated
entries are byte-for-byte untouched.

Format preservation (byte-minimal diff)
---------------------------------------
We DO NOT ``json.loads`` → mutate → ``json.dumps``: that reflows whitespace, drops
comments-as-data ordering, normalises number formatting, and would produce a noisy
diff in the eventual PR. Instead we parse for *validation + locating* (so we know
the manifest is well-formed and which sections/keys exist and their values) and
then perform a TARGETED textual replacement of just the version string token,
matched by a precise per-entry regex anchored on the (JSON-encoded) key and the
exact current value. Everything else — indentation, key order, trailing newline,
CRLF — is preserved exactly. The only bytes that change are the version numbers
we bumped.

Untrusted input — ``package.json`` is hostile
---------------------------------------------
  * Bounded size (``NPM_MANIFEST_MAX_BYTES``, default 1 MiB) — refuse oversized.
  * ``json.loads`` with an ``object_pairs_hook`` that (a) detects duplicate keys
    (we collapse to last-wins, the JSON spec's de-facto behaviour, and warn) and
    (b) is immune to ``__proto__`` / ``constructor`` / ``prototype`` keys (Python
    dicts have no prototype chain, but we still never treat such keys as a package
    to edit — they are data, matched literally if at all).
  * Non-string version values (arrays / numbers / null / objects) are SKIPPED with
    a ``value_not_string`` warning — never coerced.
  * A non-object root, invalid JSON, or a manifest with no dependency section is a
    whole-manifest :class:`ManifestParseError` (the service maps it to a 4xx).
  * BOM and CRLF are tolerated and preserved.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import structlog

from services.upgrade_recommendation import compare_versions, parse_version

from .base import (
    DependencyChange,
    ManifestEditResult,
    ManifestParseError,
    ManifestWarning,
    VersionBump,
)

log = structlog.get_logger("remediation.npm")

# The four npm dependency blocks we will edit. Order is the conventional one;
# a package may appear in more than one (e.g. dep + peerDep) — we edit each.
_DEP_SECTIONS: tuple[str, ...] = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)

# Byte-for-byte BOM marker (UTF-8). Preserved on output if present on input.
_BOM = "﻿"

# A simple npm range we can rewrite: an optional operator prefix, an optional
# leading ``v``, then a dotted numeric core (the version). We deliberately keep
# this strict — anything it does not match is left alone (flagged), never guessed.
#   group "op"      → "" | "^" | "~" | ">=" | "<=" | ">" | "<" | "="
#   group "vprefix" → "" | "v" | "V"
#   group "core"    → the version token (digits/dots/…); validated by a3 parser
_SIMPLE_RANGE_RE = re.compile(
    r"^(?P<op>\^|~|>=|<=|>|<|=)?(?P<vprefix>[vV])?(?P<core>[0-9][0-9A-Za-z.\-+]*)$"
)

# An ``x``-range (``1.2.x`` / ``1.x`` / ``1.2.*``) — we widen these to a caret on
# the target so the rewritten range still permits in-range patch/minor upgrades.
_X_RANGE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?(\.[xX*])?$|^[0-9]+\.[xX*]$")


def npm_manifest_max_bytes() -> int:
    """Max accepted ``package.json`` size. Read at call time (rule #11).

    Default 1 MiB — a real ``package.json`` is a few KiB; a megabyte already
    implies a hostile / generated file we will not edit.
    """
    return int(os.getenv("NPM_MANIFEST_MAX_BYTES", str(1024 * 1024)))


class _DuplicateAwareDecoder:
    """``object_pairs_hook`` that records duplicate keys and is pollution-safe.

    JSON allows duplicate object keys; ``json.loads`` keeps the LAST by default.
    We mirror that (last-wins) but RECORD that a collapse happened so the dry-run
    can warn — a manifest with ``"dependencies"`` twice is suspicious and the PR
    reviewer should know which one we acted on.

    Prototype-pollution keys (``__proto__`` etc.) are kept as ordinary string
    keys in a plain ``dict`` — Python dicts have no prototype chain so there is
    no pollution vector, and we never *treat* such a key as a package to bump.
    """

    def __init__(self) -> None:
        self.had_duplicate = False

    def __call__(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in seen:
                self.had_duplicate = True
            seen.add(key)
            out[key] = value  # last-wins, matching json's default
        return out


def _bump_for_target(operator: str, vprefix: str, target_parsed: Any) -> str:
    """Rebuild a simple range string for ``target`` keeping ``operator``/prefix.

    ``target_parsed`` is an a3 :class:`ParsedVersion`; we emit its ORIGINAL raw
    string (the published form) under the preserved operator + ``v`` prefix.
    """
    return f"{operator}{vprefix}{target_parsed.raw}"


def _satisfies_target(operator: str, current_parsed: Any, target_parsed: Any) -> bool:
    """True iff the existing range already covers ``target`` (→ no edit needed).

    The semantics we care about for "is this already fixed?":
      * pinned / exact (no operator, or ``=``): satisfied iff current == target.
      * caret / tilde / ``>=`` / ``>``: satisfied iff current >= target — the
        range's LOWER bound already excludes the vulnerable versions. (We do not
        attempt full SemVer caret-ceiling math; a lower bound at/above the fix is
        sufficient to call it "no bump required" and keeps the rewrite minimal.)
      * ``<`` / ``<=``: NEVER satisfied by a forward fix — an upper-bounded range
        does not pin you onto the fix, so we still rewrite the number.
    """
    cmp = compare_versions(current_parsed, target_parsed)
    if operator in ("", "="):
        return cmp == 0
    if operator in ("^", "~", ">=", ">"):
        return cmp >= 0
    # "<" / "<=" — an upper bound never asserts you are on the fix.
    return False


def _rewrite_entry(
    package: str,
    section: str,
    current_range: str,
    target: str,
) -> tuple[str | None, ManifestWarning | None, bool]:
    """Decide the new range for one entry.

    Returns ``(new_range, warning, changed)``:
      * ``new_range`` is ``None`` when we leave the entry untouched (the caller
        emits no :class:`DependencyChange` for an untouched non-edit), or a string
        when we computed a (possibly identical) replacement.
      * ``warning`` is an optional :class:`ManifestWarning` (skip reason / note).
      * ``changed`` is whether the bytes actually differ.
    """
    target_parsed = parse_version(target)
    if target_parsed is None:
        return (
            None,
            ManifestWarning(
                code="target_unparseable",
                package=package,
                detail=f"recommended version {target!r} is not a comparable version",
            ),
            False,
        )

    stripped = current_range.strip()

    # Wildcards / empty / dist-tags — the range already permits the fix (``*``,
    # ``""``) or is a tag we cannot reason about (``latest``). Leave untouched.
    if stripped in ("", "*", "x", "X", "latest", "next") or stripped.lower() == "latest":
        return (
            None,
            ManifestWarning(
                code="unparseable_range",
                package=package,
                detail=(
                    f"range {current_range!r} in {section} is a wildcard/dist-tag; "
                    "left unchanged (it already permits the fix or is non-numeric)"
                ),
            ),
            False,
        )

    # Non-registry / alias / compound sources we refuse to rewrite.
    lowered = stripped.lower()
    if (
        "||" in stripped  # OR range
        or " " in stripped  # compound (">=1 <2") or "git+ssh ..." junk
        or lowered.startswith(("npm:", "file:", "link:", "workspace:", "git", "http"))
        or "://" in stripped
    ):
        return (
            None,
            ManifestWarning(
                code="unparseable_range",
                package=package,
                detail=(
                    f"range {current_range!r} in {section} is a compound/alias/"
                    "non-registry source; left unchanged (cannot safely rewrite)"
                ),
            ),
            False,
        )

    # x-range (1.2.x / 1.x) — widen to caret on the target.
    if _X_RANGE_RE.match(stripped):
        new_range = f"^{target_parsed.raw}"
        return (new_range, None, new_range != current_range)

    match = _SIMPLE_RANGE_RE.match(stripped)
    if match is None:
        return (
            None,
            ManifestWarning(
                code="unparseable_range",
                package=package,
                detail=(
                    f"range {current_range!r} in {section} is not a simple " "range; left unchanged"
                ),
            ),
            False,
        )

    operator = match.group("op") or ""
    vprefix = match.group("vprefix") or ""
    core = match.group("core")
    current_parsed = parse_version(core)
    if current_parsed is None:
        return (
            None,
            ManifestWarning(
                code="unparseable_range",
                package=package,
                detail=f"version {core!r} in {section} range did not parse; left unchanged",
            ),
            False,
        )

    if _satisfies_target(operator, current_parsed, target_parsed):
        return (
            None,
            ManifestWarning(
                code="already_satisfied",
                package=package,
                detail=(
                    f"{section} range {current_range!r} already satisfies " f"{target!r}; no bump"
                ),
            ),
            False,
        )

    new_range = _bump_for_target(operator, vprefix, target_parsed)
    return (new_range, None, new_range != current_range)


def _find_section_span(text: str, section: str) -> tuple[int, int] | None:
    """Return the ``[start, end)`` char span of a TOP-LEVEL section's object value.

    Finds ``"section"`` then its ``{ ... }`` value and returns the span covering
    that brace-balanced object, so a value replacement inside it can never touch a
    same-named key in a DIFFERENT block (e.g. ``__proto__`` / ``resolutions`` /
    a duplicate). Brace matching is string-literal-aware (braces inside a JSON
    string do not count). Returns ``None`` if the section is not found as a
    top-level key with an object value.

    "Top-level" is approximated by depth tracking: we only accept a ``"section":``
    occurrence whose key sits at object depth 1 (the manifest root). This keeps a
    nested ``"dependencies"`` (inside some other tool's config block) from being
    mistaken for the real one.

    When the section appears MORE THAN ONCE at depth 1 (a malformed duplicate),
    we return the LAST span — matching ``json.loads``'s last-wins semantics, so we
    edit exactly the block the parser (and therefore our ``value``) came from.
    """
    enc_key = json.dumps(section)
    # Walk the text tracking brace depth + string state so we can find the
    # ``"section"`` key at depth 1 and then its object value's matched braces.
    n = len(text)
    i = 0
    depth = 0
    in_str = False
    escape = False
    found: tuple[int, int] | None = None
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            # Check if this opens the section key at depth 1.
            if depth == 1 and text.startswith(enc_key, i):
                after = i + len(enc_key)
                # Skip whitespace then require a ':' then the object's '{'.
                j = after
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j < n and text[j] == ":":
                    j += 1
                    while j < n and text[j] in " \t\r\n":
                        j += 1
                    if j < n and text[j] == "{":
                        end = _match_braces(text, j)
                        if end is not None:
                            found = (j, end)  # last-wins
            in_str = True
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return found


def _match_braces(text: str, open_idx: int) -> int | None:
    """Given ``text[open_idx] == '{'``, return the index AFTER its matching '}'.

    String-literal-aware so braces inside strings are ignored. Returns ``None``
    on an unbalanced object (should not happen for json.loads-valid input).
    """
    n = len(text)
    depth = 0
    in_str = False
    escape = False
    i = open_idx
    while i < n:
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _replace_value_token(
    text: str, section: str, key: str, current_value: str, new_value: str
) -> str | None:
    """Replace the ``"key": "current_value"`` pair INSIDE ``section``'s object.

    Format-preserving + SECTION-SCOPED: we locate the section's brace-balanced
    object span first, then match the JSON-encoded key followed by ``:`` (with
    arbitrary whitespace) and the exact JSON-encoded current value WITHIN that
    span, substituting only the value's encoded form. This guarantees a same-named
    key in another block (``__proto__`` / a duplicate / ``resolutions``) is never
    touched. Returns the new text, or ``None`` if the exact pair was not found in
    the section (the caller then skips — never a partial corrupt edit).
    """
    span = _find_section_span(text, section)
    if span is None:
        return None
    start, end = span
    segment = text[start:end]

    enc_key = json.dumps(key)
    enc_cur = json.dumps(current_value)
    enc_new = json.dumps(new_value)
    # ``"key"`` then ws ``:`` ws then the exact encoded current value. The value
    # is matched literally (escaped) so a value containing regex metacharacters
    # cannot break the pattern.
    pattern = re.compile(re.escape(enc_key) + r"(\s*:\s*)" + re.escape(enc_cur))

    def _sub(m: re.Match[str]) -> str:
        return enc_key + m.group(1) + enc_new

    new_segment, count = pattern.subn(_sub, segment, count=1)
    if count == 0:
        return None
    return text[:start] + new_segment + text[end:]


def edit_npm_manifest(
    manifest_text: str,
    bumps: list[VersionBump] | tuple[VersionBump, ...],
) -> ManifestEditResult:
    """Apply ``bumps`` to a ``package.json`` text; return edited text + diff.

    PURE — no DB, no network. NEVER raises except :class:`ManifestParseError`
    for a whole-manifest refusal (the service maps it to a 4xx).
    """
    if not isinstance(manifest_text, str):
        raise ManifestParseError("not_text", "manifest must be text")

    raw = manifest_text
    encoded_len = len(raw.encode("utf-8", errors="ignore"))
    if encoded_len > npm_manifest_max_bytes():
        raise ManifestParseError(
            "too_large",
            f"package.json is {encoded_len} bytes, over the limit",
        )

    # Preserve a BOM if present; json.loads tolerates it but we keep the byte.
    had_bom = raw.startswith(_BOM)
    body = raw[len(_BOM) :] if had_bom else raw

    decoder = _DuplicateAwareDecoder()
    try:
        parsed = json.loads(body, object_pairs_hook=decoder)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ManifestParseError("invalid_json", "package.json is not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise ManifestParseError("not_object", "package.json root is not a JSON object")

    present_sections = [s for s in _DEP_SECTIONS if isinstance(parsed.get(s), dict)]
    if not present_sections:
        raise ManifestParseError(
            "no_dependency_sections",
            "package.json has no dependencies/devDependencies/optional/peer block",
        )

    warnings: list[ManifestWarning] = []
    if decoder.had_duplicate:
        warnings.append(
            ManifestWarning(
                code="duplicate_keys_collapsed",
                package=None,
                detail="duplicate object keys present; collapsed to last-wins per JSON spec",
            )
        )

    # Index requested bumps by package name (last-wins if a caller passes dups).
    bump_by_pkg: dict[str, VersionBump] = {b.package: b for b in bumps}
    requested = set(bump_by_pkg)
    matched: set[str] = set()

    changes: list[DependencyChange] = []
    edited_text = body  # we edit the de-BOM'd body; re-attach BOM at the end

    for section in present_sections:
        block = parsed[section]
        for key, value in block.items():
            if key not in bump_by_pkg:
                continue
            # Never treat a pollution-style or non-string key as editable beyond
            # an exact match; ``key in bump_by_pkg`` already gates this, but a
            # non-string version VALUE must be skipped.
            matched.add(key)
            bump = bump_by_pkg[key]
            if not isinstance(value, str):
                warnings.append(
                    ManifestWarning(
                        code="value_not_string",
                        package=key,
                        detail=(
                            f"{section}[{key!r}] value is "
                            f"{type(value).__name__}, not a version string; skipped"
                        ),
                    )
                )
                continue

            new_range, warning, changed = _rewrite_entry(key, section, value, bump.target)
            if warning is not None:
                warnings.append(warning)
            if new_range is None or not changed:
                # already satisfied / unrewritable / identical → no text edit.
                continue

            replaced = _replace_value_token(edited_text, section, key, value, new_range)
            if replaced is None:
                # The exact key/value pair was not found verbatim (e.g. it lived
                # only in a duplicate-collapsed block, or unusual encoding). Skip
                # rather than corrupt — surface it as a warning.
                warnings.append(
                    ManifestWarning(
                        code="unparseable_range",
                        package=key,
                        detail=(
                            f"could not locate {key!r}:{value!r} in {section} for a "
                            "byte-safe edit; left unchanged"
                        ),
                    )
                )
                continue
            edited_text = replaced
            changes.append(
                DependencyChange(
                    package=key,
                    section=section,
                    before=value,
                    after=new_range,
                    changed=True,
                )
            )

    # Packages that were requested but not present in any section.
    for pkg in sorted(requested - matched):
        warnings.append(
            ManifestWarning(
                code="package_not_present",
                package=pkg,
                detail="requested package is not in any dependency section; skipped",
            )
        )

    any_change = bool(changes)

    # Lockfile guidance is ALWAYS surfaced when we actually changed something:
    # we never hand-edit package-lock.json integrity hashes (b2 scope), so the
    # consumer must regenerate it via `npm install` before the PR is mergeable.
    if any_change:
        warnings.append(
            ManifestWarning(
                code="lockfile_regeneration_required",
                package=None,
                detail=(
                    "package.json was edited; run `npm install` to regenerate "
                    "package-lock.json (integrity hashes are not hand-edited)"
                ),
            )
        )

    final_text = (_BOM + edited_text) if had_bom else edited_text

    log.info(
        "npm_manifest_edited",
        requested=len(requested),
        matched=len(matched),
        changed=any_change,
        change_count=len(changes),
        warning_count=len(warnings),
    )

    return ManifestEditResult(
        edited_text=final_text,
        changed=any_change,
        changes=tuple(changes),
        warnings=tuple(warnings),
    )


__all__ = [
    "edit_npm_manifest",
    "npm_manifest_max_bytes",
]
