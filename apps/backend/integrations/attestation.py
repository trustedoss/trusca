"""
in-toto attestation + SLSA provenance predicate builder (v2.3-s2).

After the SBOM is signed (v2.3-s1), the scan pipeline asks cosign to produce an
in-toto attestation over the SBOM whose predicate is a SLSA provenance v1
statement. This module owns the *pure* construction of that predicate — no
subprocess, no DB, no env-at-import — so it is trivially unit-testable and its
information-disclosure surface can be audited in one place. The cosign
invocation that signs the predicate lives in ``integrations.cosign``.

What goes in (and, deliberately, what does NOT)
-----------------------------------------------
The predicate is the SLSA `Provenance v1 <https://slsa.dev/provenance/v1>`_
shape, embedded inside the in-toto `Statement v1
<https://github.com/in-toto/attestation/blob/main/spec/v1/statement.md>`_:

  - ``subject``: the SBOM file name + its sha256 digest (the artifact the
    attestation is *about*). The digest is the same one s1 records on the
    signature ``ScanArtifact.sha256``, so a verifier can bind attestation →
    signature → exact bytes.
  - ``predicateType``: ``https://slsa.dev/provenance/v1``.
  - ``predicate.buildDefinition``: the build *recipe* — ``buildType`` (a URI
    naming TrustedOSS's source-scan build), ``externalParameters`` (the scan +
    project ids — opaque UUIDs, no secrets), and ``resolvedDependencies`` (the
    *materials*: the source the SBOM was generated from, by git ref and/or
    preserved-tarball sha256 when available).
  - ``predicate.runDetails``: the build *execution* — ``builder.id`` /
    ``builder.version`` (the TrustedOSS worker, configurable), and ``metadata``
    (``invocationId`` = scan id, ``startedOn`` / ``finishedOn`` timestamps).

NEVER included: the git URL (it may carry userinfo / a PAT), workspace paths,
DT api keys, the cosign key/password, or any user/team PII. The only
caller-supplied free text is the SBOM file *name* (a worker-generated workspace
basename, e.g. ``cdxgen.cdx.json``) and the optional git *ref* (a branch/commit
string) — both are length-capped and control-character-stripped defensively so a
hostile upstream value cannot bloat or corrupt the predicate JSON.

CISA 2025 "minimum elements" / NTIA 7 baseline
----------------------------------------------
The SBOM *body* (cdxgen output) carries the NTIA component-level baseline
(supplier, name, version, identifiers, dependency relationships, author,
timestamp). This attestation adds the *generation context* CISA's 2025 update
emphasises: the component HASH (subject sha256), the TOOL name + version
(``builder.id`` / ``builder.version``), and the GENERATION CONTEXT
(``metadata.startedOn``/``finishedOn`` timestamp + ``invocationId``). See
:func:`cisa_minimum_elements_present` for a machine-checkable assertion used by
the unit tests so a future refactor cannot silently drop one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# in-toto Statement / SLSA predicate type URIs (pinned strings, not env).
IN_TOTO_STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
SLSA_PROVENANCE_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"

# buildType: a URI naming THIS build process (a TrustedOSS source scan that runs
# cdxgen → SBOM). Verifiers key their expectations off this, so it is a stable
# vendor URI, not configurable.
TRUSTEDOSS_SOURCE_SCAN_BUILD_TYPE = (
    "https://github.com/trustedoss/trustedoss-portal/buildtypes/source-scan@v1"
)

# Defensive caps on the only two caller-supplied free-text fields. The SBOM file
# name is a worker basename and the git ref a branch/commit; neither should ever
# approach these, but a hostile upstream must not be able to balloon the JSON.
_MAX_NAME_LEN = 512
_MAX_REF_LEN = 256
_MAX_SHA256_LEN = 64


def _clean_text(value: str | None, *, max_len: int) -> str | None:
    """Strip control chars and cap length on a free-text predicate field.

    Returns ``None`` for ``None`` / empty / whitespace-only input so the caller
    can omit the field entirely rather than emit an empty string. Control
    characters (including NUL / CR / LF / DEL) are removed so a crafted value
    cannot inject newlines into a log line nor smuggle structure into the JSON.
    """
    if value is None:
        return None
    # Drop C0 controls (0x00-0x1f), DEL (0x7f), and C1 controls (0x80-0x9f).
    cleaned = "".join(
        ch for ch in value if not (ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F)
    ).strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_sha256(value: str | None) -> str | None:
    """Validate a hex sha256 digest (64 lowercase hex chars) or return ``None``.

    A subject without a verifiable digest is worse than useless, so we accept a
    value only when it is exactly the canonical sha256 hex shape. Anything else
    (an attacker-influenced string, a truncated digest) is dropped.
    """
    if value is None:
        return None
    candidate = value.strip().lower()
    if len(candidate) != _MAX_SHA256_LEN:
        return None
    if not all(c in "0123456789abcdef" for c in candidate):
        return None
    return candidate


def build_slsa_provenance_statement(
    *,
    sbom_name: str,
    sbom_sha256: str,
    scan_id: str,
    project_id: str,
    builder_id: str,
    builder_version: str,
    started_on: datetime | None = None,
    finished_on: datetime | None = None,
    source_git_ref: str | None = None,
    source_tarball_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the in-toto Statement (SLSA provenance v1 predicate) for an SBOM.

    Pure: no env reads, no clock side effects beyond an explicit
    ``finished_on`` default. The caller (``tasks.scan_source``) resolves the
    builder id/version from config and passes them in, so this function stays a
    deterministic, fully-testable transform.

    Args:
        sbom_name: The SBOM file's basename (the subject ``name``). Cleaned +
            capped; a worker-generated workspace basename in practice.
        sbom_sha256: Hex sha256 of the SBOM bytes (the subject ``digest.sha256``).
            Must be canonical sha256 hex; an invalid digest raises ``ValueError``
            because an attestation without a verifiable subject is meaningless.
        scan_id / project_id: Opaque UUID strings — the build's external
            parameters + invocation id. No secrets / PII.
        builder_id / builder_version: Identify the TrustedOSS build platform.
        started_on / finished_on: Build window. ``finished_on`` defaults to now
            (UTC); ``started_on`` is omitted when not supplied.
        source_git_ref: Optional git ref the SBOM was generated from (a material).
        source_tarball_sha256: Optional sha256 of the preserved source tarball
            (a material with a verifiable digest).

    Raises:
        ValueError: when ``sbom_sha256`` is not a canonical sha256 hex digest.
    """
    subject_digest = _clean_sha256(sbom_sha256)
    if subject_digest is None:
        raise ValueError("sbom_sha256 must be a 64-char hex sha256 digest")

    name = _clean_text(sbom_name, max_len=_MAX_NAME_LEN) or "sbom.cdx.json"
    finished = finished_on or datetime.now(UTC)

    # --- resolvedDependencies (materials) ---------------------------------
    materials: list[dict[str, Any]] = []
    ref = _clean_text(source_git_ref, max_len=_MAX_REF_LEN)
    if ref is not None:
        materials.append({"uri": f"git+ref:{ref}"})
    tarball_digest = _clean_sha256(source_tarball_sha256)
    if tarball_digest is not None:
        materials.append(
            {
                "uri": "trustedoss:source-tarball",
                "digest": {"sha256": tarball_digest},
            }
        )

    # --- metadata (generation context) ------------------------------------
    metadata: dict[str, Any] = {
        # invocationId binds the provenance to the exact scan run.
        "invocationId": scan_id,
        "finishedOn": _rfc3339(finished),
    }
    if started_on is not None:
        metadata["startedOn"] = _rfc3339(started_on)

    predicate: dict[str, Any] = {
        "buildDefinition": {
            "buildType": TRUSTEDOSS_SOURCE_SCAN_BUILD_TYPE,
            # Opaque ids only — no git URL (may carry a credential), no paths.
            "externalParameters": {
                "scanId": scan_id,
                "projectId": project_id,
            },
            "internalParameters": {},
            "resolvedDependencies": materials,
        },
        "runDetails": {
            "builder": {
                "id": builder_id,
                "version": {"trustedoss": builder_version},
            },
            "metadata": metadata,
        },
    }

    return {
        "_type": IN_TOTO_STATEMENT_TYPE,
        "subject": [
            {
                "name": name,
                "digest": {"sha256": subject_digest},
            }
        ],
        "predicateType": SLSA_PROVENANCE_PREDICATE_TYPE,
        "predicate": predicate,
    }


