"""
Unit tests for the vulnerability PDF report HTML builder — scan-gap G2.

Scope
-----
These tests exercise :func:`services.report_service.build_report_html` (and the
small pure helpers) **directly**. They MUST NOT import weasyprint: the HTML
builder is deliberately decoupled from the PDF backend so the unit lane runs
without the native libpango / cairo / gdk-pixbuf chain. The weasyprint-backed
``render_report_pdf`` is covered by the integration test against a rebuilt
image.

Security focus
--------------
The bulk of these tests pin the escape posture: component names, purls, CVE
ids / summaries, license names and license-distribution keys are all
attacker-influenceable (they come from third-party package metadata). Every
hostile value must be HTML-escaped, and only http/https hyperlinks may be
emitted as active links.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import Any

import pytest

from services.report_service import (
    _esc,
    _fmt_cvss,
    _safe_href,
    build_report_html,
)


def _base_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs = {
        "project_name": "Acme Web",
        "generated_at": datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC),
        "risk_score": 42.7,
        "total_components": 3,
        "severity_distribution": {
            "critical": 1,
            "high": 2,
            "medium": 0,
            "low": 0,
            "info": 0,
            "none": 0,
        },
        "license_distribution": {
            "forbidden": 1,
            "conditional": 0,
            "allowed": 2,
            "unknown": 0,
        },
        "components": [
            {
                "name": "left-pad",
                "version": "1.0.0",
                "purl": "pkg:npm/left-pad@1.0.0",
                "license": "MIT",
                "license_category": "allowed",
                "severity_max": "none",
                "vulnerability_count": 0,
            }
        ],
        "vulnerabilities": [
            {
                "cve_id": "CVE-2026-1234",
                "severity": "critical",
                "cvss_score": 9.8,
                "summary": "Remote code execution in foo",
                "status": "new",
            }
        ],
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# weasyprint must NOT be imported by this module / the HTML builder
# ---------------------------------------------------------------------------


def test_html_builder_does_not_import_weasyprint() -> None:
    """The unit lane must run without weasyprint. Building HTML must not pull
    it in, even transitively."""
    build_report_html(**_base_kwargs())
    assert "weasyprint" not in sys.modules


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_html_has_doctype_and_required_sections() -> None:
    html = build_report_html(**_base_kwargs())
    assert html.startswith("<!DOCTYPE html>")
    assert "<html" in html and "</html>" in html
    # The four report sections by heading text.
    assert "Risk Summary" in html
    assert "Vulnerability Severity Distribution" in html
    assert "License Distribution" in html
    assert "Vulnerabilities" in html
    assert "Components" in html


def test_html_renders_risk_score_and_total_components() -> None:
    html = build_report_html(**_base_kwargs(risk_score=42.7, total_components=3))
    # risk_score is rounded to an int for display.
    assert "43" in html
    assert ">3<" in html or ">3 " in html


def test_html_includes_project_name_and_generated_timestamp() -> None:
    html = build_report_html(
        **_base_kwargs(
            project_name="MyProj",
            generated_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        )
    )
    assert "MyProj" in html
    assert "2026-01-02T03:04:05+00:00" in html


def test_html_renders_cve_cvss_and_summary() -> None:
    html = build_report_html(**_base_kwargs())
    assert "CVE-2026-1234" in html
    assert "9.8" in html
    assert "Remote code execution in foo" in html


def test_empty_project_renders_no_data_placeholders() -> None:
    html = build_report_html(
        **_base_kwargs(
            total_components=0,
            components=[],
            vulnerabilities=[],
            severity_distribution=dict.fromkeys(
                ("critical", "high", "medium", "low", "info", "none"), 0
            ),
            license_distribution=dict.fromkeys(
                ("forbidden", "conditional", "allowed", "unknown"), 0
            ),
        )
    )
    assert "No vulnerabilities detected." in html
    assert "No components detected." in html
    # Still a well-formed document.
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


# ---------------------------------------------------------------------------
# Severity grouping
# ---------------------------------------------------------------------------


def test_vulnerabilities_grouped_worst_first() -> None:
    vulns = [
        {
            "cve_id": "CVE-LOW",
            "severity": "low",
            "cvss_score": 2.0,
            "summary": "x",
            "status": "new",
        },
        {
            "cve_id": "CVE-CRIT",
            "severity": "critical",
            "cvss_score": 9.9,
            "summary": "y",
            "status": "new",
        },
    ]
    html = build_report_html(**_base_kwargs(vulnerabilities=vulns))
    # Critical group heading must appear before the low group heading.
    assert html.index("CVE-CRIT") < html.index("CVE-LOW")


# ---------------------------------------------------------------------------
# Truncation honesty
# ---------------------------------------------------------------------------


def test_truncation_note_when_totals_exceed_rendered_rows() -> None:
    html = build_report_html(
        **_base_kwargs(components_total=5000, vulnerabilities_total=4000)
    )
    assert "additional components omitted" in html
    assert "additional vulnerabilities omitted" in html


# ---------------------------------------------------------------------------
# SECURITY — escaping (the load-bearing tests)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        '<script>alert(1)</script>',
        '"><img src=x onerror=alert(1)>',
        "Robert'); DROP TABLE components;--",
        "<b>bold</b> & <i>italic</i>",
    ],
)
def test_hostile_component_name_is_escaped(hostile: str) -> None:
    html = build_report_html(
        **_base_kwargs(
            components=[
                {
                    "name": hostile,
                    "version": "1.0.0",
                    "purl": "pkg:npm/x@1.0.0",
                    "license": "MIT",
                    "license_category": "allowed",
                    "severity_max": "none",
                    "vulnerability_count": 0,
                }
            ]
        )
    )
    # The raw hostile markup must never appear verbatim — only its escaped form.
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert _esc(hostile) in html


def test_hostile_cve_fields_are_escaped() -> None:
    html = build_report_html(
        **_base_kwargs(
            vulnerabilities=[
                {
                    "cve_id": "<script>cve</script>",
                    "severity": "critical",
                    "cvss_score": 9.8,
                    "summary": '<img src=x onerror=alert(1)>',
                    "status": "<b>new</b>",
                }
            ]
        )
    )
    assert "<script>cve</script>" not in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<b>new</b>" not in html
    assert "&lt;script&gt;cve&lt;/script&gt;" in html


def test_hostile_license_name_is_escaped() -> None:
    html = build_report_html(
        **_base_kwargs(
            components=[
                {
                    "name": "pkg",
                    "version": "1.0.0",
                    "purl": "pkg:npm/pkg@1.0.0",
                    "license": '<script>evil</script>',
                    "license_category": "forbidden",
                    "severity_max": "high",
                    "vulnerability_count": 1,
                }
            ]
        )
    )
    assert "<script>evil</script>" not in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html


def test_hostile_distribution_key_is_escaped() -> None:
    """A future / corrupted distribution key must not break out of the markup."""
    html = build_report_html(
        **_base_kwargs(
            severity_distribution={"critical": 1, '<script>x</script>': 2},
        )
    )
    assert "<script>x</script>" not in html


# ---------------------------------------------------------------------------
# _esc
# ---------------------------------------------------------------------------


def test_esc_quotes_all_metacharacters() -> None:
    assert _esc('<>&"\'') == "&lt;&gt;&amp;&quot;&#x27;"


def test_esc_none_is_empty_string() -> None:
    assert _esc(None) == ""


def test_esc_non_string_is_stringified_then_escaped() -> None:
    assert _esc(5) == "5"


def test_esc_clamps_pathological_field_to_max_chars() -> None:
    """G2 body-size cap: a runaway free-text field is clamped before escaping."""
    from services.report_service import _MAX_FIELD_CHARS

    out = _esc("x" * (_MAX_FIELD_CHARS * 4))
    # The escaped result is bounded (chars + the trailing ellipsis), never the
    # full multi-MiB body.
    assert len(out) <= _MAX_FIELD_CHARS + 1
    assert out.endswith("…")
    # A normal-length field is untouched (no ellipsis).
    assert _esc("CVE summary text") == "CVE summary text"


def test_build_report_html_clamps_huge_cve_summary() -> None:
    from services.report_service import _MAX_FIELD_CHARS

    huge = "S" * (_MAX_FIELD_CHARS * 10)
    html = build_report_html(
        **_base_kwargs(
            vulnerabilities=[
                {
                    "cve_id": "CVE-2099-0001",
                    "cvss_score": 9.8,
                    "summary": huge,
                    "status": "open",
                    "severity": "critical",
                }
            ]
        )
    )
    # The pathological summary never lands in full — the document stays bounded.
    assert huge not in html
    assert "…" in html


# ---------------------------------------------------------------------------
# _safe_href — only http/https survive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "//evil.example.com/x",
        "not a url",
        "",
        None,
        "ftp://host/file",
        # Embedded control chars rejected (CRLF-injection defence, reviewer Low #3).
        "http://h/\r\nX-Injected: 1",
        "https://h/\tpath",
        "http://h/\x00",
    ],
)
def test_safe_href_rejects_non_http_schemes(url) -> None:
    assert _safe_href(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://nvd.nist.gov/vuln/detail/CVE-2026-1234",
        "http://example.com/advisory",
    ],
)
def test_safe_href_allows_http_https(url: str) -> None:
    result = _safe_href(url)
    assert result is not None
    # Even allowed URLs are HTML-escaped.
    assert "<" not in result and ">" not in result


def test_safe_href_escapes_allowed_url_with_metacharacters() -> None:
    result = _safe_href('https://example.com/x?a="b"&c=<d>')
    assert result is not None
    assert "&quot;" in result
    assert "&lt;d&gt;" in result


# ---------------------------------------------------------------------------
# _fmt_cvss
# ---------------------------------------------------------------------------


def test_fmt_cvss_one_decimal() -> None:
    assert _fmt_cvss(9.8) == "9.8"
    assert _fmt_cvss(7) == "7.0"


def test_fmt_cvss_none_is_em_dash() -> None:
    assert _fmt_cvss(None) == "—"


def test_fmt_cvss_garbage_is_em_dash() -> None:
    assert _fmt_cvss("not-a-number") == "—"
