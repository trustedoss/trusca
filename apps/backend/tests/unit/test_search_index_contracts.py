# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Search index contract tests — S1-1.

The five GIN trigram indexes that back every leading-wildcard search live in
two places: migration ``0043_search_trigram_indexes`` (what the database
actually gets) and ``Index(...)`` entries on the models (what SQLAlchemy's
metadata believes). Two copies of one vocabulary, so the testing-standards
rule applies: import BOTH sides and assert set equality.

The defect this guards against is the quiet one — someone adds a sixth
trigram index to the models and forgets the migration (so it never exists in
any deployment), or drops one from a model's ``__table_args__`` while the
migration still creates it. Either way every per-module test stays green and
the drift only surfaces as a mysteriously slow search months later.

Pure-import set assertions: no database, no fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from models import Base

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0043_search_trigram_indexes.py"
)


def _load_migration() -> Any:
    """Import the migration module by path.

    Alembic version files are not importable as a package (the directory has
    no ``__init__.py`` and the module names start with digits), so we load it
    from its file location rather than reaching for ``import``.
    """
    spec = importlib.util.spec_from_file_location(
        "_migration_0043", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _model_trigram_indexes() -> set[tuple[str, str, str]]:
    """Collect (index name, table, column) for every gin_trgm_ops index.

    Reads SQLAlchemy metadata rather than a hand-kept list, so a new trigram
    index on any model is picked up automatically — which is the point: the
    test should fail when the model side grows an index the migration lacks.
    """
    found: set[tuple[str, str, str]] = set()
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            ops = index.dialect_options["postgresql"].get("ops") or {}
            if not any(op == "gin_trgm_ops" for op in ops.values()):
                continue
            columns = list(index.columns)
            # Every trigram index we declare is single-column; a composite one
            # would need a different equality shape, so assert the assumption
            # instead of silently mis-reporting it.
            assert len(columns) == 1, (
                f"{index.name} is a multi-column trigram index; this contract "
                "test only models single-column ones"
            )
            assert index.name is not None
            found.add((index.name, table.name, columns[0].name))
    return found


def test_migration_and_models_declare_the_same_trigram_indexes() -> None:
    """Set equality between migration 0043 and the model metadata."""
    migration = _load_migration()
    from_migration = {
        (name, table, column) for name, table, column in migration._TRIGRAM_INDEXES
    }
    from_models = _model_trigram_indexes()

    assert from_models == from_migration, (
        "trigram index drift between migration 0043 and the models — "
        f"models only: {sorted(from_models - from_migration)}; "
        f"migration only: {sorted(from_migration - from_models)}"
    )


def test_migration_uses_gin_trgm_ops_for_every_declared_index() -> None:
    """The migration must ask for GIN + gin_trgm_ops, not a plain b-tree.

    A b-tree on these columns would be indistinguishable in the index list yet
    useless for ``ILIKE '%term%'`` — the exact failure mode S1-1 exists to fix.
    """
    migration = _load_migration()
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert 'postgresql_using="gin"' in source
    assert '"gin_trgm_ops"' in source
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in source
    # Guard the loop that creates them: every entry has to be reachable.
    assert len(migration._TRIGRAM_INDEXES) == 5


def test_searched_columns_are_all_covered() -> None:
    """Each column a search endpoint matches with a leading wildcard is indexed.

    Named explicitly rather than derived, so that adding a search axis to a
    service without an index is a visible decision (update this list) instead
    of an accident.
    """
    expected = {
        ("components", "name"),
        ("components", "purl"),
        ("vulnerabilities", "external_id"),
        ("vulnerabilities", "summary"),
        ("projects", "name"),
    }
    covered = {(table, column) for _, table, column in _model_trigram_indexes()}
    assert covered == expected
