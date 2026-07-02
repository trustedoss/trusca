"""
Backend service tests for ``services/obligation_service.py`` — Phase 3 PR #13.

Covers three entry points + their pure helpers:

- :func:`list_project_obligations`
- :func:`get_obligation_detail`
- :func:`generate_notice`

Mirrors :file:`tests/unit/test_license_service.py` structurally:

  - Pure cases (filter normalisation, distribution ordering, header /
    empty-notice rendering) run on every PR — no DB dependency, so they
    survive a downed local Postgres.
  - DB-backed cases are gated on ``DATABASE_URL`` + ``alembic upgrade head``
    via the ``integration`` marker. CI brings up a real Postgres testcontainer.
  - The ``_isolate_engine_per_test`` autouse fixture in tests/conftest.py
    keeps asyncpg's connection pool from leaking across the per-test event
    loop pytest-asyncio creates.

Read-only domain — no mutation cases.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog
import structlog.testing
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from schemas.obligation_detail import KNOWN_OBLIGATION_KINDS
from services.obligation_service import (
    _NOTICE_COMPONENT_LABELS_CAP,
    _NOTICE_LICENSE_CAP,
    _NOTICE_LICENSE_TEXT_CAP,
    _NOTICE_OBLIGATIONS_PER_LICENSE_CAP,
    ObligationError,
    ObligationNotFound,
    _clamp_obligation_text,
    _clean_copyright,
    _format_header,
    _html_reference_line,
    _license_text_sections,
    _md_escape,
    _md_fence_for,
    _normalize_category_filter,
    _normalize_kind_filter,
    _order_distribution,
    _purl_source_url,
    _render_empty_notice,
    _render_notice,
    _render_notice_html,
    generate_notice,
    get_obligation_detail,
    list_project_obligations,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _comps(*labels: str) -> list[dict]:
    """Phase B entry shape: each credited component is a dict (label +
    copyright + source_url), not a bare label string."""
    return [{"label": lb, "copyright": None, "source_url": None} for lb in labels]


# ---------------------------------------------------------------------------
# Pure-helper tests (no DB) — run on every PR.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], []),
        (["forbidden"], ["forbidden"]),
        (["forbidden", "allowed"], ["forbidden", "allowed"]),
        (["BOGUS"], []),
        (["BOGUS", "forbidden"], ["forbidden"]),
    ],
)
def test_normalize_category_filter(
    raw: list[str] | None, expected: list[str] | None
) -> None:
    assert _normalize_category_filter(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ([], []),
        (["attribution"], ["attribution"]),
        # Trim whitespace.
        (["  attribution  "], ["attribution"]),
        # Dedupe.
        (["attribution", "attribution"], ["attribution"]),
        # Empty / pure-whitespace dropped.
        (["", "   "], []),
        # Mixed dedupe + retain order.
        (["copyleft", "attribution", "copyleft"], ["copyleft", "attribution"]),
    ],
)
def test_normalize_kind_filter(
    raw: list[str] | None, expected: list[str] | None
) -> None:
    assert _normalize_kind_filter(raw) == expected


def test_normalize_kind_filter_caps_64_chars() -> None:
    """Length cap rejects any string longer than 64 chars (DB column size)."""
    long_kind = "x" * 65
    assert _normalize_kind_filter([long_kind]) == []
    # 64 exactly is accepted.
    just_fits = "x" * 64
    assert _normalize_kind_filter([just_fits]) == [just_fits]


def test_order_distribution_known_kinds_first_then_unknown_alphabetical() -> None:
    """Known kinds preserve KNOWN_OBLIGATION_KINDS order; unknowns sort A→Z."""
    counts = {
        "z-future-kind": 7,
        "attribution": 1,
        "a-future-kind": 5,
        "copyleft": 2,
        "notice": 3,
    }
    ordered = _order_distribution(counts)
    keys = list(ordered.keys())
    # Known kinds present surface in canonical order.
    known_present = [k for k in KNOWN_OBLIGATION_KINDS if k in counts]
    assert keys[: len(known_present)] == known_present
    # Unknown kinds appended alphabetically.
    assert keys[len(known_present) :] == ["a-future-kind", "z-future-kind"]
    # Counts preserved verbatim.
    assert ordered["attribution"] == 1
    assert ordered["z-future-kind"] == 7


def test_order_distribution_only_unknown_kinds_alphabetical() -> None:
    counts = {"zeta": 2, "alpha": 1, "mu": 3}
    ordered = _order_distribution(counts)
    assert list(ordered.keys()) == ["alpha", "mu", "zeta"]


def test_order_distribution_empty_input_returns_empty() -> None:
    assert _order_distribution({}) == {}


def test_known_obligation_kinds_canonical_set() -> None:
    """The canonical allow-list shape is part of the wire contract — pin it.

    H-9: ``patent`` joined the advertised vocabulary because the catalog emits
    it (Apache-2.0 / GPL-3.0 patent grants); it must round-trip as a known kind.
    """
    assert KNOWN_OBLIGATION_KINDS == (
        "attribution",
        "notice",
        "source-disclosure",
        "copyleft",
        "modifications",
        "dynamic-linking",
        "no-endorsement",
        "patent",
    )


def test_every_emitted_kind_is_in_known_vocabulary() -> None:
    """Drift guard (H-9/M-23): no catalog row may emit a kind outside the vocab.

    This is the invariant the report broke on two fronts — ``patent`` was
    emitted but unlisted, and ``source_disclosure`` (underscore) crept in via a
    hand-written seed. Walk every catalog entry and assert its kind is known so
    a future drift fails loudly here instead of fragmenting a kind filter.
    """
    from services import obligation_catalog as cat

    emitted: set[str] = set()
    for spdx in cat.catalog_spdx_ids():
        obligations = cat.get_license_obligations(spdx)
        if obligations is None:
            continue
        emitted.update(kind for kind, _text in obligations.rows)

    unknown = emitted - set(KNOWN_OBLIGATION_KINDS)
    assert not unknown, f"catalog emits kinds outside the vocabulary: {sorted(unknown)}"
    # dynamic-linking must actually be reachable now (M-23), not just advertised.
    assert "dynamic-linking" in emitted


def test_format_header_text_includes_project_name_and_iso_datetime() -> None:
    when = datetime(2026, 5, 7, 12, 30, 45, tzinfo=UTC)
    header = _format_header("MyProj", when, fmt="text")
    assert "MyProj" in header
    # ISO8601 UTC suffix.
    assert "2026-05-07T12:30:45+00:00" in header
    # Plain text variant does NOT use markdown H1.
    assert not header.startswith("# ")


def test_format_header_markdown_uses_h1_heading() -> None:
    when = datetime(2026, 5, 7, 12, 30, 45, tzinfo=UTC)
    header = _format_header("MyProj", when, fmt="markdown")
    assert header.startswith("# Third-party Licenses for MyProj")
    # Markdown variant carries a code-formatted ISO datetime.
    assert "`2026-05-07T12:30:45+00:00`" in header


def test_render_empty_notice_text_mentions_no_scan() -> None:
    when = datetime(2026, 5, 7, 0, 0, 0, tzinfo=UTC)
    body = _render_empty_notice("MyProj", when, fmt="text")
    assert "MyProj" in body
    assert "no scan has been run" in body.lower()
    # Must end with a newline so file writers don't double-up.
    assert body.endswith("\n")


def test_render_empty_notice_markdown_uses_emphasis() -> None:
    when = datetime(2026, 5, 7, 0, 0, 0, tzinfo=UTC)
    body = _render_empty_notice("MyProj", when, fmt="markdown")
    assert body.startswith("# Third-party Licenses for MyProj")
    # Markdown variant uses italic emphasis on the empty marker.
    assert "_No scan has been run" in body


# ---------------------------------------------------------------------------
# HTML NOTICE rendering (G1) — pure, no DB. The interpolated values come from
# scanned package metadata (untrusted), so escaping is a security property.
# ---------------------------------------------------------------------------


def test_render_empty_notice_html_is_complete_document() -> None:
    when = datetime(2026, 5, 7, 0, 0, 0, tzinfo=UTC)
    body = _render_empty_notice("MyProj", when, fmt="html")
    assert body.startswith("<!DOCTYPE html>")
    assert "<title>Third-party Licenses for MyProj</title>" in body
    assert "<h1>Third-party Licenses for MyProj</h1>" in body
    assert "No scan has been run for this project yet." in body
    assert body.rstrip().endswith("</html>")
    assert body.endswith("\n")


def test_render_notice_html_renders_licenses_components_and_obligations() -> None:
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    body = _render_notice_html(
        project_name="MyProj",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "MIT",
                "name": "MIT License",
                "reference_url": "https://opensource.org/licenses/MIT",
                "components": _comps("foo @ 1.2.3", "bar @ 4.5.6"),
            }
        ],
        obligations_by_license={
            lic_id: [{"kind": "notice", "text": "Preserve the copyright.", "link": None}]
        },
    )
    assert "<h2>MIT — MIT License</h2>" in body
    # Phase B: the credited-component <li> now carries a copyright attribution
    # span (value when captured, honest fallback otherwise).
    assert "<li>foo @ 1.2.3<span" in body
    assert "<li>bar @ 4.5.6<span" in body
    assert "holders not captured in SBOM — see source" in body
    assert "Obligation: notice" in body
    assert "<pre>Preserve the copyright.</pre>" in body
    # Safe http(s) reference becomes a real link.
    assert 'href="https://opensource.org/licenses/MIT"' in body
    assert 'rel="noopener noreferrer"' in body


def test_render_notice_html_marks_licenses_without_obligations() -> None:
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    body = _render_notice_html(
        project_name="P",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": None,
                "name": "Weird",
                "reference_url": None,
                "components": _comps("x @ 1"),
            }
        ],
        obligations_by_license={lic_id: []},
    )
    # Missing SPDX id degrades gracefully, no obligations is called out.
    assert "(no SPDX id)" in body
    assert "No obligations recorded for this license." in body


def test_render_notice_html_escapes_untrusted_fields() -> None:
    """A hostile component / license / obligation must not break out of HTML."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    body = _render_notice_html(
        project_name='<script>alert("xss")</script>',
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "<b>MIT</b>",
                "name": 'name"&<>',
                "reference_url": None,
                "components": _comps("<img src=x onerror=alert(1)>"),
            }
        ],
        obligations_by_license={
            lic_id: [{"kind": "<i>k</i>", "text": "</pre><script>1</script>", "link": None}]
        },
    )
    # No raw tags from untrusted input survive.
    assert "<script>" not in body
    assert "<img " not in body
    assert "onerror=alert(1)" in body  # but only as escaped text
    assert "&lt;script&gt;" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
    # The document's own structural tags are still present.
    assert "<!DOCTYPE html>" in body
    assert "<h1>" in body


