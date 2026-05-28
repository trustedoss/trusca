"""
Shared types for the manifest-remediation adapters (v2.2 2.2-b2).

Every ecosystem adapter (npm now; pip / maven later) speaks the SAME small
vocabulary so the service layer is ecosystem-agnostic:

  * :class:`VersionBump` — the requested ``{package → target version}`` input,
    derived by the service from the a3 upgrade recommendation.
  * :class:`DependencyChange` — one applied edit (which manifest section, the
    range before/after, and whether it actually changed).
  * :class:`ManifestWarning` — a NON-fatal note (lockfile must be regenerated,
    a requested package was not present, a value was the wrong type and was
    skipped). The dry-run surfaces these so a reviewer understands the gaps.
  * :class:`ManifestEditResult` — the adapter's output: the edited manifest text
    (or the original, unchanged, when nothing applied) plus the changes and
    warnings.
  * :class:`ManifestParseError` — the adapter refuses to edit at all (malformed
    JSON, non-object root, oversized). Carries an HTTP-ish ``reason`` the service
    maps to an RFC 7807 problem; the adapter itself never raises ``HTTPException``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Reason codes for a whole-manifest refusal (no partial edit is possible).
ManifestParseReason = Literal[
    "not_text",
    "too_large",
    "invalid_json",
    "not_object",
    "no_dependency_sections",
]

# Reason codes for a per-package skip (the rest of the manifest is still edited).
ManifestWarningCode = Literal[
    "package_not_present",
    "value_not_string",
    "unparseable_range",
    "target_unparseable",
    "already_satisfied",
    "lockfile_regeneration_required",
    "lockfile_not_edited",
    "duplicate_keys_collapsed",
]


@dataclass(frozen=True)
class VersionBump:
    """A requested bump for one package: rewrite its range to satisfy ``target``.

    ``package`` is the ecosystem-canonical name (for npm the scoped name, e.g.
    ``@scope/pkg``). ``target`` is the recommended version string from a3
    (e.g. ``1.3.0``). ``current`` is advisory context for the dry-run output
    (the version the scan saw); it does NOT drive the rewrite.
    """

    package: str
    target: str
    current: str | None = None


@dataclass(frozen=True)
class DependencyChange:
    """One applied (or no-op) edit inside the manifest.

    ``section`` is the manifest block the entry lives in
    (``dependencies`` / ``devDependencies`` / ``optionalDependencies`` /
    ``peerDependencies``). ``before`` / ``after`` are the range strings; when
    ``changed`` is ``False`` the entry already satisfied the target and was left
    byte-for-byte intact.
    """

    package: str
    section: str
    before: str
    after: str
    changed: bool


@dataclass(frozen=True)
class ManifestWarning:
    """A non-fatal note attached to the edit result."""

    code: ManifestWarningCode
    package: str | None
    detail: str


@dataclass(frozen=True)
class ManifestEditResult:
    """The output of an ecosystem manifest edit.

    ``edited_text`` is the new manifest content (identical to the input when no
    change applied — callers compare/skip the PR accordingly). ``changed`` is
    ``True`` iff at least one :class:`DependencyChange` had ``changed=True``.
    """

    edited_text: str
    changed: bool
    changes: tuple[DependencyChange, ...] = ()
    warnings: tuple[ManifestWarning, ...] = ()


class ManifestParseError(Exception):
    """The adapter cannot edit the manifest at all (whole-manifest refusal).

    The service maps ``reason`` → an RFC 7807 problem; this exception never
    crosses the HTTP boundary directly.
    """

    def __init__(self, reason: ManifestParseReason, detail: str) -> None:
        super().__init__(detail)
        self.reason: ManifestParseReason = reason
        self.detail = detail


__all__ = [
    "DependencyChange",
    "ManifestEditResult",
    "ManifestParseError",
    "ManifestParseReason",
    "ManifestWarning",
    "ManifestWarningCode",
    "VersionBump",
]
