"""
Catalog contract tests — testing-standards rule: when the same vocabulary
exists in two or more places, an equality/subset test is mandatory.

Why this file exists (validation campaign, 2026-06): H-5 was exactly this
class of defect — the in-app ``notification_kind`` enum and the dispatcher's
kind catalog drifted apart silently, and nothing failed until the approval
trigger was wired. A latent cross-module drift passes every per-module unit
test; only a contract test that imports BOTH sides can catch it before the
integration point goes live.

Each test names its pair and the defect class it guards against. These are
pure-import set assertions — cheap, deterministic, no DB.
"""

from __future__ import annotations

import typing

# ---------------------------------------------------------------------------
# Notification kinds — H-5 guard
# ---------------------------------------------------------------------------

# Kinds the external dispatcher may emit that intentionally have NO in-app
# row (and therefore no notification_kind enum value). Adding a kind here is
# a deliberate decision that it never lands in the in-app inbox.
#
# Status notes (first run of this contract test surfaced the latter two):
#   - password_reset:     email-only by design (anonymous flow, no inbox).
#   - new_critical_cve:   emitted by vulnerability_rematch / trivy_db_refresh
#                         to external channels only today; the in-app rematch
#                         surface is the separate ``cve_detected`` kind. If a
#                         product decision routes rematch alerts into the
#                         inbox under THIS kind, it needs an enum migration
#                         first — this test will flag it.
#   - user_deactivated:   builder exists, no emit site yet (future admin
#                         flow); same migration rule applies when it lands.
_DISPATCH_ONLY_KINDS = {"password_reset", "new_critical_cve", "user_deactivated"}


def test_notification_kind_enum_matches_schema_literal() -> None:
    """DB enum (models) and the wire Literal (schemas) must be identical."""
    from models.notification import NOTIFICATION_KIND_VALUES
    from schemas.notification import NotificationKind

    assert set(NOTIFICATION_KIND_VALUES) == set(typing.get_args(NotificationKind))


def test_dispatcher_kinds_exist_in_inapp_enum_or_dispatch_only_list() -> None:
    """Every dispatcher kind is either a valid in-app kind or explicitly
    dispatch-only.

    H-5: the dispatcher emitted ``approval_state_changed`` before the in-app
    enum accepted it — the INSERT would have been rejected the moment the
    trigger was wired. A new dispatcher kind must land in
    ``NOTIFICATION_KIND_VALUES`` (+ migration) or be added to
    ``_DISPATCH_ONLY_KINDS`` above as a conscious decision.
    """
    from models.notification import NOTIFICATION_KIND_VALUES
    from notifications.dispatcher import NotificationKind as DispatcherKind

    dispatcher_kinds = {member.value for member in DispatcherKind}
    inapp_kinds = set(NOTIFICATION_KIND_VALUES)

    unaccounted = dispatcher_kinds - inapp_kinds - _DISPATCH_ONLY_KINDS
    assert not unaccounted, (
        f"dispatcher kinds {sorted(unaccounted)} are neither in-app "
        f"notification_kind values nor declared dispatch-only — this is the "
        f"H-5 drift class"
    )


def test_dispatcher_builders_cover_every_dispatcher_kind() -> None:
    """The message-builder registry must cover the dispatcher's kind set —
    a kind without a builder fails at dispatch time, not at import time."""
    from notifications.dispatcher import _BUILDERS
    from notifications.dispatcher import NotificationKind as DispatcherKind

    dispatcher_kinds = {member.value for member in DispatcherKind}
    assert set(_BUILDERS.keys()) == dispatcher_kinds


# ---------------------------------------------------------------------------
# Scan kind vocabulary — drift guard (testing-standards rule 2)
# ---------------------------------------------------------------------------


def test_scan_kind_enum_matches_schema_literal() -> None:
    """DB enum tuple (models) and the wire Literal (schemas) must be identical.

    ``scan_kind`` lives in three places: the native Postgres enum (migration
    0003, extended by 0032 for ``sbom``), ``SCAN_KIND_VALUES`` (the SQLAlchemy
    binding), and the ``ScanKind`` Literal (the request/response contract). A
    value added to one without the others either rejects valid input at the API
    boundary or rejects a valid INSERT at the DB — this test fails first.
    """
    from models.scan import SCAN_KIND_VALUES
    from schemas.scan import ScanKind

    assert set(SCAN_KIND_VALUES) == set(typing.get_args(ScanKind))


