"""
Unit tests for ``integrations.attestation`` — v2.3-s2 SLSA provenance predicate.

Pure (no DB, no cosign, no subprocess). The builder is a deterministic transform,
so we pin:

  - the in-toto Statement / SLSA provenance v1 shape (subject + predicateType +
    buildDefinition + runDetails),
  - the CISA-2025 generation-context elements are present (component hash, tool
    name/version, generation timestamp + invocationId),
  - NO sensitive material leaks into the predicate (git URL, paths, secrets),
  - adversarial free-text (control chars, oversized, NUL/CRLF) is sanitized,
  - an invalid SBOM digest is rejected (an un-verifiable subject is meaningless),
  - optional materials (git ref / source tarball sha256) are folded in only when
    valid.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from integrations import attestation


def _build(**overrides: object) -> dict:
    kwargs: dict = {
        "sbom_name": "cdxgen.cdx.json",
        "sbom_sha256": "a" * 64,
        "scan_id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "builder_id": "https://github.com/trustedoss/trustedoss-portal/worker",
        "builder_version": "2.3.0-dev",
    }
    kwargs.update(overrides)
    return attestation.build_slsa_provenance_statement(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_statement_has_in_toto_and_slsa_shape() -> None:
    stmt = _build()
    assert stmt["_type"] == attestation.IN_TOTO_STATEMENT_TYPE
    assert stmt["predicateType"] == attestation.SLSA_PROVENANCE_PREDICATE_TYPE
    assert stmt["predicateType"] == "https://slsa.dev/provenance/v1"

    subject = stmt["subject"]
    assert isinstance(subject, list) and len(subject) == 1
    assert subject[0]["name"] == "cdxgen.cdx.json"
    assert subject[0]["digest"]["sha256"] == "a" * 64


def test_predicate_build_definition_carries_ids_and_build_type() -> None:
    stmt = _build()
    bd = stmt["predicate"]["buildDefinition"]
    assert bd["buildType"] == attestation.TRUSTEDOSS_SOURCE_SCAN_BUILD_TYPE
    assert bd["externalParameters"]["scanId"] == "11111111-1111-1111-1111-111111111111"
    assert bd["externalParameters"]["projectId"] == "22222222-2222-2222-2222-222222222222"


def test_predicate_run_details_carries_builder_and_metadata() -> None:
    started = datetime(2026, 5, 25, 10, 0, 0, tzinfo=UTC)
    finished = datetime(2026, 5, 25, 10, 5, 0, tzinfo=UTC)
    stmt = _build(started_on=started, finished_on=finished)
    run = stmt["predicate"]["runDetails"]
    assert run["builder"]["id"] == "https://github.com/trustedoss/trustedoss-portal/worker"
    assert run["builder"]["version"]["trustedoss"] == "2.3.0-dev"
    assert run["metadata"]["invocationId"] == "11111111-1111-1111-1111-111111111111"
    assert run["metadata"]["startedOn"] == "2026-05-25T10:00:00Z"
    assert run["metadata"]["finishedOn"] == "2026-05-25T10:05:00Z"


def test_finished_on_defaults_to_now_utc_z_suffix() -> None:
    stmt = _build()
    finished = stmt["predicate"]["runDetails"]["metadata"]["finishedOn"]
    assert finished.endswith("Z")
    # Parsable as RFC3339 once Z → +00:00.
    parsed = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_started_on_omitted_when_not_supplied() -> None:
    stmt = _build()
    assert "startedOn" not in stmt["predicate"]["runDetails"]["metadata"]


# ---------------------------------------------------------------------------
# CISA / NTIA compliance self-check
# ---------------------------------------------------------------------------


def test_cisa_minimum_elements_present_for_well_formed_statement() -> None:
    assert attestation.cisa_minimum_elements_present(_build()) is True


def test_cisa_check_fails_when_digest_missing() -> None:
    stmt = _build()
    stmt["subject"][0]["digest"]["sha256"] = "not-a-digest"
    assert attestation.cisa_minimum_elements_present(stmt) is False


def test_cisa_check_fails_when_builder_id_blank() -> None:
    stmt = _build()
    stmt["predicate"]["runDetails"]["builder"]["id"] = ""
    assert attestation.cisa_minimum_elements_present(stmt) is False


def test_cisa_check_fails_when_version_missing() -> None:
    stmt = _build()
    stmt["predicate"]["runDetails"]["builder"]["version"] = {}
    assert attestation.cisa_minimum_elements_present(stmt) is False


def test_cisa_check_fails_when_finished_on_missing() -> None:
    stmt = _build()
    del stmt["predicate"]["runDetails"]["metadata"]["finishedOn"]
    assert attestation.cisa_minimum_elements_present(stmt) is False


def test_cisa_check_fails_when_invocation_id_missing() -> None:
    stmt = _build()
    del stmt["predicate"]["runDetails"]["metadata"]["invocationId"]
    assert attestation.cisa_minimum_elements_present(stmt) is False


def test_cisa_check_handles_malformed_input_gracefully() -> None:
    assert attestation.cisa_minimum_elements_present({}) is False
    assert attestation.cisa_minimum_elements_present({"subject": []}) is False
    assert attestation.cisa_minimum_elements_present({"subject": "nope"}) is False  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# Information disclosure — the predicate must NOT carry secrets / URLs / paths
# ---------------------------------------------------------------------------


def test_predicate_does_not_leak_git_url_or_paths() -> None:
    """The builder takes no git URL / path; serialized predicate is credential-free."""
    stmt = _build()
    blob = json.dumps(stmt)
    # No scheme that could carry userinfo / a host.
    assert "https://oauth2:" not in blob
    assert "@github.com" not in blob
    # No workspace path leakage.
    assert "/tmp/" not in blob
    assert "/opt/trustedoss" not in blob
    # The only URIs present are our fixed type/build-type/builder strings.
    assert "COSIGN_PASSWORD" not in blob
    assert "DT_API_KEY" not in blob


def test_builder_only_accepts_documented_kwargs() -> None:
    """A regression guard: passing an unexpected field raises (no silent leak)."""
    with pytest.raises(TypeError):
        attestation.build_slsa_provenance_statement(  # type: ignore[call-arg]
            sbom_name="x",
            sbom_sha256="a" * 64,
            scan_id="s",
            project_id="p",
            builder_id="b",
            builder_version="v",
            git_url="https://oauth2:TOKEN@github.com/x/y",  # not a real param
        )


# ---------------------------------------------------------------------------
# Adversarial free-text sanitisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_name",
    [
        "a\x00b.json",  # NUL
        "a\r\nb.json",  # CRLF (log injection)
        "a\x7fb.json",  # DEL
        "  spaced  .json  ",  # leading/trailing whitespace
    ],
)
def test_sbom_name_is_control_char_stripped(hostile_name: str) -> None:
    stmt = _build(sbom_name=hostile_name)
    name = stmt["subject"][0]["name"]
    assert "\x00" not in name
    assert "\r" not in name
    assert "\n" not in name
    assert "\x7f" not in name
    assert name == name.strip()


def test_oversized_sbom_name_is_capped() -> None:
    stmt = _build(sbom_name="x" * 5000)
    assert len(stmt["subject"][0]["name"]) <= 512


def test_blank_sbom_name_falls_back_to_default() -> None:
    stmt = _build(sbom_name="   ")
    assert stmt["subject"][0]["name"] == "sbom.cdx.json"


# ---------------------------------------------------------------------------
# Subject digest validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_digest",
    [
        "tooshort",
        "g" * 64,  # non-hex
        "A" * 64 + "extra",  # too long
        "",
    ],
)
def test_invalid_sbom_sha256_raises(bad_digest: str) -> None:
    with pytest.raises(ValueError, match="sha256"):
        _build(sbom_sha256=bad_digest)


def test_uppercase_sha256_is_normalised_to_lowercase() -> None:
    stmt = _build(sbom_sha256="A" * 64)
    assert stmt["subject"][0]["digest"]["sha256"] == "a" * 64


# ---------------------------------------------------------------------------
# Optional materials (resolvedDependencies)
# ---------------------------------------------------------------------------


def test_materials_empty_when_no_source_info() -> None:
    stmt = _build()
    assert stmt["predicate"]["buildDefinition"]["resolvedDependencies"] == []


def test_git_ref_material_folded_in() -> None:
    stmt = _build(source_git_ref="refs/heads/main")
    deps = stmt["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert any(d["uri"] == "git+ref:refs/heads/main" for d in deps)


def test_tarball_sha256_material_folded_in_with_digest() -> None:
    stmt = _build(source_tarball_sha256="b" * 64)
    deps = stmt["predicate"]["buildDefinition"]["resolvedDependencies"]
    tarball = next(d for d in deps if d["uri"] == "trustedoss:source-tarball")
    assert tarball["digest"]["sha256"] == "b" * 64


def test_invalid_tarball_sha256_is_dropped_not_raised() -> None:
    """A malformed tarball digest is a degraded material, not a hard error."""
    stmt = _build(source_tarball_sha256="not-a-real-digest")
    deps = stmt["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert all(d["uri"] != "trustedoss:source-tarball" for d in deps)


def test_control_chars_stripped_from_git_ref() -> None:
    stmt = _build(source_git_ref="refs/heads/x\r\ninjected")
    deps = stmt["predicate"]["buildDefinition"]["resolvedDependencies"]
    ref_uri = next(d["uri"] for d in deps if d["uri"].startswith("git+ref:"))
    assert "\r" not in ref_uri and "\n" not in ref_uri


def test_blank_git_ref_omits_material() -> None:
    stmt = _build(source_git_ref="   ")
    deps = stmt["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert all(not d["uri"].startswith("git+ref:") for d in deps)


# ---------------------------------------------------------------------------
# Determinism — same inputs (incl. explicit timestamps) → same statement
# ---------------------------------------------------------------------------


def test_builder_is_deterministic_with_fixed_timestamps() -> None:
    finished = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    a = _build(finished_on=finished)
    b = _build(finished_on=finished)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_naive_datetime_assumed_utc() -> None:
    naive = datetime(2026, 5, 25, 9, 0, 0)  # no tzinfo
    stmt = _build(finished_on=naive)
    assert stmt["predicate"]["runDetails"]["metadata"]["finishedOn"] == "2026-05-25T09:00:00Z"
