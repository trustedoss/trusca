"""v1 API routers.

Add new routers here and import them in `main.py`.
"""

from .admin import router as admin_router
from .approvals import router as approvals_router
from .auth import router as auth_router
from .components import router as components_router
from .licenses import router as licenses_router
from .obligations import router as obligations_router
from .projects import router as projects_router
from .sbom import router as sbom_router
from .scans import router as scans_router
from .vulnerabilities import router as vulnerabilities_router
from .ws import router as ws_router

__all__ = [
    "admin_router",
    "approvals_router",
    "auth_router",
    "components_router",
    "licenses_router",
    "obligations_router",
    "projects_router",
    "sbom_router",
    "scans_router",
    "vulnerabilities_router",
    "ws_router",
]
