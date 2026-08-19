# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""v1 API routers.

Add new routers here and import them in `main.py`.
"""

from .about import router as about_router
from .admin import router as admin_router
from .api_keys import router as api_keys_router
from .approvals import router as approvals_router
from .audit import router as audit_router
from .auth import router as auth_router
from .compliance import router as compliance_router
from .component_intake import router as component_intake_router
from .components import router as components_router
from .dashboard import router as dashboard_router
from .gate_policies import router as gate_policies_router
from .github_app import router as github_app_router
from .health import router as health_router
from .inventory import router as inventory_router
from .license_policies import router as license_policies_router
from .licenses import router as licenses_router
from .notifications import router as notifications_router
from .oauth import router as oauth_router
from .obligations import router as obligations_router
from .organization_verdicts import router as organization_verdicts_router
from .policy_gate import router as policy_gate_router
from .projects import router as projects_router
from .remediation import router as remediation_router
from .reports import router as reports_router
from .saved_searches import router as saved_searches_router
from .sbom import router as sbom_router
from .scans import router as scans_router
from .search import router as search_router
from .search_results import router as search_results_router
from .service_accounts import router as service_accounts_router
from .source_tree import router as source_tree_router
from .transition_approvals import router as transition_approvals_router
from .users_me import router as users_me_router
from .vex import router as vex_router
from .vulnerabilities import router as vulnerabilities_router
from .webhooks import github_router as webhooks_github_router
from .webhooks import gitlab_router as webhooks_gitlab_router
from .ws import router as ws_router

__all__ = [
    "about_router",
    "admin_router",
    "api_keys_router",
    "approvals_router",
    "audit_router",
    "auth_router",
    "compliance_router",
    "component_intake_router",
    "components_router",
    "dashboard_router",
    "github_app_router",
    "health_router",
    "gate_policies_router",
    "organization_verdicts_router",
    "service_accounts_router",
    "transition_approvals_router",
    "license_policies_router",
    "licenses_router",
    "notifications_router",
    "oauth_router",
    "obligations_router",
    "policy_gate_router",
    "projects_router",
    "remediation_router",
    "reports_router",
    "sbom_router",
    "scans_router",
    "inventory_router",
    "saved_searches_router",
    "search_results_router",
    "search_router",
    "source_tree_router",
    "users_me_router",
    "vex_router",
    "vulnerabilities_router",
    "webhooks_github_router",
    "webhooks_gitlab_router",
    "ws_router",
]
