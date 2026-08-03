"""License + attribution distribution contracts (Apache-2.0 §4 compliance).

Three things drift silently and each one ships a non-compliant artifact:

1. **The license copies.** The build context for every image is ``apps/backend``
   or ``apps/frontend`` (see ``.github/workflows/release.yml`` ``context:``), so
   the repo-root ``LICENSE`` / ``NOTICE`` / ``THIRD_PARTY_NOTICES.md`` are out of
   reach of ``COPY``. Copies live in each context and in ``charts/trustedoss``.
   Nothing but a test keeps them identical to the originals.

2. **The ``COPY`` lines.** A new Dockerfile added later would ship an image with
   no license on its filesystem — the OCI ``licenses`` label is metadata, not a
   copy of the license, and §4(a)/§4(d) want the files.

3. **The attribution table.** Vendored or ported third-party code is declared in
   the source file's own docstring. If ``THIRD_PARTY_NOTICES.md`` is not updated
   in the same change, the attribution never reaches anyone who receives the
   artifact — the exact gap this suite was written after finding.

Per CLAUDE.md hardening rule 2: the same information lives in two places
(source docstrings and the notices file), so a consistency test is mandatory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND.parents[1]

LICENSE_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")

# Every directory that must carry a copy of the license files: the two image
# build contexts plus the Helm chart (packaged by `helm package`, which only
# includes files under the chart directory).
COPY_TARGETS = (
    Path("apps/backend"),
    Path("apps/frontend"),
    Path("charts/trustedoss"),
)

# Source trees that can hold vendored or ported third-party code. Tests are
# excluded: they exercise our own ports, and a fixture naming an upstream path
# is not itself a derivative work being distributed.
VENDOR_SCAN_DIRS = (
    Path("apps/backend/services"),
    Path("apps/backend/integrations"),
    Path("apps/backend/tasks"),
    Path("apps/frontend/src"),
)

VENDOR_SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".sh", ".json", ".jq"}

# Two independent signals that a file carries upstream-derived material. Neither
# alone is complete: `license_flags.py` names `license-flags.jq` without a
# `docker/lib/` prefix, while `regulation_crosswalk.json` is a bare data file
# whose provenance lives in the sibling .py. Union of both, then subtract the
# allowlist below.
UPSTREAM_PATH_RE = re.compile(r"docker/(?:lib|web)/")
PORT_DECLARATION_RE = re.compile(
    r"(?:vendored\s+(?:from|verbatim)|ports?\s+(?:of\s+)?BomLens|"
    r"ported\s+from\s+BomLens|port\s+of\s+BomLens)",
    re.IGNORECASE,
)

# Files that MENTION vendored material without being derived from it themselves.
# Each entry needs a reason — an unexplained entry here is how a real derivative
# work gets excused from attribution.
NOT_DERIVATIVE = {
    # Renders the panel; the vendored logic it calls lives in lib/g7Conformance.ts,
    # which IS listed in THIRD_PARTY_NOTICES.md.
    Path("apps/frontend/src/features/scan/SbomConformancePanel.tsx"),
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def notices_text() -> str:
    return _read(REPO_ROOT / "THIRD_PARTY_NOTICES.md")


@pytest.mark.parametrize("target", COPY_TARGETS, ids=lambda p: str(p))
@pytest.mark.parametrize("filename", LICENSE_FILES)
def test_license_copy_matches_root_original(target: Path, filename: str) -> None:
    """Each distributed copy is byte-identical to the repo-root original."""
    original = REPO_ROOT / filename
    copy = REPO_ROOT / target / filename

    assert copy.is_file(), (
        f"{target / filename} is missing. Every image build context and the Helm "
        f"chart must carry the license files — see this module's docstring."
    )
    assert copy.read_bytes() == original.read_bytes(), (
        f"{target / filename} has drifted from the root {filename}. Edit the root "
        f"file and re-copy; never edit a copy directly."
    )


def test_every_dockerfile_ships_the_license_files() -> None:
    """No image is built without the license files landing on its filesystem."""
    dockerfiles = sorted(
        p
        for p in REPO_ROOT.glob("apps/*/Dockerfile*")
        if p.is_file() and not p.name.endswith((".bak", ".orig"))
    )
    assert dockerfiles, "no Dockerfiles found — has the layout changed?"

    missing = []
    for dockerfile in dockerfiles:
        body = _read(dockerfile)
        copy_lines = [
            line
            for line in body.splitlines()
            if line.startswith("COPY") and "/licenses/" in line
        ]
        if not copy_lines:
            missing.append(dockerfile.relative_to(REPO_ROOT))
            continue
        shipped = " ".join(copy_lines)
        absent = [name for name in LICENSE_FILES if name not in shipped]
        if absent:
            missing.append(
                f"{dockerfile.relative_to(REPO_ROOT)} (omits {', '.join(absent)})"
            )

    assert not missing, (
        "these Dockerfiles do not COPY the license files to /licenses/: "
        f"{missing}. Apache-2.0 §4(a)/§4(d) — an image is a distribution."
    )


def _derivative_sources() -> list[Path]:
    """Files carrying upstream-derived material, as repo-relative paths."""
    found: set[Path] = set()
    for scan_dir in VENDOR_SCAN_DIRS:
        for path in (REPO_ROOT / scan_dir).rglob("*"):
            if not path.is_file() or path.suffix not in VENDOR_SCAN_SUFFIXES:
                continue
            if "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            try:
                body = _read(path)
            except UnicodeDecodeError:
                continue
            if UPSTREAM_PATH_RE.search(body) or (
                "BomLens" in body and PORT_DECLARATION_RE.search(body)
            ):
                found.add(path.relative_to(REPO_ROOT))
    return sorted(found - NOT_DERIVATIVE)


def test_derivative_sources_are_all_attributed(notices_text: str) -> None:
    """Every vendored / ported file is listed in THIRD_PARTY_NOTICES.md.

    Add the file to the attribution table when this fails. Do NOT add it to
    NOT_DERIVATIVE unless it merely *references* vendored code that is itself
    attributed — and say which file that is, in a comment.
    """
    derivatives = _derivative_sources()
    assert derivatives, (
        "the derivative-source scan found nothing, which means the detection "
        "signals stopped matching — the known vendored files (g7_conformance.py, "
        "eol_catalog.py, …) should always match."
    )

    unattributed = [p for p in derivatives if p.as_posix() not in notices_text]
    assert not unattributed, (
        "these files carry upstream-derived material but are not listed in "
        f"THIRD_PARTY_NOTICES.md: {[p.as_posix() for p in unattributed]}"
    )


# Vendored DATA, as opposed to vendored code. The scan above keys on signals
# that only appear in source files ("ported from ...", an upstream path), so a
# snapshot of somebody else's database passes it without ever being looked at —
# which is how the OSORI snapshot reached the tree unattributed.
#
# Each entry maps the file to a string that must appear in
# THIRD_PARTY_NOTICES.md. The string is the upstream's own name, not the path,
# because attribution is owed to the source and not to our file layout.
VENDORED_DATA = {
    Path("apps/backend/services/eol/eol_snapshot.json"): "endoflife.date",
    Path("apps/backend/services/license_osori/osori_snapshot.json"): "OSORI",
    Path("apps/backend/services/g7_registry.json"): "BomLens",
    Path("apps/backend/services/malicious/malicious_snapshot.json"): "OSV",
}

#: Files matching this shape must be registered above. Without it the registry
#: is a list someone remembers to update, which is not a gate.
VENDORED_DATA_GLOBS = ("services/**/*_snapshot.json", "services/*_registry.json")


def test_every_vendored_data_file_is_registered() -> None:
    """A new snapshot cannot be added without deciding what it owes upstream."""
    backend = REPO_ROOT / "apps" / "backend"
    discovered = {
        path.relative_to(REPO_ROOT)
        for glob in VENDORED_DATA_GLOBS
        for path in backend.glob(glob)
        if path.is_file()
    }
    assert discovered, (
        "the vendored-data scan found nothing — the globs stopped matching, "
        "which reports success while checking no files at all"
    )

    unregistered = sorted(p.as_posix() for p in discovered - set(VENDORED_DATA))
    assert not unregistered, (
        f"these vendored data files are not in VENDORED_DATA: {unregistered}. "
        "Add each one with the upstream name that must appear in "
        "THIRD_PARTY_NOTICES.md, and add the notice entry too."
    )


def test_vendored_data_is_attributed(notices_text: str) -> None:
    """Each vendored data file, and its upstream, appear in the notices."""
    problems: list[str] = []
    for rel, upstream in VENDORED_DATA.items():
        if not (REPO_ROOT / rel).is_file():
            problems.append(f"{rel.as_posix()} is registered but does not exist")
            continue
        if rel.as_posix() not in notices_text:
            problems.append(f"{rel.as_posix()} is not named in THIRD_PARTY_NOTICES.md")
        if upstream not in notices_text:
            problems.append(
                f"{rel.as_posix()}: upstream '{upstream}' is not credited "
                "in THIRD_PARTY_NOTICES.md"
            )
    assert not problems, problems


def test_the_osori_snapshot_carries_its_own_attribution() -> None:
    """ODC-By 1.0 wants attribution wherever the database is used.

    The notices file covers the repository, but the snapshot also travels
    inside images and can be replaced at runtime via ``OSORI_SNAPSHOT_PATH``.
    Carrying the credit in the file itself means it survives being separated
    from this repository — which is the case the licence is actually about.
    """
    snapshot = json.loads(
        _read(REPO_ROOT / "apps/backend/services/license_osori/osori_snapshot.json")
    )
    source = snapshot.get("_source", "")
    assert "OSORI" in source and "ODC-By" in source, (
        f"the OSORI snapshot's _source field reads {source!r}; it must name "
        "both the database and its licence"
    )


def test_notices_credits_bomlens_copyright_holder(notices_text: str) -> None:
    """The upstream attribution notice is reproduced, per Apache-2.0 §4(d).

    BomLens ships a NOTICE file, so its attribution notice must travel with our
    derivative works. The copyright line is the load-bearing part.
    """
    assert "SK Telecom Co., Ltd." in notices_text
    assert "github.com/sktelecom/bomlens" in notices_text
    assert "Apache License, Version 2.0" in notices_text


def test_root_notice_points_at_the_third_party_file() -> None:
    """The root NOTICE routes readers to the attribution file.

    §4(d) allows the notices to live in the source or documentation rather than
    in NOTICE itself, but only if a recipient can actually find them.
    """
    notice = _read(REPO_ROOT / "NOTICE")
    assert "THIRD_PARTY_NOTICES.md" in notice
    assert "TRUSCA contributors" in notice, (
        "the root NOTICE must still state TRUSCA's own copyright — third-party "
        "attribution is additive, not a replacement."
    )


def test_license_appendix_names_trusca() -> None:
    """The LICENSE appendix copyright line matches NOTICE (no stale brand)."""
    license_text = _read(REPO_ROOT / "LICENSE")
    assert "Copyright 2026 TRUSCA contributors" in license_text
    assert "TrustedOSS Portal contributors" not in license_text
