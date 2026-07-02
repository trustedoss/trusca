"""
Unit tests for ``services/license_texts`` — Phase B (NOTICE license texts).

Pure filesystem/parsing cases, no DB. The catalog↔files equality contract
lives in :file:`tests/unit/test_catalog_contracts.py` (vocabulary-drift rule);
this file covers the loader's behaviour: exact-match lookup, hostile-id
rejection BEFORE any filesystem access, compound-expression splitting, and
the miss-skipping contract of ``texts_for_expression``.
"""

from __future__ import annotations

import pytest

from services.license_texts import (
    bundled_spdx_ids,
    is_safe_spdx_id,
    license_text,
    spdx_ids_for_expression,
    texts_for_expression,
)

# ---------------------------------------------------------------------------
# license_text — exact match + hostile-id rejection
# ---------------------------------------------------------------------------


def test_is_safe_spdx_id_allowlist() -> None:
    """Shared with the NOTICE text-divider scrub (security-reviewer F-2)."""
    assert is_safe_spdx_id("MIT")
    assert is_safe_spdx_id("GPL-2.0-or-later")
    assert is_safe_spdx_id("Apache-2.0")
    assert not is_safe_spdx_id(None)
    assert not is_safe_spdx_id("")
    assert not is_safe_spdx_id("MIT OR Apache-2.0")  # whitespace / expression
    assert not is_safe_spdx_id("Evil\nId")
    assert not is_safe_spdx_id("../MIT")
    assert not is_safe_spdx_id("a" * 65)


def test_license_text_returns_bundled_full_text() -> None:
    text = license_text("MIT")
    assert text is not None
    assert text.startswith("MIT License")
    assert "Permission is hereby granted" in text


def test_license_text_unknown_id_is_none() -> None:
    assert license_text("NotARealLicense-1.0") is None


def test_license_text_is_exact_match_no_case_folding() -> None:
    """Mirrors ``obligation_catalog.get_license_obligations``: no fuzzy match."""
    assert license_text("mit") is None
    assert license_text("MIT ") is None


def test_license_text_compound_expression_is_none() -> None:
    """A compound expression has no single bundled text — callers split first."""
    assert license_text("MIT OR Apache-2.0") is None


@pytest.mark.parametrize(
    "hostile",
    [
        None,
        "",
        "../MIT",
        "..",
        "MIT/../../etc/passwd",
        "MIT/..",
        "/etc/passwd",
        "\\windows\\system32",
        "MIT\x00",
        "MIT\n",
        ".hidden",
        "-leading-dash",
        "a" * 65,  # over the 64-char spdx_id column bound
    ],
)
def test_license_text_rejects_hostile_ids(hostile: str | None) -> None:
    """Path-steering / control-char / oversized ids never reach the disk."""
    assert license_text(hostile) is None


def test_bundled_ids_are_nonempty_and_loadable() -> None:
    ids = bundled_spdx_ids()
    assert len(ids) >= 32
    # Every advertised id actually loads (no orphan stems, no read errors).
    for spdx_id in ids:
        text = license_text(spdx_id)
        assert text, f"bundled id {spdx_id} did not load"


# ---------------------------------------------------------------------------
# spdx_ids_for_expression / texts_for_expression — compound splitting
# ---------------------------------------------------------------------------


def test_spdx_ids_for_expression_simple_id_passes_through() -> None:
    assert spdx_ids_for_expression("MIT") == ["MIT"]


def test_spdx_ids_for_expression_splits_compound_and_dedupes() -> None:
    assert spdx_ids_for_expression("MIT OR Apache-2.0 OR MIT") == ["MIT", "Apache-2.0"]


def test_spdx_ids_for_expression_handles_parens_and_with() -> None:
    ids = spdx_ids_for_expression("(MIT AND Apache-2.0) WITH Classpath-exception-2.0")
    assert ids == ["MIT", "Apache-2.0", "Classpath-exception-2.0"]


def test_spdx_ids_for_expression_empty_is_empty() -> None:
    assert spdx_ids_for_expression(None) == []
    assert spdx_ids_for_expression("") == []


def test_texts_for_expression_returns_every_bundled_operand() -> None:
    pairs = texts_for_expression("MIT OR Apache-2.0")
    assert [spdx_id for spdx_id, _ in pairs] == ["MIT", "Apache-2.0"]
    assert all(text for _, text in pairs)


def test_texts_for_expression_skips_unbundled_operands() -> None:
    """Miss-skipping contract: the caller keeps reference_url as the fallback."""
    pairs = texts_for_expression("MIT OR NotARealLicense-1.0")
    assert [spdx_id for spdx_id, _ in pairs] == ["MIT"]


def test_texts_for_expression_all_unknown_is_empty() -> None:
    assert texts_for_expression("LicenseRef-custom OR NotARealLicense-1.0") == []
