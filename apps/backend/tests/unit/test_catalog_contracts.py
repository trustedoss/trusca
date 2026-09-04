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

from pydantic import BaseModel

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
#   - queue_backlog_alert: S6 (concurrency-scaling-plan-2026-08-22.md
#                         §3.2/§4) - a deployment-wide Celery-queue capacity
#                         signal, not addressed to any one user, so there is
#                         no ``user_id`` to fan an in-app row out to (unlike
#                         vuln_sla_breach / malicious_detected, which fan out
#                         per team member). If a future in-app "operations"
#                         inbox surface wants this kind too, it needs an enum
#                         migration (db-designer) and a frontend catalog
#                         entry (frontend-dev) first - this test will flag it.
#   - webhook_capacity_retry_exhausted: S7 (concurrency-scaling-plan-
#                         2026-08-22.md §3.2/§4) - same reasoning as
#                         queue_backlog_alert: nobody triggered the webhook
#                         delivery by hand, so there is no ``user_id`` to
#                         address an in-app row to. Same migration rule
#                         applies if a future ops inbox wants it too.
_DISPATCH_ONLY_KINDS = {
    "password_reset",
    "new_critical_cve",
    "user_deactivated",
    "queue_backlog_alert",
    "webhook_capacity_retry_exhausted",
}


def test_notification_kind_enum_matches_schema_literal() -> None:
    """DB enum (models) and the wire Literal (schemas) must be identical."""
    from models.notification import NOTIFICATION_KIND_VALUES
    from schemas.notification import NotificationKind

    assert set(NOTIFICATION_KIND_VALUES) == set(typing.get_args(NotificationKind))


def test_notification_kind_enum_matches_the_shared_frontend_fixture() -> None:
    """Backend enum vs the fixture the frontend mirror is pinned against.

    ``tests/contracts/notification-kinds.json`` was written to be the single
    vocabulary both sides assert on, but only the frontend was ever wired to
    it — the file's own comment called the backend half a follow-up. So a kind
    added here failed nothing, and ``vuln_sla_breach`` (X1) shipped with no
    frontend label or icon: the inbox rendered a fallback icon next to a raw
    i18n key. Adding ``malicious_detected`` (MAL-2b) would have repeated it.

    Order is asserted too. The fixture mirrors enum declaration order, and the
    frontend derives a TypeScript union from the array, so a reordering that
    the set comparison forgives is still churn worth catching at review time.
    """
    import json
    from pathlib import Path

    from models.notification import NOTIFICATION_KIND_VALUES

    fixture = Path(__file__).resolve().parents[4] / "tests/contracts/notification-kinds.json"
    assert fixture.is_file(), f"shared notification-kind fixture missing: {fixture}"
    kinds = json.loads(fixture.read_text(encoding="utf-8"))["kinds"]

    assert list(NOTIFICATION_KIND_VALUES) == kinds, (
        "notification_kind drifted from the shared fixture — update "
        "tests/contracts/notification-kinds.json AND the frontend mirror "
        "(notificationsApi.ts, NotificationsPage icons/tones, locales) "
        "together, or the inbox renders a raw key."
    )


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
    # B5 added a CSV export beside the list, and it accepts the same sort
    # keys. More than one occurrence is therefore expected; what must not
    # happen is one of them drifting, so every occurrence is checked rather
    # than the count being pinned to one.
    assert sort_alternations, "no sort-key pattern found in the router"
    for alternation in sort_alternations:
        assert set(alternation.split("|")) == set(_VALID_SORT_KEYS)


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

    src = (pathlib.Path(__file__).resolve().parents[2] / "api" / "v1" / "licenses.py").read_text(
        encoding="utf-8"
    )
    patterns = re.findall(r'pattern=r"\^\(([a-z_|]+)\)\$"', src)
    review_alternations = [p for p in patterns if "behavioral_use" in p]
    # The list endpoint and its CSV export (D9) both take this filter, so more
    # than one occurrence is expected; each must still hold the same set.
    assert review_alternations, "no review_flag pattern found in the router"
    for alternation in review_alternations:
        assert set(alternation.split("|")) == set(REVIEW_FLAG_VALUES)


# ---------------------------------------------------------------------------
# Outbound-conflict verdicts (gap #27) — §2 vocabulary guard
# ---------------------------------------------------------------------------


def test_conflict_verdict_values_match_schema_literal() -> None:
    """The service tuple and the API wire Literal must be identical.

    §2: the verdict vocabulary lives in the rule data, the service tuple, the
    schema Literal, the router regex and a frontend mirror. Five places, so the
    guard is mandatory rather than nice to have.
    """
    import typing

    from schemas.license_detail import ConflictVerdict
    from services.license_conflict import CONFLICT_VERDICT_VALUES

    assert set(CONFLICT_VERDICT_VALUES) == set(typing.get_args(ConflictVerdict))


def test_license_class_values_match_schema_literal() -> None:
    import typing

    from schemas.license_detail import LicenseClass
    from services.license_class import LICENSE_CLASS_VALUES

    assert set(LICENSE_CLASS_VALUES) == set(typing.get_args(LicenseClass))


def test_conflict_router_pattern_matches_verdict_values() -> None:
    """The licenses router's ``conflict`` Query regex holds the same vocabulary.

    A verdict missing from the regex 422s a filter the UI legitimately offers;
    an extra one advertises a value nothing can produce.
    """
    import pathlib
    import re

    from services.license_conflict import CONFLICT_VERDICT_VALUES

    src = (pathlib.Path(__file__).resolve().parents[2] / "api" / "v1" / "licenses.py").read_text(
        encoding="utf-8"
    )
    patterns = re.findall(r'pattern=r"\^\(([a-z_|]+)\)\$"', src)
    conflict_alternations = [p for p in patterns if "incompatible" in p]
    # The list endpoint and its CSV export (D9) both take this filter, so more
    # than one occurrence is expected; each must still hold the same set.
    assert conflict_alternations, "no conflict pattern found in the router"
    for alternation in conflict_alternations:
        assert set(alternation.split("|")) == set(CONFLICT_VERDICT_VALUES)


