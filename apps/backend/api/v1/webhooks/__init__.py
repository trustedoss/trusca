"""
Webhook receivers — Phase 5 PR #16.

Two sub-routers:

  - ``github`` — POST /v1/webhooks/github
  - ``gitlab`` — POST /v1/webhooks/gitlab

Both are PUBLIC endpoints (no JWT required) but each delivery is authenticated
via a per-project shared secret stored in ``projects.webhook_secret``:

  - GitHub: HMAC-SHA256 signature over the request body
            (``X-Hub-Signature-256: sha256=<hex>``).
  - GitLab: shared bearer token (``X-Gitlab-Token: <secret>``), constant-time
            compared.

Idempotency is enforced via the unique index on ``webhook_deliveries
(provider, delivery_id)``.
"""

from __future__ import annotations

from .github import router as github_router
from .gitlab import router as gitlab_router

__all__ = ["github_router", "gitlab_router"]
