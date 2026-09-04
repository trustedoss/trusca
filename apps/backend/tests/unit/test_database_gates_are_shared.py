"""Every test module that needs a database says so through one helper.

ER66. This reads the test tree with ``ast`` and answers two questions: does
any module reach the database without gating on it, and how many modules still
carry their own copy of the gate instead of calling ``tests._db_required``.

Four mistakes were made writing this analysis, and they are recorded because
the next person to widen it will be offered all four again:

1. It searched ``ast.dump()`` output for ``"alembic"`` in double quotes. Dump
   writes apostrophes, so it reported zero migration gates. A count of zero is
   never self-evidently right: it means "absent" or "not seen" and the result
   alone does not say which.
2. It counted only ``pytest.skip``/``fail``/``raise`` as reactions, so ten
   sites guarding with ``assert result.returncode == 0`` looked unprotected.
   That would have put correct code on the list of things to change.
3. It judged function by function, so helpers that only parse the URL looked
   like gates that react to nothing, when their caller does the reacting.
4. After the definition widened to include ``core.config.database_url()``,
   ordinary test bodies came into scope and their ordinary asserts were counted
   as gates - eighteen tests, seven of which assert about URL parsing alone.

The rule that follows: when the definition widens, sample what it now catches
before trusting the number. A sample taken before a widening does not carry
over, because a looser condition pulls in things the earlier one excluded.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent.parent

# Modules that legitimately touch the database machinery without a gate.
EXEMPT: dict[str, str] = {
    "unit/core/test_config_database_url.py": (
        "Tests core.config.database_url itself by deleting DATABASE_URL from "
        "the environment. Gating it on that variable would remove its subject."
    ),
}

# DEBT, not a directory. These modules still carry their own copy of the gate
# and are being moved to tests._db_required in batches. Nothing may be added:
# the count is asserted to only ever fall, so a new entry fails this test even
# though the code it describes works. If you are writing a new module that
# needs a database, import tests._db_required - do not add yourself here.
STILL_ROLLING_THEIR_OWN: frozenset[str] = frozenset({
    "integration/admin/test_admin_backup_api.py",
    "integration/admin/test_admin_ops_api.py",
    "integration/admin/test_admin_teams_api.py",
    "integration/admin/test_admin_user_onboarding_api.py",
    "integration/admin/test_admin_users_api.py",
    "integration/audit/test_audit_log_retention_report.py",
    "integration/audit/test_team_scoped_audit.py",
    "integration/scan/test_component_version_classifier.py",
    "integration/scan/test_container_first_detected_sla.py",
    "integration/scan/test_container_multi_cve.py",
    "integration/scan/test_container_registry_allowlist.py",
    "integration/scan/test_container_registry_credential_isolation.py",
    "integration/scan/test_first_detected_sla_db.py",
    "integration/scan/test_ingest_sbom_pipeline.py",
    "integration/scan/test_jsonb_size_guard.py",
    "integration/scan/test_license_fetcher_integration.py",
    "integration/scan/test_license_review_flag_persist.py",
    "integration/scan/test_queue_transition_consumption.py",
    "integration/scan/test_scan_container_load_test_delay.py",
    "integration/scan/test_scan_input_manifests.py",
    "integration/scan/test_scan_rerun_reset.py",
    "integration/scan/test_scan_retention.py",
    "integration/scan/test_scan_source_dependency_fingerprint_reuse.py",
    "integration/scan/test_scan_source_dependency_graph.py",
    "integration/scan/test_scan_source_load_test_delay.py",
    "integration/scan/test_scan_source_pipeline_mock.py",
    "integration/scan/test_scan_source_scanoss.py",
    "integration/scan/test_scan_source_trivy.py",
    "integration/scan/test_scan_source_worker_shutdown_grace.py",
    "integration/scan/test_triage_carry_forward_db.py",
    "integration/scan/test_trigger_scan_enqueues_celery.py",
    "integration/scan/test_user_cancel_scan_api.py",
    "integration/scan/test_vuln_catalog_insert_race.py",
    "integration/test_action_queue_api.py",
    "integration/test_action_queue_gate_parity.py",
    "integration/test_action_queue_query_count.py",
    "integration/test_alembic_upgrade.py",
    "integration/test_anonymise_user_command.py",
    "integration/test_api_key_breadth.py",
    "integration/test_api_keys_api.py",
    "integration/test_app_role_grant_matrix.py",
    "integration/test_audit_export.py",
    "integration/test_audit_log_db_immutable.py",
    "integration/test_audit_scrub_exception_layers.py",
    "integration/test_auth_flow.py",
    "integration/test_auth_password_reset.py",
    "integration/test_auth_token_retention.py",
    "integration/test_auto_registration.py",
    "integration/test_backup_artifacts_are_named_when_complete.py",
    "integration/test_backup_task_round_trip.py",
    "integration/test_batch_onboarding_api.py",
    "integration/test_bootstrap_from_empty.py",
    "integration/test_compliance_api.py",
    "integration/test_create_super_admin_ensure_active.py",
    "integration/test_dashboard_isolation_matrix.py",
    "integration/test_dashboard_portfolio_api.py",
    "integration/test_dashboard_summary_api.py",
    "integration/test_dashboard_trends_api.py",
    "integration/test_dashboard_trends_parity.py",
    "integration/test_db_role_boot_check.py",
    "integration/test_demo_sandbox_guards_api.py",
    "integration/test_epss_availability.py",
    "integration/test_epss_catalog_refresh.py",
    "integration/test_existence_hide_state_matrix.py",
    "integration/test_external_packages_api.py",
    "integration/test_finding_assignment.py",
    "integration/test_finding_due_index_contract.py",
    "integration/test_gate_policies_api.py",
    "integration/test_github_app_api.py",
    "integration/test_health_ready.py",
    "integration/test_incomplete_backup_is_visible.py",
    "integration/test_intake_requests_api.py",
    "integration/test_inventory_api.py",
    "integration/test_kev_catalog_refresh.py",
    "integration/test_last_super_admin_db_trigger.py",
    "integration/test_license_policies_api.py",
    "integration/test_licenses_api.py",
    "integration/test_login_throttle.py",
    "integration/test_lookup_index_plan_contracts.py",
    "integration/test_metrics_endpoint.py",
    "integration/test_notification_routing.py",
    "integration/test_notifications_api.py",
    "integration/test_oauth_api.py",
    "integration/test_obligations_api.py",
    "integration/test_operational_retention.py",
    "integration/test_organization_verdicts_api.py",
    "integration/test_password_reset_ends_sessions.py",
    "integration/test_password_reset_races_session_creation.py",
    "integration/test_permission_cache.py",
    "integration/test_policy_gate_api.py",
    "integration/test_principal_single_statement.py",
    "integration/test_project_dependency_graph_api.py",
    "integration/test_project_detail_api.py",
    "integration/test_project_diff_api.py",
    "integration/test_project_governance_api.py",
    "integration/test_projects_api.py",
    "integration/test_registry_credentials.py",
    "integration/test_release_snapshots_api.py",
    "integration/test_remediation_pr_service.py",
    "integration/test_remediation_service.py",
    "integration/test_report_history_api.py",
    "integration/test_reports_api.py",
    "integration/test_request_query_budget.py",
    "integration/test_reset_demo_scope_db.py",
    "integration/test_role_separation.py",
    "integration/test_saved_searches_api.py",
    "integration/test_sbom_api.py",
    "integration/test_sbom_ingest_api.py",
    "integration/test_sbom_signature_api.py",
    "integration/test_scan_capacity_wait_estimate.py",
    "integration/test_scan_dependency_fingerprint_migration.py",
    "integration/test_scan_log_download.py",
    "integration/test_scan_scheduler.py",
    "integration/test_scan_schedules_api.py",
    "integration/test_scans_api.py",
    "integration/test_scans_cross_project_api.py",
    "integration/test_schedule_scan_notification.py",
    "integration/test_search_api.py",
    "integration/test_search_explain_load_baseline.py",
    "integration/test_search_query_plan_contracts.py",
    "integration/test_search_results_api.py",
    "integration/test_seed_demo_demo_only_db.py",
    "integration/test_seed_load_test_db.py",
    "integration/test_service_accounts_api.py",
    "integration/test_session_creation_refusal_over_http.py",
    "integration/test_source_archive_api.py",
    "integration/test_source_archive_cleaner_db.py",
    "integration/test_source_preservation_pipeline_db.py",
    "integration/test_source_tree_api.py",
    "integration/test_stale_scan_reaper.py",
    "integration/test_sweep_tasks_persist.py",
    "integration/test_task_run_history.py",
    "integration/test_task_run_metrics.py",
    "integration/test_ticket_webhook.py",
    "integration/test_transition_approvals_api.py",
    "integration/test_uncommitted_scope_guard.py",
    "integration/test_upgrade_clusters_api.py",
    "integration/test_user_anonymisation_api.py",
    "integration/test_users_me_notification_prefs_api.py",
    "integration/test_users_me_oauth_identities_api.py",
    "integration/test_vex_api.py",
    "integration/test_vex_import_api.py",
    "integration/test_vuln_sla_sweep_db.py",
    "integration/test_vulnerabilities_api.py",
    "integration/test_vulnerability_rematch_db.py",
    "integration/test_webhook_capacity_retry.py",
    "integration/test_webhooks_github.py",
    "integration/test_webhooks_gitlab.py",
    "integration/test_ws_scan_progress.py",
    "unit/test_component_approval_service.py",
})


def _string_constants(node: ast.AST) -> set[str]:
    return {
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def runs_alembic(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "run":
            for arg in node.args:
                if {"alembic", "upgrade"} <= _string_constants(arg):
                    return True
    return False


def reaches_the_database(fn: ast.AST) -> bool:
    """Both ways in: reading DATABASE_URL, and calling the resolver."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name == "database_url":
                return True
            if name in {"getenv", "environ"} and any(
                isinstance(a, ast.Constant) and a.value == "DATABASE_URL"
                for a in node.args
            ):
                return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "DATABASE_URL"
        ):
            return True
    return False


