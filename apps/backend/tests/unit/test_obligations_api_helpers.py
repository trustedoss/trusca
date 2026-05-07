"""
Pure unit tests for the obligations API module's filename helpers.

The helpers compose the ``Content-Disposition`` header for the NOTICE
download endpoint. RFC 6266 expects two parameters:

  Content-Disposition: attachment; filename="<ASCII fallback>"; filename*=UTF-8''<percent-encoded>

The ASCII fallback is consumed by legacy clients that don't understand
``filename*``; the UTF-8 extended parameter preserves the original project
name (including non-ASCII characters) so modern browsers can show it
verbatim in the download dialog.

Tests pin the contract for both halves so future refactors (e.g.,
switching to ``urllib.parse.quote_plus``) don't silently break either
client class.
"""

from __future__ import annotations

import urllib.parse

from api.v1.obligations import _format_content_disposition, _safe_filename_token


def test_safe_filename_token_strips_unsafe_chars() -> None:
    assert _safe_filename_token("Hello / World!  alpha") == "Hello-World-alpha"


def test_safe_filename_token_falls_back_to_default() -> None:
    """All-unsafe input must collapse to the literal fallback ``project``."""
    assert _safe_filename_token("///") == "project"
    assert _safe_filename_token("") == "project"


def test_format_content_disposition_starts_with_attachment() -> None:
    out = _format_content_disposition("simple", "txt")
    assert out.startswith("attachment;")


def test_format_content_disposition_ascii_fallback_is_safe() -> None:
    """The ASCII filename must contain only sanitised characters even if
    the project name carried whitespace, slashes, etc."""
    out = _format_content_disposition("Hello / World!  alpha", "txt")
    ascii_part = out.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part == "NOTICE-Hello-World-alpha.txt"
    assert " " not in ascii_part
    assert "/" not in ascii_part
    assert "!" not in ascii_part


def test_format_content_disposition_includes_utf8_extended_parameter() -> None:
    out = _format_content_disposition("simple", "md")
    marker = "filename*=UTF-8''"
    assert marker in out
    encoded = out.split(marker, 1)[1]
    assert urllib.parse.unquote(encoded) == "NOTICE-simple.md"


def test_format_content_disposition_round_trips_korean_project_name() -> None:
    """Non-ASCII project names must survive the round-trip via the UTF-8
    extended parameter even though the ASCII fallback drops them."""
    project_name = "한글-프로젝트"
    out = _format_content_disposition(project_name, "txt")
    marker = "filename*=UTF-8''"
    encoded = out.split(marker, 1)[1]
    assert urllib.parse.unquote(encoded) == f"NOTICE-{project_name}.txt"
    # ASCII fallback collapses non-ASCII to the safe-token replacement.
    ascii_part = out.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part.isascii()
    assert ascii_part.startswith("NOTICE-")
    assert ascii_part.endswith(".txt")


def test_format_content_disposition_escapes_special_chars_in_utf8() -> None:
    """The percent-encoder must quote characters the header parser would
    otherwise treat as token boundaries (`,`, `;`, `"`)."""
    out = _format_content_disposition('quote"and;comma,', "txt")
    marker = "filename*=UTF-8''"
    encoded = out.split(marker, 1)[1]
    # The encoded segment must not contain an un-escaped quote, semicolon,
    # or comma (those would break ``Content-Disposition`` parsing).
    assert '"' not in encoded
    assert ";" not in encoded
    assert "," not in encoded
    # Round-trip back to the original.
    assert urllib.parse.unquote(encoded) == 'NOTICE-quote"and;comma,.txt'