@pytest.mark.parametrize(
    ("url", "should_link"),
    [
        ("https://example.com/license", True),
        ("http://example.com/license", True),
        ("HTTPS://EXAMPLE.COM", True),  # scheme match is case-insensitive
        ("javascript:alert(1)", False),
        ("data:text/html,<script>1</script>", False),
        ("file:///etc/passwd", False),
        ("  javascript:alert(1)", False),  # leading whitespace doesn't bypass
        ("vbscript:msgbox(1)", False),
        ("//evil.example.com", False),  # scheme-relative is not http(s)
        ("https://example.com/" + "a" * 4000, False),  # over the 2 KiB href cap
    ],
)
def test_html_reference_line_only_links_safe_schemes(url: str, should_link: bool) -> None:
    line = _html_reference_line(url)
    assert line.startswith('<p class="reference">Reference: ')
    if should_link:
        assert "<a href=" in line
    else:
        # Dangerous schemes degrade to inert escaped text — never an href
        # attribute, so the scheme can't be clicked/executed.
        assert "href=" not in line


def test_html_reference_line_absent_url_is_empty() -> None:
    assert _html_reference_line(None) == ""
    assert _html_reference_line("") == ""


# ---------------------------------------------------------------------------
# G2 — markdown escape (markdown-escape decision: ESCAPE, not document-unsafe)
# ---------------------------------------------------------------------------


def test_md_escape_neutralizes_inline_markdown_and_html() -> None:
    """A hostile value cannot inject a link/emphasis/script into the markdown."""
    out = _md_escape("[click](javascript:alert(1)) **bold** `code` <script>x</script>")
    # Link / image syntax is broken (brackets + parens escaped).
    assert "\\[" in out and "\\]" in out
    assert "\\(" in out and "\\)" in out
    # Emphasis + code span markers escaped.
    assert "\\*\\*bold\\*\\*" in out
    assert "\\`code\\`" in out
    # Raw HTML angle brackets are HTML-escaped so a markdown→HTML render is inert.
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_md_escape_keeps_version_strings_readable() -> None:
    """Line-start-only markers (-, ., #) are NOT escaped so attribution reads cleanly."""
    assert _md_escape("requests @ 2.31.0") == "requests @ 2.31.0"
    assert _md_escape("scikit-learn @ 1.4.0") == "scikit-learn @ 1.4.0"


def test_md_escape_empty_is_empty() -> None:
    assert _md_escape(None) == ""
    assert _md_escape("") == ""


def test_render_notice_markdown_escapes_untrusted_fields() -> None:
    """The markdown branch must neutralize hostile component/license/obligation text."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    body = _render_notice(
        project_name="P",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "MIT",
                "name": "evil [x](javascript:alert(1))",
                "reference_url": "javascript:alert(2)",
                "components": _comps("pkg `inject` @ 1.0"),
                "components_omitted": 0,
            }
        ],
        obligations_by_license={
            lic_id: [
                {"kind": "notice", "text": "<script>bad</script>", "link": None}
            ]
        },
        fmt="markdown",
    )
    # No active markdown link survives from untrusted input (the brackets +
    # parens are backslash-escaped so ``[x](javascript:…)`` cannot form a link).
    assert "](javascript:alert(1))" not in body
    assert "\\[x\\]\\(javascript:alert\\(1\\)\\)" in body
    # The reference_url is present but inert — its parens are escaped so it is
    # plain text, never an autolink/active link.
    assert "javascript:alert\\(2\\)" in body
    # Raw script tag is HTML-escaped.
    assert "<script>bad</script>" not in body
    assert "&lt;script&gt;bad&lt;/script&gt;" in body
    # The component label's code span is neutralized.
    assert "\\`inject\\`" in body


def test_render_notice_markdown_component_fence_breakout_is_neutralized() -> None:
    """A label containing a ``` fence cannot break out (we use an escaped list)."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    body = _render_notice(
        project_name="P",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "MIT",
                "name": "MIT",
                "reference_url": None,
                "components": _comps("```\n# heading injection"),
                "components_omitted": 0,
            }
        ],
        obligations_by_license={lic_id: []},
        fmt="markdown",
    )
    # The triple-backtick is escaped, so it cannot open/close a code fence.
    assert "\\`\\`\\`" in body


_NEWLINE_INJECTION = "\n## x\n---\n> q\n* item"


@pytest.mark.parametrize("hostile_field", ["label", "name", "obligation_text"])
def test_render_notice_markdown_newline_cannot_inject_line_start_structure(
    hostile_field: str,
) -> None:
    """A value carrying its own newlines must NOT reach markdown line-start.

    Without collapsing newlines, an untrusted value like ``"\n## x\n---\n> q"``
    would push ``## x`` / ``---`` / ``> q`` / ``* item`` to column 0 and inject a
    live heading / thematic break / blockquote / list — content & attribution
    spoofing of the generated NOTICE (a legal artifact). The hostile payload is
    parametrized across the three untrusted text fields (component label /
    license name / obligation text).
    """
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    name = "MIT"
    label = "pkg @ 1.0"
    obligation_text = "Must include the license."
    if hostile_field == "label":
        label = label + _NEWLINE_INJECTION
    elif hostile_field == "name":
        name = name + _NEWLINE_INJECTION
    else:
        obligation_text = obligation_text + _NEWLINE_INJECTION

    body = _render_notice(
        project_name="P",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "MIT",
                "name": name,
                "reference_url": None,
                "components": _comps(label),
                "components_omitted": 0,
            }
        ],
        obligations_by_license={
            lic_id: [{"kind": "notice", "text": obligation_text, "link": None}]
        },
        fmt="markdown",
    )
    # The injected line-start structure from the UNTRUSTED value must not reach
    # column 0 (newlines are collapsed to spaces) — so no body LINE is a live
    # heading / blockquote / list-item / thematic-break sourced from the payload.
    # (The renderer's own ``---`` separators and ``## MIT — MIT`` heading are
    # legitimate; the payload's marker is ``## x`` / ``> q`` / ``* item``, none of
    # which the renderer emits, so a simple per-line check is unambiguous.)
    assert "\n## x" not in body
    assert "\n> q" not in body
    assert "\n* item" not in body
    assert "\n\\* item" not in body
    body_lines = body.split("\n")
    assert not any(ln.startswith("## x") for ln in body_lines)
    assert not any(ln.startswith("> q") or ln.startswith("&gt; q") for ln in body_lines)
    assert not any(ln.lstrip().startswith(("* item", "\\* item")) for ln in body_lines)
    # The payload's text still survives (collapsed onto one line), so we didn't
    # silently drop attribution — only the structural newlines were neutralized.
    # ``>`` is HTML-escaped (``&gt;``) and ``*`` is backslash-escaped (``\*``).
    assert "## x" in body
    assert "&gt; q" in body
    assert "\\* item" in body


