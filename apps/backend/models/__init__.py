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
from .auth import (  # noqa: E402,F401  (imported for metadata side effects)
    AuditLog,
    Membership,
    Organization,
    RefreshToken,
    Team,
    User,
)
from .scan import (  # noqa: E402,F401  (imported for metadata side effects)
    Component,
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
    "AuditLog",
    "Base",
    "Component",
    "ComponentVersion",
    "License",
    "LicenseFinding",
    "Membership",
    "Obligation",
    "Organization",
    "Project",
    "RefreshToken",
    "Scan",
    "ScanArtifact",
    "ScanComponent",
    "Team",
    "User",
    "Vulnerability",
    "VulnerabilityFinding",
]
