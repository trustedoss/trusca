# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Recovery codes: what they look like, and that typing them back works.

The failure this guards against is quiet. A code is stored as a bcrypt hash,
so if the form stored and the form compared differ by a hyphen or a case, the
result is a correct code being refused with nothing to say why -- and the
person refused is somebody who has already lost their phone.
"""

from __future__ import annotations

import re

import pytest

from core.recovery_codes import CODE_COUNT, generate_codes, normalise

_DISPLAY = re.compile(r"^[A-Z2-9]{5}-[A-Z2-9]{5}$")


def test_codes_are_issued_in_the_documented_shape() -> None:
    codes = generate_codes()

    assert len(codes) == CODE_COUNT
    for code in codes:
        assert _DISPLAY.match(code), code


def test_ten_codes_are_issued_because_that_is_what_the_guide_says() -> None:
    """The literal, not the constant.

    ``len(codes) == CODE_COUNT`` compares the value with itself and agrees
    with any number. What a reader was told is ten, and ten is a decision:
    each code is one sign-in rather than one attempt, so the count is how many
    times somebody can get back in after losing the authenticator. Changing it
    means changing the user guide in the same edit.
    """
    assert CODE_COUNT == 10
    assert len(generate_codes()) == 10


def test_the_alphabet_excludes_characters_that_are_read_wrong() -> None:
    """These end up on paper, so 0/O and 1/I/L are not in them.

    Asserted over a large sample rather than one draw: a single code proves
    nothing about an alphabet, and the whole point is that no code can contain
    them.
    """
    everything = "".join(generate_codes(200)).replace("-", "")

    assert not set(everything) & set("01OIL"), sorted(set(everything) & set("01OIL"))


def test_every_issued_code_is_distinct() -> None:
    """A duplicate would silently halve somebody's set."""
    codes = generate_codes(200)

    assert len(set(codes)) == len(codes)


@pytest.mark.parametrize(
    "typed",
    ["ABCDE-FGHJK", "abcde-fghjk", "ABCDEFGHJK", "abcde fghjk", "  ABCDE-FGHJK  "],
)
def test_the_ways_somebody_retypes_a_code_all_compare_equal(typed: str) -> None:
    """Case, the hyphen and stray spaces are all things people get wrong.

    The stored value is a hash, so a mismatch here is indistinguishable from a
    wrong code. Somebody reading off paper would be told their recovery code is
    invalid, on the day they most need it to work.
    """
    assert normalise(typed) == "ABCDEFGHJK"


def test_normalising_does_not_make_different_codes_equal() -> None:
    """Forgiving input must not become accepting the wrong code."""
    assert normalise("ABCDE-FGHJK") != normalise("ABCDE-FGHJM")
