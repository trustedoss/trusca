"""
Manifest-remediation adapters — v2.2 2.2-b2 (npm first).

This package turns the *upgrade recommendations* computed by 2.2-a3
(:mod:`services.upgrade_recommendation`) into a concrete EDITED dependency
manifest plus a structured, byte-minimal diff. It is the COMPUTE half of the
"finding → PR" pipeline:

  * b2 (this) — given a parsed manifest + a set of ``{package → target}`` bumps,
    return the edited manifest text and a per-package before/after diff. PURE,
    no DB, no network, no GitHub. Consumed by
    :mod:`services.remediation_service` for the dry-run endpoint.
  * b3 (later) — open the actual GitHub PR with the edited manifest and persist
    the remediation attempt. NOT part of b2.

Design
------
The dispatch contract mirrors the rest of ``integrations/`` (one adapter module
per ecosystem behind a tiny public surface, so the service layer never reaches
into ecosystem-specific parsing). npm is implemented now; pip / maven adapters
slot in beside :mod:`integrations.remediation.npm` later behind the same
:class:`ManifestEditResult` shape.

Untrusted input
---------------
A ``package.json`` is attacker-controlled (it rides in from a scanned repo or an
uploaded body). Every adapter MUST treat the manifest as hostile: bounded size,
no ``json`` parser surprises (duplicate keys, prototype-pollution keys), version
values that are not strings, and never raise a 500 into the caller. The npm
adapter returns a typed :class:`ManifestParseError` for input it refuses to edit
and *skips* (with a structured warning entry) individual entries it cannot
safely rewrite — it never partially-corrupts the manifest.
"""

from __future__ import annotations

from .base import (
    DependencyChange,
    ManifestEditResult,
    ManifestParseError,
    ManifestWarning,
    VersionBump,
)
from .npm import edit_npm_manifest

__all__ = [
    "DependencyChange",
    "ManifestEditResult",
    "ManifestParseError",
    "ManifestWarning",
    "VersionBump",
    "edit_npm_manifest",
]
