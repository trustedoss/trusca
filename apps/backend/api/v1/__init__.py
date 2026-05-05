"""v1 API routers.

Add new routers here and import them in `main.py`.
"""

from .auth import router as auth_router
from .projects import router as projects_router
from .scans import router as scans_router

__all__ = ["auth_router", "projects_router", "scans_router"]