def test_render_notice_markdown_clamps_reference_and_obligation_link_length() -> None:
    """reference_url / obligation link are bounded to 2048 chars (html parity)."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    prefix = "https://example.com/"
    huge = prefix + ("a" * 5000)
    body = _render_notice(
        project_name="P",
        generated_at=when,
        licenses_with_components=[
            {
                "license_id": lic_id,
                "spdx_id": "MIT",
                "name": "MIT",
                "reference_url": huge,
                "components": _comps("pkg @ 1.0"),
                "components_omitted": 0,
            }
        ],
        obligations_by_license={
            lic_id: [{"kind": "notice", "text": "t", "link": huge}]
        },
        fmt="markdown",
    )
    # The 5000-char URL is clamped to 2048 chars before escaping, so the raw
    # tail is gone but exactly the first 2048 chars (prefix + 'a' tail) survive.
    assert "a" * 5000 not in body
    kept = huge[:2048]
    assert kept in body
    # Nothing beyond the cap leaks (the char at index 2048 must not extend the run).
    assert (kept + "a") not in body


# ---------------------------------------------------------------------------
# G2 — NOTICE body-size caps (component-label tail + omitted note)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_records_omitted_component_count(fmt: str) -> None:
    """The cap fires → every format records an honest '+N more omitted' note."""
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1", "b @ 2"),
        "components_omitted": 12345,
    }
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            fmt=fmt,
        )
    assert "12345 more component(s) omitted" in body
    # The credited components we DID keep are still present (NOTICE stays
    # legally complete for the normal head of the list).
    assert "a @ 1" in body and "b @ 2" in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_no_omitted_note_when_under_cap(fmt: str) -> None:
    when = datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
    }
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            fmt=fmt,
        )
    assert "more component(s) omitted" not in body


def test_clamp_obligation_text_caps_at_byte_budget() -> None:
    """The shared clamp the NOTICE reuses bounds a runaway field at 64 KiB."""
    big = "x" * (200 * 1024)
    capped, truncated = _clamp_obligation_text(big)
    assert truncated is True
    assert len(capped.encode("utf-8")) <= 64 * 1024
    # A normal-sized field is untouched.
    small, small_truncated = _clamp_obligation_text("preserve attribution")
    assert small == "preserve attribution"
    assert small_truncated is False


def test_notice_component_labels_cap_is_sane() -> None:
    # The cap is large enough to never clip a realistic attribution list, but
    # bounded so a pathological scan can't produce an unbounded body.
    assert 1000 <= _NOTICE_COMPONENT_LABELS_CAP <= 100_000


# ---------------------------------------------------------------------------
# G2 follow-up — NOTICE license-count + per-license obligation caps
# (security-reviewer Low/Info from PR #107)
# ---------------------------------------------------------------------------


def test_notice_license_and_obligation_caps_are_sane() -> None:
    # Bounded so a pathological catalog can't produce an unbounded body, but far
    # past any realistic distinct-license / per-license-obligation surface.
    assert 100 <= _NOTICE_LICENSE_CAP <= 100_000
    assert 50 <= _NOTICE_OBLIGATIONS_PER_LICENSE_CAP <= 10_000


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_records_omitted_license_count(fmt: str) -> None:
    """The license cap fires → every format records '+N more license(s) omitted'."""
    when = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
        "obligations_omitted": 0,
    }
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            licenses_omitted=777,
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            fmt=fmt,
            licenses_omitted=777,
        )
    assert "777 more license(s) omitted" in body
    # The license section we DID keep is still rendered (legally complete head).
    assert "MIT" in body and "a @ 1" in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_no_license_omitted_note_under_cap(fmt: str) -> None:
    when = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
        "obligations_omitted": 0,
    }
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            licenses_omitted=0,
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: []},
            fmt=fmt,
            licenses_omitted=0,
        )
    assert "more license(s) omitted" not in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_records_omitted_obligations_per_license(fmt: str) -> None:
    """The per-license obligation cap fires → every format records the note next
    to that license's rendered obligations."""
    when = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "GPL-3.0-only",
        "name": "GNU GPL v3",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
        "obligations_omitted": 42,
    }
    obs = [{"kind": "notice", "text": "Keep this notice.", "link": None}]
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: obs},
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: obs},
            fmt=fmt,
        )
    assert "42 more obligation(s) omitted" in body
    # The obligation we DID keep is still rendered.
    assert "Keep this notice." in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_no_obligations_omitted_note_under_cap(fmt: str) -> None:
    when = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    lic_id = uuid.uuid4()
    entry = {
        "license_id": lic_id,
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
        "obligations_omitted": 0,
    }
    obs = [{"kind": "notice", "text": "Keep this notice.", "link": None}]
    if fmt == "html":
        body = _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: obs},
        )
    else:
        body = _render_notice(
            project_name="P",
            generated_at=when,
            licenses_with_components=[entry],
            obligations_by_license={lic_id: obs},
            fmt=fmt,
        )
    assert "more obligation(s) omitted" not in body


# ---------------------------------------------------------------------------
# Phase B — _purl_source_url (BomLens purl_src mirror)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        # The nine registry types the BomLens jq purl_src() maps, with version…
        (
            "pkg:maven/org.apache.commons/commons-lang3@3.12.0",
            "https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.12.0/",
        ),
        ("pkg:npm/left-pad@1.3.0", "https://www.npmjs.com/package/left-pad/v/1.3.0"),
        ("pkg:pypi/requests@2.31.0", "https://pypi.org/project/requests/2.31.0/"),
        ("pkg:gem/rails@7.0.4", "https://rubygems.org/gems/rails/versions/7.0.4"),
        ("pkg:cargo/serde@1.0.196", "https://crates.io/crates/serde/1.0.196"),
        (
            "pkg:nuget/Newtonsoft.Json@13.0.1",
            "https://www.nuget.org/packages/Newtonsoft.Json/13.0.1",
        ),
        (
            "pkg:golang/github.com/gin-gonic/gin@v1.9.1",
            "https://pkg.go.dev/github.com/gin-gonic/gin@v1.9.1",
        ),
        (
            "pkg:composer/Symfony/console@6.4.0",
            "https://packagist.org/packages/symfony/console#6.4.0",
        ),
        (
            "pkg:huggingface/meta-llama/Llama-2-7b",
            "https://huggingface.co/meta-llama/Llama-2-7b",
        ),
        # …and without version (maven needs one; the rest fall back to the page).
        ("pkg:npm/left-pad", "https://www.npmjs.com/package/left-pad"),
        ("pkg:pypi/requests", "https://pypi.org/project/requests/"),
        ("pkg:gem/rails", "https://rubygems.org/gems/rails"),
        ("pkg:cargo/serde", "https://crates.io/crates/serde"),
        ("pkg:nuget/Newtonsoft.Json", "https://www.nuget.org/packages/Newtonsoft.Json"),
        ("pkg:golang/github.com/gin-gonic/gin", "https://pkg.go.dev/github.com/gin-gonic/gin"),
        ("pkg:composer/symfony/console", "https://packagist.org/packages/symfony/console"),
        ("pkg:maven/org.apache.commons/commons-lang3", None),
        # Qualifiers / subpath are stripped before mapping (purl spec).
        (
            "pkg:maven/org.apache.commons/commons-lang3@3.12.0?type=jar#sub/path",
            "https://repo1.maven.org/maven2/org/apache/commons/commons-lang3/3.12.0/",
        ),
    ],
)
def test_purl_source_url_maps_registry_types(purl: str, expected: str | None) -> None:
    assert _purl_source_url(purl) == expected


