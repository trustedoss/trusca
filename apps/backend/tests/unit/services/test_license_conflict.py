# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Outbound-license conflict verdicts (gap #27).

Three things are worth failing a build over, and they shape the cases below:

1. "Not assessed" must stay distinguishable from "no conflict". A project with
   no declared outbound license gets ``None``, not ``compatible``.
2. The operators must fold in opposite directions — ``AND`` takes the worst
   term, ``OR`` the best. Getting these the same way round would make a
   dual-licensed dependency look broken (or a combined one look clean).
3. The whole matrix is data, so every cell is exercised against the file rather
   than against a handful of ids someone remembered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.license_class import LICENSE_CLASS_VALUES
from services.license_conflict import (
    COMPATIBLE,
    CONDITIONAL,
    CONFLICT_VERDICT_VALUES,
    INCOMPATIBLE,
    UNKNOWN,
    VERDICT_RANK,
    assess,
    expression_verdict,
    outbound_terms,
    term_verdict,
)

_RULES = json.loads(
    (Path(__file__).resolve().parents[3] / "services" / "license_compat.json").read_text(
        encoding="utf-8"
    )
)


# ---------------------------------------------------------------------------
# Rule data ↔ code contracts (CLAUDE.md hardening rule 2)
# ---------------------------------------------------------------------------


def test_verdict_vocabulary_matches_the_rule_data() -> None:
    assert set(CONFLICT_VERDICT_VALUES) == set(_RULES["_verdicts"])
    assert set(VERDICT_RANK) == set(CONFLICT_VERDICT_VALUES)


def test_matrix_keys_are_the_class_vocabulary() -> None:
    """A matrix row for a class the classifier never emits is dead rule data."""
    assert set(_RULES["matrix"]) == set(LICENSE_CLASS_VALUES)
    for outbound, row in _RULES["matrix"].items():
        assert set(row) == set(LICENSE_CLASS_VALUES), f"matrix.{outbound} is not square"


def test_every_cell_carries_a_reason() -> None:
    """The reason is shown to the user; a cell without one renders an empty box."""
    for outbound, row in _RULES["matrix"].items():
        for dependency, cell in row.items():
            assert cell["verdict"] in CONFLICT_VERDICT_VALUES
            assert cell["why"].strip(), f"matrix.{outbound}.{dependency} has no reason"
    for pair in _RULES["pairs"]:
        assert pair["verdict"] in CONFLICT_VERDICT_VALUES
        assert pair["why"].strip()


# ---------------------------------------------------------------------------
# Absent is not clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outbound", [None, "", "   "])
def test_no_outbound_means_no_verdict(outbound: str | None) -> None:
    """``None`` — not ``compatible``, not ``unknown``. Nothing was assessed."""
    assert assess(["MIT"], outbound=outbound) is None


def test_component_with_no_license_is_unknown_not_compatible() -> None:
    verdict = assess([], outbound="Apache-2.0")
    assert verdict is not None
    assert verdict.verdict == UNKNOWN


# ---------------------------------------------------------------------------
# Single-term verdicts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dependency", "outbound", "expected"),
    [
        ("MIT", "Apache-2.0", COMPATIBLE),
        ("Apache-2.0", "GPL-3.0-only", COMPATIBLE),
        ("LGPL-2.1-only", "MIT", CONDITIONAL),
        ("GPL-3.0-only", "MIT", INCOMPATIBLE),
        ("AGPL-3.0-only", "Apache-2.0", INCOMPATIBLE),
        ("AGPL-3.0-only", "GPL-3.0-only", CONDITIONAL),
        ("AGPL-3.0-only", "AGPL-3.0-only", COMPATIBLE),
        ("Frobnicate-1.0", "MIT", UNKNOWN),
        # An unrecognised OUTBOUND license leaves nothing to judge against.
        ("MIT", "Frobnicate-1.0", UNKNOWN),
    ],
)
def test_term_verdicts(dependency: str, outbound: str, expected: str) -> None:
    assert term_verdict(dependency, outbound).verdict == expected


