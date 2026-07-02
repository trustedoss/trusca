"""
E2E seed helper — Phase 2 PR #9 + Phase 3 PR #10 + Phase 3 PR #11 + Phase 3 PR #13.

The frontend e2e suite (``apps/frontend/tests/e2e/scan_flow.spec.ts``,
``apps/frontend/tests/e2e/project_detail.spec.ts``,
``apps/frontend/tests/e2e/vulnerabilities.spec.ts``,
``apps/frontend/tests/e2e/obligations.spec.ts``) needs a user with team
memberships and one or more projects so it can drive the project list,
detail, and scan progress flows. The auth surface has no team-creation
endpoint by design (Phase 3 work — onboarding wizard) and brand-new users
have no memberships, so the e2e cannot bootstrap itself purely via REST.

This script bridges the gap: invoked from a Playwright spec via
``child_process``, it creates an organization + team + user + membership +
``N`` projects directly against the live Postgres, then prints a JSON
summary to stdout that the test parses.

For PR #10 (Project Detail) the script optionally also seeds:

  * a single ``succeeded`` scan per project (``--with-scan``)
  * ``--component-count`` rows of components attached to that scan with a
    deterministic round-robin distribution across severity (critical,
    high, medium, low, info, none) and license_category (forbidden,
    conditional, allowed, unknown). Names are generated as ``{prefix}-N``
    so spec searches like ``searchComponents("react")`` can hit a known
    prefix without having to fetch the seeded id list.

For PR #13 (Obligations tab) the script optionally also seeds:

  * ``--with-obligations`` attaches a small obligation catalog to each of
    the seed-licenses created by ``--component-count``. Two obligations per
    license (kind + text + link) so distribution / list / NOTICE scenarios
    have meaningful rows. No-op when ``--component-count`` is 0 (no
    licenses are created in that mode).

For PR #11 (Vulnerabilities tab) the script optionally also seeds:

  * ``--vulnerability-count N`` distinct VulnerabilityFinding rows attached
    to fresh component_versions on the first project's scan. Each finding
    gets a fresh Vulnerability row with a deterministic severity + status
    mix. The default mix is::

        critical=2, high=5, medium=10, low=20, info=5, unknown=2

    Override the mix with ``--vulnerability-severity-mix
    'critical:N,high:N,...'`` (any unspecified bucket defaults to 0).
    Statuses cycle: 80% ``new``, 15% ``analyzing``, 5% ``not_affected`` so
    filter-by-status scenarios exercise multiple values.

Why a Python script and not Node? psycopg / asyncpg + the SQLAlchemy
factories (``tests._helpers``) are already available in this repo. Pulling
``pg`` into the frontend package just to seed a few rows would balloon the
dependency surface for one feature.

Usage:

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names alpha,beta,gamma \\
        --password 'Sup3rSecret!aabbccdd'

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names ci-smoke \\
        --with-scan \\
        --component-count 200 \\
        --component-prefix react

    python3 apps/backend/scripts/seed_e2e_user.py \\
        --project-names ci-vulns \\
        --with-scan \\
        --vulnerability-count 44

Output (stdout, single JSON line):

    {"email": "...", "password": "...", "user_id": "...",
     "team_id": "...", "project_names": ["alpha","beta","gamma"],
     "project_ids": ["...", "...", "..."],
     "scan_ids": ["...", "...", "..."],
     "component_count": 200,
     "vulnerability_count": 44}

Exit code: 0 on success, non-zero on any failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Environments where this seed script is allowed to mint a super-admin via
# ``--super-admin`` (security-reviewer F8 / CWE-489 Active Debug Code).
# Any other value of ``APP_ENV`` (production / staging / unset) refuses the
# operation. The unset case is a deliberate footgun-prevention default — a
# forgotten ``APP_ENV`` MUST NOT allow a super-admin to spawn from a
# convenience script that ended up in the prod image. Phase 7 PR #20 will
# additionally exclude ``scripts/`` from the prod Dockerfile build context.
_SUPER_ADMIN_ALLOWED_ENVS = frozenset({"dev", "test", "ci"})


# Allow running the script from any cwd — adds the backend root to sys.path
# so `from tests._helpers import ...` resolves.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Round-robin distributions used when --component-count > 0. Kept short so
# every bucket is hit even at small counts (n=12 → all severities touched
# at least twice).
_SEVERITY_CYCLE = ("critical", "high", "medium", "low", "info", "none")
_LICENSE_CATEGORY_CYCLE = ("forbidden", "conditional", "allowed", "unknown")

# PR #11 — vulnerability seed mix.
# Sum across buckets is the default `--vulnerability-count` (44) so callers
# that do not pass --vulnerability-count get a sane out-of-the-box mix when
# they request --vulnerability-count by itself.
_DEFAULT_VULN_SEVERITY_MIX: dict[str, int] = {
    "critical": 2,
    "high": 5,
    "medium": 10,
    "low": 20,
    "info": 5,
    "unknown": 2,
}
# Status mix — 80% new, 15% analyzing, 5% not_affected.
_VULN_STATUS_CYCLE: tuple[str, ...] = (
    *(("new",) * 16),
    *(("analyzing",) * 3),
    *(("not_affected",) * 1),
)
_VULN_SEVERITY_VALUES: frozenset[str] = frozenset(
    {"critical", "high", "medium", "low", "info", "unknown"}
)

# v2.1 "EPSS UI first-class" — seed a spread of EPSS (score, percentile) pairs
# so e2e sort/filter scenarios have meaningful, distinct values to assert on.
# `None` entries exercise the "EPSS absent" rendering path (NULLS LAST sort,
# em-dash cell). Each entry is (epss_score, epss_percentile) or None.
#   - 0.97 / 0.99  — high exploit probability (top of the list)
#   - 0.30 / 0.65  — middling
#   - 0.001/0.05   — low
#   - None         — unscored CVE (older DT / no EPSS publication)
# The cycle deliberately decouples EPSS from severity so the bulk seed below
# can place a high-CVSS / low-EPSS divergence (e.g. critical + 0.001) — the
# canonical "scary CVSS, unlikely to be exploited" triage demo case.
_EPSS_CYCLE: tuple[tuple[Decimal, Decimal] | None, ...] = (
    (Decimal("0.97000"), Decimal("0.99000")),
    (Decimal("0.30000"), Decimal("0.65000")),
    (Decimal("0.00100"), Decimal("0.05000")),
    None,
)

# PR #13 — obligation catalog seed.
# Two obligations per category-license. Kind matches the canonical ranking
# advertised by KNOWN_OBLIGATION_KINDS (schemas.obligation_detail) so the
# distribution payload renders in a stable order. Text and link are stubs;
# their length is comfortably > 50 chars so e2e content checks have material
# to grep against.
_OBLIGATIONS_BY_CATEGORY: dict[str, tuple[tuple[str, str, str], ...]] = {
    "forbidden": (
        (
            "copyleft",
            "Distribution requires releasing source code under the same license terms.",
            "https://example.invalid/policy/forbidden-copyleft",
        ),
        (
            "source-disclosure",
            "Customers must be granted access to the corresponding source code on demand.",
            "https://example.invalid/policy/forbidden-source",
        ),
    ),
    "conditional": (
        (
            "attribution",
            "You must include the original copyright notice in user-facing materials.",
            "https://example.invalid/policy/conditional-attribution",
        ),
        (
            "modifications",
            "Modified files must carry prominent notices of the changes made.",
            "https://example.invalid/policy/conditional-modifications",
        ),
    ),
    "allowed": (
        (
            "attribution",
            "Include the original copyright notice when redistributing source or binaries.",
            "https://example.invalid/policy/allowed-attribution",
        ),
        (
            "no-endorsement",
            "Do not use the project name or contributors to endorse derivative products.",
            "https://example.invalid/policy/allowed-no-endorsement",
        ),
    ),
    "unknown": (
        (
            "attribution",
            "License terms could not be determined automatically — preserve any attribution found.",
            "https://example.invalid/policy/unknown-attribution",
        ),
    ),
}


# ── G3.3 — preserved-source staging fixture ────────────────────────────────
# The source-tree e2e (apps/frontend/tests/e2e/source_tree.spec.ts S3/S4)
# needs a preserved tarball whose tree exercises every viewer code path:
#
#   src/app/main.py        utf-8 text file carrying an MIT header on lines 1-3
#                          (a license_matches range in the folded scancode JSON)
#   src/app/logo.bin       binary file (NUL byte) → byte-safe-notice path
#   src/app/huge.txt       oversized file (> the 2 MiB viewer cap) → truncated
#   README.md              a root-level text file so the root listing is non-empty
#
# The viewer's per-file content cap is `scan_source_viewer_max_file_bytes()`
# (default 2 MiB). `huge.txt` is sized just over that so the API returns
# `truncated=true`; the value is read at staging time so a non-default cap in
# the e2e environment still produces a truncated member.
_SOURCE_TEXT_FILE_REL = "src/app/main.py"
_SOURCE_BINARY_FILE_REL = "src/app/logo.bin"
_SOURCE_HUGE_FILE_REL = "src/app/huge.txt"
_SOURCE_README_REL = "README.md"

# The utf-8 text member's content. Lines 1-3 carry an MIT header so the
# synthetic scancode JSON can attach a `matches` range to exactly those lines —
# the source-tree viewer then highlights them with an MIT SPDX chip (S3).
_SOURCE_TEXT_CONTENT = (
    "# SPDX-License-Identifier: MIT\n"
    "# Copyright (c) 2026 TrustedOSS e2e fixture\n"
    "# Permission is hereby granted, free of charge, to any person ...\n"
    "\n"
    'def main() -> None:\n'
    '    print("hello from the preserved-source e2e fixture")\n'
    "\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)


def _build_synthetic_scancode_json(*, files_with_matches: dict[str, str]) -> str:
    """Return a minimal scancode 32.x result document as a JSON string.

    ``files_with_matches`` maps an arcname → SPDX id; each entry gets a single
    ``license_detections[].matches[]`` covering lines 1-3 so the source-tree
    viewer's per-line projection (``_matches_from_scancode``) surfaces a chip.
    The shape mirrors the real adapter output that
    ``source_tree_service._matches_from_scancode`` reads:

        {"files": [{"path": "<arc>", "type": "file",
                    "license_detections": [{"matches": [
                        {"license_expression_spdx": "MIT",
                         "start_line": 1, "end_line": 3, "score": 100.0}]}]}]}
    """
    files: list[dict[str, object]] = []
    for arcname, spdx in files_with_matches.items():
        files.append(
            {
                "path": arcname,
                "type": "file",
                "detected_license_expression_spdx": spdx,
                "license_detections": [
                    {
                        "license_expression_spdx": spdx,
                        "matches": [
                            {
                                "license_expression_spdx": spdx,
                                "start_line": 1,
                                "end_line": 3,
                                "score": 100.0,
                            }
                        ],
                    }
                ],
            }
        )
    document = {
        "headers": [{"tool_name": "scancode-toolkit", "tool_version": "e2e-seed"}],
        "files": files,
    }
    return json.dumps(document, indent=2)


def _stage_preserved_source(
    *,
    project_id: uuid.UUID,
    scan_id: uuid.UUID,
) -> str | None:
    """Build a tiny source tree + scancode JSON and preserve it as a tarball.

    Reuses ``preserve_scan_source`` so the on-disk tarball is byte-identical in
    shape to a real scan's preserved source (gzip tar + folded-in
    ``.trustedoss/scancode.json``) — guaranteeing ``source_tree_service`` reads
    it back without surprises.

    Returns the tarball path as a string on success, or ``None`` when
    preservation was skipped (quota / over-cap — never raised, mirroring the
    service contract). The caller records a ``source_tarball`` ScanArtifact row
    when a path is returned.
    """
    import tempfile
    from pathlib import Path

    from core.config import scan_source_viewer_max_file_bytes
    from services.source_preservation_service import preserve_scan_source

    viewer_cap = scan_source_viewer_max_file_bytes()

    # Stage the tree under a throwaway temp dir; preserve_scan_source tars it
    # into the project's scan-sources/ directory, after which the temp dir is
    # removed. The tarball (not the staging dir) is what the viewer reads.
    staging_root = Path(tempfile.mkdtemp(prefix="e2e-source-"))
    try:
        source_dir = staging_root / "source"
        # Nested directory + utf-8 text file with an MIT header.
        text_path = source_dir / _SOURCE_TEXT_FILE_REL
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(_SOURCE_TEXT_CONTENT, encoding="utf-8")

        # Root-level text file so the root listing is non-empty.
        (source_dir / _SOURCE_README_REL).write_text(
            "# e2e preserved-source fixture\n\nSee src/app/main.py.\n",
            encoding="utf-8",
        )

        # Binary file — a NUL byte makes source_tree_service classify it as
        # binary (encoding='binary', content=None) → byte-safe notice (S4).
        (source_dir / _SOURCE_BINARY_FILE_REL).write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(64)
        )

        # Oversized file — just over the viewer's per-file cap so read_file
        # returns truncated=true (S4). Repeated ASCII gzips to a few KiB so the
        # tarball stays well under the 512 MiB tarball cap.
        huge_path = source_dir / _SOURCE_HUGE_FILE_REL
        huge_bytes = b"A" * (viewer_cap + 4096)
        huge_path.write_bytes(huge_bytes)

        # Synthetic scancode JSON attaching an MIT match to the text file's
        # lines 1-3 so the per-line license chip surfaces in S3.
        scancode_json = _build_synthetic_scancode_json(
            files_with_matches={_SOURCE_TEXT_FILE_REL: "MIT"}
        )
        scancode_path = staging_root / "scancode.json"
        scancode_path.write_text(scancode_json, encoding="utf-8")

        tar_path = preserve_scan_source(
            scan_id=scan_id,
            project_id=project_id,
            source_dir=source_dir,
            scancode_json_path=scancode_path,
        )
        return str(tar_path) if tar_path is not None else None
    finally:
        import shutil

        shutil.rmtree(staging_root, ignore_errors=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed an e2e user + projects.")
    parser.add_argument(
        "--project-names",
        default="alpha",
        help="Comma-separated project names. Default: 'alpha'.",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Override the seeded password. Default: random strong password.",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Override the seeded email. Default: e2e-<uuid>@example.com.",
    )
    parser.add_argument(
        "--with-scan",
        action="store_true",
        default=False,
        help=(
            "Seed a `succeeded` scan per project and wire it as "
            "project.latest_scan_id. Required when --component-count > 0."
        ),
    )
    parser.add_argument(
        "--with-sbom",
        action="store_true",
        default=False,
        help=(
            "Seed a kind='sbom' (received-SBOM) succeeded scan on the FIRST "
            "project, plus its conformance verdict (model 3). Independent of "
            "--with-scan. The resulting scan id is returned as `sbom_scan_id` so "
            "an e2e can open /scans/<id> and assert the conformance panel."
        ),
    )
    parser.add_argument(
        "--with-g7",
        action="store_true",
        default=False,
        help=(
            "feat/g7-conformance. Append 4 advisory G7 AI minimum-element "
            "checks (pass x2 / absent-warn x1 / human-review x1, across the "
            "'slp' + 'models' clusters) to the seeded conformance verdict so "
            "the e2e can assert the G7 section of the panel. Statuses are "
            "pinned from the REAL evaluator's output over "
            "tests/fixtures/sbom_ingest/aibom-owasp-1_7.json; label/cluster/"
            "source metadata comes from the live g7_registry.json. Implies "
            "--with-sbom. Summary gains `g7_check_count`."
        ),
    )
    parser.add_argument(
        "--component-count",
        type=int,
        default=0,
        help=(
            "Number of components to attach to the first project's scan. "
            "Default: 0 (no components seeded). Implies --with-scan."
        ),
    )
    parser.add_argument(
        "--component-prefix",
        default="comp",
        help=(
            "Name prefix for the seeded components. Component i is named "
            "'{prefix}-{i}'. Default: 'comp'. e2e search scenarios fix this "
            "to a known string (e.g. 'react') so they can match a row by "
            "substring without having to learn ids."
        ),
    )
    parser.add_argument(
        "--with-obligations",
        action="store_true",
        default=False,
        help=(
            "Phase 3 PR #13. Attach a small obligation catalog (1-2 rows) "
            "to each seed-license created by --component-count. No-op when "
            "--component-count is 0 because no seed-licenses exist."
        ),
    )
    parser.add_argument(
        "--with-source",
        action="store_true",
        default=False,
        help=(
            "G3.3 source-tree e2e. Stage a real preserved-source tarball for "
            "the first project's succeeded scan so the source-tree endpoints "
            "(`/source-tree`, `/source-file`) return 200 instead of the 404 "
            "empty-state. Builds a tiny tree (nested dir + utf-8 text file "
            "with an MIT license_matches range + a binary file + an oversized "
            "truncated file) and a synthetic scancode JSON, then reuses "
            "services.source_preservation_service.preserve_scan_source(...) so "
            "the tarball format matches what source_tree_service reads "
            "(`.trustedoss/scancode.json` folded in). Records a "
            "`source_tarball` ScanArtifact row. Implies --with-scan. No-op "
            "without a project. Default: off (no regression to existing seeds)."
        ),
    )
    parser.add_argument(
        "--vulnerability-count",
        type=int,
        default=0,
        help=(
            "Phase 3 PR #11. Number of CVE findings to attach to the first "
            "project's scan. Each finding gets a fresh component_version + "
            "Vulnerability with a deterministic severity + status mix. "
            "Default: 0 (no findings seeded). Implies --with-scan."
        ),
    )
    parser.add_argument(
        "--vulnerability-severity-mix",
        default=None,
        help=(
            "Override the default severity mix for --vulnerability-count. "
            "Format: 'critical:N,high:N,medium:N,low:N,info:N,unknown:N'. "
            "Buckets not listed default to 0; the sum is clamped to "
            "--vulnerability-count. Default: 'critical:2,high:5,medium:10,"
            "low:20,info:5,unknown:2'."
        ),
    )
    # ── Phase 4 PR #13 — admin e2e fixtures ────────────────────────────────
    parser.add_argument(
        "--super-admin",
        action="store_true",
        default=False,
        help=(
            "Phase 4 PR #13. Mark the seeded primary user as a super-admin "
            "(``User.is_superuser=True``). Required for the admin-panel e2e "
            "scenarios that exercise ``/admin/users`` and ``/admin/teams``."
        ),
    )
    parser.add_argument(
        "--extra-members",
        type=int,
        default=0,
        help=(
            "Phase 4 PR #13. Seed N additional users with ``developer`` role "
            "in the same team as the primary user. Their emails follow "
            "``e2e-extra-{i}-<suffix>@example.com`` and they share the "
            "primary user's password. Output JSON gets an ``extra_members`` "
            "list with per-user ``user_id``/``email``/``role`` triples."
        ),
    )
    parser.add_argument(
        "--extra-team-admin",
        action="store_true",
        default=False,
        help=(
            "Phase 4 PR #13. When combined with --extra-members, the *first* "
            "extra user is given the ``team_admin`` role instead of "
            "``developer``. Used by the role-management scenarios."
        ),
    )
    # ── Phase 5 D bundle — Connected Accounts e2e fixtures ──────────────────
    parser.add_argument(
        "--with-oauth-identity",
        choices=("github", "google"),
        default=None,
        help=(
            "Phase 5 D bundle. Insert one OAuthIdentity row for the primary "
            "user pinned to the chosen provider with a deterministic test "
            "fixture for ``provider_user_id`` and ``email``. Used by the "
            "auth_and_profile e2e to exercise the Unlink-with-fallback "
            "scenario without driving a real OAuth callback. The user "
            "still gets the password the seed script normally sets, so "
            "the SPA login flow keeps working — the OAuth identity is a "
            "secondary auth method."
        ),
    )
    # ── Marathon bundle 2 (D1) — OAuth-only user fixture ───────────────────
    parser.add_argument(
        "--no-password",
        action="store_true",
        default=False,
        help=(
            "Marathon bundle 2 (D1). Provision an OAuth-only user — "
            "``hashed_password`` is set to an empty string so password login "
            "always fails (bcrypt verify of '' against '' is rejected by "
            "passlib). Requires ``--with-oauth-identity`` so the user has at "
            "least one auth method; refused with ValueError otherwise. When "
            "set, the seed also mints + persists a refresh token and emits "
            "``refresh_token`` + ``refresh_token_cookie_name`` in the JSON so "
            "the e2e can ``addCookies`` instead of trying password login. "
            "Used by ``auth_and_profile.spec.ts`` test 3 (last-only "
            "blocks-login)."
        ),
    )
    parser.add_argument(
        "--with-refresh-token",
        action="store_true",
        default=False,
        help=(
            "Mint + persist a refresh token for a PASSWORD user too (the "
            "--no-password path already does this for OAuth-only users). The "
            "e2e then authenticates via the refresh-cookie path "
            "(auth.loginViaRefreshCookie) instead of POST /auth/login, so a "
            "full single-IP suite run never trips the 5/min login limiter "
            "(test-hardening Tier N follow-up)."
        ),
    )
    # ── Marathon bundle 5 (4a) — header bell unread badge fixture ───────────
    parser.add_argument(
        "--with-notifications",
        type=int,
        default=0,
        metavar="COUNT",
        help=(
            "Marathon bundle 5 (4a). Insert COUNT unread notifications for "
            "the primary seeded user so the screenshot capture for the "
            "user-guide notifications page can show the bell badge with a "
            "non-zero count. Kinds rotate through the closed enum so the "
            "list page renders mixed icons. Default: 0 (no notifications)."
        ),
    )
    return parser.parse_args()


def _parse_severity_mix(raw: str | None, *, total: int) -> dict[str, int]:
    """Parse the ``--vulnerability-severity-mix`` flag.

    Returns a dict keyed by severity bucket. Values are clamped to the
    requested total (truncating proportionally is not worth the
    complexity for an e2e seed; we just stop emitting once we've reached
    the count). Invalid buckets are ignored with a stderr warning.
    """
    if raw is None or not raw.strip():
        # Use the default mix scaled to `total` if the caller didn't override.
        default = dict(_DEFAULT_VULN_SEVERITY_MIX)
        default_sum = sum(default.values())
        if total == default_sum:
            return default
        # Caller asked for a non-default total — use the default ratios.
        out: dict[str, int] = {}
        remaining = total
        keys = list(default.keys())
        for i, key in enumerate(keys):
            if i == len(keys) - 1:
                out[key] = max(remaining, 0)
            else:
                share = round(default[key] * total / default_sum) if default_sum else 0
                share = max(0, min(share, remaining))
                out[key] = share
                remaining -= share
        return out

    parsed: dict[str, int] = {sev: 0 for sev in _VULN_SEVERITY_VALUES}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            print(f"ignoring malformed severity mix entry: {chunk!r}", file=sys.stderr)
            continue
        sev, _, n_raw = chunk.partition(":")
        sev = sev.strip().lower()
        if sev not in _VULN_SEVERITY_VALUES:
            print(f"ignoring unknown severity bucket: {sev!r}", file=sys.stderr)
            continue
        try:
            n = int(n_raw.strip())
        except ValueError:
            print(f"ignoring non-integer count in {chunk!r}", file=sys.stderr)
            continue
        parsed[sev] = max(parsed[sev], n)

    return parsed


# feat/g7-conformance — pinned statuses for the ``--with-g7`` seed. The mix
# (pass / absent-warn / human-review across TWO clusters) was captured from the
# REAL evaluator (``services.g7_conformance.evaluate_g7``) over the recorded
# fixture ``tests/fixtures/sbom_ingest/aibom-owasp-1_7.json`` — no invented
# values, only a deterministic subset. Detail strings are the evaluator's own
# constants; label/cluster/source/role are resolved from the live
# ``g7_registry.json`` at seed time so a registry refresh cannot desync the
# persisted shape. id → (status, detail, evidence).
_G7_SEED_PLAN: dict[str, tuple[str, str, list[str] | None]] = {
    # slp cluster — pass (source=declared)
    "g7-slp-name": ("pass", "present", None),
    # slp cluster — no automated source (source=na) → human review
    "g7-slp-data-flow": (
        "warn",
        "requires human review (no automated source)",
        None,
    ),
    # models cluster — automated but absent → advisory warn (source=auto)
    "g7-model-hash-value": ("warn", "not present in the SBOM", None),
    # models cluster — pass with real evidence (fixture model is Apache-2.0)
    "g7-model-license": ("pass", "present", ["Apache-2.0"]),
}


def _g7_seed_checks() -> list:
    """Build the ``--with-g7`` advisory checks (list of ``Check``).

    Iterates the registry in document order so the persisted per-cluster row
    order matches what ``evaluate_g7`` would emit for the same elements.
    Raises ``ValueError`` when a planned element vanished from the registry —
    a silent drop would make the e2e assert against a partial seed.
    """
    from services import g7_conformance as _g7
    from services.sbom_conformance import Check

    checks: list[Check] = []
    for cluster_id, element in _g7.iter_elements():
        element_id = str(element.get("id") or "")
        plan = _G7_SEED_PLAN.get(element_id)
        if plan is None:
            continue
        status, detail, evidence = plan
        checks.append(
            Check(
                id=element_id,
                label=str(element.get("label") or element_id),
                required=False,
                status=status,
                detail=detail,
                cluster=cluster_id,
                source=str(element.get("source") or "") or None,
                role=str(element.get("role") or "") or None,
                evidence=evidence,
            )
        )
    if len(checks) != len(_G7_SEED_PLAN):
        missing = sorted(set(_G7_SEED_PLAN) - {c.id for c in checks})
        raise ValueError(
            f"g7 registry no longer contains seed elements: {missing}"
        )
    return checks


async def _seed(  # noqa: PLR0915 — a single linear seed routine reads better than 5 helpers
    *,
    project_names: list[str],
    email: str | None,
    password: str | None,
    with_scan: bool,
    with_sbom: bool = False,
    with_g7: bool = False,
    component_count: int,
    component_prefix: str,
    vulnerability_count: int = 0,
    vulnerability_severity_mix: str | None = None,
    with_obligations: bool = False,
    with_source: bool = False,
    super_admin: bool = False,
    extra_members: int = 0,
    extra_team_admin: bool = False,
    with_oauth_identity: str | None = None,
    no_password: bool = False,
    with_refresh_token: bool = False,
    with_notifications: int = 0,
) -> dict[str, object]:
    """Create the org/team/user/membership/projects[/scans/components]."""
    # M2 — defense-in-depth: re-check APP_ENV inside _seed so the guard
    # cannot be bypassed by calling _seed() directly (e.g. from a test helper
    # that skips main()).  The check in main() is the primary gate; this one
    # catches accidental direct invocations.
    if super_admin:
        _refuse_super_admin_outside_safe_env()
    # Marathon bundle 2 (D1) — OAuth-only user must keep at least one auth
    # method or the user becomes unrecoverable. Refuse before any DB work
    # so the caller sees a clean ValueError instead of an opaque foreign-key
    # / NOT-NULL surprise.
    if no_password and with_oauth_identity is None:
        raise ValueError(
            "--no-password requires --with-oauth-identity so the seeded "
            "user has at least one authentication method."
        )
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from core.config import database_url
    from core.security import (
        create_refresh_token,
        hash_password,
        hash_refresh_token,
    )
    from models import (
        Component,
        ComponentVersion,
        License,
        LicenseFinding,
        Membership,
        OAuthIdentity,
        Obligation,
        Organization,
        Project,
        RefreshToken,
        SbomConformance,
        Scan,
        ScanArtifact,
        ScanComponent,
        Team,
        User,
        Vulnerability,
        VulnerabilityFinding,
    )

    # When --no-password is set the password field is irrelevant — `chosen_password`
    # stays empty so the JSON output reflects "no password set" honestly.
    if no_password:
        chosen_password = ""
    else:
        chosen_password = password or f"Sup3rSecret!{uuid.uuid4().hex[:12]}"
    chosen_email = email or f"e2e-{uuid.uuid4().hex[:12]}@example.com"

    # --component-count implies --with-scan; we cannot attach components
    # without a scan to anchor on.
    if component_count > 0 and not with_scan:
        with_scan = True
    # --vulnerability-count likewise implies --with-scan.
    if vulnerability_count > 0 and not with_scan:
        with_scan = True
    # --with-source needs a succeeded scan to anchor the preserved tarball on.
    if with_source and not with_scan:
        with_scan = True

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as session:
            suffix = uuid.uuid4().hex[:10]
            org = Organization(name=f"E2E Org {suffix}", slug=f"e2e-org-{suffix}")
            session.add(org)
            await session.commit()
            await session.refresh(org)

            team = Team(
                organization_id=org.id,
                name=f"E2E Team {suffix}",
                slug=f"e2e-team-{suffix}",
            )
            session.add(team)
            await session.commit()
            await session.refresh(team)

            # When --no-password is requested we store an empty string. The
            # User model's column is NOT NULL, but the auth flow's
            # ``has_password = bool(user.hashed_password)`` check (in
            # services/oauth_identity_service.py) treats "" as "no password",
            # which is exactly what the OAuth-only fixture needs to trip
            # OAuthUnlinkBlocksLoginError on the last identity.
            hashed_pw = "" if no_password else hash_password(chosen_password)
            user = User(
                email=chosen_email.strip().lower(),
                hashed_password=hashed_pw,
                full_name="E2E Seed User",
                is_active=True,
                is_superuser=super_admin,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            # Primary user always gets a developer membership in the team so
            # team-scoped flows (project list, scans) keep working. The
            # ``is_superuser`` flag is the source of truth for the admin
            # existence-hide guard, independent of this membership row.
            membership = Membership(
                user_id=user.id, team_id=team.id, role="developer"
            )
            session.add(membership)
            await session.commit()

            # Phase 4 PR #13 — extra members for admin e2e scenarios.
            extra_members_summary: list[dict[str, str]] = []
            if extra_members > 0:
                hashed = hash_password(chosen_password)
                for i in range(extra_members):
                    role = (
                        "team_admin"
                        if extra_team_admin and i == 0
                        else "developer"
                    )
                    extra_email = f"e2e-extra-{i}-{suffix}@example.com"
                    extra_user = User(
                        email=extra_email,
                        hashed_password=hashed,
                        full_name=f"E2E Extra User {i}",
                        is_active=True,
                        is_superuser=False,
                        is_verified=True,
                    )
                    session.add(extra_user)
                    await session.flush()  # need extra_user.id

                    extra_membership = Membership(
                        user_id=extra_user.id,
                        team_id=team.id,
                        role=role,
                    )
                    session.add(extra_membership)
                    extra_members_summary.append(
                        {
                            "user_id": str(extra_user.id),
                            "email": extra_user.email,
                            "role": role,
                        }
                    )
                await session.commit()

            # Phase 5 D bundle — seed an OAuthIdentity row when requested so
            # the auth_and_profile e2e can exercise the Unlink flow without
            # driving a real IdP callback. provider_user_id is a deterministic
            # test fixture pinned to the suffix so concurrent seed runs do
            # not collide on the (provider, provider_user_id) unique index.
            oauth_identity_summary: dict[str, str] | None = None
            if with_oauth_identity is not None:
                oauth_row = OAuthIdentity(
                    user_id=user.id,
                    provider=with_oauth_identity,
                    provider_user_id=f"e2e-{suffix}",
                    email=user.email,
                    avatar_url=None,
                )
                session.add(oauth_row)
                await session.commit()
                await session.refresh(oauth_row)
                oauth_identity_summary = {
                    "id": str(oauth_row.id),
                    "provider": with_oauth_identity,
                    "provider_user_id": oauth_row.provider_user_id,
                }

            # Marathon bundle 2 (D1) — mint a refresh token + persist its row
            # when --no-password is set, so the e2e can authenticate via the
            # refresh-cookie path (the only viable entry for an OAuth-only
            # user without driving a real IdP callback). The /auth/refresh
            # endpoint reads this cookie, looks up the row by jti, and issues
            # an access token.
            refresh_token_summary: dict[str, str] | None = None
            if no_password or with_refresh_token:
                token_str, jti, expires_at = create_refresh_token(
                    subject=str(user.id)
                )
                token_row = RefreshToken(
                    user_id=user.id,
                    jti=jti,
                    token_hash=hash_refresh_token(token_str),
                    parent_jti=None,
                    expires_at=expires_at,
                )
                session.add(token_row)
                await session.commit()
                refresh_token_summary = {
                    "token": token_str,
                    "cookie_name": "refresh_token",
                    "expires_at": expires_at.isoformat(),
                }

            # Marathon bundle 5 (4a) — header bell unread-badge fixture.
            # Insert COUNT unread notifications spread across the closed
            # kind enum so the screenshot capture sees a mixed list +
            # non-zero badge.
            seeded_notifications = 0
            if with_notifications > 0:
                from models import Notification

                _kinds = (
                    "scan_completed",
                    "cve_detected",
                    "policy_gate_failed",
                    "approval_pending",
                    "license_violation",
                )
                _bodies = {
                    "scan_completed": "Project scan completed successfully.",
                    "cve_detected": "New CVE-2099-EXAMPLE detected in component X.",
                    "policy_gate_failed": "Build gate blocked: forbidden license found.",
                    "approval_pending": "Component approval request pending review.",
                    "license_violation": "Conditional license requires legal review.",
                }
                for i in range(with_notifications):
                    kind = _kinds[i % len(_kinds)]
                    n = Notification(
                        user_id=user.id,
                        kind=kind,
                        title=f"{kind.replace('_', ' ').title()} #{i + 1}",
                        body=_bodies[kind],
                        link="/projects" if kind != "approval_pending" else "/approvals",
                    )
                    session.add(n)
                    seeded_notifications += 1
                await session.commit()

            project_ids: list[str] = []
            scan_ids: list[str] = []
            project_rows: list[Project] = []
            for name in project_names:
                slug = f"{name.lower()}-{uuid.uuid4().hex[:6]}"
                project = Project(
                    team_id=team.id,
                    name=name,
                    slug=slug,
                    description=f"Seeded for e2e — {name}",
                    # A git_url makes the SourceSelectDialog default to the
                    # "git" method, so the scan-flow e2e can trigger a scan
                    # without attaching a file. The clone itself is async and
                    # irrelevant to the specs (they assert the progress drawer's
                    # initial frame, not scan completion).
                    git_url=f"https://github.com/trustedoss-e2e/{slug}.git",
                    default_branch="main",
                    visibility="team",
                    created_by_user_id=user.id,
                )
                session.add(project)
                await session.commit()
                await session.refresh(project)
                project_ids.append(str(project.id))
                project_rows.append(project)

                if with_scan:
                    scan = Scan(
                        project_id=project.id,
                        kind="source",
                        status="succeeded",
                        progress_percent=100,
                        started_at=datetime.now(tz=UTC),
                        completed_at=datetime.now(tz=UTC),
                        scan_metadata={"seeded": True},
                    )
                    session.add(scan)
                    await session.commit()
                    await session.refresh(scan)
                    project.latest_scan_id = scan.id
                    project.updated_at = datetime.now(tz=UTC)
                    await session.commit()
                    await session.refresh(project)
                    scan_ids.append(str(scan.id))

            # Model 3 — seed a received-SBOM scan (kind='sbom') + its conformance
            # verdict on the FIRST project so the conformance-panel e2e can open
            # /scans/<sbom_scan_id> and assert the badge + per-check table. The
            # verdict is computed by the REAL scorer over a small inline CycloneDX
            # (deterministic 'warn' — full PURLs + graph + licenses but no hashes),
            # so the seeded row matches production shape exactly.
            sbom_scan_id: str | None = None
            g7_check_count = 0
            # --with-g7 rides on the sbom seed (implied at the CLI too; this
            # keeps direct _seed() callers honest).
            with_sbom = with_sbom or with_g7
            if with_sbom and project_rows:
                from services import sbom_conformance as _conf

                sbom_doc = {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "metadata": {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "tools": [{"name": "cdxgen"}],
                        "component": {
                            "type": "application",
                            "name": "seeded-sbom-app",
                            "version": "1.0.0",
                        },
                    },
                    "components": [
                        {
                            "type": "library",
                            "name": "lodash",
                            "version": "4.17.21",
                            "purl": "pkg:npm/lodash@4.17.21",
                            "licenses": [{"license": {"id": "MIT"}}],
                        },
                        {
                            "type": "library",
                            "name": "debug",
                            "version": "4.3.4",
                            "purl": "pkg:npm/debug@4.3.4",
                            "licenses": [{"license": {"id": "MIT"}}],
                        },
                    ],
                    "dependencies": [
                        {
                            "ref": "pkg:npm/debug@4.3.4",
                            "dependsOn": ["pkg:npm/lodash@4.17.21"],
                        }
                    ],
                }
                verdict = _conf.evaluate(json.dumps(sbom_doc).encode())
                # feat/g7-conformance — advisory G7 checks are appended to the
                # checks JSONB only; the verdict counters/result stay from the
                # core evaluate() (aggregation contract: cluster-tagged checks
                # never move the overall result).
                seeded_checks = [c.as_dict() for c in verdict.checks]
                if with_g7:
                    g7_checks = _g7_seed_checks()
                    seeded_checks.extend(c.as_dict() for c in g7_checks)
                    g7_check_count = len(g7_checks)
                first = project_rows[0]
                sbom_scan = Scan(
                    project_id=first.id,
                    kind="sbom",
                    status="succeeded",
                    progress_percent=100,
                    started_at=datetime.now(tz=UTC),
                    completed_at=datetime.now(tz=UTC),
                    scan_metadata={"seeded": True, "source_type": "sbom"},
                )
                session.add(sbom_scan)
                await session.commit()
                await session.refresh(sbom_scan)
                session.add(
                    SbomConformance(
                        scan_id=sbom_scan.id,
                        project_id=first.id,
                        source_format=verdict.source_format,
                        result=verdict.result,
                        n_fail=verdict.n_fail,
                        n_warn=verdict.n_warn,
                        component_count=verdict.component_count,
                        purl_coverage_pct=verdict.purl_coverage_pct,
                        license_coverage_pct=verdict.license_coverage_pct,
                        hash_coverage_pct=verdict.hash_coverage_pct,
                        checks=seeded_checks,
                    )
                )
                await session.commit()
                sbom_scan_id = str(sbom_scan.id)
                scan_ids.append(sbom_scan_id)

            # G3.3 — stage a preserved-source tarball for the first project's
            # scan so the source-tree viewer endpoints return a populated tree
            # (lights up source_tree.spec.ts S3/S4). The tarball is built via
            # preserve_scan_source so its on-disk shape exactly matches a real
            # scan's; we then record a `source_tarball` ScanArtifact row.
            source_tarball_path: str | None = None
            if with_source and project_rows:
                first_project = project_rows[0]
                anchor_scan_id = first_project.latest_scan_id
                assert anchor_scan_id is not None  # with_scan was forced True
                source_tarball_path = _stage_preserved_source(
                    project_id=first_project.id,
                    scan_id=anchor_scan_id,
                )
                if source_tarball_path is not None:
                    size = os.path.getsize(source_tarball_path)
                    artifact = ScanArtifact(
                        scan_id=anchor_scan_id,
                        kind="source_tarball",
                        storage_path=source_tarball_path,
                        byte_size=size,
                    )
                    session.add(artifact)
                    await session.commit()

            seeded_components = 0
            seeded_obligations_count = 0
            if component_count > 0 and project_rows:
                # Anchor every seeded component on the first project's scan.
                first_project = project_rows[0]
                anchor_scan_id = first_project.latest_scan_id
                assert anchor_scan_id is not None  # with_scan was forced True

                # Pre-create one license per category so we can attach a
                # license_finding deterministically per component.
                licenses_by_cat: dict[str, License] = {}
                for cat in _LICENSE_CATEGORY_CYCLE:
                    spdx = f"E2E-{cat[:4].upper()}-{suffix}"
                    licence = License(
                        spdx_id=spdx,
                        name=f"E2E License {cat}",
                        category=cat,
                    )
                    session.add(licence)
                    licenses_by_cat[cat] = licence
                await session.commit()
                for licence in licenses_by_cat.values():
                    await session.refresh(licence)

                # PR #13 — obligation catalog rows hanging off each seed
                # license. Only seeded when the caller asked for them so we
                # don't perturb existing PR #10 / PR #11 e2e fixtures that
                # don't expect obligations.
                if with_obligations:
                    for cat, licence in licenses_by_cat.items():
                        for kind, text, link in _OBLIGATIONS_BY_CATEGORY.get(cat, ()):
                            obligation = Obligation(
                                license_id=licence.id,
                                kind=kind,
                                text=text,
                                link=link,
                            )
                            session.add(obligation)
                            seeded_obligations_count += 1
                    if seeded_obligations_count:
                        await session.commit()

                # Pre-create one vulnerability per non-trivial severity. The
                # 'info' / 'none' buckets get no finding (so the component's
                # severity_max collapses to the absence of CVEs).
                vulns_by_severity: dict[str, Vulnerability] = {}
                # Per-severity EPSS so the component-mode seed exercises the
                # EPSS column. `critical` is deliberately the CVSS↔EPSS
                # divergence case: a 9.8 CVSS with a 0.001 EPSS ("scary score,
                # unlikely to be exploited") — the headline v2.1 triage demo.
                _SEV_EPSS: dict[str, tuple[Decimal, Decimal, Decimal] | None] = {
                    # severity: (cvss_score, epss_score, epss_percentile)
                    "critical": (Decimal("9.8"), Decimal("0.00100"), Decimal("0.05000")),
                    "high": (Decimal("8.1"), Decimal("0.97000"), Decimal("0.99000")),
                    "medium": (Decimal("5.4"), Decimal("0.30000"), Decimal("0.65000")),
                    "low": None,  # unscored CVE — EPSS absent
                }
                for sev in ("critical", "high", "medium", "low"):
                    sev_epss = _SEV_EPSS.get(sev)
                    v = Vulnerability(
                        external_id=f"CVE-2099-{sev[:3].upper()}-{suffix}",
                        source="NVD",
                        severity=sev,
                        cvss_score=sev_epss[0] if sev_epss else None,
                        epss_score=sev_epss[1] if sev_epss else None,
                        epss_percentile=sev_epss[2] if sev_epss else None,
                        summary=f"e2e seed CVE — {sev}",
                    )
                    session.add(v)
                    vulns_by_severity[sev] = v
                await session.commit()
                for v in vulns_by_severity.values():
                    await session.refresh(v)

                # Now create components in batches. We commit every 100 rows
                # so the connection isn't held with a huge in-memory tx.
                BATCH = 100
                for i in range(component_count):
                    cname = f"{component_prefix}-{i:05d}"
                    purl = f"pkg:npm/{cname}"
                    component = Component(
                        purl=purl,
                        package_type="npm",
                        name=cname,
                    )
                    session.add(component)
                    await session.flush()  # need component.id

                    cv = ComponentVersion(
                        component_id=component.id,
                        version="1.0.0",
                        purl_with_version=f"{purl}@1.0.0",
                    )
                    session.add(cv)
                    await session.flush()

                    sc = ScanComponent(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        direct=True,
                        raw_data={"seed_index": i},
                    )
                    session.add(sc)

                    # License — round-robin across the four categories.
                    cat = _LICENSE_CATEGORY_CYCLE[i % len(_LICENSE_CATEGORY_CYCLE)]
                    lf = LicenseFinding(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        license_id=licenses_by_cat[cat].id,
                        kind="concluded",
                        source_path=f"seed/{i}",
                    )
                    session.add(lf)

                    # Severity — round-robin across the six buckets.
                    sev = _SEVERITY_CYCLE[i % len(_SEVERITY_CYCLE)]
                    if sev in vulns_by_severity:
                        vf = VulnerabilityFinding(
                            scan_id=anchor_scan_id,
                            component_version_id=cv.id,
                            vulnerability_id=vulns_by_severity[sev].id,
                        )
                        session.add(vf)
                    # info / none → no VF, severity_max collapses to "none".

                    if (i + 1) % BATCH == 0:
                        await session.commit()
                        seeded_components = i + 1
                await session.commit()
                seeded_components = component_count

            seeded_vulnerabilities = 0
            if vulnerability_count > 0 and project_rows:
                first_project = project_rows[0]
                anchor_scan_id = first_project.latest_scan_id
                assert anchor_scan_id is not None  # with_scan was forced True

                mix = _parse_severity_mix(
                    vulnerability_severity_mix, total=vulnerability_count
                )
                # Build the seed plan: a flat list of `severity` values, one
                # per finding, ordered for deterministic output.
                seed_plan: list[str] = []
                for sev in ("critical", "high", "medium", "low", "info", "unknown"):
                    seed_plan.extend([sev] * mix.get(sev, 0))
                # Clamp / pad to the requested total. If the mix sum is less
                # than the count, pad with `low` (the most benign bucket).
                # If it's greater, truncate.
                if len(seed_plan) > vulnerability_count:
                    seed_plan = seed_plan[:vulnerability_count]
                while len(seed_plan) < vulnerability_count:
                    seed_plan.append("low")

                BATCH = 50
                for idx, sev in enumerate(seed_plan):
                    vname = f"vuln-{idx:05d}"
                    purl = f"pkg:npm/{vname}"
                    component = Component(
                        purl=f"{purl}-{suffix}",
                        package_type="npm",
                        name=vname,
                    )
                    session.add(component)
                    await session.flush()

                    cv = ComponentVersion(
                        component_id=component.id,
                        version="1.0.0",
                        purl_with_version=f"{purl}-{suffix}@1.0.0",
                    )
                    session.add(cv)
                    await session.flush()

                    sc = ScanComponent(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        direct=True,
                        raw_data={"vuln_seed_index": idx},
                    )
                    session.add(sc)

                    # Round-robin EPSS across the seed so sort/filter scenarios
                    # see distinct values and a `None` (unscored) bucket. The
                    # cycle is decoupled from severity, so the plan naturally
                    # produces CVSS↔EPSS divergence rows.
                    epss_pair = _EPSS_CYCLE[idx % len(_EPSS_CYCLE)]
                    vuln = Vulnerability(
                        external_id=f"CVE-2099-VLN-{suffix}-{idx:05d}",
                        source="NVD",
                        severity=sev,
                        cvss_score=None,
                        epss_score=epss_pair[0] if epss_pair else None,
                        epss_percentile=epss_pair[1] if epss_pair else None,
                        summary=f"e2e seed vulnerability {idx} ({sev})",
                    )
                    session.add(vuln)
                    await session.flush()

                    status = _VULN_STATUS_CYCLE[idx % len(_VULN_STATUS_CYCLE)]
                    finding = VulnerabilityFinding(
                        scan_id=anchor_scan_id,
                        component_version_id=cv.id,
                        vulnerability_id=vuln.id,
                        status=status,
                        analysis_state=status,
                    )
                    session.add(finding)

                    if (idx + 1) % BATCH == 0:
                        await session.commit()
                await session.commit()
                seeded_vulnerabilities = vulnerability_count

            return {
                "email": user.email,
                "password": chosen_password,
                "no_password": bool(no_password),
                "user_id": str(user.id),
                "is_super_admin": bool(super_admin),
                "team_id": str(team.id),
                "project_names": project_names,
                "project_ids": project_ids,
                "scan_ids": scan_ids,
                "sbom_scan_id": sbom_scan_id,
                "g7_check_count": g7_check_count,
                "component_count": seeded_components,
                "vulnerability_count": seeded_vulnerabilities,
                "obligation_count": seeded_obligations_count,
                "source_tarball": source_tarball_path,
                "extra_members": extra_members_summary,
                "oauth_identity": oauth_identity_summary,
                "refresh_token": refresh_token_summary,
                "notification_count": seeded_notifications,
            }
    finally:
        await engine.dispose()


def _refuse_super_admin_outside_safe_env() -> None:
    """
    Refuse to run when ``--super-admin`` is requested outside dev/test/ci.

    Security-reviewer F8 (CWE-489 Active Debug Code in Production):
    ``--super-admin`` writes ``is_superuser=True`` directly via the seed
    helper. If this script ever ships with the prod image and the on-call
    runs it by accident (e.g. for a "quick test"), a super-admin appears
    out of band — bypassing the audit trail's actor record and any
    onboarding flow.

    Read ``APP_ENV`` at runtime (CLAUDE.md core rule #11 — no module-level
    env caching). Default of "" / unset → refuse.
    """
    current_env = (os.getenv("APP_ENV") or "").strip().lower()
    if current_env in _SUPER_ADMIN_ALLOWED_ENVS:
        return
    allowed = sorted(_SUPER_ADMIN_ALLOWED_ENVS)
    print(
        "Refusing to create super-admin: APP_ENV="
        f"{current_env or '<unset>'} not in {{{', '.join(allowed)}}}. "
        "Set APP_ENV=dev to override.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def main() -> int:
    args = _parse_args()
    project_names = [n.strip() for n in args.project_names.split(",") if n.strip()]
    if not project_names:
        print("at least one --project-name required", file=sys.stderr)
        return 2
    if args.component_count < 0:
        print("--component-count must be non-negative", file=sys.stderr)
        return 2
    if args.vulnerability_count < 0:
        print("--vulnerability-count must be non-negative", file=sys.stderr)
        return 2
    if args.extra_members < 0:
        print("--extra-members must be non-negative", file=sys.stderr)
        return 2
    if args.with_notifications < 0:
        print("--with-notifications must be non-negative", file=sys.stderr)
        return 2

    # F8 — gate the super-admin convenience path on a known-safe APP_ENV.
    # The check runs ONLY when --super-admin is requested; the rest of the
    # seed (project / component fixtures) is harmless without the flag.
    if args.super_admin:
        _refuse_super_admin_outside_safe_env()

    try:
        summary = asyncio.run(
            _seed(
                project_names=project_names,
                email=args.email,
                password=args.password,
                with_scan=args.with_scan,
                with_sbom=args.with_sbom,
                with_g7=args.with_g7,
                component_count=args.component_count,
                component_prefix=args.component_prefix,
                vulnerability_count=args.vulnerability_count,
                vulnerability_severity_mix=args.vulnerability_severity_mix,
                with_obligations=args.with_obligations,
                with_source=args.with_source,
                super_admin=args.super_admin,
                extra_members=args.extra_members,
                extra_team_admin=args.extra_team_admin,
                with_oauth_identity=args.with_oauth_identity,
                no_password=args.no_password,
                with_refresh_token=args.with_refresh_token,
                with_notifications=args.with_notifications,
            )
        )
    except ValueError as exc:
        # Validation errors (e.g. --no-password without --with-oauth-identity)
        # land here. Distinct exit code so callers can branch on the failure
        # mode without parsing stderr.
        print(f"seed precondition failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — top-level CLI handler
        print(f"seed failed: {exc}", file=sys.stderr)
        return 1

    # Single-line JSON so the caller can parse one stdout line trivially.
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
