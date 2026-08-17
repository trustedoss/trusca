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
_DISPATCH_ONLY_KINDS = {"password_reset", "new_critical_cve", "user_deactivated"}


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

    fixture = (
        Path(__file__).resolve().parents[4] / "tests/contracts/notification-kinds.json"
    )
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

    src = (
        pathlib.Path(__file__).resolve().parents[2] / "api" / "v1" / "licenses.py"
    ).read_text(encoding="utf-8")
    patterns = re.findall(r'pattern=r"\^\(([a-z_|]+)\)\$"', src)
    conflict_alternations = [p for p in patterns if "incompatible" in p]
    assert len(conflict_alternations) == 1, (
        f"expected exactly one conflict pattern in the router, found "
        f"{len(conflict_alternations)}: {conflict_alternations}"
    )
    assert set(conflict_alternations[0].split("|")) == set(CONFLICT_VERDICT_VALUES)


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

    assert not (catalogued - summarized), (
        f"catalogued licenses with no summary: {sorted(catalogued - summarized)}"
    )
    assert not (summarized - catalogued), (
        f"summaries for licenses outside the catalog: {sorted(summarized - catalogued)}"
    )


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
        assert not _has_hangul(summary.en), (
            f"English summary contains Hangul for {spdx_id} — swapped fields?"
        )


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
        assert "outbound-license-conflicts" in body, (
            f"{guide.name} no longer carries the outbound-conflict section"
        )
        missing = [v for v in CONFLICT_VERDICT_VALUES if f"`{v}`" not in body]
        assert not missing, (
            f"{guide.name} does not document these verdicts: {missing}"
        )


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
        assert "ai-usage-verdicts" in body, (
            f"{guide.name} no longer carries the usage-verdict section"
        )
        missing_verdicts = [v for v in AI_VERDICT_VALUES if f"`{v}`" not in body]
        assert not missing_verdicts, (
            f"{guide.name} does not document these verdicts: {missing_verdicts}"
        )
        missing_scenarios = [s for s in USAGE_SCENARIOS if f"`{s}`" not in body]
        assert not missing_scenarios, (
            f"{guide.name} does not document these scenarios: {missing_scenarios}"
        )


def test_developer_reachable_csv_exports_are_rate_limited() -> None:
    """Every CSV export a developer can reach carries a rate limit.

    The limiter is opt-in in this app: ``core/ratelimit.py`` sets
    ``default_limits=[]``, so a route without the decorator has no limit at
    all. One export walks its list service a couple of hundred times and holds
    a pooled connection for the whole stream, which makes an unthrottled one
    the cheapest denial-of-service primitive the lowest role has. This is a
    contract rather than a behavioural test because exercising the limiter for
    real needs wall-clock time and a live backend; what regresses in practice
    is someone adding a fourth export and forgetting the decorator.

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
    assert len(matched) == 3, (
        f"expected the three developer-reachable CSV exports, matched: {matched}"
    )
    assert not unlimited, (
        f"CSV export routes reachable by a developer with no rate limit: {unlimited}"
    )


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
