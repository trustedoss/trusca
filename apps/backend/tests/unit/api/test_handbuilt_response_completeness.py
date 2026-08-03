"""Contract — hand-built response constructions pass EVERY model field.

Several endpoints construct their response model field by field (not
``**payload`` / ``model_validate``). That pattern has silently dropped
fields THREE times now: ``dependency_scope`` (W2 #31 — the wire carried
``null`` for two releases), the Phase M ``eol_*`` block on the component
drawer, and ``eol_count`` on the project overview (masked by the schema's
``default=0``, caught by the components_eol e2e). A dropped field never
fails a type check — Pydantic happily defaults it — so only a completeness
contract catches the next one.

Each assertion walks the endpoint module's AST and requires a keyword
argument for every field of the hand-built model. Extend ``_cases`` whenever
a new endpoint hand-builds its response.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest


def _passed_kwargs(module: Any, model_name: str) -> set[str]:
    source = Path(module.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == model_name
        ):
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    return set()


def _cases() -> list[tuple[str, Any, type[Any]]]:
    import api.v1.components as components_module
    import api.v1.projects as projects_module
    from schemas.project_detail import (
        ComponentDetailResponse,
        ProjectOverviewResponse,
    )

    return [
        ("components", components_module, ComponentDetailResponse),
        ("projects-overview", projects_module, ProjectOverviewResponse),
    ]


@pytest.mark.parametrize(
    ("label", "module", "model"),
    _cases(),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_every_response_field_is_passed_by_the_endpoint(
    label: str, module: Any, model: type[Any]
) -> None:
    passed = _passed_kwargs(module, model.__name__)
    assert passed, f"{model.__name__} construction not found in {label}"

    expected = set(model.model_fields)
    missing = expected - passed
    assert not missing, (
        f"{label} builds {model.__name__} without {sorted(missing)} — the "
        f"field silently defaults on the wire (the dependency_scope / eol_* "
        f"/ eol_count drop class)"
    )