# ---------------------------------------------------------------------------
# Obligation kind vocabulary — H-9 guard
# ---------------------------------------------------------------------------


def test_emitted_obligation_kinds_are_advertised() -> None:
    """Every kind the catalog can emit must be in the advertised vocabulary.

    H-9: the catalog emitted ``patent`` while the advertised
    ``KNOWN_OBLIGATION_KINDS`` lacked it, so kind filters and distribution
    counts binned a real obligation as "unknown". The advertised list may be
    a superset (e.g. ``no-endorsement`` is advertised but not yet emitted);
    the emitter must never be.
    """
    from schemas.obligation_detail import KNOWN_OBLIGATION_KINDS
    from services import obligation_catalog

    emitted = {
        value
        for name, value in vars(obligation_catalog).items()
        if name.startswith("KIND_") and isinstance(value, str)
    }
    assert emitted, "KIND_* introspection found nothing — module layout changed?"
    assert emitted <= set(KNOWN_OBLIGATION_KINDS), (
        f"catalog emits kinds not advertised in KNOWN_OBLIGATION_KINDS: "
        f"{sorted(emitted - set(KNOWN_OBLIGATION_KINDS))} — the H-9 drift class"
    )


# ---------------------------------------------------------------------------
# Bundled license texts ↔ obligation catalog — vocabulary-drift guard
# ---------------------------------------------------------------------------


def test_license_text_files_match_obligation_catalog_ids() -> None:
    """Every catalogued SPDX id has a bundled full text, and vice versa.

    The NOTICE's "License Texts" section (Phase B) promises the standard full
    text for every license the obligation catalog governs. A catalog id added
    without vendoring ``services/license_texts/<id>.txt`` silently degrades
    that license to the "text not bundled" pointer; an orphan ``.txt`` is dead
    weight that will rot. Same-vocabulary-in-two-places rule (H-5 class).
    """
    from services.license_texts import bundled_spdx_ids
    from services.obligation_catalog import catalog_spdx_ids

    bundled = bundled_spdx_ids()
    catalogued = catalog_spdx_ids()
    assert bundled == catalogued, (
        f"catalog ids without a bundled text: {sorted(catalogued - bundled)}; "
        f"bundled texts not in the catalog: {sorted(bundled - catalogued)}"
    )


# ---------------------------------------------------------------------------
# VEX state mapping — H-4 guard
# ---------------------------------------------------------------------------

# CycloneDX 1.6 impactAnalysisState values we map onto. Subset of the spec's
# full set (resolved_with_pedigree / workaround_available exist but are
# intentionally unused).
_CYCLONEDX_ANALYSIS_STATES = {
    "in_triage",
    "exploitable",
    "not_affected",
    "false_positive",
    "resolved",
}


def test_cyclonedx_state_map_covers_every_finding_status() -> None:
    """The VEX export map must cover the full 7-state finding status enum.

    A status without a mapping raises KeyError mid-export (H-4 made this map
    the single source for both the VEX document and the SBOM embedding, so a
    new status that misses this map breaks two surfaces at once).
    """
    from models.scan import VULN_FINDING_STATUS_VALUES
    from services.vex_export import CYCLONEDX_STATE_MAP

    assert set(CYCLONEDX_STATE_MAP.keys()) == set(VULN_FINDING_STATUS_VALUES)


def test_cyclonedx_state_map_targets_are_valid_spec_states() -> None:
    from services.vex_export import CYCLONEDX_STATE_MAP

    assert set(CYCLONEDX_STATE_MAP.values()) <= _CYCLONEDX_ANALYSIS_STATES


