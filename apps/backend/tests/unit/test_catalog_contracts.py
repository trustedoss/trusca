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