def test_explicit_pair_overrides_the_class_matrix() -> None:
    """GPL-2.0-only + Apache-2.0: the class answer is wrong, the pair is right.

    By class this is strong-copyleft outbound with a permissive dependency,
    which the matrix calls compatible. The FSF's reading is the opposite, and
    the pair exists to say so.
    """
    assert term_verdict("Apache-2.0", "GPL-2.0-only").verdict == INCOMPATIBLE
    # Case-insensitive on both sides, as upstream matches.
    assert term_verdict("apache-2.0", "gpl-2.0-only").verdict == INCOMPATIBLE
    # GPL-3.0 does not carry the same problem.
    assert term_verdict("Apache-2.0", "GPL-3.0-only").verdict == COMPATIBLE


def test_verdict_carries_the_dependency_class() -> None:
    verdict = term_verdict("AGPL-3.0-only", "MIT")
    assert verdict.dependency_class == "network-copyleft"


# ---------------------------------------------------------------------------
# Operators fold in opposite directions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "outbound", "expected"),
    [
        # OR: the consumer picks one, so one clean alternative clears it.
        ("MIT OR GPL-3.0-only", "Apache-2.0", COMPATIBLE),
        ("GPL-3.0-only OR AGPL-3.0-only", "MIT", INCOMPATIBLE),
        # AND: every term applies, so one bad term condemns the whole.
        ("MIT AND GPL-3.0-only", "Apache-2.0", INCOMPATIBLE),
        ("MIT AND ISC", "Apache-2.0", COMPATIBLE),
        # AND binds tighter than OR, per the SPDX spec.
        ("MIT OR ISC AND GPL-3.0-only", "Apache-2.0", COMPATIBLE),
        # Parentheses — upstream's jq parser gives up here and records unknown.
        ("(MIT OR GPL-3.0-only) AND Apache-2.0", "Apache-2.0", COMPATIBLE),
        ("(GPL-3.0-only OR AGPL-3.0-only) AND MIT", "Apache-2.0", INCOMPATIBLE),
    ],
)
def test_expression_folding(expression: str, outbound: str, expected: str) -> None:
    verdict = expression_verdict(expression, outbound)
    assert verdict is not None
    assert verdict.verdict == expected


def test_with_caps_an_incompatible_verdict_at_conditional() -> None:
    """An exception clause exists to permit the combination, so it softens it."""
    plain = expression_verdict("GPL-2.0-only", "MIT")
    assert plain is not None
    assert plain.verdict == INCOMPATIBLE

    excepted = expression_verdict("GPL-2.0-only WITH Classpath-exception-2.0", "MIT")
    assert excepted is not None
    assert excepted.verdict == CONDITIONAL
    # The original reason survives inside the softened one, so the reader can
    # see what the exception is standing in for.
    assert plain.why in excepted.why


def test_with_does_not_upgrade_a_clean_verdict() -> None:
    """The cap only ever softens; it must not turn compatible into something else."""
    verdict = expression_verdict("Apache-2.0 WITH LLVM-exception", "MIT")
    assert verdict is not None
    assert verdict.verdict == COMPATIBLE


@pytest.mark.parametrize(
    "expression",
    [
        "MIT AND",
        "(((",
        "MIT OR OR Apache-2.0",
        "M" * 5000,
    ],
)
def test_unparseable_expression_yields_no_verdict(expression: str) -> None:
    assert expression_verdict(expression, "Apache-2.0") is None


def test_unparseable_component_license_is_unknown() -> None:
    """At component level an unreadable declaration must still say something."""
    verdict = assess(["MIT AND"], outbound="Apache-2.0")
    assert verdict is not None
    assert verdict.verdict == UNKNOWN


def test_several_license_entries_are_alternatives() -> None:
    """CycloneDX lets a consumer pick one entry, so the best verdict wins."""
    verdict = assess(["GPL-3.0-only", "MIT"], outbound="Apache-2.0")
    assert verdict is not None
    assert verdict.verdict == COMPATIBLE


