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
from .github_app import (  # noqa: E402,F401  (imported for metadata side effects)
    GitHubAppCredential,
    GitHubAppInstallation,
)
from .license_fetch_cache import (  # noqa: E402,F401  (imported for metadata side effects)
    LicenseFetchCache,
)
from .license_policy import (  # noqa: E402,F401  (imported for metadata side effects)
    LicensePolicy,
)
from .notification import (  # noqa: E402,F401  (imported for metadata side effects)
    NOTIFICATION_KIND_VALUES,
    Notification,
    NotificationPreferences,
)
from .oauth_identity import (  # noqa: E402,F401  (imported for metadata side effects)
    OAUTH_PROVIDER_VALUES,
    OAuthIdentity,
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

__all__ = [
    "APIKey",
    "ApprovalStatus",
    "AuditLog",
    "Base",
    "Component",
    "ComponentApproval",
    "ComponentDependencyEdge",
    "ComponentVersion",
    "GitHubAppCredential",
    "GitHubAppInstallation",
    "License",
    "LicenseFetchCache",
    "LicenseFinding",
    "LicensePolicy",
    "Membership",
    "NOTIFICATION_KIND_VALUES",
    "Notification",
    "NotificationPreferences",
    "OAUTH_PROVIDER_VALUES",
    "OAuthIdentity",
    "Obligation",
    "Organization",
    "PasswordResetToken",
    "Project",
    "RefreshToken",
    "Scan",
    "ScanArtifact",
    "ScanComponent",
    "Team",
    "User",
    "Vulnerability",
    "VulnerabilityFinding",
    "WebhookDelivery",
]
