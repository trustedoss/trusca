# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Admin API-key hash-migration status route (A5).

Endpoint: ``GET /v1/admin/api-keys/hash-migration``, counts active API
keys by stored-hash format. Auth gated by the parent admin router
(super-admin only, existence-hide 404 for anyone else).

concurrency-scaling-plan-2026-08-22.md §3.3 A5 moved API-key hashing from
bcrypt to a fast keyed HMAC-SHA256, expand-first: new keys hash with HMAC,
existing keys keep their bcrypt hash until reissued. The contraction step
(dropping bcrypt reads from the authentication path) is a separate,
follow-up change that only ships once every active key has moved. This
endpoint is how an operator confirms that from data instead of guessing
from elapsed time.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, require_super_admin_or_404
from schemas.api_key import APIKeyHashMigrationOut
from services.api_key_service import count_legacy_hash_api_keys

router = APIRouter(prefix="/api-keys", tags=["admin"])
log = structlog.get_logger("admin.api_keys.api")


@router.get(
    "/hash-migration",
    response_model=APIKeyHashMigrationOut,
    summary="Active API-key count by stored-hash format (bcrypt vs HMAC-SHA256, A5)",
)
async def get_api_key_hash_migration_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> APIKeyHashMigrationOut:
    counts = await count_legacy_hash_api_keys(session)
    return APIKeyHashMigrationOut(
        legacy_bcrypt_count=counts.legacy_bcrypt,
        hmac_sha256_count=counts.hmac_sha256,
        active_total=counts.total,
    )


__all__ = ["router"]