@pytest.mark.parametrize(
    "hostile",
    [
        None,
        "",
        "not-a-purl",
        "pkg:",
        "pkg:npm",  # type only, no name segment
        "pkg:generic/something@1.0",  # unknown type → never guess a URL
        "pkg:npm/a b@1.0",  # whitespace fails the character allowlist
        'pkg:npm/x"y@1.0',  # quote fails the character allowlist
        "pkg:npm/<script>@1.0",  # angle brackets fail the allowlist
        "pkg:npm/x\ny@1.0",  # control char fails the allowlist
        "pkg:npm/" + "a" * 600,  # over the 512-char bound
        # F-4: traversal-looking / percent-encoded path parts never become a
        # "source" URL (misleading-link prevention; deliberate deviation from
        # the BomLens jq, which maps these verbatim). Scoped-npm purls encode
        # ``@`` as ``%40`` per the purl spec, so they land here too — such
        # components fall back to the src-less attribution line.
        "pkg:npm/../evil@1.0",
        "pkg:golang/github.com/x/../evil@v1.0.0",
        "pkg:maven/org..evil/lib@1.0",  # dotted ns expands to slashes
        "pkg:pypi/requests@..",
        "pkg:npm/%40babel/core@7.24.0",
        "pkg:pypi/req%2e%2euests@1.0",
    ],
)
def test_purl_source_url_rejects_hostile_or_unknown(hostile: str | None) -> None:
    assert _purl_source_url(hostile) is None


# ---------------------------------------------------------------------------
# Phase B — _clean_copyright (untrusted SBOM attribution string)
# ---------------------------------------------------------------------------


def test_clean_copyright_passes_normal_value() -> None:
    assert _clean_copyright("Copyright (c) 2016 Left Pad Inc.") == (
        "Copyright (c) 2016 Left Pad Inc."
    )


@pytest.mark.parametrize("raw", [None, "", "   ", 42, {"a": 1}, ["x"]])
def test_clean_copyright_non_string_or_blank_is_none(raw: object) -> None:
    assert _clean_copyright(raw) is None


def test_clean_copyright_strips_control_chars_and_newlines() -> None:
    """CR/LF/NUL are dropped (sanitize_jsonb_text) so a hostile copyright can
    never inject a new NOTICE line or abort a render."""
    assert _clean_copyright("Evil\x00\r\nInjected \x1b[31mred") == "EvilInjected [31mred"


def test_clean_copyright_clamps_length() -> None:
    cleaned = _clean_copyright("h" * 10_000)
    assert cleaned is not None
    assert len(cleaned) == 500


# ---------------------------------------------------------------------------
# Phase B — _md_fence_for / _license_text_sections
# ---------------------------------------------------------------------------


def test_md_fence_is_longer_than_any_backtick_run() -> None:
    assert _md_fence_for("no backticks here") == "```"
    assert _md_fence_for("has `single` ticks") == "```"
    assert _md_fence_for("hostile ``` fence inside") == "````"
    assert _md_fence_for("worse ````` run") == "``````"


def test_license_text_sections_dedupes_compound_operands() -> None:
    """`MIT` + `MIT OR Apache-2.0` → each operand's text exactly once, sorted."""
    entries = [
        {"spdx_id": "MIT"},
        {"spdx_id": "MIT OR Apache-2.0"},
    ]
    sections, omitted = _license_text_sections(entries)
    assert [sid for sid, _ in sections] == ["Apache-2.0", "MIT"]
    assert all(text is not None for _, text in sections)
    assert omitted == 0


def test_license_text_sections_marks_unbundled_ids() -> None:
    sections, omitted = _license_text_sections([{"spdx_id": "NotARealLicense-1.0"}])
    assert sections == [("NotARealLicense-1.0", None)]
    assert omitted == 0


def test_license_text_sections_empty_for_no_spdx_ids() -> None:
    sections, omitted = _license_text_sections([{"spdx_id": None}])
    assert sections == []
    assert omitted == 0


def test_license_text_sections_caps_full_texts_with_omitted_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import services.obligation_service as svc

    monkeypatch.setattr(svc, "_NOTICE_LICENSE_TEXT_CAP", 1)
    sections, omitted = _license_text_sections([{"spdx_id": "MIT OR Apache-2.0"}])
    # One full text kept (first in spdx-asc order), one clipped by the cap.
    assert [(sid, text is not None) for sid, text in sections] == [("Apache-2.0", True)]
    assert omitted == 1