def test_conflict_summary_fields_cover_every_verdict() -> None:
    """A verdict with no counter would be silently missing from the summary."""
    from schemas.license_detail import ConflictSummary
    from services.license_conflict import CONFLICT_VERDICT_VALUES

    assert set(ConflictSummary.model_fields) == set(CONFLICT_VERDICT_VALUES)


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
    from schemas.project_detail import ComponentDetailResponse, ComponentSummary
    from services.eol.eol_catalog import EOL_STATES

    expected = {"eol", "supported", "unknown"}
    assert set(EOL_STATES) == expected

    def _literal_states(model: type[BaseModel], field: str) -> set[str]:
        annotation = model.model_fields[field].annotation
        # ``Literal["eol","supported","unknown"] | None`` — walk the union.
        states: set[str] = set()
        for arg in typing.get_args(annotation):
            states.update(a for a in typing.get_args(arg) if isinstance(a, str))
        return states

    assert _literal_states(ComponentSummary, "eol_state") == expected
    assert _literal_states(ComponentDetailResponse, "eol_state") == expected


def test_malicious_states_catalog_matches_schema_literals() -> None:
    """Same three-place vocabulary guard for ``malicious_state`` (#26).

    One asymmetry worth stating: unlike ``eol_state``, the NULL outside this
    set is not "untracked" but "never assessed", and ``clear`` is a real
    verdict rather than a default. The index is a DENY list — absence from it
    means the snapshot looked and did not list the package. Collapsing the two
    would let an unevaluated catalog read as a clean one, which is why both the
    schema Literals and the FE mirror keep exactly these two members.
    """
    import typing

    from schemas.project_detail import ComponentDetailResponse, ComponentSummary
    from services.malicious.malicious_catalog import MALICIOUS_STATES

    expected = {"flagged", "clear"}
    assert set(MALICIOUS_STATES) == expected

    def _literal_states(model: type[BaseModel], field: str) -> set[str]:
        annotation = model.model_fields[field].annotation
        states: set[str] = set()
        for arg in typing.get_args(annotation):
            states.update(a for a in typing.get_args(arg) if isinstance(a, str))
        return states

    assert _literal_states(ComponentSummary, "malicious_state") == expected
    assert _literal_states(ComponentDetailResponse, "malicious_state") == expected


# ---------------------------------------------------------------------------
# License content translations (C1a) — EN ↔ KO drift guards
# ---------------------------------------------------------------------------


def test_every_catalog_obligation_paragraph_has_a_korean_rendering() -> None:
    """Each English obligation paragraph must carry a Korean translation.

    ``services.license_translations`` keys its translations by the exact
    English paragraph, which buys reuse (52 licenses share 48 paragraphs —
    translate once, every license inherits it) at the cost of a drift class:
    editing an English paragraph silently orphans its Korean text. This test
    is the closure — same-vocabulary-in-two-places rule (H-5 class).

    Failing here means one of two things: you added a paragraph (translate it)
    or you edited one (update its Korean rendering).
    """
    from services.license_translations import translated_obligation_texts
    from services.obligation_catalog import _CATALOG

    catalog_texts = {text for entry in _CATALOG.values() for _, text in entry.rows}
    translated = translated_obligation_texts()

    # Guard against a vacuous pass if either side stops loading.
    assert catalog_texts, "catalog exposes no obligation paragraphs — layout changed?"
    assert translated, "translation module exposes nothing — layout changed?"

    assert not (catalog_texts - translated), (
        "catalog paragraphs with no Korean rendering: "
        f"{sorted(t[:60] for t in catalog_texts - translated)}"
    )
    assert not (translated - catalog_texts), (
        "orphan translations no catalog paragraph reaches (edited English?): "
        f"{sorted(t[:60] for t in translated - catalog_texts)}"
    )


def test_every_catalog_license_has_both_summaries() -> None:
    """Every catalogued SPDX id has an EN + KO summary, and no orphans."""
    from services.license_translations import summarized_spdx_ids
    from services.obligation_catalog import catalog_spdx_ids

    catalogued = catalog_spdx_ids()
    summarized = summarized_spdx_ids()

    assert catalogued, "catalog exposes no SPDX ids — layout changed?"
    assert summarized, "summary module exposes nothing — layout changed?"

    assert not (
        catalogued - summarized
    ), f"catalogued licenses with no summary: {sorted(catalogued - summarized)}"
    assert not (
        summarized - catalogued
    ), f"summaries for licenses outside the catalog: {sorted(summarized - catalogued)}"


def test_translated_content_is_non_empty_and_actually_korean() -> None:
    """Korean text must be present and contain Hangul — not an English copy.

    A copy-paste that leaves the English string in the ``ko`` slot passes a
    set-equality test but ships an untranslated UI, so assert the script.
    """
    from services.license_translations import (
        _LICENSE_SUMMARY,
        _OBLIGATION_TEXT_KO,
    )

    def _has_hangul(text: str) -> bool:
        return any("가" <= ch <= "힣" for ch in text)

    for english, korean in _OBLIGATION_TEXT_KO.items():
        assert korean.strip(), f"empty Korean rendering for: {english[:60]}"
        assert _has_hangul(korean), f"Korean rendering has no Hangul: {english[:60]}"

    for spdx_id, summary in _LICENSE_SUMMARY.items():
        assert summary.en.strip(), f"empty English summary for {spdx_id}"
        assert summary.ko.strip(), f"empty Korean summary for {spdx_id}"
        assert _has_hangul(summary.ko), f"Korean summary has no Hangul for {spdx_id}"
        assert not _has_hangul(
            summary.en
        ), f"English summary contains Hangul for {spdx_id}: swapped fields?"


# ---------------------------------------------------------------------------
# The guide is an oracle (CLAUDE.md hardening rule 4)
# ---------------------------------------------------------------------------


def test_user_guide_documents_every_conflict_verdict() -> None:
    """Both guides must name every verdict a row can carry.

    A verdict the guide never mentions is one a reader meets for the first
    time in a table with no explanation of what it obliges them to do. This
    fails when a verdict is added and the docs are not, which is the order
    these things usually happen in.
    """
    import pathlib

    from services.license_conflict import CONFLICT_VERDICT_VALUES

    repo_root = pathlib.Path(__file__).resolve().parents[4]
    guides = (
        repo_root / "docs-site/docs/user-guide/components-and-licenses.md",
        repo_root
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "user-guide/components-and-licenses.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert (
            "outbound-license-conflicts" in body
        ), f"{guide.name} no longer carries the outbound-conflict section"
        missing = [v for v in CONFLICT_VERDICT_VALUES if f"`{v}`" not in body]
        assert not missing, f"{guide.name} does not document these verdicts: {missing}"


# ---------------------------------------------------------------------------
# AI usage-scenario verdicts (gap #28): the vocabulary lives in four places
# ---------------------------------------------------------------------------