HELPER_CALLS = frozenset({"migrate_to_head", "require_database_url"})


def calls_shared_helper(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in HELPER_CALLS:
                return True
    return False


def gate_reaction(fn: ast.AST) -> set[str]:
    """What this function does when the database is not there.

    An ``assert`` counts only inside a function that runs the migration, where
    it can only be about the return code. Elsewhere an assert is the test's
    subject, not a gate (mistake 4 above).
    """
    acts: set[str] = set()
    for node in ast.walk(fn):
        # Delegating to the shared helper IS the gate. Leaving this out is how
        # the first converted module still read as ungated: the guard knew
        # every old spelling and not the one it exists to promote. Fifth
        # instance of the definition being narrower than the question.
        if isinstance(node, ast.Call):
            called = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if called in HELPER_CALLS:
                acts.add("shared helper")
            del called
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            owner = getattr(node.func.value, "id", "")
            if attr in {"skip", "fail"} and owner == "pytest":
                acts.add(f"pytest.{attr}")
        if isinstance(node, ast.Raise):
            acts.add("raise")
        if isinstance(node, ast.Assert) and runs_alembic(fn):
            acts.add("assert")
    return acts


def uses_the_shared_helper(tree: ast.AST) -> bool:
    """Whether the module imports the one helper rather than writing its own."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.endswith("_db_required"):
                return True
            if any(a.name == "_db_required" for a in node.names):
                return True
        if isinstance(node, ast.Import):
            if any(a.name.endswith("_db_required") for a in node.names):
                return True
    return False


def _survey() -> tuple[set[str], set[str], set[str]]:
    """(modules using the database, modules with a gate, modules with own copy)."""
    uses: set[str] = set()
    gated: set[str] = set()
    own_copy: set[str] = set()
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        rel = path.relative_to(TESTS_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover - a parse failure is its own problem
            continue
        functions = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        for fn in functions:
            # Selection has to be as wide as the question and no wider. A
            # fixture that calls the shared helper touches neither
            # DATABASE_URL nor subprocess, so selecting on those two alone
            # dropped it before its reaction was looked at. Selecting on "has
            # any reaction" instead pulled in 83 modules that skip over a
            # missing binary and never touch a database. The helper call is
            # the third way in, and only it.
            if not (
                reaches_the_database(fn)
                or runs_alembic(fn)
                or calls_shared_helper(fn)
            ):
                continue
            acts = gate_reaction(fn)
            uses.add(rel)
            if acts:
                gated.add(rel)
                if not uses_the_shared_helper(tree):
                    own_copy.add(rel)
    return uses, gated, own_copy


def test_no_module_reaches_the_database_without_gating_on_it() -> None:
    uses, gated, _ = _survey()
    ungated = {m for m in uses - gated if m not in EXEMPT}
    assert not ungated, (
        "these modules use the database but never say what to do when it is "
        f"absent, so they depend on some other module having migrated first: "
        f"{sorted(ungated)}"
    )


def test_the_exemptions_are_all_real() -> None:
    # An exemption for a module that no longer exists, or that has since grown
    # a gate, is a line nobody will remove on their own.
    uses, gated, _ = _survey()
    for module in EXEMPT:
        assert (TESTS_ROOT / module).exists(), f"{module} is exempted but does not exist"
        assert module in uses, f"{module} is exempted but does not use the database"
        assert module not in gated, f"{module} now gates properly; drop the exemption"


def test_the_debt_list_only_shrinks() -> None:
    _, _, own_copy = _survey()
    added = own_copy - STILL_ROLLING_THEIR_OWN
    assert not added, (
        "these modules gate on the database with their own copy instead of "
        f"tests._db_required: {sorted(added)}. Do not add them to "
        "STILL_ROLLING_THEIR_OWN - that list is debt being paid off, not a "
        "place to register new copies."
    )
    stale = STILL_ROLLING_THEIR_OWN - own_copy
    assert not stale, (
        f"already moved, remove from STILL_ROLLING_THEIR_OWN: {sorted(stale)}"
    )


def test_ci_sets_the_flag_on_the_job_that_runs_the_database_tests() -> None:
    """The helper only changes anything where the flag is set.

    Everything else in this file could pass with the flag set nowhere, and the
    suite would go on skipping exactly as before. This reads the workflow so
    that deleting the line is a test failure rather than a silent return to
    the old behaviour.
    """
    import yaml

    workflow = yaml.safe_load(
        (TESTS_ROOT.parent.parent.parent / ".github/workflows/ci.yml").read_text()
    )
    jobs = workflow["jobs"]
    runners = [
        name
        for name, job in jobs.items()
        if any("pytest" in str(step.get("run", "")) for step in job.get("steps", []))
    ]
    assert runners, "no job in ci.yml appears to run pytest"
    for name in runners:
        env = jobs[name].get("env") or {}
        assert str(env.get("TRUSCA_TESTS_REQUIRE_DB", "")).strip() in {"1", "true"}, (
            f"job {name!r} runs the database tests without "
            "TRUSCA_TESTS_REQUIRE_DB, so a broken migration would let them "
            "skip and the job would pass"
        )
