"""OpenAPI contract drift gate (Tier N).

Catches the bug class where the API contract silently changes — an endpoint is
renamed/removed or its parameters shift — and clients (frontend, CI action, the
load test) keep calling the old shape. The 2026-05-22 session hit two instances
of this class: a stale locustfile targeting removed endpoints, and the frontend
requesting ``?size=200`` while the backend caps ``size`` at 100.

The committed snapshot (``openapi_endpoints.json``) is the source of truth for
the wire surface. A drift fails the PR with an explicit add/remove/changed diff;
an INTENTIONAL change is landed by regenerating the snapshot (reviewed in the
diff), via ``REGEN_OPENAPI_SNAPSHOT=1 pytest -k openapi_no_drift``.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from main import app

SNAPSHOT = Path(__file__).parent / "openapi_endpoints.json"
_METHODS = {"get", "post", "put", "patch", "delete"}


def _signature() -> dict[str, list[str]]:
    """METHOD path → sorted parameter names. Stable across unrelated edits."""
    spec = app.openapi()
    sig: dict[str, list[str]] = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() in _METHODS:
                params = sorted(
                    p.get("name", "") for p in op.get("parameters", []) if isinstance(p, dict)
                )
                sig[f"{method.upper()} {path}"] = params
    return sig


def test_openapi_no_drift() -> None:
    current = _signature()
    if os.getenv("REGEN_OPENAPI_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return
    assert SNAPSHOT.exists(), (
        "OpenAPI snapshot missing — generate it with "
        "REGEN_OPENAPI_SNAPSHOT=1 pytest -k openapi_no_drift"
    )
    expected = json.loads(SNAPSHOT.read_text())
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    changed = {
        k: {"snapshot": expected[k], "current": current[k]}
        for k in set(current) & set(expected)
        if current[k] != expected[k]
    }
    assert not (added or removed or changed), (
        "OpenAPI contract drift — if intentional, regenerate the snapshot "
        "(REGEN_OPENAPI_SNAPSHOT=1) and review the diff in the PR:\n"
        + json.dumps(
            {"added": added, "removed": removed, "changed": changed},
            indent=2,
            sort_keys=True,
        )
    )


# ---------------------------------------------------------------------------
# Internal-reference leak gate
# ---------------------------------------------------------------------------
#
# FastAPI copies a route function's docstring into the OpenAPI ``description``.
# The schema is committed, published to the docs site, and served at
# ``/api/docs`` — so a docstring is public output, not an internal note, and
# nothing about writing one says so.
#
# That is not hypothetical: one route explained a 404 decision by citing an
# internal review finding by its reviewer and tracker id, and pointed at an
# internal planning document by path. Both reached the published schema. The
# explanation was worth keeping; the two references were not.
#
# This checks the ASSEMBLED schema rather than the source, so it also covers
# ``summary=``, ``description=`` and tag metadata passed to the decorator —
# every route into the document, not just the docstring one.

_INTERNAL_MARKERS = (
    # Internal document trees. `docs/` is leaving the repository; a path into
    # it is a dead link that still names the document.
    re.compile(r"docs/[a-z0-9._-]+/|docs/[a-z0-9._-]+\.md", re.IGNORECASE),
    # Agent names from the harness.
    re.compile(r"\b(security review|db-designer|test-writer|doc-writer|"
               r"scan-pipeline-specialist|backend-developer|frontend-dev|"
               r"i18n-specialist|devops-engineer|ip-counsel)\b", re.IGNORECASE),
    # Assistant memory keys.
    re.compile(r"\bMEMORY:|feedback_[a-z_]{4,}", re.IGNORECASE),
    # Internal tracker labels and delivery-plan phases.
    #
    # No trailing ``\b`` after the issue number. Some ids carry a letter
    # suffix, and ``\d+\b`` does not match ``43e`` — the digit-to-letter seam
    # is not a word boundary. One such id sat in a published schema while an
    # earlier version of this pattern reported the document clean.
    re.compile(r"\bW\d+-#\d+", re.IGNORECASE),
    re.compile(r"\bPR #\d+\b", re.IGNORECASE),
    re.compile(r"\bPhase \d+\b", re.IGNORECASE),
)


def _schema_text_fields() -> list[tuple[str, str]]:
    """(location, text) for every human-readable string in the schema."""
    spec = app.openapi()
    out: list[tuple[str, str]] = []

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"description", "summary", "title"} and isinstance(value, str):
                    out.append((f"{where}.{key}", value))
                else:
                    walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    walk(spec, "openapi")
    return out


def test_openapi_carries_no_internal_references() -> None:
    """No internal path, agent name, memory key or tracker label is published.

    When this fails, fix the docstring — do not add an exception. The schema is
    read by people outside this project, and an internal reference tells them
    about our process without telling them anything about the API.
    """
    hits = [
        (where, marker.pattern, text.strip()[:120])
        for where, text in _schema_text_fields()
        for marker in _INTERNAL_MARKERS
        if marker.search(text)
    ]

    assert not hits, (
        "internal references reached the published OpenAPI schema:\n"
        + "\n".join(f"  {where}\n    {snippet}" for where, _pattern, snippet in hits)
    )


def test_offset_page_parameters_declare_an_upper_bound() -> None:
    """Every ``page`` query parameter must cap how far the offset can go.

    Python integers are unbounded, so ``page`` with only ``ge=1`` accepts a
    number of any size, multiplies it into an OFFSET, and hands asyncpg a value
    outside int64 — a 500 rather than a 422:

        asyncpg.exceptions.DataError: invalid input for query argument $2:
        423599349510580893366758764207878963180 (value out of int64 range)

    Schema-based fuzzing surfaced this on two endpoints; all 19 listing
    endpoints were missing the bound. Asserting it against the generated spec
    rather than the source keeps a newly added endpoint from reintroducing it.
    Use ``le=PAGE_MAX`` from ``core.pagination``.
    """
    spec = app.openapi()
    missing = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in _METHODS:
                continue
            for param in op.get("parameters", []):
                if not isinstance(param, dict) or param.get("name") != "page":
                    continue
                if param.get("schema", {}).get("maximum") is None:
                    missing.append(f"{method.upper()} {path}")

    assert not missing, (
        "page parameters without an upper bound (add le=PAGE_MAX):\n  "
        + "\n  ".join(sorted(missing))
    )


def test_error_responses_are_declared_as_problem_json() -> None:
    """Documented 4xx/5xx responses must use the media type we actually send.

    Every error leaves this API through ``core.errors.problem_response`` as
    ``application/problem+json``. FastAPI's generated 422 said
    ``application/json``, so the document promised one shape and the server
    sent another on every endpoint that takes input. ``core.openapi`` corrects
    this centrally; this asserts it stayed corrected.
    """
    from core.errors import PROBLEM_CONTENT_TYPE

    spec = app.openapi()
    wrong = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in _METHODS:
                continue
            for code, resp in op.get("responses", {}).items():
                if not code.isdigit() or int(code) < 400:
                    continue
                content = resp.get("content")
                if not content:
                    continue
                for media_type in content:
                    if media_type != PROBLEM_CONTENT_TYPE:
                        wrong.append(f"{method.upper()} {path} {code} -> {media_type}")

    assert not wrong, (
        "error responses not declared as problem+json:\n  " + "\n  ".join(sorted(wrong))
    )


def test_authenticated_operations_declare_401() -> None:
    """Anything behind the auth dependency must document that it can 401.

    Undocumented status codes were the largest category the schema fuzzer
    reported. Declaring them is what lets a generated client handle the
    response instead of treating it as a surprise.
    """
    from core.openapi import PUBLIC_PATHS, _is_public

    spec = app.openapi()
    missing = []
    for path, methods in spec.get("paths", {}).items():
        if _is_public(path):
            continue
        for method, op in methods.items():
            if method.lower() not in _METHODS:
                continue
            if "401" not in op.get("responses", {}):
                missing.append(f"{method.upper()} {path}")

    assert not missing, (
        "operations behind auth without a documented 401 (add the path to "
        f"PUBLIC_PATHS if it is genuinely public; currently {len(PUBLIC_PATHS)} "
        "are):\n  " + "\n  ".join(sorted(missing))
    )