def test_ai_verdict_values_match_the_schema_literal() -> None:
    """Service vocabulary and wire Literal must be the same four values.

    §2: a verdict the service can produce and the schema does not name would
    fail validation on the way out, after the work of computing it.
    """
    import typing

    from schemas.sbom import AiVerdict
    from services.ai_risk_assessment import AI_VERDICT_VALUES

    assert set(AI_VERDICT_VALUES) == set(typing.get_args(AiVerdict))


def test_ai_verdict_rank_covers_every_verdict() -> None:
    """The worst-of fold has to have an opinion about every value it may see."""
    from services.ai_risk_assessment import AI_VERDICT_RANK, AI_VERDICT_VALUES

    assert set(AI_VERDICT_RANK) == set(AI_VERDICT_VALUES)


def test_usage_scenarios_match_the_schema_literal_and_tuple() -> None:
    """The four scenarios are spelled in the service, the schema tuple, and the
    wire Literal. A value accepted by one and unknown to another would be stored
    and then silently ignored at assessment time.
    """
    import typing

    from schemas.scan import AI_USAGE_SCENARIOS, AiUsageContext
    from services.ai_risk_assessment import USAGE_SCENARIOS

    assert set(USAGE_SCENARIOS) == set(AI_USAGE_SCENARIOS)
    assert set(USAGE_SCENARIOS) == set(typing.get_args(AiUsageContext))


def test_registry_verdicts_are_all_known() -> None:
    """Every verdict the vendored registry states must be one the fold ranks.

    The registry is upstream data. A verdict spelled differently there would
    rank as the fallback and quietly change the fold.
    """
    from services.ai_risk_assessment import AI_VERDICT_VALUES, _knowledge

    terms = _knowledge()["licenseTerms"]
    stated = {t["verdict"] for t in terms}
    for term in terms:
        stated.update((term.get("scenarioVerdicts") or {}).values())
    assert stated <= set(AI_VERDICT_VALUES), f"unknown verdicts: {stated - set(AI_VERDICT_VALUES)}"


def test_registry_scenarios_are_all_known() -> None:
    """Scenario keys in the registry must be scenarios the project can be set to."""
    from services.ai_risk_assessment import USAGE_SCENARIOS, _knowledge

    used: set[str] = set()
    for term in _knowledge()["licenseTerms"]:
        used.update((term.get("scenarioVerdicts") or {}).keys())
        for condition in term.get("conditions") or []:
            used.update(condition.get("appliesTo") or [])
    assert used <= set(USAGE_SCENARIOS), f"unknown scenarios: {used - set(USAGE_SCENARIOS)}"


def test_registry_conditions_all_have_labels() -> None:
    """Every condition a term cites must resolve to a label the UI can show.

    The API sends condition ids and the label map separately; an id with no
    label renders as a bare slug next to a verdict, which is worse than saying
    nothing.
    """
    from services.ai_risk_assessment import _knowledge, condition_labels

    labels = condition_labels()
    cited: set[str] = set()
    for term in _knowledge()["licenseTerms"]:
        for condition in term.get("conditions") or []:
            cid = condition.get("id")
            if isinstance(cid, str):
                cited.add(cid)
    missing = cited - set(labels)
    assert not missing, f"conditions with no label: {sorted(missing)}"


def test_ai_verdicts_and_scenarios_are_documented_in_both_guides() -> None:
    """Both language guides must explain every verdict and every scenario.

    Rule §4: the guide is an oracle. A value the UI can render and the guide
    never names leaves the reader with a word next to their model and nothing
    that says what it obliges them to do. This fails when a verdict or scenario
    is added and the docs are not, which is the order these things happen in.
    """
    import pathlib

    from services.ai_risk_assessment import AI_VERDICT_VALUES, USAGE_SCENARIOS

    repo_root = pathlib.Path(__file__).resolve().parents[4]
    guides = (
        repo_root / "docs-site/docs/user-guide/ai-sbom-conformance.md",
        repo_root
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current"
        / "user-guide/ai-sbom-conformance.md",
    )
    for guide in guides:
        assert guide.is_file(), f"{guide} is missing"
        body = guide.read_text(encoding="utf-8")
        assert (
            "ai-usage-verdicts" in body
        ), f"{guide.name} no longer carries the usage-verdict section"
        missing_verdicts = [v for v in AI_VERDICT_VALUES if f"`{v}`" not in body]
        assert (
            not missing_verdicts
        ), f"{guide.name} does not document these verdicts: {missing_verdicts}"
        missing_scenarios = [s for s in USAGE_SCENARIOS if f"`{s}`" not in body]
        assert (
            not missing_scenarios
        ), f"{guide.name} does not document these scenarios: {missing_scenarios}"


def test_developer_reachable_csv_exports_are_rate_limited() -> None:
    """Every CSV export a developer can reach carries a rate limit.

    The limiter is opt-in in this app: ``core/ratelimit.py`` sets
    ``default_limits=[]``, so a route without the decorator has no limit at
    all. One export walks its list service a couple of hundred times and holds
    a pooled connection for the whole stream, which makes an unthrottled one
    the cheapest denial-of-service primitive the lowest role has. This is a
    contract rather than a behavioural test because exercising the limiter for
    real needs wall-clock time and a live backend; what regresses in practice
    is someone adding a new export and forgetting the decorator.

    ``/v1/admin/audit/export.csv`` is deliberately absent: it is super-admin
    only, so the blast radius of an unthrottled call is one trusted operator.
    """
    from main import app

    matched: list[str] = []
    unlimited: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not path.endswith("export.csv"):
            continue
        if "/admin/" in path:
            continue
        matched.append(path)
        # slowapi wraps the endpoint, so a decorated route has __wrapped__.
        if getattr(endpoint, "__wrapped__", None) is None:
            unlimited.append(path)

    # Without this the assertion below is vacuous: a rename of the path suffix
    # would leave nothing matched and the test would pass having checked
    # nothing at all.
    assert (
        len(matched) == 5
    ), f"expected the five developer-reachable CSV exports, matched: {matched}"
    assert (
        not unlimited
    ), f"CSV export routes reachable by a developer with no rate limit: {unlimited}"


# ---------------------------------------------------------------------------
# Role vocabulary (N1 guard)
# ---------------------------------------------------------------------------