def test_control_characters_are_rejected_not_classified() -> None:
    """Hostile input degrades to "unknown"; it never reaches a matrix cell."""
    verdict = assess(["MIT\x00GPL-3.0-only"], outbound="Apache-2.0")
    assert verdict is not None
    assert verdict.verdict == UNKNOWN


# ---------------------------------------------------------------------------
# The outbound side is an expression too (security review, Medium)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dependency", "outbound", "expected"),
    [
        # The failure that motivated this: a compound declaration used to fall
        # to "uncategorized" and turn EVERY verdict into unknown, while the
        # screen still claimed it had measured against the declared license.
        ("GPL-3.0-only", "MIT OR Apache-2.0", INCOMPATIBLE),
        ("MIT", "MIT OR Apache-2.0", COMPATIBLE),
        ("MIT", "Apache-2.0 AND MIT", COMPATIBLE),
        # Worst-of across outbound terms, both operators. A consumer may end up
        # relying on the MIT branch, and AGPL cannot be shipped under it — the
        # old whole-string classification read this as strong-copyleft and
        # softened it to conditional, hiding exactly that branch.
        ("AGPL-3.0-only", "MIT OR GPL-3.0-only", INCOMPATIBLE),
        ("LGPL-2.1-only", "MIT OR Apache-2.0", CONDITIONAL),
        # An explicit pair still applies when it is one term among several.
        ("Apache-2.0", "GPL-2.0-only OR MIT", INCOMPATIBLE),
        ("Apache-2.0", "MIT AND GPL-2.0-only", INCOMPATIBLE),
    ],
)
def test_compound_outbound_is_parsed_not_pattern_matched(
    dependency: str, outbound: str, expected: str
) -> None:
    verdict = assess([dependency], outbound=outbound)
    assert verdict is not None
    assert verdict.verdict == expected


@pytest.mark.parametrize("outbound", ["MIT AND", "(((", "MIT MIT", "A\x00B"])
def test_unreadable_outbound_yields_unknown_not_a_guess(outbound: str) -> None:
    """An outbound we cannot parse must not be silently classified.

    ``None`` is reserved for "nothing was declared"; a declaration we cannot
    read is a different state and has to reach the user as ``unknown``.
    """
    verdict = assess(["MIT"], outbound=outbound)
    assert verdict is not None
    assert verdict.verdict == UNKNOWN


def test_outbound_terms_flattens_both_operators() -> None:
    assert outbound_terms("MIT") == ["MIT"]
    assert outbound_terms("MIT OR Apache-2.0") == ["MIT", "Apache-2.0"]
    assert outbound_terms("MIT AND Apache-2.0") == ["MIT", "Apache-2.0"]
    assert outbound_terms("(MIT OR Apache-2.0) AND ISC") == ["MIT", "Apache-2.0", "ISC"]
    assert outbound_terms("MIT AND") is None


# ---------------------------------------------------------------------------
# Deprecated SPDX spellings still hit the explicit pairs (security review, Low)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outbound", "expected"),
    [
        ("GPL-2.0-only", INCOMPATIBLE),
        # SPDX deprecated the bare form, but it is what people type — and it
        # used to miss the pair and answer "compatible", reversing a rule the
        # data states outright.
        ("GPL-2.0", INCOMPATIBLE),
        ("gpl-2.0", INCOMPATIBLE),
        # ``-or-later`` genuinely does not carry the problem: it may be
        # satisfied under GPL-3.0. Normalising it into ``-only`` would be a
        # false positive, so the expansion is deliberately one-directional.
        ("GPL-2.0-or-later", COMPATIBLE),
        ("GPL-2.0+", COMPATIBLE),
    ],
)
def test_deprecated_gpl_spelling_reaches_the_explicit_pair(
    outbound: str, expected: str
) -> None:
    assert term_verdict("Apache-2.0", outbound).verdict == expected
