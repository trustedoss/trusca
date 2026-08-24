# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Dependency-set fingerprint for ``scans.dependency_fingerprint``.

S8 (concurrency-scaling-plan-2026-08-22.md §3.2) skips re-running cdxgen when
a commit's dependency set has not changed since the project's last successful
scan on the same ref, reusing that scan's preserved SBOM for vulnerability
re-matching instead. This module computes the fingerprint that comparison
needs. It does NOT decide whether to reuse anything: that decision (reading
two fingerprints and choosing a pipeline path) belongs to a later revision
that also has to make that choice at a point in the pipeline this module has
no visibility into. This module is one pure function: given the same
inputs, always the same digest.

What must be true of the inputs, and why:

  Lockfile / manifest hashes decide the dependency SET. This deliberately
  reuses ``services.scan_inputs.collect_manifest_inventory`` (the same
  per-ecosystem catalog of manifest AND lockfile names already gathered for
  scan provenance, ``scans.input_manifests``) rather than a narrower
  "lockfiles only" list. Several ecosystems this product scans have no
  separate lockfile at all (Maven's ``pom.xml``, a bare ``requirements.txt``,
  Gradle without dependency locking): for those, the manifest IS the
  authoritative declaration of what gets resolved, and a fingerprint that
  ignored it would call an SBOM reusable after a dependency version changed
  in exactly those ecosystems. Where a lockfile does exist (``package-lock.
  json``, ``poetry.lock``, ``go.sum``, ...) it is already in the same
  catalog, so nothing is lost by not special-casing it.

  Scanner version and scan config decide what cdxgen does WITH that
  dependency set. Two scans that saw byte-identical lockfiles can still
  produce different SBOMs if the scanner was upgraded or a toggle that
  shapes its output changed in between; the plan calls this out explicitly
  as the accuracy requirement S8 must not violate. Both are supplied by the
  caller (this module has no access to ``core.config`` or the filesystem by
  design, see "Division of responsibility" below), so the pipeline is the
  single place that decides which config keys count as "shapes SBOM output"
  and stays free to add one without this module changing.

  Vulnerability-DB state and license policy are NOT inputs here on purpose.
  Both are inputs to the vulnerability-MATCHING stage, not to SBOM
  generation, and the reuse design this fingerprint exists to support always
  re-runs matching regardless of whether the SBOM itself is reused (plan
  §3.2: "둘 다 SBOM이 아니라 매칭 단계의 입력이므로 매칭을 다시 도는 설계와
  어긋나지 않는다").

Division of responsibility (why this lives in ``models/`` and stays pure):
  The obvious home for a hash-computation helper in this codebase is
  ``services/`` (see ``services.remediation_pr_service``'s
  ``change_fingerprint``, the closest existing precedent). This module is
  colocated with the ``Scan`` model instead, and takes its scanner-version
  and scan-config inputs as plain arguments rather than reading
  ``core.config`` or invoking cdxgen itself, because computing and WRITING
  the fingerprint at scan-success time (the part that needs
  ``core.config.cdxgen_spec_version`` and friends, and a place in
  ``tasks.scan_source``'s pipeline) is a follow-on change outside this
  session's scope. Keeping this function pure and dependency-free means that
  follow-on change can import it from wherever it ends up living without
  this module needing to move first.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

#: Bumped whenever the hashing shape changes: which fields are hashed, in
#: what order, or how they are serialized. A code change that touches this
#: module without also changing any lockfile byte, scanner version, or scan
#: config must still produce a fingerprint that differs from what an older
#: worker would have written for the identical scan; otherwise the very
#: first scan a new worker processes after an unrelated fingerprint-format
#: change could read as "unchanged" against a fingerprint an old worker
#: wrote under a different, incompatible scheme.
FINGERPRINT_SCHEMA_VERSION: Final = 1

#: JSON-serializable scalar types a scan-config value may hold. Anything else
#: is coerced to ``str`` (see ``_normalize_config``) rather than rejected;
#: this function must never raise on a config shape it was not expecting,
#: the same defensive posture ``services.scan_inputs`` takes throughout.
_ConfigScalar = bool | int | float | str


def compute_scan_fingerprint(
    *,
    manifest_inventory: Mapping[str, Any] | None,
    scanner_version: str,
    scan_config: Mapping[str, Any],
) -> str | None:
    """Return a deterministic SHA-256 hex digest, or None when one cannot be trusted.

    ``manifest_inventory`` is the exact shape
    ``services.scan_inputs.collect_manifest_inventory`` returns (and
    ``scans.input_manifests`` stores): ``{"files": [{"path", "size",
    "sha256"}, ...], "count", "truncated"}``. Passing the already-collected
    inventory instead of a source directory keeps this function filesystem-
    free: the walk, its bounds, and its skip-list live in exactly one place
    (``scan_inputs.py``) and this function trusts that place's judgment about
    what counts as a dependency declaration.

    Returns ``None`` (deliberately, not a digest of "nothing") when the
    inventory cannot be trusted to describe the WHOLE tree:

    - ``manifest_inventory`` is ``None``: no manifest/lockfile was found
      (or the scan has no source tree, e.g. container / SBOM-ingest scans).
      There is no dependency-set identity to fingerprint.
    - ``manifest_inventory["truncated"]`` is true: the walk stopped at
      ``scan_inputs.MAX_ENTRIES`` before covering the tree. A file that
      changed past the cutoff would go undetected, which is exactly the
      failure mode a fingerprint exists to prevent.
    - any recorded file has ``sha256`` of ``None``: that file was too large
      to hash (``scan_inputs.MAX_HASH_BYTES``) or could not be read.
      "Unknown content" and "unchanged content" must never collide.

    A ``None`` return must never be treated as equal to another ``None`` by
    a caller comparing two scans' fingerprints: two un-fingerprinted scans
    have not been shown to share a dependency set, they simply were not
    compared. Callers enforce that; this function only refuses to assert a
    digest it cannot stand behind.

    Deterministic: two calls with equal arguments (independent of Python
    dict insertion order; every mapping is sorted before serializing)
    always return the same digest. Two calls where any manifest hash, the
    scanner version, or any scan-config value differs return different
    digests with overwhelming probability (SHA-256 preimage resistance).
    """
    if not manifest_inventory:
        return None
    if manifest_inventory.get("truncated"):
        return None

    files = manifest_inventory.get("files")
    if not isinstance(files, Sequence) or isinstance(files, str | bytes) or not files:
        return None

    entries: list[tuple[str, str]] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            return None
        path = entry.get("path")
        digest = entry.get("sha256")
        if not isinstance(path, str) or not path:
            return None
        if not isinstance(digest, str) or not digest:
            # Too large to hash, or unreadable this pass (scan_inputs._sha256
            # returns None in both cases). "Unchanged" cannot be claimed
            # about a file whose content this scan never actually read.
            return None
        entries.append((path, digest))

    # Sort defensively even though collect_manifest_inventory already sorts
    # its output: this function's determinism must not depend on a caller
    # upholding an invariant it cannot verify from the shape alone.
    entries.sort(key=lambda pair: pair[0])

    payload = {
        "schema": FINGERPRINT_SCHEMA_VERSION,
        "files": entries,
        "scanner_version": scanner_version,
        "scan_config": _normalize_config(scan_config),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_config(scan_config: Mapping[str, Any]) -> dict[str, Any]:
    """Coerce scan-config values to JSON-serializable scalars.

    A config value this function was not told to expect (a nested dict, a
    custom object) is stringified rather than dropped or raising: dropping
    it would silently narrow the fingerprint's inputs, and this function has
    no basis to decide such a value is safe to ignore.
    """
    normalized: dict[str, Any] = {}
    for key, value in scan_config.items():
        if value is None or isinstance(value, bool | int | float | str):
            normalized[key] = value
        else:
            normalized[key] = str(value)
    return normalized


__all__ = [
    "FINGERPRINT_SCHEMA_VERSION",
    "compute_scan_fingerprint",
]