def test_role_enum_matches_the_shared_fixture() -> None:
    """Backend enum vs the fixture the frontend mirror is pinned against.

    The frontend resolves a role it does not recognise by falling back to a
    grade it does, so a role that exists only on the backend does not raise
    anywhere: it renders as a different, higher grade. That is the failure
    mode this pair exists to catch, and it is why ``viewer`` had to land on
    both sides in one change rather than backend-first.
    """
    import json
    from pathlib import Path

    from models.auth import ROLE_VALUES

    fixture = json.loads(
        (Path(__file__).resolve().parents[4] / "tests/contracts/user-roles.json").read_text()
    )
    assert set(ROLE_VALUES) == set(fixture["roles"]), (
        "models/auth.py::ROLE_VALUES and tests/contracts/user-roles.json "
        "disagree; update both plus the frontend mirror in lib/roles.ts"
    )


def test_role_priority_orders_the_same_way_as_the_shared_fixture() -> None:
    """Names are not enough: the two sides must also rank them alike.

    The backend map starts at 1 so that an unknown role compares as 0 and is
    denied everywhere; the frontend starts at 0 because it has no such
    sentinel. The numbers therefore differ by design and only the order is
    the contract.
    """
    import json
    from pathlib import Path

    from core.security import _ROLE_PRIORITY

    fixture = json.loads(
        (Path(__file__).resolve().parents[4] / "tests/contracts/user-roles.json").read_text()
    )
    ordered = sorted(_ROLE_PRIORITY, key=lambda role: _ROLE_PRIORITY[role])
    assert ordered == fixture["privilege"]
    assert set(_ROLE_PRIORITY) == set(fixture["roles"]), (
        "a role missing from the priority map compares as privilege 0 and is "
        "denied everywhere, which passes every security assertion while the "
        "grade is unusable"
    )


def test_every_role_can_be_assigned_somewhere() -> None:
    """The enum and the two assignment vocabularies must not drift apart.

    A grade that exists in the database but in neither assignment surface is
    unreachable: nothing rejects it, nothing can grant it, and it looks
    supported from the model layer down. super_admin is the deliberate
    exception, held by a user flag rather than a team membership.
    """
    from models.auth import ROLE_VALUES
    from schemas.admin import _ROLE_VALUES, _TEAM_ROLE_VALUES

    assert set(_ROLE_VALUES) == set(ROLE_VALUES)
    assert set(_TEAM_ROLE_VALUES) == set(ROLE_VALUES) - {"super_admin"}, (
        "a team membership can carry every grade except super_admin, which is "
        "a user flag; adding a grade means adding it here too"
    )


def test_the_oauth_provider_name_appears_in_every_list_that_gates_it() -> None:
    """Seven separate closures decide which providers exist.

    The first version of this test named four and checked four, and the three
    it left out included the database enum. The generic provider shipped with
    no ``ALTER TYPE`` behind it, this test stayed green, and the failure
    surfaced only on a real sign-in: the button rendered, the user consented,
    and the callback raised on the first query against the identities table.
    A vocabulary test that stops short of the vocabulary that actually stores
    the value is worse than none, because it reads as coverage.

    The frontend mirrors and the label catalogue are asserted on their own
    side (``tests/unit/contracts/catalogMirrors.test.ts``); this covers the
    four Python-side lists plus the enum the column is typed with.
    """
    import typing

    from api.v1.oauth import _PROVIDER_ORDER
    from integrations.oauth.base import ProviderName, get_provider
    from models.oauth_identity import OAUTH_PROVIDER_VALUES
    from schemas.oauth_identity import OAuthProvider

    declared = set(typing.get_args(ProviderName))

    assert set(typing.get_args(OAuthProvider)) == declared
    assert set(_PROVIDER_ORDER) == declared
    assert set(OAUTH_PROVIDER_VALUES) == declared, (
        "models.oauth_identity.OAUTH_PROVIDER_VALUES types the column the "
        "identity row is written to; a provider missing here reaches Postgres "
        "as an invalid enum value at sign-in time"
    )
    for name in declared:
        # Raises ValueError if the adapter lookup does not know the name.
        assert get_provider(name) is not None


# ---------------------------------------------------------------------------
# Approvable statuses: backend validator vs the editor that offers them
# ---------------------------------------------------------------------------


def test_approvable_statuses_match_the_shared_fixture() -> None:
    """The list the API accepts vs the list the policy editor draws.

    These are the statuses an organization may put behind a second person.
    Drift is invisible until somebody configures the policy: a status the
    editor offers and the validator rejects fails on save, after the work; one
    the validator accepts and the editor omits is a control nobody can reach.
    """
    import json
    from pathlib import Path

    from schemas.gate_policy import APPROVABLE_STATUSES

    fixture = Path(__file__).resolve().parents[4] / "tests/contracts/approvable-statuses.json"
    assert fixture.is_file(), f"shared approvable-status fixture missing: {fixture}"
    statuses = json.loads(fixture.read_text(encoding="utf-8"))["statuses"]

    assert APPROVABLE_STATUSES == frozenset(statuses), (
        "approvable statuses drifted from the shared fixture. Update "
        "tests/contracts/approvable-statuses.json AND the frontend mirror "
        "(APPROVABLE_STATUSES in gatePoliciesApi.ts, plus the policies locale "
        "labels) together."
    )


def test_every_approvable_status_is_a_real_finding_status() -> None:
    """A status that cannot be reached cannot be gated.

    The validator would accept a name that no transition ever produces, and
    the policy would look configured while catching nothing.
    """
    from schemas.gate_policy import APPROVABLE_STATUSES
    from services.vulnerability_service import ALL_STATUSES

    assert APPROVABLE_STATUSES <= set(ALL_STATUSES)


def test_approval_failure_reasons_match_the_shared_fixture() -> None:
    """The tokens the API stamps vs the list the UI can translate.

    A token the backend sends and the frontend does not know falls back to a
    generic message, which is exactly the outcome the tokens were introduced to
    avoid: the reader is told the call failed and not what to do about it.
    """
    import json
    from pathlib import Path

    from api.v1.transition_approvals import _REASON_FOR

    fixture = Path(__file__).resolve().parents[4] / "tests/contracts/approval-failure-reasons.json"
    assert fixture.is_file(), f"shared approval-reason fixture missing: {fixture}"
    reasons = json.loads(fixture.read_text(encoding="utf-8"))["reasons"]

    assert set(_REASON_FOR.values()) == set(reasons), (
        "approval failure reasons drifted from the shared fixture. Update "
        "tests/contracts/approval-failure-reasons.json, the frontend mirror "
        "(APPROVAL_FAILURE_REASONS in transitionApprovalsApi.ts) and the "
        "locale copy together."
    )