def test_vulnerability_sort_keys_router_pattern_matches_service_set() -> None:
    """The router's ``sort`` Query regex and the service's ``_VALID_SORT_KEYS``
    hold the same vocabulary in two places (hardening rule §2). A key added to
    one but not the other either 422s a valid sort or lets an unknown key
    through to the service's fallback — both silent drifts. Parse the regex
    alternation out of the route signature and assert set equality.
    """
    import pathlib
    import re

    from services.vulnerability_service import _VALID_SORT_KEYS

    # Read the router source as text instead of importing it — importing
    # api.v1 drags the whole router package (heavy app wiring) into a unit
    # test that only needs one Query() pattern literal.
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "api" / "v1" / "vulnerabilities.py"
    ).read_text(encoding="utf-8")
    patterns = re.findall(r'pattern=r"\^\(([a-z_|]+)\)\$"', src)
    sort_alternations = [p for p in patterns if "severity" in p]
    assert len(sort_alternations) == 1, (
        f"expected exactly one sort-key pattern in the router, found "
        f"{len(sort_alternations)}: {sort_alternations}"
    )
    assert set(sort_alternations[0].split("|")) == set(_VALID_SORT_KEYS)


# ---------------------------------------------------------------------------
# Review flags — AI license review class (Phase D) — §2 vocabulary guard
# ---------------------------------------------------------------------------


def test_review_flag_values_match_schema_literal() -> None:
    """The classifier's single source of truth (``REVIEW_FLAG_VALUES``) and the
    API wire Literal (``schemas.license_detail.ReviewFlag``) must be identical.

    §2: the same review-flag vocabulary lives in the classifier, the schema
    Literal, and (later) a frontend mirror. A token added to one side without
    the other silently 422s a valid filter or advertises a value the persistence
    layer never stores.
    """
    import typing

    from schemas.license_detail import ReviewFlag
    from services.license_flags import REVIEW_FLAG_VALUES

    assert set(REVIEW_FLAG_VALUES) == set(typing.get_args(ReviewFlag))


def test_review_flag_router_pattern_matches_classifier_values() -> None:
    """The licenses router's ``review_flag`` Query regex holds the same
    vocabulary as ``REVIEW_FLAG_VALUES`` (hardening rule §2).
    """
    import pathlib
    import re

    from services.license_flags import REVIEW_FLAG_VALUES

    src = (
        pathlib.Path(__file__).resolve().parents[2] / "api" / "v1" / "licenses.py"
    ).read_text(encoding="utf-8")
    patterns = re.findall(r'pattern=r"\^\(([a-z_|]+)\)\$"', src)
    review_alternations = [p for p in patterns if "behavioral_use" in p]
    assert len(review_alternations) == 1, (
        f"expected exactly one review_flag pattern in the router, found "
        f"{len(review_alternations)}: {review_alternations}"
    )
    assert set(review_alternations[0].split("|")) == set(REVIEW_FLAG_VALUES)


# ---------------------------------------------------------------------------
# EOL state vocabulary — Phase M
# ---------------------------------------------------------------------------


def test_eol_states_catalog_matches_schema_literals() -> None:
    """The closed ``eol_state`` vocabulary lives in three places: the catalog
    tuple (``services.eol.eol_catalog.EOL_STATES``, the values the evaluator
    persists into ``component_versions.eol_state``), the ``ComponentSummary``
    Literal and the ``ComponentDetailResponse`` Literal (the wire contracts).
    The FE mirror half is
    ``apps/frontend/tests/unit/contracts/catalogMirrors.test.ts``.
    """
    from services.eol.eol_catalog import EOL_STATES
    from schemas.project_detail import ComponentDetailResponse, ComponentSummary

    expected = {"eol", "supported", "unknown"}
    assert set(EOL_STATES) == expected

    def _literal_states(model: type, field: str) -> set[str]:
        annotation = model.model_fields[field].annotation
        # ``Literal["eol","supported","unknown"] | None`` — walk the union.
        states: set[str] = set()
        for arg in typing.get_args(annotation):
            states.update(a for a in typing.get_args(arg) if isinstance(a, str))
        return states

    assert _literal_states(ComponentSummary, "eol_state") == expected
    assert _literal_states(ComponentDetailResponse, "eol_state") == expected
