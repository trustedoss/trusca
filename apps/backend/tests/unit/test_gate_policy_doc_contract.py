# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Doc oracle for the build gate policy precondition (hardening rule 4).

The API overview's "Build gate policies" section tells a client which status
and which problem ``type`` it gets for each case, and that the precondition
headers are optional. A client is written against that table, so it has to
agree with what the API declares.

The other direction matters as much: the pairs are read off the live OpenAPI
document and the service constants, not listed here, so changing a status or a
URI in the code without the guide fails this file whatever the prose says.
What the endpoints actually return for each row is driven end to end in
``tests/integration/test_gate_policies_api.py``.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from main import app
from services.gate_policy_service import GATE_POLICY_CONFLICT_TYPE, GATE_POLICY_NOT_SET_TYPE

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
GUIDES = (
    REPO_ROOT / "docs-site" / "docs" / "reference" / "api-overview.md",
    REPO_ROOT
    / "docs-site"
    / "i18n"
    / "ko"
    / "docusaurus-plugin-content-docs"
    / "current"
    / "reference"
    / "api-overview.md",
)
TEAM_PATH = "/v1/gate-policies/teams/{team_id}"
ORG_PATH = "/v1/gate-policies/org/{organization_id}"

_ROW = re.compile(r"^\|[^|]*\|\s*`(\d{3})`[^|]*\|\s*`?([^|`]*?)`?\s*\|\s*$")


def _section(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find("{#gate-policies}")
    assert start != -1, f"{path.name} lost the gate-policies section this oracle reads"
    end = text.find("\n## ", start)
    return text[start : end if end != -1 else len(text)]


def _documented_pairs(path: pathlib.Path) -> set[tuple[int, str]]:
    """(status, problem type) for every table row that names a domain type."""
    pairs = set()
    for line in _section(path).splitlines():
        match = _ROW.match(line.strip())
        if match and match.group(2).startswith("https://"):
            pairs.add((int(match.group(1)), match.group(2)))
    return pairs


def _declared_pairs() -> set[tuple[int, str]]:
    """(status, problem type) from the examples the routes declare in OpenAPI."""
    spec = app.openapi()
    pairs = set()
    routes = ((TEAM_PATH, "put"), (TEAM_PATH, "get"), (TEAM_PATH, "delete"), (ORG_PATH, "put"))
    for path, method in routes:
        for code, response in spec["paths"][path][method]["responses"].items():
            for media in response.get("content", {}).values():
                example = media.get("example")
                if isinstance(example, dict) and str(example.get("type", "")).startswith("https://"):
                    assert example["status"] == int(code), (path, method, code)
                    pairs.add((int(code), example["type"]))
    return pairs


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.parts[-4])
def test_the_guide_documents_exactly_the_declared_problem_types(guide: pathlib.Path) -> None:
    assert guide.is_file(), f"{guide} moved; this oracle needs updating"
    expected = {(409, GATE_POLICY_CONFLICT_TYPE), (404, GATE_POLICY_NOT_SET_TYPE)}

    assert _declared_pairs() == expected, (
        "the gate policy routes no longer declare the problem types the service "
        "stamps; update the OpenAPI examples in api/v1/gate_policies.py"
    )
    assert _documented_pairs(guide) == expected, (
        f"{guide.name}: the gate-policies table does not match the statuses and "
        "problem types the API declares"
    )


@pytest.mark.parametrize("path", [TEAM_PATH, ORG_PATH])
def test_the_precondition_headers_stay_optional(path: str) -> None:
    """The guide promises a PUT without either header still succeeds (no 428)."""
    params = {
        p["name"]: p for p in app.openapi()["paths"][path]["put"].get("parameters", [])
    }
    for name in ("if-match", "if-none-match"):
        assert name in params, f"PUT {path} no longer declares {name}"
        assert params[name]["in"] == "header"
        assert not params[name].get("required", False), (
            f"PUT {path} now requires {name}; the guide says it is optional"
        )
    assert "428" not in app.openapi()["paths"][path]["put"]["responses"]


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: p.parts[-4])
def test_the_guide_says_the_headers_are_optional_and_name_the_etag(guide: pathlib.Path) -> None:
    section = _section(guide)
    for token in ("`ETag`", "`If-Match`", "`If-None-Match: *`", "`200`", "`403`"):
        assert token in section, f"{guide.name}: gate-policies section no longer mentions {token}"