def test_every_approval_failure_has_its_own_token() -> None:
    """One token per exception, so no two failures collapse into one message.

    ``ApprovalSelfDecision`` subclasses ``ApprovalForbidden`` and the router
    looks the token up by exact type for that reason. If they ever shared a
    token the distinction would be gone and nobody would notice.
    """
    from api.v1.transition_approvals import _REASON_FOR

    assert len(set(_REASON_FOR.values())) == len(_REASON_FOR)


# ---------------------------------------------------------------------------
# Distribution models: the API's set vs the form that offers them
# ---------------------------------------------------------------------------


def test_distribution_models_match_the_shared_fixture() -> None:
    """A value the form offers and the API rejects fails only on save.

    And one the API accepts but the form omits is a setting nobody can reach
    from the screen, which is worse: nothing fails, it is simply missing.
    """
    import json
    from pathlib import Path

    from schemas.scan import DISTRIBUTION_MODELS

    fixture = Path(__file__).resolve().parents[4] / "tests/contracts/distribution-models.json"
    assert fixture.is_file(), f"shared distribution-model fixture missing: {fixture}"
    models = json.loads(fixture.read_text(encoding="utf-8"))["models"]

    assert list(DISTRIBUTION_MODELS) == models, (
        "distribution models drifted from the shared fixture. Update "
        "tests/contracts/distribution-models.json, the frontend mirror "
        "(DISTRIBUTION_MODELS in projectsApi.ts) and the locale copy together."
    )


def test_the_unset_filter_sentinel_is_not_a_distribution_model() -> None:
    """They answer opposite questions and must not collide.

    ``unset`` asks for the projects still to be filled in. If it were also a
    stored value, filtering for it would return both those and the ones that
    had deliberately chosen it, and the two are not the same set.
    """
    from schemas.scan import DISTRIBUTION_MODELS
    from services.project_service import UNSET_DISTRIBUTION_MODEL

    assert UNSET_DISTRIBUTION_MODEL not in DISTRIBUTION_MODELS


# ---------------------------------------------------------------------------
# API key breadths: the validator, the CHECK constraint and the dropdown
# ---------------------------------------------------------------------------


def test_api_key_breadths_match_the_shared_fixture() -> None:
    """The value the dropdown offers goes straight into an authorization call.

    An option the form shows and the API rejects fails on save; a breadth the
    API accepts and the form omits is one nobody can choose. Neither fails any
    per-module test, which is the drift class this rule exists for.
    """
    import json
    from pathlib import Path

    from schemas.api_key import API_KEY_PERMISSION_BREADTHS

    fixture = Path(__file__).resolve().parents[4] / "tests/contracts/api-key-breadths.json"
    assert fixture.is_file(), f"shared api-key-breadth fixture missing: {fixture}"
    breadths = json.loads(fixture.read_text(encoding="utf-8"))["breadths"]

    assert set(API_KEY_PERMISSION_BREADTHS) == set(breadths), (
        "API key breadths drifted from the shared fixture. Update "
        "tests/contracts/api-key-breadths.json, the frontend mirror "
        "(API_KEY_PERMISSION_BREADTHS in types/apiKey.ts) and the CHECK "
        "constraint together."
    )


def test_the_check_constraint_bounds_the_same_set() -> None:
    """The database is the last line, so it has to agree with the first.

    A value the schema accepts and the constraint rejects is a 500 at issuance
    time; one the constraint allows and the schema does not is a breadth that
    can only be reached by writing SQL, which is how a key ends up with a
    breadth the auth path has never seen.
    """
    import re

    from models.api_key import APIKey
    from schemas.api_key import API_KEY_PERMISSION_BREADTHS

    table = APIKey.__table__
    constraint = next(
        c
        for c in table.constraints  # type: ignore[attr-defined]
        if getattr(c, "name", None) == "ck_api_keys_permission_breadth"
    )
    named = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))

    assert named == set(API_KEY_PERMISSION_BREADTHS)


# ---------------------------------------------------------------------------
# Intake requests vs the approvals they turn into
# ---------------------------------------------------------------------------


def test_intake_and_approval_share_one_status_vocabulary() -> None:
    """A request and an approval are the same question at different times.

    Two enums with the same four names is the drift that stays green per
    module and surfaces the first time something joins them, which here would
    be the carry-over that writes an intake verdict onto an approval row.
    """
    from models.component_approval import APPROVAL_STATUS_VALUES
    from models.component_intake import ComponentIntakeRequest

    intake_enum = ComponentIntakeRequest.__table__.c.status.type  # type: ignore[attr-defined]

    assert set(intake_enum.enums) == set(APPROVAL_STATUS_VALUES)  # type: ignore[attr-defined]


def test_intake_and_approval_share_one_transition_matrix() -> None:
    """The same states have to mean the same moves.

    An intake request that could jump from pending to approved while an
    approval could not would let the ask-before-using path skip the review
    step the states exist to record, and the carried verdict would land on the
    approval as though it had been through it.
    """
    from services.component_approval_service import _TRANSITION_MAP
    from services.component_intake_service import _TRANSITIONS

    assert _TRANSITIONS == _TRANSITION_MAP


# ---------------------------------------------------------------------------
# Obligation fulfilment statuses
# ---------------------------------------------------------------------------


def test_fulfilment_statuses_match_the_database_and_the_shared_fixture() -> None:
    """Three copies of one vocabulary: the enum, the tuple, and the fixture.

    The tuple is what the service validates against and what the wire
    description advertises; the CHECK constraint is what the database will
    actually accept. A name in the tuple but not the constraint is a status
    the API promises and the INSERT refuses, which fails at the last possible
    moment, on save.
    """
    import json
    import re
    from pathlib import Path

    from models.obligation_fulfilment import (
        OBLIGATION_FULFILMENT_STATUSES,
        ObligationFulfilment,
    )

    table = ObligationFulfilment.__table__  # type: ignore[attr-defined]
    constraint = next(
        c
        for c in table.constraints  # type: ignore[attr-defined]
        if getattr(c, "name", None) == "ck_obligation_fulfilments_status"
    )
    allowed_by_the_database = set(re.findall(r"'([a-z_]+)'", str(constraint.sqltext)))
    fixture = (
        Path(__file__).resolve().parents[4] / "tests/contracts/obligation-fulfilment-statuses.json"
    )
    assert fixture.is_file(), f"shared fulfilment-status fixture missing: {fixture}"
    statuses = json.loads(fixture.read_text(encoding="utf-8"))["statuses"]

    assert allowed_by_the_database == set(OBLIGATION_FULFILMENT_STATUSES)
    assert list(OBLIGATION_FULFILMENT_STATUSES) == statuses, (
        "fulfilment statuses drifted from the shared fixture. Update "
        "tests/contracts/obligation-fulfilment-statuses.json AND the frontend "
        "mirror (obligationsApi.ts, the status control, locales) together."
    )


