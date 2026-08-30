# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""app-role UPDATE/DELETE gap closure for 21 tables the runtime actually mutates

Revision ID: 0072
Revises: 0071
Create Date: 2026-08-30

Phase: role-separation hardening follow-up
Kind: schema (DDL-only, no data migration)
Forward-only: yes

What:
  Migration 0014 made every table created from that point on default to
  SELECT/INSERT-only for the ``trustedoss_app`` runtime role (via
  ``ALTER DEFAULT PRIVILEGES``), on purpose - a new table starts append-only
  unless a later migration opts it in. None of migrations 0015-0071 ever
  did that opt-in, even for tables whose service-layer code updates or
  deletes rows in them. In a role-separated deployment (``DATABASE_URL_APP``,
  the L1 runtime), every one of those writes fails with
  ``permission denied for table <x>``, surfaced as an unhandled 500.

  This migration closes that gap with an explicit, per-table GRANT for
  exactly the privileges the service layer was found to actually use
  (verified against the code, not assumed from the table's shape):

  UPDATE only (row mutated in place, never deleted):
    audit_export_cursors, component_intake_requests, eol_sync_state,
    github_app_credentials, kev_sync_state, malicious_sync_state,
    organization_component_verdicts, remediation_pull_requests,
    transition_approvals

  DELETE only (row removed, never mutated in place):
    component_dependency_edges, notification_routing_rules,
    report_downloads, saved_searches, sbom_conformance

  UPDATE + DELETE (both observed):
    gate_policies, github_app_installations, license_policies,
    notice_templates, obligation_fulfilments, report_format_templates,
    scan_schedules

Why (testing-hardening-plan-2026-08.md §6, "B1이 드러낸 별건 결함"):
  Discovered during B1's (PR #238) security-reviewer pass: a census of the
  22 tables declared append-only in ``tests/fixtures/app_role_privileges.json``
  found at least 18 where the service layer does in fact UPDATE or DELETE,
  plus a bulk-DELETE-on-rescan path on two more. The most severe instance is
  ``github_app_service.py::revoke_credential()`` - a leaked GitHub App PEM
  key cannot be revoked (its UPDATE on ``github_app_credentials`` is denied)
  in a role-separated deployment. CVSS 7.1 (High), assessed by
  security-reviewer: AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H.

  A follow-up security-reviewer pass on this very migration (2026-08-30)
  caught one the original census missed: ``report_downloads`` has no
  UPDATE/DELETE endpoint (append-only from the API's perspective, which is
  why the original census called it correctly append-only), but
  ``tasks/operational_retention.py``'s daily beat sweeper bulk-DELETEs aged
  rows from it, and that task connects through the same
  ``database_url()`` -> ``DATABASE_URL_APP`` resolution every other runtime
  code path uses. Without this table's DELETE grant, the 365-day
  compliance-audit trail ("who downloaded what") this table exists to
  retain would never actually get swept in a role-separated deployment -
  the precise unbounded-growth problem the sweeper exists to prevent.

  ``audit_logs`` was checked and confirmed correctly append-only (no
  UPDATE/DELETE anywhere, including background tasks) - no change here.

How each table's privilege set was determined:
  Read every service/task module that imports the table's model, looking for
  ``session.delete(...)``, a bulk ``delete(Model)`` statement, an
  ``ON CONFLICT DO UPDATE`` upsert, or an ORM instance whose attributes are
  mutated and then committed (a ``SELECT ... FOR UPDATE`` lock is corroborating
  evidence of an intended UPDATE). No table below got UPDATE or DELETE it
  was not observed to use - the point of this migration is precision, not a
  blanket "just grant everything" pass. This reading also has to cover
  background/Celery tasks, not just API-facing service functions -
  ``report_downloads`` (see "Why" above) is DELETE-only precisely because
  its only mutation path is a sweeper task, invisible to a grep scoped to
  ``services/``. Three results depart from the informal guess in the
  tracking doc: ``notification_routing_rules`` has a DELETE endpoint but no
  UPDATE/PATCH one (its rule is create/delete-only), ``github_app_installations``
  needs BOTH (a re-link mutates the existing installation row's
  ``account_login``/``project_id``, and ``unlink_installation`` deletes it)
  rather than the UPDATE-only grouping the doc's rough heuristic suggested,
  and ``report_downloads`` itself was originally (incorrectly) assumed
  append-only until the tasks/ sweep above.

Follow-up:
  ``tests/fixtures/app_role_privileges.json`` is updated in the same PR so
  ``test_declared_privileges_match_actual_grants``
  (tests/integration/test_app_role_grant_matrix.py, B1) locks these 21
  tables to their new, correct privilege sets from this point on.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    NotImplementedError. Manual rollback if ever needed: connect as the
    owner role and REVOKE the specific privilege(s) listed above per table.
  - No-op (like 0014) when the ``trustedoss_app`` role does not exist -
    single-role deployments are unaffected.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Table -> extra privileges beyond the SELECT/INSERT every table already has
# by default (0014's ALTER DEFAULT PRIVILEGES). See the module docstring for
# how each entry was verified against the actual service-layer code.
_UPDATE_ONLY: tuple[str, ...] = (
    "audit_export_cursors",
    "component_intake_requests",
    "eol_sync_state",
    "github_app_credentials",
    "kev_sync_state",
    "malicious_sync_state",
    "organization_component_verdicts",
    "remediation_pull_requests",
    "transition_approvals",
)

_DELETE_ONLY: tuple[str, ...] = (
    "component_dependency_edges",
    "notification_routing_rules",
    "report_downloads",
    "saved_searches",
    "sbom_conformance",
)

_UPDATE_AND_DELETE: tuple[str, ...] = (
    "gate_policies",
    "github_app_installations",
    "license_policies",
    "notice_templates",
    "obligation_fulfilments",
    "report_format_templates",
    "scan_schedules",
)


def _grant_ddl() -> str:
    lines = [
        "DO $$",
        "BEGIN",
        "    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN",
    ]
    for table in _UPDATE_ONLY:
        lines.append(f"        GRANT UPDATE ON {table} TO trustedoss_app;")
    for table in _DELETE_ONLY:
        lines.append(f"        GRANT DELETE ON {table} TO trustedoss_app;")
    for table in _UPDATE_AND_DELETE:
        lines.append(f"        GRANT UPDATE, DELETE ON {table} TO trustedoss_app;")
    lines.extend(
        [
            "        RAISE NOTICE",
            "            'trustedoss_app role: closed the UPDATE/DELETE gap on "
            "21 tables (testing-hardening-plan-2026-08.md B1 follow-up)';",
            "    ELSE",
            "        RAISE NOTICE 'trustedoss_app role not found - "
            "single-role legacy mode (no-op)';",
            "    END IF;",
            "END $$;",
        ]
    )
    return "\n".join(lines)


_GRANT_DDL = _grant_ddl()


def upgrade() -> None:
    op.execute(_GRANT_DDL)


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
