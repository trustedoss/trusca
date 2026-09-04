# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``_detail_response`` must pass every field the response model declares.

The builder names each field of ``VulnerabilityDetailResponse`` by hand. A key
the service puts in the payload but nobody adds to that call is dropped
silently: the model field has a default, so it serialises as ``null`` and a
client reads "the server does not have this value" while the database does.

This has happened twice. The builder's own comment records ``kev`` /
``kev_due_date`` going missing during X1, and ER28a's ownership fields went the
same way. Nothing catches it: mypy is happy because the fields have defaults,
and the endpoint returns 200.

Why this reads the SOURCE and not a response
--------------------------------------------
The obvious test is "populate a finding, PATCH it, assert the field comes back".
That only guards fields the fixture happens to populate. The next person adds a
field, does not extend the fixture, and the guard silently stops covering it,
which is the same disease as the defect it exists to catch: something missing
from a hand-maintained list and nothing noticing.

Comparing the call's keyword arguments to the model's fields needs no fixture
and no data, so a new field is covered the moment it is declared.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from schemas.vulnerability_detail import VulnerabilityDetailResponse

#: Model fields the builder deliberately does not pass, each with the reason.
#: Empty today: the builder passes all 41. An entry here turns an omission from
#: an accident into a decision somebody had to write down.
INTENTIONALLY_NOT_PASSED: dict[str, str] = {}

_API_MODULE = (
    pathlib.Path(__file__).resolve().parents[3] / "api" / "v1" / "vulnerabilities.py"
)


def _builder_keyword_arguments() -> set[str]:
    """Names passed to ``VulnerabilityDetailResponse(...)`` in the builder.

    Read structurally rather than by matching text: a substring search would
    also match the name in a comment or a docstring, and would miss a call
    reformatted across lines.
    """
    tree = ast.parse(_API_MODULE.read_text())
    builder = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_detail_response"
        ),
        None,
    )
    assert builder is not None, "_detail_response is gone; this guard needs updating"

    for node in ast.walk(builder):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "VulnerabilityDetailResponse"
        ):
            assert not node.args, (
                "the builder now passes positional arguments; this guard reads "
                "keywords only and would stop seeing them"
            )
            return {kw.arg for kw in node.keywords if kw.arg is not None}
    pytest.fail("no VulnerabilityDetailResponse(...) call found in _detail_response")


def test_the_detail_builder_drops_nothing() -> None:
    declared = set(VulnerabilityDetailResponse.model_fields)
    passed = _builder_keyword_arguments()

    missing = declared - passed - set(INTENTIONALLY_NOT_PASSED)
    assert not missing, (
        f"_detail_response does not pass {sorted(missing)}. The response model "
        f"declares them, so they will serialise as null and read as 'the server "
        f"has no value' even when the database does. Add them to the call, or "
        f"to INTENTIONALLY_NOT_PASSED with a reason."
    )


def test_the_builder_passes_nothing_the_model_does_not_declare() -> None:
    """The other direction: a name kept after the field was renamed would raise
    at request time, not at import, so nothing would find it until a caller hit
    that endpoint."""
    declared = set(VulnerabilityDetailResponse.model_fields)
    unexpected = _builder_keyword_arguments() - declared
    assert not unexpected, (
        f"_detail_response passes {sorted(unexpected)}, which the response model "
        f"does not declare"
    )


def test_every_allowed_omission_states_a_reason() -> None:
    """An allow-list without reasons becomes a place to silence this guard."""
    for field, reason in INTENTIONALLY_NOT_PASSED.items():
        assert field in VulnerabilityDetailResponse.model_fields, (
            f"{field} is allow-listed but the model no longer declares it; "
            f"remove the entry"
        )
        assert len(reason.split()) >= 4, (
            f"the reason for omitting {field} is too short to be a reason"
        )