def test_license_text_sections_caps_unbundled_pointer_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-3: the 'text not bundled' one-liners are bounded on their own axis."""
    import services.obligation_service as svc

    monkeypatch.setattr(svc, "_NOTICE_LICENSE_TEXT_POINTER_CAP", 2)
    entries = [{"spdx_id": f"NotBundled-{i}.0"} for i in range(5)]
    sections, omitted = _license_text_sections(entries)
    # Two pointer rows kept (spdx asc head), three clipped into the footer.
    assert [(sid, text) for sid, text in sections] == [
        ("NotBundled-0.0", None),
        ("NotBundled-1.0", None),
    ]
    assert omitted == 3


def test_notice_license_text_pointer_cap_is_sane() -> None:
    from services.obligation_service import _NOTICE_LICENSE_TEXT_POINTER_CAP

    assert 100 <= _NOTICE_LICENSE_TEXT_POINTER_CAP <= 10_000


def test_notice_license_text_cap_is_sane() -> None:
    # Must cover the whole bundled set with real headroom, but stay bounded.
    assert 32 <= _NOTICE_LICENSE_TEXT_CAP <= 10_000


# ---------------------------------------------------------------------------
# Phase B — License Texts section rendering (all three formats)
# ---------------------------------------------------------------------------


def _render(fmt: str, entries: list[dict], obs: dict | None = None) -> str:
    when = datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC)
    obs = obs or {}
    if fmt == "html":
        return _render_notice_html(
            project_name="P",
            generated_at=when,
            licenses_with_components=entries,
            obligations_by_license=obs,
        )
    return _render_notice(
        project_name="P",
        generated_at=when,
        licenses_with_components=entries,
        obligations_by_license=obs,
        fmt=fmt,
    )


def _mit_entry(**overrides: object) -> dict:
    entry: dict = {
        "license_id": uuid.uuid4(),
        "spdx_id": "MIT",
        "name": "MIT License",
        "reference_url": None,
        "components": _comps("a @ 1"),
        "components_omitted": 0,
        "obligations_omitted": 0,
    }
    entry.update(overrides)
    return entry


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_appends_bundled_license_full_text(fmt: str) -> None:
    body = _render(fmt, [_mit_entry()])
    assert "License Texts" in body
    if fmt == "html":
        assert '<section class="license-texts">' in body
        assert "<h3>MIT</h3>" in body
        # The full text rides in an escaped <pre>: the literal "<year>" from
        # the vendored MIT text must never appear as a raw tag.
        assert "Copyright (c) &lt;year&gt;" in body
        assert "<year>" not in body
    else:
        # BomLens-parity divider + verbatim standard text.
        assert "----- MIT -----" in body
        assert "Permission is hereby granted" in body
    if fmt == "markdown":
        # The verbatim texts are wrapped in a fenced block sized past any
        # backtick run in the content.
        assert "```text" in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_compound_expression_texts_deduped(fmt: str) -> None:
    """`MIT` + `MIT OR Apache-2.0` in one document → each text exactly once."""
    entries = [
        _mit_entry(),
        _mit_entry(spdx_id="MIT OR Apache-2.0", name="Dual"),
    ]
    body = _render(fmt, entries)
    if fmt == "html":
        assert body.count("<h3>MIT</h3>") == 1
        assert body.count("<h3>Apache-2.0</h3>") == 1
    else:
        assert body.count("----- MIT -----") == 1
        assert body.count("----- Apache-2.0 -----") == 1


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_unbundled_text_gets_pointer_line(fmt: str) -> None:
    body = _render(fmt, [_mit_entry(spdx_id="NotARealLicense-1.0", name="Custom")])
    assert "text not bundled" in body
    assert "see the license reference above" in body


def test_render_notice_text_divider_scrubs_hostile_unbundled_id() -> None:
    """F-2: a scan-derived operand id failing the strict SPDX allowlist cannot
    forge ``----- <id> -----`` section boundaries in the plain-text branch —
    control chars (incl. newlines) are scrubbed before the divider is built."""
    hostile = "Evil\n----- FAKE -----\nX"
    body = _render("text", [_mit_entry(spdx_id=hostile, name="Custom")])
    # Only inspect the License Texts section: the body heading above renders
    # scan text under the pre-existing text/plain posture and is out of scope.
    tail = body.split("License Texts", 1)[1]
    # The scrubbed id sits on ONE divider line; the smuggled newlines are gone.
    assert "----- Evil----- FAKE -----X -----" in tail
    assert "\n----- FAKE -----\n" not in tail
    # A well-formed id passes through the divider untouched.
    safe_body = _render("text", [_mit_entry()])
    assert "----- MIT -----" in safe_body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_no_license_texts_section_without_spdx_ids(fmt: str) -> None:
    body = _render(fmt, [_mit_entry(spdx_id=None, name="Anonymous")])
    assert "License Texts" not in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_license_text_cap_records_omitted(
    fmt: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.obligation_service as svc

    monkeypatch.setattr(svc, "_NOTICE_LICENSE_TEXT_CAP", 1)
    body = _render(fmt, [_mit_entry(spdx_id="MIT OR Apache-2.0", name="Dual")])
    assert "1 more license text(s) omitted" in body
    # The kept head (Apache-2.0, spdx-asc) is present; MIT's text is clipped.
    if fmt == "html":
        assert "<h3>Apache-2.0</h3>" in body
        assert "<h3>MIT</h3>" not in body
    else:
        assert "----- Apache-2.0 -----" in body
        assert "----- MIT -----" not in body


# ---------------------------------------------------------------------------
# Phase B — per-component copyright attribution rendering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_shows_captured_copyright(fmt: str) -> None:
    entry = _mit_entry(
        components=[
            {
                "label": "left-pad @ 1.3.0",
                "copyright": "Copyright 2016 Left Pad Inc.",
                "source_url": None,
            }
        ]
    )
    body = _render(fmt, [entry])
    assert "Copyright: Copyright 2016 Left Pad Inc." in body
    assert "holders not captured" not in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_copyright_fallback_includes_source_url(fmt: str) -> None:
    entry = _mit_entry(
        components=[
            {
                "label": "requests @ 2.31.0",
                "copyright": None,
                "source_url": "https://pypi.org/project/requests/2.31.0/",
            }
        ]
    )
    body = _render(fmt, [entry])
    assert "Copyright: holders not captured in SBOM — see source" in body
    assert "https://pypi.org/project/requests/2.31.0/" in body
    if fmt == "html":
        # The source pointer is linkified via _safe_href (https only).
        assert 'href="https://pypi.org/project/requests/2.31.0/"' in body


@pytest.mark.parametrize("fmt", ["text", "markdown", "html"])
def test_render_notice_copyright_fallback_without_source_stays_honest(fmt: str) -> None:
    entry = _mit_entry(
        components=[{"label": "mystery @ 0.1", "copyright": None, "source_url": None}]
    )
    body = _render(fmt, [entry])
    assert "Copyright: holders not captured in SBOM — see source" in body


def test_render_notice_html_escapes_hostile_copyright() -> None:
    entry = _mit_entry(
        components=[
            {
                "label": "evil @ 1.0",
                "copyright": '<script>alert("cp")</script>',
                "source_url": None,
            }
        ]
    )
    body = _render("html", [entry])
    assert "<script>alert" not in body
    assert "&lt;script&gt;alert" in body


def test_render_notice_markdown_escapes_hostile_copyright() -> None:
    entry = _mit_entry(
        components=[
            {
                "label": "evil @ 1.0",
                "copyright": "[x](javascript:alert(1)) **bold**",
                "source_url": None,
            }
        ]
    )
    body = _render("markdown", [entry])
    assert "](javascript:alert(1))" not in body
    assert "\\[x\\]\\(javascript:alert\\(1\\)\\)" in body
    assert "\\*\\*bold\\*\\*" in body


# ---------------------------------------------------------------------------
# DB-backed tests start here — gated on DATABASE_URL.
# ---------------------------------------------------------------------------


pytestmark_db = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip obligation service DB tests")
    return url


@pytest.fixture(scope="module")
def _migrate_once() -> None:
    """Run alembic upgrade head once per module — only for tests that need DB.

    Not autouse because the pure cases above have no DB dependency. Tests
    that pull in ``db_session`` transitively activate this.
    """
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; obligation service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session(_migrate_once) -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Local fixture builders
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession,
    *,
    name: str | None = None,
    version: str = "1.0.0",
    package_type: str = "npm",
):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    cname = name or f"pkg-{suffix}"
    purl = f"pkg:{package_type}/{cname}"
    component = Component(purl=purl, package_type=package_type, name=cname)
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return component, cv


async def _make_license(
    session: AsyncSession,
    *,
    spdx_id: str | None = None,
    name: str | None = None,
    category: str = "allowed",
    reference_url: str | None = None,
):
    from models import License as LicenseModel

    suffix = unique_suffix()
    lic = LicenseModel(
        spdx_id=spdx_id if spdx_id is not None else f"SPDX-{suffix}",
        name=name or f"License {suffix}",
        category=category,
        is_osi_approved=False,
        is_fsf_libre=False,
        is_deprecated_license_id=False,
        reference_url=reference_url,
    )
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _attach_license_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    license_id: uuid.UUID,
    kind: str = "concluded",
    source_path: str | None = None,
):
    from models import LicenseFinding

    suffix = unique_suffix()
    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind=kind,
        source_path=source_path or f"path/{suffix}",
        raw_data={},
    )
    session.add(lf)
    await session.commit()
    await session.refresh(lf)
    return lf


async def _make_obligation(
    session: AsyncSession,
    *,
    license_id: uuid.UUID,
    kind: str = "attribution",
    text: str | None = None,
    link: str | None = None,
):
    from models import Obligation

    suffix = unique_suffix()
    ob = Obligation(
        license_id=license_id,
        kind=kind,
        text=text or f"obligation text {suffix}",
        link=link,
    )
    session.add(ob)
    await session.commit()
    await session.refresh(ob)
    return ob


async def _make_scan_component(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    raw_data: dict,
    dependency_path: str | None = None,
):
    """A scan_components row carrying cdxgen-verbatim raw_data (Phase B —
    the NOTICE reads ``raw_data["copyright"]`` from here)."""
    from models import ScanComponent

    sc = ScanComponent(
        scan_id=scan_id,
        component_version_id=cv_id,
        dependency_path=dependency_path,
        raw_data=raw_data,
    )
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _get_or_create_license_by_spdx(session: AsyncSession, spdx_id: str):
    """licenses.spdx_id is UNIQUE — a real id like ``MIT`` may already exist
    from an earlier test run or seed, so fetch-or-create instead of insert."""
    from sqlalchemy import select as sa_select

    from models import License as LicenseModel

    existing = (
        await session.execute(
            sa_select(LicenseModel).where(LicenseModel.spdx_id == spdx_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return await _make_license(session, spdx_id=spdx_id, name=spdx_id)


async def _make_project_with_scan(session: AsyncSession):
    """Set up org → team → user → membership → project → succeeded scan."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status="succeeded")
    project.latest_scan_id = scan.id
    project.updated_at = datetime.now(tz=UTC)
    await session.commit()
    await session.refresh(project)
    return team, user, project, scan


# ---------------------------------------------------------------------------
# list_project_obligations — happy / pagination / filters / search / sort
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_list_returns_empty_when_project_has_no_latest_scan(
    db_session: AsyncSession,
) -> None:
    """`latest_scan_id is None` → ([], {}, 0). Empty distribution ok — chart
    falls back to the empty-state card in this case."""
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    items, distribution, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor
    )
    assert items == []
    assert distribution == {}
    assert total == 0