def _rfc3339(value: datetime) -> str:
    """Render a datetime as an RFC 3339 / ISO 8601 UTC string with a ``Z`` suffix.

    Naive datetimes are assumed UTC; aware datetimes are converted. The ``Z``
    form is what in-toto / SLSA examples use and is unambiguous across parsers.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Compliance self-checks (used by unit tests so a refactor cannot drop a field)
# ---------------------------------------------------------------------------


def cisa_minimum_elements_present(statement: dict[str, Any]) -> bool:
    """True iff the statement carries the CISA-2025 generation-context elements.

    Checks the three generation-context elements an SBOM *attestation* is
    responsible for (the per-component NTIA baseline lives in the SBOM body, not
    here):

      1. component HASH — ``subject[].digest.sha256`` is a non-empty hex digest;
      2. tool name + version — ``runDetails.builder.id`` and a non-empty
         ``runDetails.builder.version``;
      3. generation context — ``runDetails.metadata.finishedOn`` timestamp AND
         an ``invocationId`` tying the provenance to a specific run.
    """
    try:
        subject = statement["subject"][0]
        digest = subject["digest"]["sha256"]
        if not (isinstance(digest, str) and _clean_sha256(digest) is not None):
            return False

        run = statement["predicate"]["runDetails"]
        builder = run["builder"]
        if not (isinstance(builder.get("id"), str) and builder["id"]):
            return False
        version = builder.get("version")
        if not version:
            return False

        meta = run["metadata"]
        if not (isinstance(meta.get("finishedOn"), str) and meta["finishedOn"]):
            return False
        if not (isinstance(meta.get("invocationId"), str) and meta["invocationId"]):
            return False
    except (KeyError, IndexError, TypeError):
        return False
    return True


__all__ = [
    "IN_TOTO_STATEMENT_TYPE",
    "SLSA_PROVENANCE_PREDICATE_TYPE",
    "TRUSTEDOSS_SOURCE_SCAN_BUILD_TYPE",
    "build_slsa_provenance_statement",
    "cisa_minimum_elements_present",
]