def test_all_three_verdict_surfaces_share_one_status_vocabulary() -> None:
    """A project approval, an organization verdict, and an intake request.

    Two of these were already pinned to each other. The organization verdict
    joined later and reuses the same enum object, so today they cannot drift.
    That is the kind of guarantee that survives until somebody gives one
    surface its own enum for a reason that looks local, which is how the
    two-surface version of this drift got in.

    Asserted as a three-way equality rather than two pairs, so a fourth
    surface added tomorrow has one place to answer to.
    """
    from models.component_approval import APPROVAL_STATUS_VALUES, ComponentApproval
    from models.component_intake import ComponentIntakeRequest
    from models.organization_component_verdict import OrganizationComponentVerdict

    def column_values(model: type) -> set[str]:
        enum_type = model.__table__.c.status.type  # type: ignore[attr-defined]
        return set(enum_type.enums)  # type: ignore[attr-defined]

    declared = set(APPROVAL_STATUS_VALUES)

    assert column_values(ComponentApproval) == declared
    assert column_values(OrganizationComponentVerdict) == declared
    assert column_values(ComponentIntakeRequest) == declared


def test_the_grades_a_deployment_setting_accepts_are_the_assignable_ones(
    monkeypatch,
) -> None:
    """Two settings decide a grade, and both spell the set out by hand.

    ``DEFAULT_MEMBER_ROLE`` and ``OIDC_GROUP_ROLE_MAP`` each carry a literal
    set of grades they will accept, and each drops anything else on purpose:
    the first to keep a typo from granting more than the operator meant, the
    second to keep a group from minting an administrator. Both refusals are
    right and both sets are copies of the enum.

    A grade added to the enum without being added here is one an operator can
    write into their environment and never receive, with a warning in a log
    nobody is reading. Pinned against the assignable set rather than the whole
    enum, because ``super_admin`` is excluded from both by the same deliberate
    decision that excludes it from a team membership.

    Asserted by calling the functions rather than by reading their source: the
    first version of this test scraped the quoted grades out of the source and
    passed happily when a grade was removed from the accepted set, because the
    same word appears in the fallback one line below.

    One case is deliberately not covered, because it cannot be: dropping
    ``viewer`` from what ``DEFAULT_MEMBER_ROLE`` accepts changes nothing
    observable, since the floor an unreadable value falls to is ``viewer``
    too. Every other grade is distinguishable, which is the half that matters:
    those are the ones whose loss would silently downgrade somebody.
    """
    from core.config import default_member_role, oidc_group_role_map
    from models.auth import ROLE_VALUES

    assignable = set(ROLE_VALUES) - {"super_admin"}

    for grade in assignable:
        monkeypatch.setenv("DEFAULT_MEMBER_ROLE", grade)
        assert default_member_role() == grade, (
            f"DEFAULT_MEMBER_ROLE refuses {grade!r}, which a team membership "
            "can carry; an operator writing it gets something else"
        )
    monkeypatch.setenv("DEFAULT_MEMBER_ROLE", "super_admin")
    assert default_member_role() != "super_admin"

    monkeypatch.setenv(
        "OIDC_GROUP_ROLE_MAP",
        ",".join(f"group-{grade}:{grade}" for grade in sorted(assignable)),
    )
    mapped = oidc_group_role_map()
    assert (
        set(mapped.values()) == assignable
    ), "OIDC_GROUP_ROLE_MAP dropped a grade a team membership can carry"

    monkeypatch.setenv("OIDC_GROUP_ROLE_MAP", "everyone:super_admin")
    assert oidc_group_role_map() == {}


# ---------------------------------------------------------------------------
# Notification routing conditions
# ---------------------------------------------------------------------------


def test_a_routing_rules_conditions_use_the_vocabularies_they_name() -> None:
    """A rule's condition is written in three borrowed alphabets.

    Kinds come from the dispatcher, severities from the finding enum, channels
    from the delivery layer. Each is validated against its source at write
    time, which is the behaviour worth having; what this pins is that the
    source is the real one rather than a copy that will drift.

    The drift is quiet in a particular way: a rule stored against a stale
    vocabulary is not rejected and not fired. It sits in the table looking
    like coverage nobody has.
    """
    from models.notification import NOTIFICATION_KIND_VALUES
    from models.scan import VULN_SEVERITY_VALUES
    from notifications.dispatcher import _KNOWN_CHANNELS
    from schemas.notification_routing import NotificationRoutingRuleIn
    from services.notification_routing_service import _SEVERITY_ORDER

    # Every severity the resolver can rank is one the schema accepts, and the
    # other way round. A severity the schema admits but the resolver cannot
    # rank would be a rule that never matches anything.
    assert set(_SEVERITY_ORDER) == set(VULN_SEVERITY_VALUES)

    for kind in NOTIFICATION_KIND_VALUES:
        NotificationRoutingRuleIn(name="k", kinds=[kind], email_recipients=["ops@example.com"])
    for severity in VULN_SEVERITY_VALUES:
        NotificationRoutingRuleIn(
            name="s", min_severity=severity, email_recipients=["ops@example.com"]
        )
    for channel in _KNOWN_CHANNELS:
        NotificationRoutingRuleIn(name="c", channels=[channel])


def test_the_severity_order_a_rule_ranks_by_is_worst_first() -> None:
    """A floor means "this and everything above it", and above is a position.

    Reversing the list would turn "at least high" into "high and below", which
    is the same words and the opposite set, and every test naming a severity
    by hand would still pass.
    """
    from services.notification_routing_service import _SEVERITY_ORDER

    assert _SEVERITY_ORDER[0] == "critical"
    assert _SEVERITY_ORDER.index("critical") < _SEVERITY_ORDER.index("high")
    assert _SEVERITY_ORDER.index("high") < _SEVERITY_ORDER.index("medium")
    assert _SEVERITY_ORDER.index("medium") < _SEVERITY_ORDER.index("low")