@pytestmark_db
async def test_list_happy_path_returns_items_distribution_and_total(
    db_session: AsyncSession,
) -> None:
    """Two licenses × two obligation kinds → 4 rows, distribution sums by kind.

    Distribution emits known kinds in KNOWN_OBLIGATION_KINDS order so the
    chart's primary axis stays stable as the catalog grows.
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv1 = await _make_component_version(db_session)
    _, cv2 = await _make_component_version(db_session)
    mit = await _make_license(
        db_session, spdx_id=f"MIT-{suffix}", name="MIT", category="allowed"
    )
    gpl = await _make_license(
        db_session, spdx_id=f"GPL-{suffix}", name="GPL-3.0", category="forbidden"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=mit.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=gpl.id)

    await _make_obligation(db_session, license_id=mit.id, kind="attribution")
    await _make_obligation(db_session, license_id=mit.id, kind="notice")
    await _make_obligation(db_session, license_id=gpl.id, kind="copyleft")
    await _make_obligation(db_session, license_id=gpl.id, kind="source-disclosure")

    items, distribution, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor
    )
    assert total == 4
    assert len(items) == 4

    # Distribution carries each surfaced kind exactly once.
    assert distribution == {
        "attribution": 1,
        "notice": 1,
        "source-disclosure": 1,
        "copyleft": 1,
    }
    # Insertion order follows KNOWN_OBLIGATION_KINDS for the four observed kinds.
    assert list(distribution.keys()) == [
        "attribution",
        "notice",
        "source-disclosure",
        "copyleft",
    ]
    # Each row carries the parent license metadata.
    spdx_ids = {row["license_spdx_id"] for row in items}
    assert spdx_ids == {f"MIT-{suffix}", f"GPL-{suffix}"}


@pytestmark_db
async def test_list_paginates_and_returns_total(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    # 5 distinct licenses, 1 cv each, 1 obligation each.
    for i in range(5):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(
            db_session,
            spdx_id=f"PAG-{suffix}-{i}",
            name=f"Pag {suffix} {i}",
            category="allowed",
        )
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )
        await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    p1, _, total1 = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, limit=2, offset=0
    )
    assert len(p1) == 2
    assert total1 == 5

    p2, _, total2 = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, limit=2, offset=2
    )
    assert len(p2) == 2
    assert total2 == 5
    # Stable tie-break → disjoint pages.
    assert {row["id"] for row in p1} & {row["id"] for row in p2} == set()


@pytestmark_db
async def test_list_filter_kinds_narrows_results(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"K-{suffix}", name="K", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(db_session, license_id=lic.id, kind="attribution")
    await _make_obligation(db_session, license_id=lic.id, kind="copyleft")
    await _make_obligation(db_session, license_id=lic.id, kind="notice")

    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, kinds=["copyleft"]
    )
    assert total == 1
    assert items[0]["kind"] == "copyleft"

    items, _, total = await list_project_obligations(
        db_session,
        project_id=project.id,
        actor=actor,
        kinds=["copyleft", "attribution"],
    )
    assert total == 2
    assert {r["kind"] for r in items} == {"copyleft", "attribution"}


@pytestmark_db
async def test_list_filter_categories_narrows_results(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_a = await _make_component_version(db_session)
    _, cv_b = await _make_component_version(db_session)
    forb = await _make_license(
        db_session, spdx_id=f"F-{suffix}", name="forb", category="forbidden"
    )
    allow = await _make_license(
        db_session, spdx_id=f"A-{suffix}", name="allow", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_a.id, license_id=forb.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_b.id, license_id=allow.id)
    await _make_obligation(db_session, license_id=forb.id, kind="copyleft")
    await _make_obligation(db_session, license_id=allow.id, kind="attribution")

    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, categories=["forbidden"]
    )
    assert total == 1
    assert items[0]["license_category"] == "forbidden"


@pytestmark_db
async def test_list_search_matches_spdx_name_kind_text(
    db_session: AsyncSession,
) -> None:
    """Search hits across spdx_id, license name, kind, and obligation text."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session,
        spdx_id=f"NDL-{suffix}",
        name=f"Needle license {suffix}",
        category="allowed",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(
        db_session,
        license_id=lic.id,
        kind="attribution",
        text="please preserve this notice in your binaries",
    )
    await _make_obligation(
        db_session,
        license_id=lic.id,
        kind="copyleft",
        text="distribute under the same license",
    )

    # SPDX hit (returns both obligations on the same license).
    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, search="NDL"
    )
    assert total == 2

    # Kind hit.
    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, search="copyleft"
    )
    assert total == 1
    assert items[0]["kind"] == "copyleft"

    # License-name hit.
    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, search="Needle"
    )
    assert total == 2

    # Obligation-text hit.
    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, search="binaries"
    )
    assert total == 1
    assert items[0]["kind"] == "attribution"


@pytestmark_db
async def test_list_search_escapes_like_wildcards(
    db_session: AsyncSession,
) -> None:
    """A bare ``%`` search MUST NOT collapse to "match everything".

    Regression for the ``_escape_like`` integration shared with
    :file:`services/vulnerability_service.py` (PR #11).
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_pct = await _make_component_version(db_session)
    _, cv_nopct = await _make_component_version(db_session)
    pct = await _make_license(
        db_session,
        spdx_id=f"PCT-{suffix}",
        name=f"50% off license {suffix}",
        category="allowed",
    )
    nopct = await _make_license(
        db_session,
        spdx_id=f"NOPCT-{suffix}",
        name=f"plain license {suffix}",
        category="allowed",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_pct.id, license_id=pct.id)
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv_nopct.id, license_id=nopct.id
    )
    await _make_obligation(db_session, license_id=pct.id, kind="attribution")
    await _make_obligation(db_session, license_id=nopct.id, kind="attribution")

    # Bare `%` would otherwise match both rows — must match only the literal.
    items, _, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, search="50%"
    )
    assert total == 1
    assert items[0]["license_spdx_id"] == f"PCT-{suffix}"


@pytestmark_db
async def test_list_sort_by_kind_asc_and_desc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"K-{suffix}", name="lic", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    for kind in ("zeta-kind", "alpha-kind", "mu-kind"):
        await _make_obligation(db_session, license_id=lic.id, kind=kind)

    items, _, _ = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, sort="kind", order="asc"
    )
    kinds = [r["kind"] for r in items]
    assert kinds == sorted(kinds)

    items, _, _ = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, sort="kind", order="desc"
    )
    kinds_desc = [r["kind"] for r in items]
    assert kinds_desc == sorted(kinds_desc, reverse=True)


@pytestmark_db
async def test_list_sort_by_license_name_asc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    for n in ("zeta", "alpha", "mu"):
        _, cv = await _make_component_version(db_session)
        lic = await _make_license(
            db_session, spdx_id=f"S-{n}-{suffix}", name=f"{n}-{suffix}", category="allowed"
        )
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
        await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    items, _, _ = await list_project_obligations(
        db_session,
        project_id=project.id,
        actor=actor,
        sort="license_name",
        order="asc",
    )
    names = [r["license_name"] for r in items]
    assert names == sorted(names)


@pytestmark_db
async def test_list_sort_by_category_desc_puts_forbidden_first(
    db_session: AsyncSession,
) -> None:
    """Default sort=category, order=desc surfaces forbidden before allowed.

    Rank: forbidden=3, conditional=2, allowed=1, unknown=0.
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv_a = await _make_component_version(db_session)
    _, cv_b = await _make_component_version(db_session)
    _, cv_c = await _make_component_version(db_session)
    allow = await _make_license(
        db_session, spdx_id=f"AL-{suffix}", name="Allow", category="allowed"
    )
    forb = await _make_license(
        db_session, spdx_id=f"FB-{suffix}", name="Forbid", category="forbidden"
    )
    cond = await _make_license(
        db_session, spdx_id=f"CD-{suffix}", name="Cond", category="conditional"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_a.id, license_id=allow.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_b.id, license_id=forb.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv_c.id, license_id=cond.id)
    for lic in (allow, forb, cond):
        await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    items, _, _ = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, sort="category", order="desc"
    )
    cats = [r["license_category"] for r in items]
    assert cats.index("forbidden") < cats.index("conditional") < cats.index("allowed")


@pytestmark_db
async def test_list_sort_by_affected_count_desc(db_session: AsyncSession) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    big = await _make_license(
        db_session, spdx_id=f"BIG-{suffix}", name="big", category="allowed"
    )
    small = await _make_license(
        db_session, spdx_id=f"SML-{suffix}", name="small", category="allowed"
    )
    # `big` covers 3 cvs, `small` covers 1.
    for _ in range(3):
        _, cv = await _make_component_version(db_session)
        await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=big.id)
    _, cv = await _make_component_version(db_session)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=small.id)
    await _make_obligation(db_session, license_id=big.id, kind="attribution")
    await _make_obligation(db_session, license_id=small.id, kind="attribution")

    items, _, _ = await list_project_obligations(
        db_session,
        project_id=project.id,
        actor=actor,
        sort="affected_count",
        order="desc",
    )
    counts = [r["affected_count"] for r in items]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 3


