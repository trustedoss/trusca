"""
Vulnerability PDF report rendering — Scan-gap G2.

Two responsibilities, deliberately split so the HTML builder is unit-testable
without weasyprint (and its native libpango / cairo / gdk-pixbuf chain) being
installed in the running image:

- :func:`build_report_html` — a *pure* function that turns the gathered report
  data into a self-contained HTML string. No I/O, no DB, no weasyprint.
- :func:`render_report_pdf` — converts that HTML to PDF bytes. weasyprint is
  imported **lazily inside the function** so importing this module (and the
  HTML builder) never requires weasyprint to be present.

Why no Celery?
--------------
CLAUDE.md core rule #3 routes long-running scan work (ORT/cdxgen/Trivy/DT)
through Celery. This report is *not* scan work: the data is already
materialized in PostgreSQL (latest scan aggregates) and weasyprint renders a
bounded document (capped component / vulnerability lists) in seconds. It runs
synchronously in the request, like the SBOM export and NOTICE generator.

Security — XSS / HTML injection (CRITICAL)
------------------------------------------
Component names, purls, CVE ids/summaries and license names are all
attacker-influenceable (they originate from third-party package metadata that
flows through cdxgen → ORT → DT). Every such value MUST be HTML-escaped before
it lands in the document. We funnel *all* untrusted text through
:func:`_esc` (``html.escape`` with ``quote=True``) and only emit hyperlinks
through :func:`_safe_href`, which whitelists ``http`` / ``https`` schemes so a
``javascript:`` / ``data:`` / ``file:`` reference cannot smuggle script into a
PDF viewer that honours links. Mirrors the escape posture established for the
G1 NOTICE work.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

import structlog

log = structlog.get_logger("report.service")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Display order for severity buckets — worst first. Mirrors the ranking in
# ``project_detail_service`` and the design-system risk palette.
_SEVERITY_ORDER: tuple[str, ...] = ("critical", "high", "medium", "low", "info", "none")

# Severity -> hex color, lifted from the CLAUDE.md design system risk palette
# so the PDF matches the in-app risk colors. ``none`` collapses onto the Info
# grey since a component with no findings is informational, not a risk.
_SEVERITY_COLOR: dict[str, str] = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "low": "#2563eb",
    "info": "#71717a",
    "none": "#71717a",
    "unknown": "#71717a",
}

# License category display order — worst (forbidden) first.
_LICENSE_ORDER: tuple[str, ...] = ("forbidden", "conditional", "allowed", "unknown")

_LICENSE_COLOR: dict[str, str] = {
    "forbidden": "#dc2626",
    "conditional": "#ca8a04",
    "allowed": "#16a34a",
    "unknown": "#71717a",
}

# Defense-in-depth caps so a pathological scan (tens of thousands of rows)
# cannot inflate the rendered document — and, with it, the synchronous
# request time — without bound. The HTML builder records how many rows were
# omitted so the document is honest about truncation.
_MAX_COMPONENTS = 1000
_MAX_VULNERABILITIES = 1000

# G2 body-size cap (per-field): the row-count caps above bound how MANY rows the
# document carries, but a single pathological field (a multi-MiB CVE summary or
# a runaway component name from crafted package metadata) could still inflate
# the rendered HTML — and with it the synchronous render time. We clamp each
# free-text field to this many characters before escaping; an ellipsis marks a
# clamped value. 2000 chars is far past any real CVE summary while bounding the
# tail.
_MAX_FIELD_CHARS = 2000


# ---------------------------------------------------------------------------
# Escaping helpers (CRITICAL — every untrusted value flows through these)
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    """HTML-escape any value (``&``, ``<``, ``>``, ``"`` and ``'``).

    ``quote=True`` so the result is safe both as element text *and* inside a
    double/single-quoted attribute value. ``None`` renders as an empty string
    rather than the literal ``"None"``.

    G2 body-size cap: the raw value is clamped to ``_MAX_FIELD_CHARS`` (with an
    ellipsis) BEFORE escaping so a single pathological free-text field cannot
    inflate the rendered document. The clamp is on the source string, so the
    escaped result never splits an HTML entity.
    """
    if value is None:
        return ""
    text = str(value)
    if len(text) > _MAX_FIELD_CHARS:
        text = text[:_MAX_FIELD_CHARS] + "…"
    return html.escape(text, quote=True)


def _safe_href(url: Any) -> str | None:
    """Return an escaped ``href`` only for ``http`` / ``https`` URLs.

    Any other scheme (``javascript:``, ``data:``, ``file:``, ``vbscript:``,
    scheme-relative ``//evil``, or an unparseable value) returns ``None`` so
    the caller renders the reference as inert text instead of an active link.
    A PDF viewer that honours hyperlinks must never be handed a script URI.
    """
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    # An http(s) URL never contains control chars; tolerating an embedded
    # CR/LF/TAB would make this a CRLF-injection vector if the helper is ever
    # reused in a header / ``Location`` context (security-reviewer Low #3).
    if any(ord(c) < 0x20 or c == "\x7f" for c in raw):
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if not parsed.netloc:
        return None
    return _esc(raw)


def _fmt_cvss(value: Any) -> str:
    """Render a CVSS score as a one-decimal string, or an em dash when null."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# HTML builder (pure — no weasyprint, no I/O)
# ---------------------------------------------------------------------------


def build_report_html(
    *,
    project_name: str,
    generated_at: datetime,
    risk_score: float,
    total_components: int,
    severity_distribution: dict[str, int],
    license_distribution: dict[str, int],
    components: list[dict[str, Any]],
    vulnerabilities: list[dict[str, Any]],
    components_total: int | None = None,
    vulnerabilities_total: int | None = None,
) -> str:
    """Build a self-contained HTML report string.

    The function is pure: identical inputs produce identical output and it
    performs no I/O. ``components`` / ``vulnerabilities`` are the dict shapes
    returned by :func:`services.project_detail_service.list_components_for_project`
    and :func:`services.vulnerability_service.list_project_vulnerabilities`
    respectively — we read them defensively with ``.get`` so a future column
    addition does not break rendering.

    Every untrusted field is routed through :func:`_esc` / :func:`_safe_href`.
    """
    capped_components = components[:_MAX_COMPONENTS]
    capped_vulns = vulnerabilities[:_MAX_VULNERABILITIES]

    comp_total = components_total if components_total is not None else len(components)
    vuln_total = (
        vulnerabilities_total if vulnerabilities_total is not None else len(vulnerabilities)
    )

    generated_iso = generated_at.astimezone(UTC).replace(microsecond=0).isoformat()

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append(f"<title>Vulnerability Report — {_esc(project_name)}</title>")
    parts.append(_render_styles())
    parts.append("</head>")
    parts.append("<body>")

    # --- Header ---------------------------------------------------------
    parts.append('<header class="report-header">')
    parts.append('<div class="brand">TrustedOSS Portal</div>')
    parts.append('<h1 class="report-title">Vulnerability Report</h1>')
    parts.append(f'<div class="project-name">{_esc(project_name)}</div>')
    parts.append(f'<div class="generated">Generated: {_esc(generated_iso)}</div>')
    parts.append("</header>")

    # --- Risk summary ---------------------------------------------------
    parts.append(_render_risk_summary(risk_score, total_components))

    # --- Severity distribution -----------------------------------------
    parts.append(
        _render_distribution_section(
            heading="Vulnerability Severity Distribution",
            order=_SEVERITY_ORDER,
            colors=_SEVERITY_COLOR,
            distribution=severity_distribution,
        )
    )

    # --- License distribution ------------------------------------------
    parts.append(
        _render_distribution_section(
            heading="License Distribution",
            order=_LICENSE_ORDER,
            colors=_LICENSE_COLOR,
            distribution=license_distribution,
        )
    )

    # --- Vulnerabilities (grouped by severity) -------------------------
    parts.append(_render_vulnerabilities_section(capped_vulns, vuln_total))

    # --- Components -----------------------------------------------------
    parts.append(_render_components_section(capped_components, comp_total))

    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts)


def _render_styles() -> str:
    """Inline stylesheet. Static — contains no untrusted data."""
    return (
        "<style>\n"
        "  @page { size: A4; margin: 18mm 14mm; }\n"
        "  * { box-sizing: border-box; }\n"
        "  body { font-family: 'Helvetica Neue', Arial, sans-serif; color: #0f172a;\n"
        "         font-size: 10px; line-height: 1.4; }\n"
        "  .report-header { border-bottom: 2px solid #0f172a; padding-bottom: 10px;\n"
        "                   margin-bottom: 16px; }\n"
        "  .brand { font-size: 9px; letter-spacing: 1px; text-transform: uppercase;\n"
        "           color: #64748b; }\n"
        "  .report-title { font-size: 20px; margin: 4px 0 2px 0; }\n"
        "  .project-name { font-size: 13px; font-weight: 600; }\n"
        "  .generated { font-size: 9px; color: #64748b; margin-top: 2px; }\n"
        "  h2 { font-size: 13px; margin: 18px 0 6px 0; border-bottom: 1px solid #e2e8f0;\n"
        "       padding-bottom: 3px; }\n"
        "  h3 { font-size: 11px; margin: 12px 0 4px 0; }\n"
        "  .summary-grid { display: flex; gap: 16px; margin: 8px 0; }\n"
        "  .summary-card { border: 1px solid #e2e8f0; border-radius: 4px; padding: 8px 12px; }\n"
        "  .summary-card .label { font-size: 8px; text-transform: uppercase;\n"
        "                          letter-spacing: 0.5px; color: #64748b; }\n"
        "  .summary-card .value { font-size: 18px; font-weight: 700; }\n"
        "  table { width: 100%; border-collapse: collapse; margin: 6px 0 4px 0; }\n"
        "  th, td { text-align: left; padding: 4px 6px; border-bottom: 1px solid #e2e8f0;\n"
        "           vertical-align: top; word-break: break-word; }\n"
        "  th { background: #f1f5f9; font-size: 8px; text-transform: uppercase;\n"
        "       letter-spacing: 0.5px; color: #475569; }\n"
        "  .mono { font-family: 'Courier New', monospace; font-size: 9px; }\n"
        "  .badge { display: inline-block; padding: 1px 6px; border-radius: 8px;\n"
        "           color: #fff; font-size: 8px; font-weight: 600; text-transform: uppercase; }\n"
        "  .dist-row { display: flex; align-items: center; gap: 8px; margin: 2px 0; }\n"
        "  .dist-label { width: 90px; font-size: 9px; }\n"
        "  .dist-count { width: 40px; text-align: right; font-weight: 600; }\n"
        "  .dist-bar { height: 10px; border-radius: 2px; }\n"
        "  .muted { color: #64748b; }\n"
        "  .truncation-note { font-size: 8px; color: #64748b; margin-top: 4px;\n"
        "                     font-style: italic; }\n"
        "</style>"
    )


def _render_risk_summary(risk_score: float, total_components: int) -> str:
    score = max(0, min(100, int(round(risk_score))))
    return (
        '<section class="risk-summary">\n'
        "  <h2>Risk Summary</h2>\n"
        '  <div class="summary-grid">\n'
        '    <div class="summary-card">\n'
        '      <div class="label">Risk Score</div>\n'
        f'      <div class="value">{score}<span class="muted" '
        'style="font-size:10px;font-weight:400;"> / 100</span></div>\n'
        "    </div>\n"
        '    <div class="summary-card">\n'
        '      <div class="label">Total Components</div>\n'
        f'      <div class="value">{int(total_components)}</div>\n'
        "    </div>\n"
        "  </div>\n"
        "</section>"
    )


def _render_distribution_section(
    *,
    heading: str,
    order: tuple[str, ...],
    colors: dict[str, str],
    distribution: dict[str, int],
) -> str:
    # Preserve the canonical order, then append any unexpected keys so a new
    # enum value still shows up rather than vanishing. ``heading`` is a static
    # literal supplied by us, never user input.
    keys: list[str] = [k for k in order if k in distribution]
    keys.extend(k for k in distribution if k not in order)

    max_count = max((int(distribution.get(k, 0)) for k in keys), default=0)
    rows: list[str] = []
    for key in keys:
        count = int(distribution.get(key, 0))
        color = colors.get(key, "#71717a")
        # Bar width is proportional to the largest bucket; min 2px so a
        # nonzero count is always visible. All values here are server-derived
        # ints / known keys, but escape the label defensively anyway.
        width_px = 2 if max_count == 0 else max(2, int(160 * count / max_count))
        rows.append(
            '  <div class="dist-row">'
            f'<span class="dist-label">{_esc(key.capitalize())}</span>'
            f'<span class="dist-count">{count}</span>'
            f'<span class="dist-bar" style="width:{width_px}px;background:{_esc(color)};">'
            "</span></div>"
        )
    body = "\n".join(rows) if rows else '  <div class="muted">No data.</div>'
    return f'<section>\n  <h2>{_esc(heading)}</h2>\n{body}\n</section>'


def _severity_of(item: dict[str, Any]) -> str:
    sev = item.get("severity") or item.get("severity_max") or "unknown"
    return str(sev).lower()


def _render_vulnerabilities_section(
    vulnerabilities: list[dict[str, Any]],
    total: int,
) -> str:
    parts: list[str] = ['<section>', '  <h2>Vulnerabilities</h2>']
    if not vulnerabilities:
        parts.append('  <div class="muted">No vulnerabilities detected.</div>')
        parts.append("</section>")
        return "\n".join(parts)

    # Group by severity in worst-first order.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for v in vulnerabilities:
        grouped.setdefault(_severity_of(v), []).append(v)

    ordered_keys = [k for k in _SEVERITY_ORDER if k in grouped]
    ordered_keys.extend(k for k in grouped if k not in _SEVERITY_ORDER)

    for key in ordered_keys:
        rows = grouped[key]
        color = _SEVERITY_COLOR.get(key, "#71717a")
        parts.append(
            f'  <h3><span class="badge" style="background:{_esc(color)};">'
            f"{_esc(key.capitalize())}</span> "
            f'<span class="muted">({len(rows)})</span></h3>'
        )
        parts.append("  <table>")
        parts.append(
            "    <thead><tr><th>CVE</th><th>CVSS</th>"
            "<th>Summary</th><th>Status</th></tr></thead>"
        )
        parts.append("    <tbody>")
        for v in rows:
            cve = _esc(v.get("cve_id"))
            cvss = _esc(_fmt_cvss(v.get("cvss_score")))
            summary = _esc(v.get("summary") or "")
            vstatus = _esc(v.get("status") or "")
            parts.append(
                f'      <tr><td class="mono">{cve}</td><td>{cvss}</td>'
                f"<td>{summary}</td><td>{vstatus}</td></tr>"
            )
        parts.append("    </tbody>")
        parts.append("  </table>")

    if total > len(vulnerabilities):
        omitted = total - len(vulnerabilities)
        parts.append(
            f'  <div class="truncation-note">{omitted} additional '
            f"vulnerabilities omitted (showing {len(vulnerabilities)} of {total}).</div>"
        )
    parts.append("</section>")
    return "\n".join(parts)


def _render_components_section(
    components: list[dict[str, Any]],
    total: int,
) -> str:
    parts: list[str] = ['<section>', '  <h2>Components</h2>']
    if not components:
        parts.append('  <div class="muted">No components detected.</div>')
        parts.append("</section>")
        return "\n".join(parts)

    parts.append("  <table>")
    parts.append(
        "    <thead><tr><th>Name</th><th>Version</th><th>License</th>"
        "<th>Max Severity</th><th>Vulns</th></tr></thead>"
    )
    parts.append("    <tbody>")
    for c in components:
        name = _esc(c.get("name"))
        version = _esc(c.get("version"))
        lic = _esc(c.get("license") or "")
        sev = _severity_of(c)
        sev_color = _SEVERITY_COLOR.get(sev, "#71717a")
        vuln_count = int(c.get("vulnerability_count") or 0)
        parts.append(
            f'      <tr><td>{name}</td><td class="mono">{version}</td>'
            f"<td>{lic}</td>"
            f'<td><span class="badge" style="background:{_esc(sev_color)};">'
            f"{_esc(sev.capitalize())}</span></td>"
            f"<td>{vuln_count}</td></tr>"
        )
    parts.append("    </tbody>")
    parts.append("  </table>")

    if total > len(components):
        omitted = total - len(components)
        parts.append(
            f'  <div class="truncation-note">{omitted} additional components '
            f"omitted (showing {len(components)} of {total}).</div>"
        )
    parts.append("</section>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PDF conversion (weasyprint imported LAZILY here)
# ---------------------------------------------------------------------------


def render_report_pdf(html_str: str, *, base_url: str | None = None) -> bytes:
    """Convert an HTML report string into PDF bytes via weasyprint.

    weasyprint is imported **inside** this function on purpose: it pulls in
    native libraries (libpango / cairo / gdk-pixbuf) that are only present in
    the rebuilt backend image. Keeping the import lazy lets
    :func:`build_report_html` and this module import cleanly in environments
    (CI unit lane, a not-yet-rebuilt image) where weasyprint is absent — the
    ImportError only surfaces if someone actually asks for a PDF.

    ``base_url`` is left at ``None`` by default: the document is fully
    self-contained (inline ``<style>``, no external CSS / images / fonts), so
    weasyprint never needs to resolve a relative URL. Passing ``None`` also
    means weasyprint will not be able to fetch ``file://`` resources even if a
    crafted ``url(...)`` slipped through, which is the conservative default.
    """
    try:
        import weasyprint  # noqa: PLC0415 — lazy by design (see docstring)
    except ImportError as exc:  # pragma: no cover - depends on image rebuild
        raise ReportRenderingError(
            "weasyprint is not installed in this image; rebuild the backend "
            "image to enable PDF reports"
        ) from exc

    document = weasyprint.HTML(string=html_str, base_url=base_url)
    pdf = document.write_pdf()
    # weasyprint's type stub declares Optional[bytes]; write_pdf() with no
    # target always returns bytes. Narrow for mypy + guard defensively.
    if pdf is None:  # pragma: no cover - never happens with target=None
        raise ReportRenderingError("weasyprint returned no PDF bytes")
    return cast(bytes, pdf)


class ReportRenderingError(RuntimeError):
    """Raised when PDF rendering fails (e.g. weasyprint missing/erroring)."""


__all__ = [
    "ReportRenderingError",
    "build_report_html",
    "render_report_pdf",
]
