# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
SQLAlchemy declarative base + domain model registry.

Importing this package side-effect-imports every domain model so that
`Base.metadata` is populated. Alembic's env.py points at this metadata for
autogenerate.

Convention: one module per domain (auth, scan, vulnerability, ...). Add new
domains here as they land.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide declarative base. Inherit this in every model."""


# Re-export domain models so that `import models` is enough to populate metadata.
# Keep imports below the Base definition — the auth module imports `Base` from us.
from .api_key import (  # noqa: E402,F401  (imported for metadata side effects)
    APIKey,
    WebhookDelivery,
)
from .audit_export import (  # noqa: E402,F401  (imported for metadata side effects)
    AuditExportCursor,
)
from .auth import (  # noqa: E402,F401  (imported for metadata side effects)
    AuditLog,
    Membership,
    Organization,
    PasswordResetToken,
    RefreshToken,
    Team,
    User,
)
from .component_approval import (  # noqa: E402,F401  (imported for metadata side effects)
    ApprovalStatus,
    ComponentApproval,
)
from .component_intake import (  # noqa: E402,F401  (imported for metadata side effects)
    ComponentIntakeRequest,
)
from .eol_sync_state import (  # noqa: E402,F401  (imported for metadata side effects)
    EolSyncState,
)
from .gate_policy import (  # noqa: E402,F401  (imported for metadata side effects)
    GatePolicy,
)
from .github_app import (  # noqa: E402,F401  (imported for metadata side effects)
    GitHubAppCredential,
    GitHubAppInstallation,
)
from .kev_sync_state import (  # noqa: E402,F401  (imported for metadata side effects)
    KevSyncState,
)
from .license_fetch_cache import (  # noqa: E402,F401  (imported for metadata side effects)
    LicenseFetchCache,
)
from .license_policy import (  # noqa: E402,F401  (imported for metadata side effects)
    LicensePolicy,
)
from .malicious_sync_state import (  # noqa: E402,F401  (imported for metadata side effects)
    MaliciousSyncState,
)
from .notice_template import (  # noqa: E402,F401  (imported for metadata side effects)
    NOTICE_TEMPLATE_FORMAT_VALUES,
    NoticeTemplate,
)
from .notification import (  # noqa: E402,F401  (imported for metadata side effects)
    NOTIFICATION_KIND_VALUES,
    Notification,
    NotificationPreferences,
)
from .notification_routing import (  # noqa: E402,F401  (imported for metadata side effects)
    NotificationRoutingRule,
)
from .oauth_identity import (  # noqa: E402,F401  (imported for metadata side effects)
    OAUTH_PROVIDER_VALUES,
    OAuthIdentity,
)
from .obligation_fulfilment import (  # noqa: E402,F401  (imported for metadata side effects)
    OBLIGATION_FULFILMENT_STATUSES,
    ObligationFulfilment,
)
from .organization_component_verdict import (  # noqa: E402,F401  (imported for metadata side effects)
    OrganizationComponentVerdict,
)
from .remediation_pr import (  # noqa: E402,F401  (imported for metadata side effects)
    REMEDIATION_PR_STATUS_VALUES,
    RemediationPullRequest,
)
from .report_download import (  # noqa: E402,F401  (imported for metadata side effects)
    REPORT_TYPE_VALUES,
    ReportDownload,
)
from .report_format_template import (  # noqa: E402,F401  (imported for metadata side effects)
    REPORT_COMPONENT_COLUMNS,
    REPORT_VULNERABILITY_COLUMNS,
    ReportFormatTemplate,
)
from .saved_search import (  # noqa: E402,F401  (imported for metadata side effects)
    SavedSearch,
)
from .sbom_conformance import (  # noqa: E402,F401  (imported for metadata side effects)
    SbomConformance,
)
from .scan import (  # noqa: E402,F401  (imported for metadata side effects)
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    License,
    LicenseFinding,
    Obligation,
    Project,
    Scan,
    ScanArtifact,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from .scan_schedule import (  # noqa: E402,F401  (imported for metadata side effects)
    SCAN_SCHEDULE_CADENCE_VALUES,
    ScanSchedule,
)
from .transition_approval import (  # noqa: E402,F401  (imported for metadata side effects)
    TRANSITION_APPROVAL_STATES,
    TransitionApproval,
)

__all__ = [
    "APIKey",
    "ApprovalStatus",
    "AuditLog",
    "Base",
    "Component",
    "ComponentApproval",
    "ComponentDependencyEdge",
    "ComponentVersion",
    "EolSyncState",
    "MaliciousSyncState",
    "GitHubAppCredential",
    "GitHubAppInstallation",
    "KevSyncState",
    "License",
    "LicenseFetchCache",
    "LicenseFinding",
    "GatePolicy",
    "TRANSITION_APPROVAL_STATES",
    "ComponentIntakeRequest",
    "OBLIGATION_FULFILMENT_STATUSES",
    "ObligationFulfilment",
    "OrganizationComponentVerdict",
    "TransitionApproval",
    "LicensePolicy",
    "Membership",
    "NOTIFICATION_KIND_VALUES",
    "Notification",
    "NotificationPreferences",
    "AuditExportCursor",
    "NotificationRoutingRule",
    "NOTICE_TEMPLATE_FORMAT_VALUES",
    "NoticeTemplate",
    "OAUTH_PROVIDER_VALUES",
    "OAuthIdentity",
    "Obligation",
    "Organization",
    "PasswordResetToken",
    "Project",
    "REMEDIATION_PR_STATUS_VALUES",
    "REPORT_TYPE_VALUES",
    "REPORT_COMPONENT_COLUMNS",
    "REPORT_VULNERABILITY_COLUMNS",
    "RefreshToken",
    "RemediationPullRequest",
    "ReportDownload",
    "ReportFormatTemplate",
    "SavedSearch",
    "SbomConformance",
    "SCAN_SCHEDULE_CADENCE_VALUES",
    "Scan",
    "ScanArtifact",
    "ScanComponent",
    "ScanSchedule",
    "Team",
    "User",
    "Vulnerability",
    "VulnerabilityFinding",
    "WebhookDelivery",
]
