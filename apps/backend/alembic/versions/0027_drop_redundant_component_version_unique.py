"""drop_redundant_component_version_unique — W8-#46 Maven classifier P0

Revision ID: 0027
Revises: 0026
Created: 2026-05-27

Phase: post-v2.3 W8 (scan-bench 발견 후속)
PR: W8-#46 (Maven classifier purl UniqueViolation, GA blocker)
Kind: schema (constraint drop)
Forward-only: yes

What:
  - Drop ``uq_component_versions_component_version`` UNIQUE constraint on
    (component_id, version).
  - Keep ``uq_component_versions_purl_with_version`` UNIQUE constraint on
    purl_with_version (this is the natural key and is strictly more correct
    than the dropped constraint).
  - Keep ``ix_component_versions_component_id`` index for query performance.

Why:
  - cdxgen emits the same Maven artefact under multiple purls when classifier
    qualifiers differ (e.g. ``pkg:maven/com.github.jnr/jffi@1.3.1`` and
    ``pkg:maven/com.github.jnr/jffi@1.3.1?classifier=native&type=jar``).
    These are correctly separate ``components`` rows (the ``components.purl``
    column is unique and qualifier-aware), but the redundant
    ``(component_id, version)`` UNIQUE constraint collapsed them at the
    ``component_versions`` layer — the second INSERT for the same
    (component_id, "1.3.1") tuple violated the constraint even though the
    full purl_with_version differed.
  - Reproduced 2 times against WebGoat v8.2.2 (com.github.jnr/jffi@1.3.1) via
    scan-bench. See ``docs/scans/realworld-benchmark-2026-05-27.md``.
  - Impact: every Java multi-module + native-classifier OSS (JNI/JFFI/Netty
    native/Tomcat native/snappy-java) was unscannable. P0 GA blocker for
    v2.4.0 (first DT-free public release).

Why drop (not add qualifier column):
  - ``component_versions.purl_with_version`` is already defined NOT NULL +
    UNIQUE on the column itself (see ``models/scan.py`` and
    ``alembic/versions/0003_scan_schema.py`` line 205). It encodes the full
    qualifier-aware identity. Adding a separate ``classifier_qualifier``
    column would duplicate state already encoded in the purl.
  - No code anywhere in ``apps/backend`` looks up ComponentVersion by the
    ``(component_id, version)`` tuple — every lookup uses
    ``purl_with_version`` (e.g. ``tasks/scan_source.py:2570``
    ``_get_or_create_component_version``). The dropped constraint was pure
    schema redundancy that happened to also be schema-incorrect.

Backfill:
  - None. Existing rows are already valid under the surviving
    ``purl_with_version`` UNIQUE constraint (the dropped constraint was
    strictly stricter than the natural key, so any existing row that
    satisfied it also satisfies the survivor).

Downgrade:
  - Forward-only per CLAUDE.md §6 (post-GA migration policy). Re-adding the
    constraint would now fail on any DB that has accepted classifier-qualified
    rows since this migration ran.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_component_versions_component_version",
        "component_versions",
        type_="unique",
    )


def downgrade() -> None:
    """Forward-only per CLAUDE.md §6 — do not run in production."""
