# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""#400 (offline install bundle half): scripts/bundle-offline.sh and
scripts/install.sh --offline.

Follows this repo's "source-text static verification" pattern (see
test_helm_notes_connection_budget.py / test_helm_rwx_storage_warning.py) for
the parts that would otherwise need a real `docker save` / `docker load` /
`trivy --download-db-only` - several GB of image + vulnerability-DB traffic
that has no place in a unit-test run.

Security review (post-implementation) moved the checksum-verify and
tar-extraction steps to run BEFORE "Pre-flight checks" (the docker-compose /
docker presence check) specifically so they stay testable through a real
`bash` subprocess without needing Docker installed in this job: a handful of
tests below build a tiny fake bundle (a plain file, or a small real tar) and
run `install.sh --offline` against it, asserting it fails during checksum
verification or extraction - well before it would ever reach a `docker`
call.

Six images, not the issue's approximate "four": #400's Direction section says
"packages the four images", but docker-compose.yml (measured, not assumed)
pulls six distinct image references - traefik, postgres, redis,
trusca-backend, trusca-backend-worker, trusca-frontend. An air-gapped
`docker-compose up` needs all six or it stalls on whichever one is missing,
so test_bundle_references_all_six_compose_images pins the actual count.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
BUNDLE_SH = REPO_ROOT / "scripts" / "bundle-offline.sh"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

# The six image references docker-compose.yml pulls, in the form each
# appears literally in docker-compose.yml (image / tag pinned separately).
_COMPOSE_IMAGE_NAMES = (
    "traefik",
    "postgres",
    "redis",
    "trusca-backend",
    "trusca-backend-worker",
    "trusca-frontend",
)


def _bundle_source() -> str:
    return BUNDLE_SH.read_text()


def _install_source() -> str:
    return INSTALL_SH.read_text()


# ---------------------------------------------------------------------------
# Real subprocess: --help and the --offline missing-value case. Both exit
# during CLI flag parsing, before either script's first external tool call.
# ---------------------------------------------------------------------------


def test_install_sh_help_documents_offline() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "--offline" in result.stdout
    assert "bundle-offline.sh" in result.stdout