# ---------------------------------------------------------------------------
# What the metrics endpoint publishes
# ---------------------------------------------------------------------------


def test_the_metrics_contract_declares_only_aggregates_with_closed_labels() -> None:
    """The shape rule, checked on the file rather than on one scrape.

    A scrape shows what today's data happens to contain. This reads the
    decision itself, so a series added with a label like ``project`` fails
    here even on a deployment with no projects in it, which is exactly the
    machine a contributor runs the suite on.
    """
    import json
    from pathlib import Path

    contract = json.loads(
        (Path(__file__).resolve().parents[4] / "tests/contracts/metrics-series.json").read_text(
            encoding="utf-8"
        )
    )

    # Label keys that would carry a name somebody chose. Severity and status
    # are closed vocabularies this codebase owns; a project or a package is
    # not, and neither is anything ending in name, email or id.
    forbidden = {"project", "package", "component", "user", "team", "repository"}

    assert contract["series"], "the contract must list what is published"
    for series in contract["series"]:
        assert series["name"].startswith("trusca_"), series["name"]
        assert series["type"] in {"gauge", "counter"}, series
        assert series["help"].strip(), f"{series['name']} has no help text"
        for label in series["labels"]:
            assert label not in forbidden, (
                f"{series['name']} carries a {label} label; a metric label is "
                "a closed vocabulary this codebase owns, never a name that "
                "came from a user"
            )
            assert not label.endswith(
                ("_name", "_email", "_id")
            ), f"{series['name']} carries {label}, which names a row"


def test_the_severity_labels_a_metric_can_carry_are_the_finding_severities() -> None:
    """The label vocabulary and the enum are the same list.

    A severity added to the enum and not to the metric would be findings that
    exist and are counted nowhere, which reads on a dashboard as the work
    having gone away.
    """
    # The renderer seeds its bucket dict from the enum, so this pins the
    # relationship rather than a hand-written copy: the assertion is that
    # nobody has replaced that seed with a literal list.
    import inspect

    from models.scan import VULN_SEVERITY_VALUES
    from services.metrics_service import render_metrics  # noqa: F401  (import guard)

    source = inspect.getsource(
        __import__("services.metrics_service", fromlist=["render_metrics"]).render_metrics
    )
    assert "VULN_SEVERITY_VALUES" in source, (
        "the open-findings buckets must be seeded from the severity enum, not "
        "from a literal list that will fall behind it"
    )
    assert len(VULN_SEVERITY_VALUES) >= 4


def test_the_queue_backlog_series_declare_the_toggle_that_actually_gates_them() -> None:
    """M2 (concurrency plan 2026-08-22 §3.1): the contract's ``toggle`` field
    for the two broker-backlog series names the real env var, not a stale
    one. A contract that names the wrong key would read as a working toggle
    to anyone auditing this file while actually gating nothing.
    """
    import inspect
    import json
    from pathlib import Path

    import core.config as config_module

    contract = json.loads(
        (Path(__file__).resolve().parents[4] / "tests/contracts/metrics-series.json").read_text(
            encoding="utf-8"
        )
    )
    queue_backlog_series = {
        series["name"]: series
        for series in contract["series"]
        if series["name"] in {"trusca_broker_queue_backlog", "trusca_scan_queue_wait_seconds"}
    }
    assert set(queue_backlog_series) == {
        "trusca_broker_queue_backlog",
        "trusca_scan_queue_wait_seconds",
    }, "M2's two series must both be declared in the contract"

    toggle_source = inspect.getsource(config_module.queue_backlog_metrics_enabled)
    for name, series in queue_backlog_series.items():
        toggle = series["toggle"]
        assert toggle in toggle_source, (
            f"{name} declares toggle {toggle!r}, which "
            "queue_backlog_metrics_enabled() does not read"
        )

    # And the series the renderer emits behind that toggle are exactly the
    # ones the contract declares as gated by it (the reciprocal check, so a
    # series added to the code without a matching contract entry, or a
    # contract entry the code never emits, both fail here).
    render_source = inspect.getsource(
        __import__("services.metrics_service", fromlist=["render_metrics"]).render_metrics
    )
    gated_block = render_source.split("if queue_backlog_metrics_enabled():", 1)[1]
    for name in queue_backlog_series:
        assert name in gated_block, f"{name} is not emitted inside the M2 toggle's branch"
    ungated_block = render_source.split("if queue_backlog_metrics_enabled():", 1)[0]
    for name in queue_backlog_series:
        assert name not in ungated_block, (
            f"{name} is emitted outside the M2 toggle's branch, so it would "
            "publish even with the toggle off"
        )


# ---------------------------------------------------------------------------
# Report column vocabulary: N22 guard
# ---------------------------------------------------------------------------


def test_report_column_headings_cover_exactly_the_canonical_vulnerability_columns() -> None:
    """``models.REPORT_VULNERABILITY_COLUMNS`` is the single source of truth
    for the vulnerability-table column vocabulary. The renderer's heading
    dict and cell-selector branches are hand-written copies of that set, and a
    column added to one without the other renders as a header with no cells,
    or a selection that silently drops a column, either invisible to the
    per-module tests, which only exercise columns that already exist."""
    from models import REPORT_VULNERABILITY_COLUMNS
    from services.report_service import _VULN_COLUMN_HEADINGS

    assert set(_VULN_COLUMN_HEADINGS) == set(REPORT_VULNERABILITY_COLUMNS)


def test_report_column_headings_cover_exactly_the_canonical_component_columns() -> None:
    from models import REPORT_COMPONENT_COLUMNS
    from services.report_service import _COMPONENT_COLUMN_HEADINGS

    assert set(_COMPONENT_COLUMN_HEADINGS) == set(REPORT_COMPONENT_COLUMNS)


def test_search_min_query_len_agrees_between_the_two_search_services() -> None:
    """Two backend copies of one floor: the palette endpoint and the full
    search page.

    Concurrency-scaling plan Q1 raised the floor from 2 to 3 (the palette's
    ``GET /v1/search`` and the full page's ``GET /v1/search/results`` are
    separate endpoints on purpose, see ``search_results_service``'s module
    docstring, but they must not diverge on WHEN they refuse to search, only
    on how they answer once they do). A change to one without the other
    would mean the palette rejects a query the full page happily answers, or
    the reverse, both invisible to per-module tests.

    The frontend half of this contract is
    ``apps/frontend/tests/unit/contracts/searchMinQueryLenContract.test.ts``.
    """
    from services.search_results_service import MIN_QUERY_LEN as RESULTS_MIN_QUERY_LEN
    from services.search_service import MIN_QUERY_LEN as PALETTE_MIN_QUERY_LEN

    assert PALETTE_MIN_QUERY_LEN == RESULTS_MIN_QUERY_LEN == 3


