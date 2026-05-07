"""
Pure unit tests for the response size caps introduced in chore PR #3.

Two helpers are pinned here:

  - ``services.obligation_service._clamp_obligation_text`` — clamps the
    drawer's ``text`` field at :data:`_OBLIGATION_TEXT_CAP_BYTES` bytes.
  - The cap constants ``_AFFECTED_COMPONENTS_CAP`` (license + obligation)
    and ``_OBLIGATION_TEXT_CAP_BYTES`` are pinned at their wire values so
    a future bump must be deliberate (security-reviewer F4 — without these,
    a silent constant change would not be caught by the existing tests).

The full ``_load_affected_components`` truncation path is exercised by
the integration tests (`tests/integration/test_obligations_api.py` and
`tests/integration/test_licenses_api.py`); those need a live Postgres
to seed >500 license_findings, so they live behind the integration mark.
This file focuses on the pure-helper side: byte-cap correctness and
constant pinning.
"""

from __future__ import annotations

from services.license_service import (
    _AFFECTED_COMPONENTS_CAP as _LIC_AFFECTED_CAP,
)
from services.obligation_service import (
    _AFFECTED_COMPONENTS_CAP as _OBL_AFFECTED_CAP,
)
from services.obligation_service import (
    _OBLIGATION_TEXT_CAP_BYTES,
    _clamp_obligation_text,
)

# ---------------------------------------------------------------------------
# Constant pinning
# ---------------------------------------------------------------------------


def test_affected_components_cap_pinned_at_500() -> None:
    """Both services share the 500-row cap. Bumping the cap must touch the
    schema docs + the i18n disclosure copy too — pin it here so a silent
    change doesn't slip past review."""
    assert _LIC_AFFECTED_CAP == 500
    assert _OBL_AFFECTED_CAP == 500


def test_obligation_text_cap_pinned_at_64_kib() -> None:
    """64 KiB matches the schema's ``Field`` description on
    ``ObligationDetailResponse.text``. Same rationale as the affected-cap."""
    assert _OBLIGATION_TEXT_CAP_BYTES == 64 * 1024


# ---------------------------------------------------------------------------
# _clamp_obligation_text
# ---------------------------------------------------------------------------


def test_clamp_returns_text_unchanged_when_under_cap() -> None:
    text = "Preserve the attribution notice in derivative works."
    out, truncated = _clamp_obligation_text(text)
    assert out == text
    assert truncated is False


def test_clamp_returns_text_unchanged_at_exact_cap_boundary() -> None:
    """A string whose UTF-8 encoding is exactly the cap must not be clamped."""
    text = "x" * _OBLIGATION_TEXT_CAP_BYTES
    assert len(text.encode("utf-8")) == _OBLIGATION_TEXT_CAP_BYTES
    out, truncated = _clamp_obligation_text(text)
    assert truncated is False
    assert out == text


def test_clamp_truncates_when_one_byte_over_cap() -> None:
    text = "a" * (_OBLIGATION_TEXT_CAP_BYTES + 1)
    out, truncated = _clamp_obligation_text(text)
    assert truncated is True
    # Result must fit the cap exactly.
    assert len(out.encode("utf-8")) <= _OBLIGATION_TEXT_CAP_BYTES


def test_clamp_handles_multibyte_codepoint_at_boundary() -> None:
    """The slice happens on bytes; the helper must drop any partial
    multi-byte trailing sequence rather than emit invalid UTF-8."""
    # A run of ASCII so the boundary lands one byte short of a 3-byte
    # codepoint, then the codepoint itself.
    prefix_bytes = _OBLIGATION_TEXT_CAP_BYTES - 1
    text = ("a" * prefix_bytes) + "한"  # "한" is 3 bytes in UTF-8
    encoded = text.encode("utf-8")
    assert len(encoded) > _OBLIGATION_TEXT_CAP_BYTES

    out, truncated = _clamp_obligation_text(text)

    assert truncated is True
    # The clamped output must be valid UTF-8 — no replacement chars, no
    # raw bytes — so re-encoding it is reversible.
    out.encode("utf-8").decode("utf-8")
    # And the partial trailing codepoint must have been dropped, not
    # rendered as U+FFFD.
    assert "�" not in out


def test_clamp_handles_empty_string() -> None:
    out, truncated = _clamp_obligation_text("")
    assert out == ""
    assert truncated is False