@pytestmark_db
async def test_list_distribution_unfiltered_when_filter_active(
    db_session: AsyncSession,
) -> None:
    """Distribution must reflect the underlying scan, not the active filter,
    so the chart's axis doesn't collapse when the user narrows by kind."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"D-{suffix}", name="D", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(db_session, license_id=lic.id, kind="attribution")
    await _make_obligation(db_session, license_id=lic.id, kind="copyleft")

    # Active filter narrows items but distribution still shows both kinds.
    items, distribution, total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor, kinds=["attribution"]
    )
    assert total == 1
    assert distribution == {"attribution": 1, "copyleft": 1}


@pytestmark_db
async def test_list_idor_other_team_returns_404_and_logs(
    db_session: AsyncSession,
) -> None:
    """List endpoint existence-hides cross-team as 404 (Low #4) + emits
    ``authz.cross_team_attempt``."""
    from services.project_service import ProjectNotFound

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(ProjectNotFound):
            await list_project_obligations(
                db_session, project_id=project.id, actor=actor
            )

    assert any(
        evt.get("event") == "authz.cross_team_attempt"
        and evt.get("resource") == "project_obligations"
        for evt in captured
    )


@pytestmark_db
async def test_list_unknown_project_is_404(db_session: AsyncSession) -> None:
    from services.project_service import ProjectNotFound

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(ProjectNotFound):
        await list_project_obligations(
            db_session, project_id=uuid.uuid4(), actor=actor
        )


@pytestmark_db
async def test_list_invalid_sort_raises_obligation_error(
    db_session: AsyncSession,
) -> None:
    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ObligationError):
        await list_project_obligations(
            db_session, project_id=project.id, actor=actor, sort="bogus"
        )


@pytestmark_db
async def test_list_invalid_order_raises_obligation_error(
    db_session: AsyncSession,
) -> None:
    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    with pytest.raises(ObligationError):
        await list_project_obligations(
            db_session, project_id=project.id, actor=actor, order="sideways"
        )


# ---------------------------------------------------------------------------
# get_obligation_detail — happy + cross-team existence-hide
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_detail_happy_path_includes_parent_license_and_affected_components(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv1 = await _make_component_version(db_session, name=f"alpha-{suffix}")
    _, cv2 = await _make_component_version(db_session, name=f"bravo-{suffix}")
    lic = await _make_license(
        db_session,
        spdx_id=f"DET-{suffix}",
        name="Detail license",
        category="conditional",
        reference_url="https://example.com/license",
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv1.id, license_id=lic.id)
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv2.id, license_id=lic.id)
    ob = await _make_obligation(
        db_session,
        license_id=lic.id,
        kind="attribution",
        text="Preserve the original copyright notice.",
        link="https://example.com/policy",
    )

    payload = await get_obligation_detail(
        db_session, project_id=project.id, obligation_id=ob.id, actor=actor
    )
    assert payload["id"] == ob.id
    assert payload["license_id"] == lic.id
    assert payload["license_spdx_id"] == f"DET-{suffix}"
    assert payload["license_category"] == "conditional"
    assert payload["license_reference_url"] == "https://example.com/license"
    assert payload["kind"] == "attribution"
    assert payload["text"] == "Preserve the original copyright notice."
    assert payload["link"] == "https://example.com/policy"
    # Both cvs that carry the parent license appear, ordered by name.
    names = [c["component_name"] for c in payload["affected_components"]]
    assert names == sorted(names)
    assert len(payload["affected_components"]) == 2


@pytestmark_db
async def test_detail_unknown_obligation_id_is_404(db_session: AsyncSession) -> None:
    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    with pytest.raises(ObligationNotFound):
        await get_obligation_detail(
            db_session, project_id=project.id, obligation_id=uuid.uuid4(), actor=actor
        )


@pytestmark_db
async def test_detail_obligation_not_visible_in_scan_returns_404(
    db_session: AsyncSession,
) -> None:
    """Obligation exists, but its parent license is NOT in the latest scan
    → existence-hide as 404."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    # License attached to the scan.
    _, cv = await _make_component_version(db_session)
    in_scan = await _make_license(
        db_session, spdx_id=f"IN-{suffix}", name="in scan", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=in_scan.id)

    # License NOT attached to the scan — its obligation is hidden from this project.
    not_in_scan = await _make_license(
        db_session, spdx_id=f"OUT-{suffix}", name="out", category="allowed"
    )
    ob = await _make_obligation(db_session, license_id=not_in_scan.id, kind="attribution")

    with pytest.raises(ObligationNotFound):
        await get_obligation_detail(
            db_session, project_id=project.id, obligation_id=ob.id, actor=actor
        )


@pytestmark_db
async def test_detail_cross_team_user_gets_404_not_403(
    db_session: AsyncSession,
) -> None:
    """Cross-team caller existence-hides as 404 + emits cross-team log."""
    target_team, _, project, scan = await _make_project_with_scan(db_session)
    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session)
    lic = await _make_license(
        db_session, spdx_id=f"CR-{suffix}", name="cross", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    ob = await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    org2 = await make_organization(db_session)
    other_team = await make_team(db_session, organization=org2)
    outsider = await make_user(db_session)
    await make_membership(db_session, user=outsider, team=other_team, role="developer")
    actor = principal_for(outsider, team_ids=[other_team.id], role="developer")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(ObligationNotFound):
            await get_obligation_detail(
                db_session, project_id=project.id, obligation_id=ob.id, actor=actor
            )

    assert any(
        evt.get("event") == "authz.cross_team_attempt"
        and evt.get("resource") == "obligation_detail"
        for evt in captured
    )


@pytestmark_db
async def test_detail_project_with_no_latest_scan_is_404(
    db_session: AsyncSession,
) -> None:
    """Project without latest scan can't surface any obligation as visible."""
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    lic = await _make_license(
        db_session, spdx_id=f"NS-{suffix}", name="no scan", category="allowed"
    )
    ob = await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    with pytest.raises(ObligationNotFound):
        await get_obligation_detail(
            db_session, project_id=project.id, obligation_id=ob.id, actor=actor
        )


# ---------------------------------------------------------------------------
# generate_notice
# ---------------------------------------------------------------------------


@pytestmark_db
async def test_generate_notice_text_format_includes_dividers_components_and_obligations(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"alpha-{suffix}")
    lic = await _make_license(
        db_session, spdx_id=f"NX-{suffix}", name=f"NX License {suffix}", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(
        db_session,
        license_id=lic.id,
        kind="attribution",
        text="preserve attribution",
        link="https://example.com/attribution",
    )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    assert payload["format"] == "text"
    assert payload["license_count"] == 1
    assert payload["obligation_count"] == 1
    body = payload["body"]
    # Divider lines bracket each license block.
    assert "=" * 80 in body
    # SPDX id + license name surface in the body.
    assert f"NX-{suffix}" in body
    # The component label uses "name @ version" form.
    assert f"alpha-{suffix}" in body
    assert "1.0.0" in body
    # Obligation kind + text + link surface in the body.
    assert "Obligation: attribution" in body
    assert "preserve attribution" in body
    assert "https://example.com/attribution" in body


@pytestmark_db
async def test_generate_notice_markdown_format_uses_h1_and_code_blocks(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"alpha-{suffix}")
    lic = await _make_license(
        db_session, spdx_id=f"MD-{suffix}", name=f"MD License {suffix}", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(db_session, license_id=lic.id, kind="attribution")

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="markdown"
    )
    body = payload["body"]
    assert body.startswith("# Third-party Licenses for ")
    # H2 license heading.
    assert f"## MD-{suffix}" in body
    # Components rendered as an escaped bullet list (G2/markdown-escape: a
    # fenced code block could be broken out of by a label containing ```).
    assert f"- alpha-{suffix} @ 1.0.0" in body
    # Bold obligation label.
    assert "**Obligation: attribution**" in body


@pytestmark_db
async def test_generate_notice_html_format_emits_complete_document(
    db_session: AsyncSession,
) -> None:
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"alpha-{suffix}")
    lic = await _make_license(
        db_session, spdx_id=f"HT-{suffix}", name=f"HT License {suffix}", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    await _make_obligation(
        db_session,
        license_id=lic.id,
        kind="attribution",
        text="preserve attribution",
        link="https://example.com/attribution",
    )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="html"
    )
    assert payload["format"] == "html"
    assert payload["license_count"] == 1
    assert payload["obligation_count"] == 1
    body = payload["body"]
    assert body.startswith("<!DOCTYPE html>")
    assert body.rstrip().endswith("</html>")
    assert f"HT-{suffix}" in body
    assert f"alpha-{suffix}" in body
    assert "<pre>preserve attribution</pre>" in body
    # Safe http(s) obligation link is linkified.
    assert 'href="https://example.com/attribution"' in body


@pytestmark_db
async def test_generate_notice_empty_when_project_has_no_scan(
    db_session: AsyncSession,
) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    assert payload["license_count"] == 0
    assert payload["obligation_count"] == 0
    assert "no scan has been run" in payload["body"].lower()


@pytestmark_db
async def test_generate_notice_license_without_obligations_renders_marker(
    db_session: AsyncSession,
) -> None:
    """A license that's in the scan but has no obligations rows still shows
    its components — the obligation block becomes a "(no obligations recorded)"
    marker so the document remains unambiguous about what the catalog covers."""
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"orphan-{suffix}")
    lic = await _make_license(
        db_session, spdx_id=f"OR-{suffix}", name="Orphan", category="allowed"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    # No obligations attached.

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    assert payload["license_count"] == 1
    assert payload["obligation_count"] == 0
    assert "no obligations recorded" in payload["body"].lower()


@pytestmark_db
async def test_generate_notice_caps_obligations_per_license_with_omitted_note(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a license carries more obligations than the per-license cap, the
    NOTICE body keeps the head, records an honest '+N more obligation(s) omitted'
    note, and the inspection ``obligation_count`` reflects the TRUE total."""
    import services.obligation_service as svc

    # Shrink the per-license cap so a handful of rows trips it (keeps the test
    # fast — no need to seed thousands of obligations).
    monkeypatch.setattr(svc, "_NOTICE_OBLIGATIONS_PER_LICENSE_CAP", 2)

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"many-ob-{suffix}")
    lic = await _make_license(
        db_session, spdx_id=f"OB-{suffix}", name="ManyOb", category="conditional"
    )
    await _attach_license_finding(db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id)
    # 5 obligations on one license, cap is 2 → 3 omitted.
    for i in range(5):
        await _make_obligation(
            db_session, license_id=lic.id, kind=f"kind-{i:02d}", text=f"duty {i}"
        )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    body = payload["body"]
    assert "3 more obligation(s) omitted" in body
    # The kept head (first 2 by kind) is present; the omitted tail is not.
    assert "duty 0" in body and "duty 1" in body
    # Inspection count reports the TRUE total (5), not the rendered (2).
    assert payload["obligation_count"] == 5


@pytestmark_db
async def test_generate_notice_caps_license_count_with_omitted_footer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the scan surfaces more distinct licenses than the license cap, the
    NOTICE renders the head sections + an honest '+N more license(s) omitted'
    footer, and ``license_count`` reflects the TRUE total."""
    import services.obligation_service as svc

    monkeypatch.setattr(svc, "_NOTICE_LICENSE_CAP", 2)

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")

    suffix = unique_suffix()
    _, cv = await _make_component_version(db_session, name=f"multi-lic-{suffix}")
    # 4 distinct licenses on one component → cap 2 → 2 omitted.
    for i in range(4):
        lic = await _make_license(
            db_session,
            spdx_id=f"LC{i:02d}-{suffix}",
            name=f"Lic {i}",
            category="allowed",
        )
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    body = payload["body"]
    assert "2 more license(s) omitted" in body
    # Inspection count reports the TRUE total (4), not the rendered (2).
    assert payload["license_count"] == 4


@pytestmark_db
async def test_generate_notice_renders_copyright_and_bundled_license_text(
    db_session: AsyncSession,
) -> None:
    """End-to-end over ``_load_notice_data`` (Phase B):

    - captured copyright comes from ``scan_components.raw_data`` (cdxgen
      verbatim, CR/LF scrubbed), and a diamond dependency (second row at
      another path WITHOUT copyright) never erases or duplicates it;
    - a component with no captured copyright falls back to the honest
      "not captured" attribution + its purl-derived registry URL;
    - the MIT full text lands in the License Texts section;
    - the inspection counts are unaffected by the new sections (regression
      guard for the X-Notice-* headers).
    """
    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    suffix = unique_suffix()

    _, cv_with = await _make_component_version(db_session, name=f"withcp-{suffix}")
    _, cv_without = await _make_component_version(db_session, name=f"nocp-{suffix}")

    lic = await _get_or_create_license_by_spdx(db_session, "MIT")
    for cv in (cv_with, cv_without):
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )

    # cdxgen-shaped raw_data — realistic density (bom-ref / purl / licenses /
    # properties alongside copyright), mirroring a real `cdxgen` component
    # entry rather than a minimal one-key dict (testing-standards rule 3).
    copyright_line = f"Copyright (c) 2016 WithCp Contributors {suffix}"
    raw = {
        "type": "library",
        "bom-ref": f"pkg:npm/withcp-{suffix}@1.0.0",
        "name": f"withcp-{suffix}",
        "version": "1.0.0",
        "purl": f"pkg:npm/withcp-{suffix}@1.0.0",
        # Embedded CR/LF is hostile (NOTICE line injection) — must be scrubbed.
        "copyright": copyright_line + "\r\n tail",
        "licenses": [{"license": {"id": "MIT"}}],
        "properties": [{"name": "SrcFile", "value": "/app/package-lock.json"}],
    }
    await _make_scan_component(
        db_session,
        scan_id=scan.id,
        cv_id=cv_with.id,
        raw_data=raw,
        dependency_path=f"root/withcp-{suffix}",
    )
    # Diamond dependency: same component version at a second path, this row's
    # raw_data carries NO copyright — the captured one must still win, once.
    raw_no_cp = {k: v for k, v in raw.items() if k != "copyright"}
    await _make_scan_component(
        db_session,
        scan_id=scan.id,
        cv_id=cv_with.id,
        raw_data=raw_no_cp,
        dependency_path=f"root/other/withcp-{suffix}",
    )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    body = payload["body"]
    # Captured copyright renders with CR/LF collapsed out, exactly once.
    assert f"Copyright: {copyright_line} tail" in body
    assert body.count(f"withcp-{suffix} @ 1.0.0") == 1
    # The copyright-less component gets the honest fallback + npm registry URL
    # inferred from its purl.
    assert "Copyright: holders not captured in SBOM — see source (" in body
    assert f"https://www.npmjs.com/package/nocp-{suffix}/v/1.0.0" in body
    # Bundled MIT full text is appended.
    assert "License Texts" in body
    assert "----- MIT -----" in body
    assert "Permission is hereby granted" in body
    # X-Notice-* regression: counts reflect licenses/obligations, not sections.
    assert payload["license_count"] == 1
    # MIT is in the obligation catalog → sync_catalog_obligations seeds rows.
    assert payload["obligation_count"] >= 1


@pytestmark_db
async def test_generate_notice_clamps_oversized_copyright_in_sql(
    db_session: AsyncSession,
) -> None:
    """security-reviewer F-1: raw_data admits huge values, so the copyright is
    clamped INSIDE the SQL aggregate (``left(... ->> 'copyright', cap)``) — the
    64 KiB payload below must never reach Python or the rendered body in full.
    Asserted at the realistic density the hardening rules require (multiple
    keys per raw_data row, several components on one license)."""
    from services.obligation_service import _NOTICE_COPYRIGHT_CAP_CHARS

    team, user, project, scan = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    suffix = unique_suffix()

    lic = await _make_license(
        db_session, spdx_id=f"CPCAP-{suffix}", name="CpCap", category="allowed"
    )
    huge = "H" * (64 * 1024)  # one oversized copyright, well past the cap
    for i in range(3):
        _, cv = await _make_component_version(db_session, name=f"cpcap{i}-{suffix}")
        await _attach_license_finding(
            db_session, scan_id=scan.id, cv_id=cv.id, license_id=lic.id
        )
        await _make_scan_component(
            db_session,
            scan_id=scan.id,
            cv_id=cv.id,
            raw_data={
                "type": "library",
                "name": f"cpcap{i}-{suffix}",
                "version": "1.0.0",
                "purl": f"pkg:npm/cpcap{i}-{suffix}@1.0.0",
                "copyright": huge,
            },
            dependency_path=f"root/cpcap{i}-{suffix}",
        )

    payload = await generate_notice(
        db_session, project_id=project.id, actor=actor, fmt="text"
    )
    body = payload["body"]
    # The clamped head renders; the oversized tail never reaches the body.
    clamped = "H" * _NOTICE_COPYRIGHT_CAP_CHARS
    assert f"Copyright: {clamped}" in body
    assert clamped + "H" not in body
    # Belt and braces: the whole document stays far below 3 × 64 KiB.
    assert len(body) < 64 * 1024


@pytestmark_db
async def test_generate_notice_invalid_format_raises_obligation_error(
    db_session: AsyncSession,
) -> None:
    team, user, project, _ = await _make_project_with_scan(db_session)
    actor = principal_for(user, team_ids=[team.id], role="developer")
    with pytest.raises(ObligationError):
        await generate_notice(
            db_session, project_id=project.id, actor=actor, fmt="binary"
        )


@pytestmark_db
async def test_generate_notice_idor_other_team_returns_404_and_logs(
    db_session: AsyncSession,
) -> None:
    """Notice endpoint existence-hides cross-team as 404 (Low #4) + emits
    ``authz.cross_team_attempt``."""
    from services.project_service import ProjectNotFound

    org = await make_organization(db_session)
    target_team = await make_team(db_session, organization=org)
    other_team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=target_team)

    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=other_team, role="developer")
    actor = principal_for(user, team_ids=[other_team.id], role="developer")

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(ProjectNotFound):
            await generate_notice(
                db_session, project_id=project.id, actor=actor, fmt="text"
            )

    assert any(
        evt.get("event") == "authz.cross_team_attempt"
        and evt.get("resource") == "project_notice"
        for evt in captured
    )