def test_bundle_offline_sh_help_runs_without_docker_or_trivy() -> None:
    result = subprocess.run(
        ["bash", str(BUNDLE_SH), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "--tag" in result.stdout
    assert "--registry" in result.stdout
    assert "--output" in result.stdout


def test_install_sh_offline_flag_requires_a_bundle_path() -> None:
    """`--offline` with no following argument must fail argument parsing,
    not fall through and treat the NEXT flag (or nothing) as the bundle
    path."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--offline"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "--offline" in result.stderr


def test_install_sh_offline_requires_a_checksum_sidecar(tmp_path: Path) -> None:
    """#400-S1 (security review): a bundle with no <bundle>.sha256 next to it
    must be rejected before extraction, not silently trusted."""
    bundle = tmp_path / "bundle.tar"
    bundle.write_text("not a real tar - checksum check must reject before tar even runs")
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--offline", str(bundle)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "checksum" in result.stderr.lower()


def test_install_sh_offline_rejects_a_wrong_checksum(tmp_path: Path) -> None:
    """#400-S1: a checksum sidecar that does not match the bundle's actual
    content must fail closed - this is the corrupted/tampered-bundle case
    the finding described."""
    bundle = tmp_path / "bundle.tar"
    bundle.write_text("legitimate-looking bundle content")
    (tmp_path / "bundle.tar.sha256").write_text(
        "0" * 64 + "  bundle.tar\n"  # a checksum that cannot match anything
    )
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--offline", str(bundle)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr.lower()


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_install_sh_offline_accepts_a_correct_checksum_and_proceeds_past_it(
    tmp_path: Path,
) -> None:
    """The positive case for the two tests above: a correct checksum must NOT
    be rejected - if it were, the offline path would be unusable even with a
    genuine, untampered bundle. This bundle is not a valid tar, so the
    process still fails, but it must fail at EXTRACTION, not at the checksum
    step - proving the checksum step itself passed."""
    bundle = tmp_path / "bundle.tar"
    content = b"not a real tar, but the checksum for it below is correct"
    bundle.write_bytes(content)
    (tmp_path / "bundle.tar.sha256").write_text(f"{_sha256_hex(content)}  bundle.tar\n")
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--offline", str(bundle)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "checksum verified" in result.stdout
    assert "checksum mismatch" not in result.stderr.lower()
    assert result.returncode != 0  # fails later, at tar extraction - expected


CONTAINMENT_LIB = REPO_ROOT / "scripts" / "lib" / "tar_containment_check.sh"


def _run_containment_check(bundle: Path) -> subprocess.CompletedProcess[str]:
    script = f'source "{CONTAINMENT_LIB}"; verify_bundle_members_are_contained "$1"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(bundle)],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_containment_check_rejects_a_dotdot_member(tmp_path: Path) -> None:
    """#400-S3 (security review): this is the case a POST-extraction check
    cannot catch - a member named e.g. ../../../etc/cron.d/x, if written by
    an unprotected tar, lands OUTSIDE the extraction directory, so walking
    the extraction directory afterward would never see it there to catch it
    (tried exactly that approach first; verified by removing the check that
    it does not actually work, since `find $dir` cannot enumerate paths
    outside $dir). This checks the tar's MEMBER LIST instead, before
    extraction ever happens - `tar -tf` lists a member's raw name with no
    filesystem write and no sanitization, confirmed against this machine's
    own tar."""
    import tarfile

    bundle = tmp_path / "evil.tar"
    with tarfile.open(bundle, "w") as tf:
        info = tarfile.TarInfo(name="../../../trusca-offline-traversal-canary")
        info.size = 0
        tf.addfile(info)

    result = _run_containment_check(bundle)
    assert result.returncode == 1
    assert ".." in result.stderr


def test_containment_check_rejects_an_absolute_member() -> None:
    import tarfile
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td) / "evil.tar"
        with tarfile.open(bundle, "w") as tf:
            info = tarfile.TarInfo(name="/etc/trusca-offline-traversal-canary")
            info.size = 0
            tf.addfile(info)

        result = _run_containment_check(bundle)
        assert result.returncode == 1
        assert "absolute" in result.stderr


def test_containment_check_accepts_a_normal_bundles_members() -> None:
    """The positive case - a bundle shaped like a real one built by
    bundle-offline.sh must not be rejected."""
    import tarfile
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td) / "good.tar"
        with tarfile.open(bundle, "w") as tf:
            for name in ("images/images.tar", "MANIFEST.txt", "charts/trustedoss/Chart.yaml"):
                info = tarfile.TarInfo(name=name)
                info.size = 0
                tf.addfile(info)

        result = _run_containment_check(bundle)
        assert result.returncode == 0, result.stderr


def test_install_sh_offline_rejects_a_path_traversal_member(tmp_path: Path) -> None:
    """End-to-end version of test_containment_check_rejects_a_dotdot_member,
    through install.sh --offline itself rather than the helper directly -
    confirms the two are actually wired together, and that the install
    aborts before ever calling `tar -xf` (before any file could be written
    at all, from either the traversal member or anything after it in the
    tar)."""
    import tarfile

    bundle = tmp_path / "bundle.tar"
    with tarfile.open(bundle, "w") as tf:
        info = tarfile.TarInfo(name="../../../trusca-offline-traversal-canary")
        info.size = 0
        tf.addfile(info)
    checksum = _sha256_hex(bundle.read_bytes())
    (tmp_path / "bundle.tar.sha256").write_text(f"{checksum}  bundle.tar\n")

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--offline", str(bundle)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    # Confirms the process got PAST the checksum step (this bundle's checksum
    # is correct) and failed at the member-path check, not before it.
    assert "checksum verified" in result.stdout
    assert "extracting" not in result.stdout, "must reject before ever calling tar -xf"
    canary = Path("/") / "trusca-offline-traversal-canary"
    assert not canary.exists(), "tar member escaped to the filesystem root"


def test_install_sh_rejects_unknown_flags_after_adding_offline() -> None:
    """Guards against a parsing rewrite that silently swallows unknown
    arguments instead of rejecting them (the loop was converted from a
    simple `for arg in "$@"` to a `while` + `shift` loop to support
    --offline's value argument -- an easy place to lose the fallback
    `*)` branch)."""
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--not-a-real-flag"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


# ---------------------------------------------------------------------------
# Static source checks - everything that would otherwise need a real
# docker/trivy run.
# ---------------------------------------------------------------------------


def test_both_scripts_use_strict_mode() -> None:
    for path, source in ((BUNDLE_SH, _bundle_source()), (INSTALL_SH, _install_source())):
        assert "set -euo pipefail" in source, f"{path} is missing 'set -euo pipefail'"


def test_bundle_references_all_six_compose_images() -> None:
    source = _bundle_source()
    for name in _COMPOSE_IMAGE_NAMES:
        assert name in source, (
            f"scripts/bundle-offline.sh no longer references {name!r} -- an "
            "air-gapped docker-compose up would stall pulling this image"
        )


def test_bundle_has_no_latest_tag() -> None:
    """CLAUDE.md core rule #9 -- every image pin carries an explicit
    version, never `:latest`."""
    source = _bundle_source()
    assert ":latest" not in source


def test_bundle_pins_the_three_third_party_images_by_exact_tag() -> None:
    """traefik / postgres / redis are not templated through
    IMAGE_REGISTRY/IMAGE_TAG like the trusca-* images -- they must carry the
    exact tag docker-compose.yml pins, or the bundle could silently drift
    from what the compose file actually pulls."""
    source = _bundle_source()
    assert "traefik:v3.6.1" in source
    assert "postgres:17.2-alpine" in source
    assert "redis:7.4-alpine" in source


def test_bundle_reads_image_tag_and_registry_at_runtime_not_hardcoded() -> None:
    """CLAUDE.md core rule #11 -- IMAGE_TAG / IMAGE_REGISTRY must be read
    from the environment / .env at run time, never baked into the script as
    a fixed literal for the trusca-* images."""
    source = _bundle_source()
    assert "${IMAGE_REGISTRY}/trusca-backend:${IMAGE_TAG}" in source
    assert "${IMAGE_REGISTRY}/trusca-backend-worker:${IMAGE_TAG}" in source
    assert "${IMAGE_REGISTRY}/trusca-frontend:${IMAGE_TAG}" in source


def test_bundle_downloads_trivy_db_and_java_db() -> None:
    source = _bundle_source()
    assert "--download-db-only" in source
    assert "--download-java-db-only" in source


def test_bundle_packs_the_helm_chart_and_compose_files() -> None:
    source = _bundle_source()
    assert "charts/trustedoss" in source
    assert "docker-compose.yml" in source
    assert "docker-compose.dev.yml" in source
    assert ".env.example" in source


def test_bundle_output_is_written_atomically() -> None:
    """A failure partway through must not leave a truncated tar at the
    final path -- the bundle is written to a `.partial` sibling and moved
    into place only after packing succeeds, and the cleanup trap removes any
    leftover `.partial` file."""
    source = _bundle_source()
    assert "PARTIAL_OUTPUT" in source
    assert 'mv "$PARTIAL_OUTPUT" "$OUTPUT"' in source
    assert "trap cleanup EXIT" in source


def test_bundle_writes_a_checksum_sidecar_after_the_final_mv() -> None:
    """#400-S1: the checksum must cover the FINAL bytes at $OUTPUT, so it has
    to be computed after the atomic mv, not before (hashing the .partial
    file would be checksumming bytes that might still change)."""
    source = _bundle_source()
    mv_index = source.index('mv "$PARTIAL_OUTPUT" "$OUTPUT"')
    checksum_index = source.index('sha256_of "$OUTPUT"')
    assert checksum_index > mv_index, (
        "the checksum is computed before the atomic mv - it could hash "
        "bytes that are not the bundle's final content"
    )
    assert '"${OUTPUT}.sha256"' in source


def test_install_sh_offline_flag_is_parsed_and_stored() -> None:
    source = _install_source()
    assert "--offline)" in source
    assert "--offline=*)" in source
    assert "OFFLINE_BUNDLE=" in source


def test_install_sh_offline_case_consumes_both_the_flag_and_its_value() -> None:
    """The ``--offline)`` case must ``shift 2`` (flag + value), not ``shift 1``
    -- a ``shift 1`` bug would leave the bundle path in ``$1`` on the next
    loop iteration, where it falls through to the ``*)`` unknown-argument
    branch and the install aborts with a confusing "unknown argument: <path>"
    instead of using it. Scoped to the exact case block via regex, not a bare
    ``"shift 2" in source`` check, so a `shift 2` elsewhere in the file could
    not make this pass. (Mutation-tested: a ``shift 1`` substitution here
    made this test fail while the rest of the suite stayed green.)"""
    import re

    source = _install_source()
    match = re.search(r"--offline\)\n(?:.*\n)*?\s*;;", source)
    assert match, "could not find the --offline) case block in install.sh"
    assert "shift 2" in match.group(0)


def test_install_sh_offline_skips_pull_and_docker_loads_instead() -> None:
    source = _install_source()
    assert "docker load -i" in source
    # The unconditional `$DC -f docker-compose.yml pull` this replaced must
    # now be reachable only on the non-offline branch.
    assert 'if [[ -n "$OFFLINE_BUNDLE" ]]; then' in source
    assert "skipping '$DC pull'" in source


def test_install_sh_offline_seeds_the_trivy_cache_volume() -> None:
    """The trivy-cache seed must run against the worker-scan SERVICE (via
    `docker-compose run`), not a hand-computed `<project>_trivy-cache`
    volume name -- see vulnerability-data.md's "Volume name" tip for why the
    latter breaks under a non-default COMPOSE_PROJECT_NAME."""
    source = _install_source()
    assert "run --rm --no-deps" in source
    assert "worker-scan" in source
    assert "/var/lib/trivy" in source


def test_install_sh_offline_disables_the_online_bootstrap_after_seeding() -> None:
    """Once the bundle has seeded the cache, the worker's own network
    download must be turned off (TRIVY_DB_BOOTSTRAP_ON_START=false) so an
    air-gapped host does not repeatedly retry an unreachable ghcr.io."""
    source = _install_source()
    assert "TRIVY_DB_BOOTSTRAP_ON_START" in source
    assert '"false"' in source


def test_install_sh_offline_extraction_scratch_dir_is_cleaned_up() -> None:
    source = _install_source()
    assert "mktemp -d" in source
    assert 'rm -rf "$OFFLINE_EXTRACT_DIR"' in source


def test_install_sh_offline_verifies_every_manifest_image_after_load() -> None:
    """#400-S2: `docker load` succeeding is not enough - install.sh must
    confirm each image the bundle's own MANIFEST.txt lists is actually
    present afterward, or a truncated/mismatched-tag bundle would load
    "fine" and Compose would silently fall back to a network pull later."""
    source = _install_source()
    assert "docker image inspect" in source
    assert "MANIFEST.txt" in source


def test_install_sh_offline_pins_env_to_the_bundles_own_tag() -> None:
    """#400-S2 follow-up (found by running the feature end-to-end, not just
    reviewing the diff): .env's IMAGE_REGISTRY/IMAGE_TAG must be overwritten
    to match what the bundle's MANIFEST.txt recorded, or docker-compose.yml
    resolves a DIFFERENT tag than what was just docker-loaded and falls back
    to a network pull - reproduced locally against a real bundle before this
    fix existed."""
    source = _install_source()
    assert "OFFLINE_IMAGE_REGISTRY" in source
    assert "OFFLINE_IMAGE_TAG" in source
    assert '"IMAGE_REGISTRY"' in source
    assert '"IMAGE_TAG"' in source