def test_report_format_template_schema_validates_against_the_same_vocabulary() -> None:
    """The organization-template schema's column validator and the renderer
    must reject/accept the identical set, otherwise an admin could save a
    template naming a column the renderer does not know how to draw."""
    import pytest
    from pydantic import ValidationError

    from models import REPORT_COMPONENT_COLUMNS, REPORT_VULNERABILITY_COLUMNS
    from schemas.report_format_template import ReportFormatTemplateUpsertIn

    ReportFormatTemplateUpsertIn(vulnerability_columns=list(REPORT_VULNERABILITY_COLUMNS))
    ReportFormatTemplateUpsertIn(component_columns=list(REPORT_COMPONENT_COLUMNS))

    with pytest.raises(ValidationError, match="unknown"):
        ReportFormatTemplateUpsertIn(vulnerability_columns=["not-a-real-column"])
    with pytest.raises(ValidationError, match="unknown"):
        ReportFormatTemplateUpsertIn(component_columns=["not-a-real-column"])


def test_external_package_ecosystems_match_the_shared_fixture() -> None:
    """Backend allow-list vs the fixture the frontend ecosystem <Select> is
    pinned against.

    deps.dev returns 404 for both an unknown package and an unknown system
    slug, so this closed list is the only thing telling a caller "that
    ecosystem was never valid" instead of a confusing empty result. A slug
    the form offers but the backend rejects (or the reverse) fails silently
    for whoever hits the mismatch first.

    The frontend half of this contract is
    ``apps/frontend/tests/unit/contracts/catalogMirrors.test.ts``.
    """
    import json
    from pathlib import Path

    from integrations.depsdev import PURL_TYPE_BY_SLUG, SYSTEM_SLUGS

    fixture = (
        Path(__file__).resolve().parents[4] / "tests/contracts/external-package-ecosystems.json"
    )
    assert fixture.is_file(), f"shared ecosystem fixture missing: {fixture}"
    ecosystems = json.loads(fixture.read_text(encoding="utf-8"))["ecosystems"]

    fixture_slugs = {entry["slug"] for entry in ecosystems}
    fixture_purl_types = {entry["slug"]: entry["purl_type"] for entry in ecosystems}

    assert fixture_slugs == SYSTEM_SLUGS, (
        "SYSTEM_SLUGS drifted from tests/contracts/external-package-ecosystems.json "
        "-- update both, and the frontend ecosystem <Select>."
    )
    assert fixture_purl_types == PURL_TYPE_BY_SLUG, (
        "PURL_TYPE_BY_SLUG drifted from tests/contracts/external-package-ecosystems.json"
    )


def test_the_assignee_filter_vocabulary_is_the_same_in_every_place_it_lives() -> None:
    """Four copies of three tokens, and nothing compared them until ER67.

    The service validates against ``_VALID_ASSIGNEE_FILTER``, two route
    parameters validate against a regex, and the frontend keeps its own set to
    decide whether a URL parameter is worth sending. A token added to the
    service and missed in a pattern is a 422 on a request the product just
    learned to make; missed in the frontend, the option is unreachable and
    nothing reports it.

    All four are read here rather than restated. Restating them would make this
    a fifth copy, which is the shape of the problem.
    """
    import re
    from pathlib import Path

    from api.v1 import vulnerabilities as routes
    from services.vulnerability_service import _VALID_ASSIGNEE_FILTER

    expected = set(_VALID_ASSIGNEE_FILTER)
    assert len(expected) >= 3, "the vocabulary shrank; this contract assumed three"

    source = Path(routes.__file__).read_text(encoding="utf-8")
    patterns = re.findall(r'pattern=r"\^\(((?:me|unassigned|inactive|\|)+)\)\$"', source)
    assert len(patterns) == 2, (
        f"expected two assignee route patterns, found {len(patterns)}. If a "
        "route was added or removed, this contract has to move with it."
    )
    for pattern in patterns:
        assert set(pattern.split("|")) == expected, (
            f"a route accepts {sorted(set(pattern.split('|')))} while the "
            f"service accepts {sorted(expected)}. The narrower one wins at "
            "runtime, so a token the product sends is refused at the edge."
        )

    frontend = (
        Path(__file__).resolve().parents[4]
        / "apps/frontend/src/features/projects/components/VulnerabilitiesTab.tsx"
    )
    assert frontend.is_file(), f"frontend mirror moved: {frontend}"
    mirror = re.search(
        r"VALID_ASSIGNEE = new Set<AssigneeFilter>\(\[([^\]]*)\]\)",
        frontend.read_text(encoding="utf-8"),
    )
    assert mirror, "the frontend no longer declares VALID_ASSIGNEE as a literal set"
    tokens = {t.strip().strip('"').strip("'") for t in mirror.group(1).split(",") if t.strip()}
    assert tokens == expected, (
        f"the frontend accepts {sorted(tokens)} and the service accepts "
        f"{sorted(expected)}. A token missing there makes the filter "
        "unreachable from the screen with nothing reporting it."
    )


def test_the_guide_documents_every_ownership_filter_token() -> None:
    """Hardening rule 4. The guide tells people what to type in the URL.

    Each token is a thing a reader can put in a link, so one that exists and is
    not described is a feature nobody finds, and one that is described and does
    not exist is a link that 422s. Read off the service rather than listed
    here, so adding a token without a word in the guide fails.
    """
    from pathlib import Path

    from services.vulnerability_service import _VALID_ASSIGNEE_FILTER

    root = Path(__file__).resolve().parents[4]
    pages = [
        root / "docs-site/docs/user-guide/vulnerabilities.md",
        root
        / "docs-site/i18n/ko/docusaurus-plugin-content-docs/current/user-guide/vulnerabilities.md",
    ]
    for page in pages:
        assert page.is_file(), f"{page} moved; this oracle needs updating"
        text = page.read_text(encoding="utf-8")
        missing = sorted(
            token
            for token in _VALID_ASSIGNEE_FILTER
            if f"?assignee={token}" not in text
        )
        assert not missing, (
            f"{page.name} does not show ?assignee={missing}. The filter is "
            "reachable and undocumented, so the only people who find it are "
            "the ones who read the source."
        )
